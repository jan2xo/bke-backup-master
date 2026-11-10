#!/usr/bin/env python3
"""BKE Backup Master: independent PG17 -> age -> Cloudflare R2 archive and isolated restore.

No Digital Solutions application imports, Wrangler, or Cloudflare Worker.
No arbitrary restore destination: recovery always creates its own network-isolated PG17 Docker container.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import Request, urlopen

API_HOST = "api.cloudflare.com"
LIMIT = 299_000_000  # Conservative margin below Cloudflare's 300 MB REST body limit
CHUNK = 1024 * 1024
RECEIPT_VERSION = 1
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
KEY_RE = re.compile(r"^backups/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9TZ_-]+-[0-9a-f]{32}\.dump\.age$")


class BackupError(Exception):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or value.strip() != value:
        raise BackupError(f"missing or malformed required setting: {name}")
    return value


def command(argv: list[str], *, env=None, capture=True) -> str:
    p = subprocess.run(argv, env=env, check=False, text=True,
                       stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    if p.returncode:
        raise BackupError(f"command failed ({Path(argv[0]).name}, exit {p.returncode})")
    return (p.stdout or "").strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def validate_neon_url(raw: str) -> dict[str, str]:
    url = urlsplit(raw)
    if url.scheme not in ("postgres", "postgresql") or not url.hostname or not url.username:
        raise BackupError("NEON_READONLY_DATABASE_URL must be a PostgreSQL connection URL")
    if not url.password or not url.path.strip("/"):
        raise BackupError("Neon connection requires a password and database")
    if not url.hostname.endswith(".neon.tech") or "pooler" in url.hostname.lower():
        raise BackupError("Neon connection must use an unpooled direct .neon.tech host")
    try:
        port = url.port or 5432
    except ValueError as exc:
        raise BackupError("invalid PostgreSQL port") from exc
    if port != 5432:
        raise BackupError("direct Neon PostgreSQL port must be 5432")
    query = parse_qs(url.query, strict_parsing=True)
    if query.get("sslmode") not in (["require"], ["verify-full"]):
        raise BackupError("Neon connection requires explicit sslmode=require or verify-full")
    if set(query) - {"sslmode", "channel_binding"}:
        raise BackupError("unsupported PostgreSQL URL query setting")
    if query.get("channel_binding", ["require"]) != ["require"]:
        raise BackupError("Neon connection requires channel_binding=require if specified")
    return {
        "PGHOST": url.hostname, "PGPORT": str(port), "PGUSER": unquote(url.username),
        "PGPASSWORD": unquote(url.password), "PGDATABASE": unquote(url.path[1:]),
        "PGSSLMODE": query["sslmode"][0], "PGCHANNELBINDING": "require",
        "PGOPTIONS": "-c default_transaction_read_only=on",
    }


def verify_pg17(binary: str, executable: str) -> None:
    version = command([binary, "--version"])
    if not re.search(r"\b17(?:\.\d+)?\b", version):
        raise BackupError(f"{executable} must be PostgreSQL 17")


def make_key() -> str:
    now = datetime.now(timezone.utc)
    return "backups/" + now.strftime("%Y/%m/%d/%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(16) + ".dump.age"


def object_path(account: str, bucket: str, key: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", account, re.I):
        raise BackupError("invalid Cloudflare account identifier")
    if not BUCKET_RE.fullmatch(bucket):
        raise BackupError("invalid Cloudflare bucket name")
    if not key.startswith("backups/") or ".." in key or "\n" in key:
        raise BackupError("invalid R2 archive object key")
    return "/client/v4/accounts/" + account + "/r2/buckets/" + quote(bucket, safe="") + "/objects/" + quote(key, safe="/")


def r2_settings() -> tuple[str, str, str]:
    return required("CF_ACCOUNT_ID"), required("CF_R2_BUCKET"), required("CF_R2_BACKUP_API_TOKEN")


def upload_rest(path: Path, key: str, *, account: str, bucket: str, token: str) -> None:
    if path.stat().st_size > LIMIT:
        raise BackupError("REST archive exceeds 300 MB; S3 multipart credentials required")
    boundary = "bke" + secrets.token_hex(12)
    header = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"body\"; filename=\"archive\"\r\n"
              "Content-Type: application/octet-stream\r\n\r\n").encode()
    trailer = f"\r\n--{boundary}--\r\n".encode()
    size = len(header) + path.stat().st_size + len(trailer)
    conn = http.client.HTTPSConnection(API_HOST, timeout=90)
    try:
        conn.putrequest("PUT", object_path(account, bucket, key))
        conn.putheader("Authorization", "Bearer " + token)
        conn.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        conn.putheader("Content-Length", str(size))
        conn.endheaders()
        conn.send(header)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(CHUNK), b""):
                conn.send(block)
        conn.send(trailer)
        response = conn.getresponse()
        body = response.read(CHUNK)
        if response.status < 200 or response.status >= 300:
            raise BackupError(f"R2 REST upload failed (HTTP {response.status})")
        try:
            result = json.loads(body)
            if result.get("success") is not True or result.get("result", {}).get("key") != key:
                raise ValueError("bad REST upload confirmation")
        except (ValueError, KeyError, AttributeError) as exc:
            raise BackupError("invalid R2 REST upload acknowledgement") from exc
    finally:
        conn.close()


def upload_archive(path: Path, key: str, *, account: str, bucket: str, token: str) -> None:
    if path.stat().st_size <= LIMIT:
        upload_rest(path, key, account=account, bucket=bucket, token=token)
        return
    # Multipart is the S3-compatible HTTP API, not Wrangler or a Worker.
    access = required("CF_R2_ACCESS_KEY_ID")
    secret = required("CF_R2_SECRET_ACCESS_KEY")
    try:
        import boto3
        from boto3.s3.transfer import TransferConfig
    except ImportError as exc:
        raise BackupError("install requirements-multipart.txt for archives over 300 MB") from exc
    client = boto3.client("s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
                          region_name="auto", aws_access_key_id=access, aws_secret_access_key=secret)
    config = TransferConfig(multipart_threshold=64 * CHUNK, multipart_chunksize=64 * CHUNK)
    try:
        client.upload_file(str(path), bucket, key, Config=config)
    except Exception as exc:
        raise BackupError("S3-compatible multipart upload failed") from exc


def download_rest(key: str, dest: Path, *, account: str, bucket: str, token: str,
                  max_bytes: int = 100 * 1024**3) -> tuple[str, int]:
    url = "https://" + API_HOST + object_path(account, bucket, key)
    request = Request(url, headers={"Authorization": "Bearer " + token}, method="GET")
    h = hashlib.sha256()
    size = 0
    try:
        with urlopen(request, timeout=90) as response, dest.open("xb") as f:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise BackupError("R2 download exceeds configured safety size")
                h.update(block)
                f.write(block)
    except BackupError:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise BackupError("R2 download failed") from exc
    return h.hexdigest(), size


def dump_encrypted(destination: Path, source_url: str, recipient: str) -> None:
    if not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
        raise BackupError("AGE_RECIPIENT must be a single age X25519 public recipient")
    safe_base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR")
                 if k in os.environ}
    env = dict(safe_base)
    env.update(validate_neon_url(source_url))
    verify_pg17("pg_dump", "pg_dump")
    if not shutil.which("age"):
        raise BackupError("age encryption binary is required")
    # A pipe that is not concurrently drained can deadlock under heavy pg_dump stderr.
    # Suppress potentially credential-bearing stderr and report only safe process exit codes.
    dump = subprocess.Popen(["pg_dump", "-Fc", "--no-owner", "--no-acl", "-d", env["PGDATABASE"]],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert dump.stdout is not None
    try:
        age = subprocess.Popen(["age", "-r", recipient, "-o", str(destination)],
                               stdin=dump.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=safe_base)
        dump.stdout.close()
        age.wait()
        dump.wait()
        if dump.returncode or age.returncode:
            raise BackupError(f"encrypted pg_dump failed (pg_dump={dump.returncode}, age={age.returncode})")
        if not destination.exists() or destination.stat().st_size < 32:
            raise BackupError("encrypted archive missing or empty")
    except Exception:
        dump.kill() if dump.poll() is None else None
        dump.wait()
        destination.unlink(missing_ok=True)
        raise


def save_private_receipt(receipt: dict, dest: Path) -> None:
    old_umask = os.umask(0o077)
    try:
        with dest.open("x", encoding="utf-8") as f:
            json.dump(receipt, f, sort_keys=True, indent=2)
            f.write("\n")
    finally:
        os.umask(old_umask)


def backup(args) -> None:
    account, bucket, token = r2_settings()
    source = required("NEON_READONLY_DATABASE_URL")
    recipient = required("AGE_RECIPIENT")
    key = make_key()
    with tempfile.TemporaryDirectory(prefix="bke-backup-") as d:
        archive = Path(d) / "archive.age"
        dump_encrypted(archive, source, recipient)
        original_sha = sha256(archive)
        length = archive.stat().st_size
        upload_archive(archive, key, account=account, bucket=bucket, token=token)
        readback = Path(d) / "readback.age"
        found_sha, found_length = download_rest(key, readback, account=account, bucket=bucket, token=token)
        if found_sha != original_sha or found_length != length:
            raise BackupError("OFFSITE_CHECKSUM_MISMATCH")
        receipt = {
            "version": RECEIPT_VERSION, "object_key": key, "ciphertext_sha256": original_sha,
            "ciphertext_size": length, "created_utc": datetime.now(timezone.utc).isoformat(),
            "archive_format": "postgresql17-custom-age-x25519",
            "source_commit": os.environ.get("GITHUB_SHA") or "local",
            "verification": "encrypted-readback-sha256-pass",
        }
        receipt_file = Path(d) / "receipt.json"
        save_private_receipt(receipt, receipt_file)
        upload_rest(receipt_file, key + ".receipt.json", account=account, bucket=bucket, token=token)
        if args.receipt_out:
            save_private_receipt(receipt, Path(args.receipt_out))
    print("BACKUP PASS: ciphertext uploaded and downloaded SHA-256 verified; private R2 receipt stored.")


def validate_receipt(receipt: dict, key: str) -> None:
    if not KEY_RE.fullmatch(key) or receipt.get("object_key") != key:
        raise BackupError("receipt object key mismatch")
    if receipt.get("version") != RECEIPT_VERSION:
        raise BackupError("unsupported receipt version")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("ciphertext_sha256", ""))):
        raise BackupError("invalid ciphertext checksum")
    size = receipt.get("ciphertext_size")
    if type(size) is not int or not (32 <= size <= 100 * 1024**3):
        raise BackupError("invalid ciphertext size")
    if receipt.get("archive_format") != "postgresql17-custom-age-x25519":
        raise BackupError("unsupported archive format")


def isolation_preflight() -> None:
    # Refuse any ambient database configuration before doing even a read from R2.
    forbidden = {"DATABASE_URL", "DIRECT_URL", "NEON_READONLY_DATABASE_URL",
                 "PRODUCTION_DATABASE_URL", "RESTORE_TARGET_DATABASE_URL",
                 "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSERVICE"}
    present = sorted(name for name in os.environ if os.environ.get(name) and (name in forbidden or name.startswith("PG")))
    if present:
        raise BackupError("unsafe restore environment contains database destination/source settings: " +
                          ", ".join(present))
    if not shutil.which("docker"):
        raise BackupError("Docker is required for a fresh, isolated restore destination")
    if not shutil.which("age"):
        raise BackupError("age is required for offline decryption")
    if any(os.environ.get(name) for name in ("DOCKER_HOST", "DOCKER_CONTEXT",
                                            "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")):
        raise BackupError("restore refuses Docker endpoint/context environment overrides")
    try:
        contexts = json.loads(command(["docker", "context", "inspect"]))
        address = contexts[0]["Endpoints"]["docker"]["Host"]
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        raise BackupError("cannot verify Docker daemon endpoint") from exc
    if not address.startswith("unix://"):
        raise BackupError("restore requires a local Unix-socket Docker engine")


def docker(argv: list[str]) -> str:
    return command(["docker", *argv])


def restore_isolated(archive: Path) -> dict:
    # NEVER accepts an arbitrary host/URL/database name or pre-existing container.
    nonce = secrets.token_hex(12)
    container = "bke-restore-" + nonce
    volume = "bke-restore-" + nonce
    db = "bke_restore_" + nonce
    created_volume = False
    started = False
    try:
        if subprocess.run(["docker", "volume", "inspect", volume], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            raise BackupError("RESTORE_VOLUME_ALREADY_EXISTS")
        docker(["volume", "create", volume])
        created_volume = True
        docker(["run", "--rm", "-d", "--name", container, "--network", "none",
                "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
                "--env", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17"])
        started = True
        meta = json.loads(docker(["inspect", container]))
        if len(meta) != 1 or meta[0]["HostConfig"]["NetworkMode"] != "none" or not meta[0]["State"]["Running"]:
            raise BackupError("isolated container verification failed")
        for _ in range(45):
            p = subprocess.run(["docker", "exec", container, "pg_isready", "-U", "postgres"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if p.returncode == 0:
                break
            time.sleep(1)
        else:
            raise BackupError("isolated PostgreSQL did not become ready")
        version = docker(["exec", container, "psql", "-U", "postgres", "-Atc", "SHOW server_version_num"])
        if not version.startswith("17"):
            raise BackupError("isolated PostgreSQL must be version 17")
        docker(["exec", container, "createdb", "-U", "postgres", db])
        empty = docker(["exec", container, "psql", "-U", "postgres", "-d", db, "-Atc",
                        "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"])
        if empty != "0":
            raise BackupError("RESTORE_TARGET_NOT_EMPTY")
        docker(["cp", str(archive), container + ":/tmp/source.dump"])
        docker(["exec", container, "pg_restore", "--no-owner", "--no-acl",
                "--exit-on-error", "--single-transaction", "-U", "postgres",
                "-d", db, "/tmp/source.dump"])
        tables = int(docker(["exec", container, "psql", "-U", "postgres", "-d", db, "-Atc",
                             "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"]))
        if tables < 1:
            raise BackupError("RESTORE_HAS_NO_APPLICATION_TABLES")
        has_migrations = docker(["exec", container, "psql", "-U", "postgres", "-d", db,
                                 "-Atc", "SELECT to_regclass('public._prisma_migrations') IS NOT NULL"])
        if has_migrations != "t":
            raise BackupError("PRISMA_MIGRATIONS_MISSING")
        migration_rows = docker(["exec", container, "psql", "-U", "postgres", "-d", db, "-Atc",
                                "SELECT count(*) FROM public._prisma_migrations WHERE finished_at IS NOT NULL AND rolled_back_at IS NULL"])
        if int(migration_rows) < 1:
            raise BackupError("NO_COMPLETED_PRISMA_MIGRATIONS")
        pending = docker(["exec", container, "psql", "-U", "postgres", "-d", db, "-Atc",
                         "SELECT count(*) FROM public._prisma_migrations WHERE finished_at IS NULL AND rolled_back_at IS NULL"])
        if int(pending):
            raise BackupError("UNFINISHED_PRISMA_MIGRATIONS")
        unvalidated = docker(["exec", container, "psql", "-U", "postgres", "-d", db, "-Atc",
                              "SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated"])
        if int(unvalidated):
            raise BackupError("UNVALIDATED_FOREIGN_KEYS")
        critical_counts = {}
        for table in ("User", "Order", "Payment", "Invoice", "Subscription", "License"):
            count = docker(["exec", container, "psql", "-U", "postgres", "-d", db, "-Atc",
                            f'SELECT count(*) FROM public."{table}"'])
            critical_counts[table] = int(count)
        return {"database_restore": "PASS", "pg_major": 17, "tables": tables,
                "critical_table_counts": critical_counts,
                "successful_prisma_migrations": int(migration_rows),
                "application_startup": "NOT_TESTED", "target": "fresh-network-none-container-removed"}
    finally:
        cleanup_errors = []
        if started:
            result = subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            if result.returncode:
                cleanup_errors.append("container")
        if created_volume:
            result = subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
            if result.returncode:
                cleanup_errors.append("volume")
        if cleanup_errors:
            raise BackupError("ISOLATED_RESTORE_CLEANUP_FAILED: " + ",".join(cleanup_errors))


def restore(args) -> None:
    isolation_preflight()
    identity = Path(args.identity)
    if not identity.is_file() or identity.is_symlink():
        raise BackupError("offline age identity must be an existing regular local file")
    if identity.stat().st_mode & 0o077:
        raise BackupError("offline age identity permissions must be 0600 or stricter")
    key = args.object_key
    if not KEY_RE.fullmatch(key):
        raise BackupError("invalid object key")
    account, bucket, token = r2_settings()
    with tempfile.TemporaryDirectory(prefix="bke-restore-") as d:
        directory = Path(d)
        receipt_path = directory / "receipt.json"
        download_rest(key + ".receipt.json", receipt_path, account=account, bucket=bucket, token=token,
                      max_bytes=CHUNK)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise BackupError("invalid remote receipt") from exc
        validate_receipt(receipt, key)
        encrypted = directory / "encrypted.age"
        ciphertext_sha, size = download_rest(key, encrypted, account=account, bucket=bucket,
                                             token=token, max_bytes=receipt["ciphertext_size"])
        if ciphertext_sha != receipt["ciphertext_sha256"] or size != receipt["ciphertext_size"]:
            raise BackupError("CIPHERTEXT_CHECKSUM_MISMATCH")
        plaintext = directory / "plaintext.dump"
        command(["age", "-d", "-i", str(identity), "-o", str(plaintext), str(encrypted)])
        with plaintext.open("rb") as dump_stream:
            magic = dump_stream.read(5)
        if magic != b"PGDMP":
            raise BackupError("DECRYPTED_ARCHIVE_IS_NOT_CUSTOM_PGDUMP")
        report = restore_isolated(plaintext)
        if args.report_out:
            save_private_receipt(report, Path(args.report_out))
        print("DATABASE RESTORE PASS: fresh offline PostgreSQL 17 validated; application startup NOT TESTED.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    b = sub.add_parser("backup", help="manual-only encrypted backup")
    b.add_argument("--receipt-out", help="private local receipt path; must not be in GitHub")
    r = sub.add_parser("restore", help="download and restore into a NEW offline Docker PostgreSQL 17")
    r.add_argument("--object-key", required=True)
    r.add_argument("--identity", required=True, help="offline age secret identity file (chmod 600)")
    r.add_argument("--report-out", help="private local verification report path")
    args = parser.parse_args(argv)
    old_umask = os.umask(0o077)
    try:
        if args.action == "backup":
            backup(args)
        else:
            restore(args)
        return 0
    except (BackupError, OSError, subprocess.SubprocessError) as exc:
        # Do not dump subprocess stderr: it can contain connection strings or provider details.
        print("FAIL: " + str(exc), file=sys.stderr)
        return 1
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    sys.exit(main())
