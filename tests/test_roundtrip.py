"""Real disposable pg_dump/age/pg_restore, fake in-memory R2; no production resources."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bke_backup_master as b


@unittest.skipUnless(os.getenv("BKE_SYNTHETIC_INTEGRATION") == "1", "manual dispatch only")
class SyntheticRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = "bke-fixture-" + os.urandom(10).hex()
        subprocess.run(["docker","run","-d","--rm","--name",cls.source,"--network","none",
                        "-e","POSTGRES_HOST_AUTH_METHOD=trust","postgres:17"],
                       check=True, stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if subprocess.run(["docker","exec",cls.source,"pg_isready","-U","postgres"],
                                  capture_output=True).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("synthetic PostgreSQL failed to start")
            def sql(query):
                subprocess.run(["docker","exec","-u","postgres",cls.source,"psql","-X",
                                "-v","ON_ERROR_STOP=1","-U","postgres","-d","fixture",
                                "-c",query], check=True, capture_output=True)
            subprocess.run(["docker","exec","-u","postgres",cls.source,"createdb","fixture"],check=True)
            sql("CREATE TABLE public._prisma_migrations (id text PRIMARY KEY, finished_at timestamptz, rolled_back_at timestamptz)")
            sql("INSERT INTO public._prisma_migrations VALUES ('fixture', now(), null)")
            sql('CREATE TABLE public."User" (id integer PRIMARY KEY, email text NOT NULL)')
            sql('CREATE TABLE public."Order" (id integer PRIMARY KEY, user_id integer REFERENCES public."User"(id))')
            for model in ("Payment", "Invoice", "Subscription", "License"):
                sql(f'CREATE TABLE public."{model}" (id integer PRIMARY KEY)')
            sql('INSERT INTO public."User" VALUES (1, \u0027synthetic@example.invalid\u0027)')
            sql('INSERT INTO public."Order" VALUES (1, 1)')
        except Exception:
            subprocess.run(["docker","rm","-f",cls.source],capture_output=True)
            raise

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker","rm","-f",cls.source],capture_output=True)

    def test_encrypted_roundtrip_checksum_and_restore(self):
        with tempfile.TemporaryDirectory(prefix="bke-synthetic-") as directory:
            root=Path(directory)
            private=root/"identity.txt"
            subprocess.run(["age-keygen","-o",str(private)],check=True,capture_output=True)
            private.chmod(0o600)
            recipient=subprocess.run(["age-keygen","-y",str(private)],check=True,
                                     capture_output=True,text=True).stdout.strip()
            wrappers=root/"bin"
            wrappers.mkdir()
            pg=wrappers/"pg_dump"
            pg.write_text("#!/bin/sh\nexec docker exec -u postgres "+self.source+" pg_dump \"$@\"\n")
            pg.chmod(0o700)
            r2={}
            def put(path,key,**kwargs):
                r2[key]=path.read_bytes()
            def get(key,dest,**kwargs):
                if key not in r2:
                    raise b.BackupError("mock R2 missing")
                data=r2[key]
                if len(data)>kwargs.get("max_bytes", 10**12):
                    raise b.BackupError("R2 download exceeds configured safety size")
                with dest.open("xb") as out:
                    out.write(data)
                return hashlib.sha256(data).hexdigest(),len(data)
            pgconfig={"PGHOST":"127.0.0.1","PGPORT":"5432","PGUSER":"postgres",
                      "PGPASSWORD":"fixture","PGDATABASE":"fixture","PGSSLMODE":"disable"}
            receipt_path=root/"receipt-private.json"
            variables={"NEON_READONLY_DATABASE_URL":"postgresql://stub:p@ep-test.neon.tech/fixture?sslmode=require",
                       "AGE_RECIPIENT":recipient,
                       "PATH":str(wrappers)+os.pathsep+os.environ["PATH"]}
            # Synthetic stub only: pg_dump wrapper executes actual pg_dump INSIDE the disposable PG17 container.
            with patch.dict(os.environ,variables), \
                 patch.object(b,"validate_neon_url",return_value=pgconfig), \
                 patch.object(b,"r2_settings",return_value=("a"*32,"fixture","token")), \
                 patch.object(b,"upload_rest",side_effect=put), \
                 patch.object(b,"download_rest",side_effect=get):
                b.backup(SimpleNamespace(receipt_out=str(receipt_path)))
            receipt=json.loads(receipt_path.read_text())
            key=receipt["object_key"]
            self.assertIn(key,r2)
            self.assertIn(key+".receipt.json",r2)
            report_path=root/"report-private.json"
            with patch.object(b,"r2_settings",return_value=("a"*32,"fixture","token")), \
                 patch.object(b,"download_rest",side_effect=get):
                b.restore(SimpleNamespace(object_key=key,identity=str(private),report_out=str(report_path)))
                report=json.loads(report_path.read_text())
                self.assertEqual(report["database_restore"],"PASS")
                self.assertGreaterEqual(report["tables"],3)
                self.assertEqual(report["application_startup"],"NOT_TESTED")
                self.assertEqual(report["critical_table_counts"]["User"],1)
                self.assertEqual(report["critical_table_counts"]["Order"],1)
                self.assertEqual(report["critical_table_counts"]["License"],0)
                original=r2[key]
                r2[key]=original[:-1]+bytes([original[-1]^1])
                with self.assertRaisesRegex(b.BackupError,"CIPHERTEXT_CHECKSUM_MISMATCH"):
                    b.restore(SimpleNamespace(object_key=key,identity=str(private),report_out=None))
                r2[key]=original
                r2[key+".receipt.json"]=b'{"object_key":"tampered"}'
                with self.assertRaises(b.BackupError):
                    b.restore(SimpleNamespace(object_key=key,identity=str(private),report_out=None))

if __name__=="__main__":
    unittest.main()
