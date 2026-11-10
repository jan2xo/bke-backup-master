# Autonomous PostgreSQL backups + recovery-request contract

**Status:** Source only; automatic production export is **DISARMED** until the repository owner explicitly configures the environment and sets a repository variable. Keep the source repo PUBLIC if preferred. No application Worker, standalone server, Wrangler or separate backup engine is required.

## Scheduled backup: 03:17 Asia/Manila daily

The canonical backup workflow is `.github/workflows/scheduled-encrypted-backup.yml`. Its schedule is `17 19 * * *` (UTC, equivalent to 03:17 Asia/Manila next day), and its only other trigger is a manual `workflow_dispatch`. The default-branch schedule is separate from CI; pushes and PRs DO NOT start production exports.

The scheduler is **disarmed** unless the repository-level Actions variable `BACKUP_AUTOMATION_ENABLED` is exactly `true`. When armed, it first calls `scripts/check_environment.py` without production secrets to verify `Backup-Production` already exists and allows only the exact `main` branch. An absent/misconfigured environment fails closed before a job can auto-create an unprotected environment. No reviewer is required to approve routine backups; do NOT configure required reviewers on this environment or the schedule will stall.

After the preflight, the scheduled job enters the restricted `Backup-Production` environment, rechecks the branch policy and activation, runs PostgreSQL 17 `pg_dump` with a direct, read-only Neon connection, pipes the output into age encryption and uploads ciphertext to a private Cloudflare R2 bucket. It downloads the ciphertext, validates SHA-256/size and saves a private receipt. GitHub artifacts/logs must never include the archive, source URL, receipt content, credentials, or the private age identity.

### One-time GitHub configuration

1. Repository **Settings → Environments → Backup-Production**. Create that environment explicitly; select **Selected branches and tags → main only**, exact pattern without wildcards. Reviewers optional for manual tasks but **disable required reviewers for this scheduled backup lane**.
2. Under that environment, configure **secrets** `NEON_READONLY_DATABASE_URL` (direct, unpooled Neon PG17; separate minimally privileged read-only role), `CF_ACCOUNT_ID`, `CF_R2_BACKUP_API_TOKEN`. For archives larger than REST's 300 MB endpoint limit, additionally configure scoped `CF_R2_ACCESS_KEY_ID` / `CF_R2_SECRET_ACCESS_KEY`.
3. Configure **environment variable** `AGE_RECIPIENT` containing only the public age recipient key. Keep the corresponding age **private identity offline**, independently escrowed, NEVER as a GitHub secret or source file.
4. With the environment, key escrow and read-only grants verified, set the **repository-level Actions variable `BACKUP_AUTOMATION_ENABLED=true`**. This activates the scheduled workflow on `main`; leave unset/false to disable exports. Configure an appropriate alert/health check and monitor completed backups before treating the schedule as reliable.
5. No real production export, secret provisioning or activation occurs during this source-only PR.

**Timing limitations:** GitHub cron is best-effort. It can be delayed/dropped under load, and on public repositories scheduled workflows automatically disable after **60 days of repository inactivity**. This is not a guaranteed RPO or 24/7 scheduler. Monitor the age of the last verified backup independently and alert on gaps. Configure external scheduler/watchdog only after separate scope review; don't use an unsolicited public webhook for backup execution.

## Digital Solutions recovery request

**Do not use `repository_dispatch` for this integration:** GitHub requires **Contents:write**, which would grant Digital Solutions authority to change the backup source. Use GitHub **Issues:write only** with a narrowly scoped GitHub App installation token. Digital Solutions may create an issue with an opaque request ID and commit SHA; the `issues.opened` event wakes a read-only validation workflow in this repository. No R2 credentials, age identity, database URL or backup object key is sent from Digital Solutions.

Example public issue contract (only non-secret data):

Title:
```text
BKE RECOVERY REQUEST 11111111-2222-4333-8444-555555555555
```
Body:
```json
{"request_id":"11111111-2222-4333-8444-555555555555","source":"bke-digital-solutions","intent":"RESTORE_ISOLATED_REQUEST_ONLY","source_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

Configure repository-level Actions variable `RESTORE_DISPATCH_SENDER_ID` to the **numeric GitHub App sender ID** authorized to create requests. Despite its historical name, this is used for the **Issues-only** receiver. The workflow checks the actual GitHub event sender and issue creator, correct UUID v4 title/body, exact fixed fields, source name, immutable commit, and isolated request-only intent. Unrecognized public Issues do not allocate a runner. Only the GitHub App with Issues:write may submit trusted requests; it must NOT have Contents:write or Actions:write on Backup Master.

When accepted, GitHub Actions records a durable `RECOVERY REQUEST ACCEPTED` result for that issue. This is an **acknowledgment of intent, not a restore**. The GitHub issue and workflow run are the request ledger. No automatic download, decryption, restore, production connection, callback URL or retention deletion occurs. Duplicate issues may produce duplicate acknowledgments; operators must deduplicate by request ID before any future recovery activity.

**Recovery execution remains separate:** An operator chooses a verified archive, uses the **offline age identity** on a trusted, disposable machine, restores into a **new empty network-isolated PostgreSQL 17** container, and independently verifies schema/migrations/application startup. No production database destination is accepted by the restore tool. Requests to replace production must not be implemented as a webhook action.

### Recovery integration status

Backup Master now defines the **receiver contract only**. No Digital Solutions API route or GitHub App sender has been deployed, no token has been provisioned, and no live GitHub Issues request has been executed. Changes in Digital Solutions' authority/authorization/security path belong to its own explicitly certified integration wave.

## Evidence and outstanding gates

A successful disposable test proves only **source behavior**. The following remain separately verifiable: real Neon read-only export, actual R2 ciphertext upload/readback, independent receipt custody and restore identity, isolated production-data PG17 restore, disposable Digital Solutions application startup, external private R2 objects/provider secrets/software releases, retention and alerts. Track full disaster recovery at https://github.com/jan2xo/bke-digital-solutions/issues/317.
