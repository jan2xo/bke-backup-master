# Public repository — backup operations hardening

Repository visibility: **PUBLIC by owner decision**. Public source is allowed. **Never** commit Neon URLs, Cloudflare access tokens, offline age identities, production database dumps, object inventories or private receipt/report metadata.

On GitHub Free, protected deployment environments with required reviewers and environment secrets are available for **public** repositories. Keeping the code public does not itself authorize a production backup.

## Safety protocol

\`manual-production-backup.yml\` is the only production-data runner. It has only an explicit \`workflow_dispatch\` trigger; no push, PR, merge, schedule, cron or workflow_call starts a real backup.

It runs two successive security checks:

1. A **no-secrets** job refuses anything other than a manual, exact-40-character \`main\` SHA request naming the \`BACKUP_ONLY_NO_RESTORE\` operation. It queries GitHub's current environment policy **before** any job references that environment, refusing missing/blank required reviewers or non-\`main\` deployment branch policies. This also prevents a workflow from silently creating a new **unprotected** environment on first use.
2. Only if #1 passes will GitHub request approval for the \`Backup-Production\` environment. Once the reviewer approves, the backup job **rechecks the environment policy** before invoking \`pg_dump\` or receiving credentials in the execution step.

If any Github API request fails or returns unexpected structure, fail closed. Do **not** bypass the gate to make it green. Keep \`GitHub Actions\` audit records and reject environment changes that do not contain a positive, explicit human review.

**Current status:** no production environment or secrets have been created by this source wave; the workflow is inert until an operator deliberately configures it.

## One-time manual configuration (GitHub Settings)

1. Open \`jan2xo/bke-backup-master\` → **Settings → Environments → New environment**.
2. Name it **\`Backup-Production\`**.
3. Turn on **Required reviewers**, select at least one real GitHub reviewer (not an automation user). The job will remain paused for approval; do not bypass it.
4. Set **Deployment branches and tags** to **Selected branches and tags** and allow **only the exact branch \`main\`**. No \`*\`, \`main-*\` or feature branches. A missing environment or permissive branch rule makes the preflight reject execution.
5. Add **environment secrets**, never repository-wide secrets:
   - \`NEON_READONLY_DATABASE_URL\` — dedicated direct/unpooled Neon PostgreSQL 17 account, read-only \`pg_dump\` privilege, TLS and \`channel_binding=require\`.
   - \`CF_ACCOUNT_ID\` — Cloudflare account ID.
   - \`CF_R2_BACKUP_API_TOKEN\` — restricted backup bucket token allowing upload and readback. Prefer separate tokens for restore.
   - Optional \`CF_R2_ACCESS_KEY_ID\` / \`CF_R2_SECRET_ACCESS_KEY\` — restricted S3-compatible credentials for files exceeding the REST size limit. Without these, the engine fails closed for larger backups.
6. Add **environment variable** \`AGE_RECIPIENT\` — **public** X25519 recipient, \`age1...\`. The private identity must be escrowed **OFF GitHub** (not as a secret, not on a runner, not in a code review, not in an issue).
7. Verify from the GitHub Settings page that required reviewers and the **exact \`main\`** branch policy have persisted. **Do not start the real job until separate production-backup authorization.**

### Production backup — authorized operators only

After explicit approval to export production data, the operator checks the current \`main\` SHA and selects **Actions → manual-production-backup → Run workflow**, branch \`main\`, entering exactly that SHA and \`BACKUP_ONLY_NO_RESTORE\`. Then, after inspecting the source commit and recipient, the configured reviewer approves the pending \`Backup-Production\` deployment.

The pipeline invokes \`pg_dump\` via official PostgreSQL 17 container, pipes stdout to \`age\`, uploads ciphertext to private R2, downloads and SHA-256 verifies readback, then stores a private receipt adjacent to the encrypted object. Do not export results, dumps, receipts, identities, GitHub artifacts, or archive keys to comments/logs.

No production restore workflow exists. The private identity remains on an independently controlled operator computer for a **separately approved**, isolated PostgreSQL 17 restoration proof.

## Production operational limits and next gates

- Public source is not proof of secret safety: environment reviewers and branch limits are operational controls that must be **actually configured and verified**.
- **No R2 object lock/WORM guarantees** from random object keys. Access-token scope and private-bucket policy require review.
- R2 in the same Cloudflare account is not an independently verified account/provider recovery copy.
- The database-only result cannot restore independent private object store, external provider secrets, application binaries or a live deployment.
- Retention proposal (7 daily/4 weekly/12 monthly), alerting, second independent encrypted copy, restore drills and disposable Digital Solutions startup remain separate gates. Nothing auto-deletes, schedules or restores.
- Do not mark [Digital Solutions issue #317](https://github.com/jan2xo/bke-digital-solutions/issues/317) closed until every required dependency has restoration evidence.

**Certification policy:** PR is a ledger; cheap PR Guard on change, synthetic source certification explicitly dispatched for exact SHA, SHA-locked merge only after PASS. Operational/production proofs always remain separate.
