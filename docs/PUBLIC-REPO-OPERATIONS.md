# Public repository security and automated backup operations

**Repository visibility: PUBLIC (owner decision).** Public source is acceptable. Private dump archives, encryption identity, production database credentials and sensitive recovery metadata must never be placed in source, issues, logs or GitHub artifacts.

## Routine backup: no reviewer

The original manual-only reviewer workflow has been superseded by [the autonomous backup and recovery request contract](AUTOMATED-RECOVERY-CONTRACT.md). The single current production-export owner is `.github/workflows/scheduled-encrypted-backup.yml`.

- **Backup trigger:** daily 03:17 Asia/Manila via GitHub Actions cron, with optional manual dispatch. Never on PR or push.
- **Activation:** unset/false `BACKUP_AUTOMATION_ENABLED` (repository-level Actions variable) means **no export**. Only `true` enables the job. It is not activated by adding source files to GitHub.
- **Environment:** `Backup-Production` must explicitly exist, allow **main** only, and contain the minimal backup-scoped secrets. The initial no-secrets guard verifies the environment before any export job references it, preventing auto-created unprotected environments.
- **Reviewers:** routine backups run **without required reviewers**. Put approvals on independent *destructive recovery* operations, not on read-only backups.
- **Encryption:** backup uses only public age recipient; offline private identity is **outside GitHub**.
- **Restore requests:** GitHub `issues.opened` with a narrowly scoped **Issues:write** GitHub App token. No repository_dispatch Contents:write token, no restore credentials, no production destination, no automatic restoration.
- **Operational proof:** source-only CI does not prove real production exports; manually configure the environment, verify role/token scope, test actual R2 readback and isolated recoverability, then arm the schedule. GitHub cron is best-effort and may be dropped or disabled after 60 inactive days on a public repository.

Read the [full protocol](AUTOMATED-RECOVERY-CONTRACT.md) before configuring any secrets or activating production exports. Full disaster recovery still requires independent private-object and provider recovery and is tracked at https://github.com/jan2xo/bke-digital-solutions/issues/317.
