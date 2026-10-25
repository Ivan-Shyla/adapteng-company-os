# Marketing staging provision

Status: **prepared, not run.** Nothing here has created a schema, a role, a credential or a
workflow. The code is `scripts/marketing_staging.py`; the workflow is
`.github/workflows/marketing-staging-provision.yml`; the tests are
`scripts/test_marketing_staging.py`.

It prepares the isolated environment the Marketing pipeline needs on the self-hosted instance
(see `n8n/exports/manual/marketing-staging-20261025/README.md` in the platform repository):

- schema `marketing_staging` and role `marketing_staging_app` in the existing `adapteng_ops` database;
- migrations 009 and 010 applied inside that schema only;
- one n8n credential, `ADAPTENG_MARKETING_STAGING_POSTGRES`, and six inactive staging workflows
  bound to it, replacing the production credential that the connector bound to the first one.

## The SSH channel (diagnosis)

| Check | Result |
|---|---|
| Variables `PROD_SSH_HOST`, `PROD_SSH_PORT`, `PROD_SSH_USER` | present |
| Secrets `PROD_SSH_PRIVATE_KEY`, `PROD_SSH_KNOWN_HOSTS` | present (set 2026-08-03; values not read) |
| Runs of `prod-ssh.yml` | three, 2026-09-07 and 2026-09-14 (two), all failed the same way |
| Failure | `ssh: connect to host ... port 22: Connection timed out`, return code 255 |
| TCP probe of the same host and port from the owner's own computer, 2026-10-25 | also times out |

**Cause:** the TCP connection to port 22 is not answered, from GitHub's runners and from the
owner's computer alike. That is a network layer problem (a host or provider firewall that does not
admit SSH, or sshd listening elsewhere), not a key, host-key or script problem: the failure happens
before any key is offered. The channel's state today is not otherwise known; the last run was in
September. The new operations stop with this exact reason before any write.

**Recovery, smallest first, no secrets in chat:**

1. *Do not open SSH to the internet.* Use the owner-run path below: the database step runs in the
   Coolify terminal of the database resource, which needs no network change.
2. To restore the designed path, the owner must make TCP 22 reachable from where the job runs. GitHub's
   hosted runners have no fixed address, so the honest options are a very wide allow rule (not
   recommended) or restoring the self-hosted `adapteng-ops` runner (it was registered and then failed
   with a 401; it needs a new registration by the owner). Either is a separate decision.

## What cannot go wrong, and why

| Risk | Control | Proven by |
|---|---|---|
| Arbitrary SQL from a dispatch box | The workflow has three inputs: an operation name, a phrase, and an id checked against `^[A-Za-z0-9]{8,32}$`. All SQL is constants in the script or the pinned migrations | `WorkflowFileTests`, `MigrationScanTests` |
| Wrong container | exactly one running container whose name is the Coolify resource uuid and whose image is Postgres; otherwise stop | `ContainerTests` |
| Wrong migration | commit `fce7fd87...` and a sha256 per file; any difference stops; text with `search_path`, `GRANT`, `CREATE SCHEMA`, `DROP`, `public.`, `adapteng_ops.`, `COPY`, backslash commands is refused | `PinnedSourceTests`, `MigrationScanTests` |
| Objects in `public` or `adapteng_ops` | one transaction; search path pinned to the new schema; before and after object counts of every other schema compared inside the transaction, and any difference aborts it | real PostgreSQL 16 test with a deliberately escaping migration |
| The role reaching production data | inside the same transaction: no privilege on any relation or sequence outside its schema, no CREATE anywhere else, no executable SECURITY DEFINER function outside, no database-level CREATE, no membership; any finding aborts and nothing is committed | real PostgreSQL 16 tests (a PUBLIC-readable table and a PUBLIC SECURITY DEFINER function both stop it) |
| A destructive role | `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT`, 5 connections, no DELETE or TRUNCATE anywhere, no UPDATE on approvals, read-only config | `verify` proof and the authenticated client test |
| A write without a recovery point | the Coolify backup check must show a successful backup no older than 36 hours | `OperationTests` |
| A write by accident | exact confirmation phrase, protected environment with a required reviewer, `main` only | `WorkflowFileTests` |
| A leaked password | generated in memory, masked for GitHub before it exists elsewhere, registered for redaction, sent on stdin and in one request body; no file, argument or output | `test_the_password_never_reaches_the_output` |
| The wrong n8n credential | every Postgres node bound to the staging credential by name and id; the id must not be used by any non-staging workflow; nodes enabled; workflows inactive, manual only, never run | `ImportTests` |
| An existing workflow overwritten | only the six exact names; an active one, one that has executions, or two with one name stops the run | `ImportTests` |

## Approval gates

1. Review and merge of this pull request (the workflow only runs from `main`).
2. The GitHub environment `marketing-staging-provision`: required reviewer (the owner), deployment
   branch `main`. Writes wait for it.
3. The confirmation phrase, typed at dispatch: `PROVISION marketing_staging`,
   `CONFIGURE n8n marketing_staging` or `IMPORT workflows marketing_staging`.
4. A backup no older than 36 hours.
5. For the owner-run path, the owner's own check of the backup in Coolify before pasting.

## Operations

| Operation | Writes | Needs SSH | What it does |
|---|---|---|---|
| `preflight` | no | yes | container, state, pins, scan, backup age, n8n instance |
| `provision-database` | yes | yes | the one transaction: schema, role, 009, 010, grants, isolation proof |
| `configure-n8n` | yes | yes | sets a new role password, creates the n8n credential, imports six inactive workflows bound to it, reads back |
| `import-workflows` | yes | **no** | imports the six workflows bound to a credential the owner created, reads back |
| `verify` | no | yes | proves the role reaches nothing outside its schema, and the n8n readback |
| `owner-sql` (local only) | no | no | writes the same transaction to a file for the Coolify terminal |

`configure-n8n` is the complete path once SSH works. `import-workflows` plus `owner-sql` is the path
that needs no SSH.

## The one instruction for the owner (SSH is down)

Do this once, in this order. Nothing in it is pasted into chat.

1. **Backup.** In Coolify open `adapteng-ops-db`, Backups, and confirm the newest successful backup is
   less than 36 hours old (the `Backup freshness` workflow is the same check).
2. **Merge** this pull request, then in GitHub, Settings, Environments, create
   `marketing-staging-provision` with yourself as required reviewer and deployment branch `main`
   (already done if the pull request says so).
3. **Make the SQL file** on your computer, in a checkout of this repository on `main`, with `gh` signed in:
   ```
   python scripts/marketing_staging.py --operation owner-sql --out %USERPROFILE%\marketing_staging_provision.sql
   ```
   The file contains no secret. It was built from the pinned migrations and checked against their hashes.
4. **Run it on the database.** In Coolify open `adapteng-ops-db`, Terminal, start
   `psql -v ON_ERROR_STOP=1 --no-psqlrc -U "$POSTGRES_USER" -d "$POSTGRES_DB"`, paste the whole file,
   then `\q`. If an error appears, nothing was created (it is one transaction); stop and report the error
   text. Otherwise two `COMMIT` lines appear and the server prints the new role's password **once**.
5. **Create the credential** in the n8n editor: Credentials, new, Postgres. Name
   `ADAPTENG_MARKETING_STAGING_POSTGRES`; host `pgdgbwzsuuxhw55g8v5opjgn`; port 5432; database
   `adapteng_ops`; user `marketing_staging_app`; the password from step 4; SSL disable; maximum
   connections 5. Run the credential test; it must pass. Copy the credential id from the address bar.
   Close the Coolify terminal tab.
6. **Import the workflows.** GitHub, Actions, **Marketing staging provision**, Run workflow on `main`:
   operation `import-workflows`, confirm `IMPORT workflows marketing_staging`, credential_id from
   step 5. Approve the environment prompt. The job replaces the one workflow that was imported with the
   production credential and creates the other five, all inactive.
7. **Check in the editor** that `STG MM-20 Discover` shows `ADAPTENG_MARKETING_STAGING_POSTGRES` on its
   Postgres node and that no staging workflow is active. Do not run anything yet.

When SSH is restored, run instead: `preflight`, `provision-database`, `configure-n8n`, `verify`.

## Undo

Only the owner, only after looking at what exists: in the n8n editor delete the six workflows named
`STG ...` and the credential `ADAPTENG_MARKETING_STAGING_POSTGRES`; on the database, after listing
`\dn+` and `\du`, `DROP SCHEMA marketing_staging CASCADE;` and `DROP ROLE marketing_staging_app;`.
No operation here drops anything.

## Not done

No dispatch, no provisioning, no import, no activation, no merge. The staging workflows were never
executed.
