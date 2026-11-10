# BKE Backup Master

**Independent PostgreSQL 17 → age → Cloudflare R2 REST encrypted backup + isolated restore.**

Source-only implementation; **production remains LOCKED**. This repository does not depend on Digital Solutions, Licensing Agent, Launcher, Cloudflare Workers or Wrangler.

## Recovery contract

The backup engine runs \`pg_dump -Fc --no-owner --no-acl\` with a direct, unpooled read-only Neon database role. The PostgreSQL dump is **piped directly into age encryption**: no local plaintext dump is written during export. The ciphertext is stored under a UTC and random-UUID-like 128-bit unique key. For objects under 300 MB, upload and download use Cloudflare's native R2 REST API; larger encrypted files require separate scoped S3-compatible multipart API credentials and the optional boto3 dependency. The engine reads the remote ciphertext back, checks SHA-256 and exact size, and only then publishes an adjacent private receipt.

The restore engine takes an **exact archive key** plus an offline \`age\` secret identity file with permissions 0600. It downloads the receipt and ciphertext, verifies SHA-256/size, decrypts, validates PostgreSQL custom-format magic, and restores only inside a newly created \`postgres:17\` Docker container using \`--network none\`. Its new database starts empty; \`pg_restore --no-owner --no-acl --exit-on-error --single-transaction\` must succeed. It then checks a nonempty application schema, Prisma completed/unresolved migration records and invalid foreign keys. On exit it removes the temporary container and volume. There is **no destination URL or existing-container option**.

A successful database restore does **not** prove that Digital Solutions itself starts, or that separate private R2 objects, release binaries, credentials or services can be restored. Full disaster recovery stays on [Digital Solutions issue #317](https://github.com/jan2xo/bke-digital-solutions/issues/317).

## Configuration (protected operator environment only)

| Variable | Scope |
|---|---|
| \`NEON_READONLY_DATABASE_URL\` | Backup only; Neon direct/unpooled TLS URL with dedicated SELECT-only role |
| \`AGE_RECIPIENT\` | Backup-only public age X25519 recipient |
| \`CF_ACCOUNT_ID\` | Cloudflare account ID |
| \`CF_R2_BUCKET\` | Backup bucket |
| \`CF_R2_BACKUP_API_TOKEN\` | Scoped Cloudflare API bearer token |
| \`CF_R2_ACCESS_KEY_ID\`, \`CF_R2_SECRET_ACCESS_KEY\` | Optional scoped S3 API credentials, required **only** for >300 MB |
| \`GITHUB_SHA\` | Optional provenance field for a private receipt |

The private age identity must be kept **offline outside GitHub**, with independently tested recovery. For restore, unset \`NEON_READONLY_DATABASE_URL\` and all production/ambient \`DATABASE_URL\`, \`DIRECT_URL\`, \`PG*\` connection variables. The engine explicitly rejects them before any R2 read. Also keep connection strings, real dumps, local receipts and restore reports out of GitHub.

Requires Python 3.11+, \`pg_dump\` 17, \`age\`, and Docker with PostgreSQL 17 image for restore.

### Authorized operator usage

\`\`\`sh
python3 bke_backup_master.py backup --receipt-out /secure/offline/receipt.json

python3 bke_backup_master.py restore \
  --object-key 'backups/YYYY/MM/DD/YYYYMMDDTHHMMSSZ-<32-hex>.dump.age' \
  --identity /secure/offline/age-identity.txt \
  --report-out /secure/offline/restore-report.json
\`\`\`

Never paste real secrets in command arguments or issue comments. Run recovery on a trusted disposable operator host: Docker access is privileged. The restore Docker container has **no network** and is created afresh every time; the plaintext dump is limited to ephemeral working storage. Protect the host filesystem because crash/abrupt shutdown can leave temporary plaintext behind.

## Manual public-repository production-backup wiring

The owner elected to **keep this source repository public**. Public GitHub repositories can use protected environments with required reviewers and environment secrets. The new `manual-production-backup.yml` is **workflow_dispatch only**, with two read-only GitHub API guard checks before any production credentials can be used. Its first guard rejects a missing/unprotected environment, so a normal workflow cannot silently create an unprotected `Backup-Production` environment. It **never** runs on push, PR or merge, and it is **not** authorized/configured to export production data by this source wave.

See [public-repository operations and environment setup](docs/PUBLIC-REPO-OPERATIONS.md). Never paste secrets into a public discussion, a log or a commit.

## Exact-head certification

- \`pr-guard.yml\`: only cheap Python syntax, safety tests and workflow-trigger hygiene run automatically for PR events.
- \`source-certification.yml\`: **manual \`workflow_dispatch\` only**, requires exact \`expected_sha\`, runs a real PostgreSQL 17 synthetic fixture through real \`pg_dump\` + \`age\`, simulated in-memory R2 upload/download, actual decryption and actual isolated PostgreSQL 17 \`pg_restore\`. Tampered receipts/ciphertext must fail. This proves source pipeline, **not** Cloudflare's live API or a real Neon production archive.
- A manual production backup workflow is provided as an **inert, fail-closed template**, not executed; no production secrets or automatic export/schedule are provisioned. Source certification must pass against the final PR head before SHA-locked merge.
- Keep the repository **public** as requested. Do **not** dispatch the real backup until `Backup-Production` has verified required reviewers and an exact `main` branch deployment policy; the new workflow checks these **before** referencing the environment.

## Operational gates still open

A later separately authorized step must establish protected \`Backup-Production\` and least-privilege account/db credentials on this **public** repository, perform one manually approved real export, independently verify R2 readback, restore on an isolated host, test disposable app startup, and document offline key escrow, alerts and retention. Proposed retention is **7 daily / 4 weekly / 12 monthly** but is not activated; legal holds and deletion require explicit policy.

**Truth labels:** SOURCE SYNTHETIC PASS ≠ PRODUCTION BACKUP PASS ≠ OFFSITE VERIFIED ≠ DATABASE RESTORE PASS ≠ APPLICATION RECOVERY PASS. An R2 object with a receipt still shares the account-level failure domain; cross-provider/off-account backup independence remains separate proof. Unique object keys are not WORM/object-lock enforcement.

Cloudflare reference: https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/objects/methods/upload/
