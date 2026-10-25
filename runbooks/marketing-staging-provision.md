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

Everything it does is additive. It changes no existing table, role, credential, workflow, schedule,
DNS record or firewall rule, and it activates and executes nothing.

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
September. The SSH operations stop with this exact reason before any write.

**Recovery, smallest first, no secrets in chat:**

1. *Do not open SSH to the internet.* Use the Coolify terminal procedure below: the database step runs
   in the terminal of the database resource, which needs no network change.
2. To restore the designed path, the owner must make TCP 22 reachable from where the job runs. GitHub's
   hosted runners have no fixed address, so the honest options are a very wide allow rule (not
   recommended) or restoring the self-hosted `adapteng-ops` runner (it was registered and then failed
   with a 401; it needs a new registration by the owner). Either is a separate decision, and this
   change touches neither.

## Two different backup questions

A fresh backup and a proven restore are different things, and one check cannot stand for both. They are two separate gates.

| Gate | Question | Who answers | Status |
|---|---|---|---|
| **Recovery point** | Is there a recent successful Coolify logical backup of `adapteng_ops`? | automated check (`recovery_point_gate`, newest successful backup at most 36 hours old) and, on the Coolify terminal path, the owner's own look at Coolify, Backups | answerable now |
| **Restore proof** | Has a backup of the production database actually been restored and checked? | a person, with a production-derived restore test | **open: not established** |

What the recovery point check does not show: that the backup can be restored, that it is complete, or
that it covers this change. It reads the age of the newest successful backup job and nothing else.

What exists as evidence today: a **synthetic** full-backup and WAL rehearsal against B2 (not derived
from production data). It shows the tooling works on test data. It is not production restore proof, and
this runbook does not call it one.

Why the open restore proof does not block this change, and what that rests on:

- the change is additive and is proven additive inside its own transaction (object counts of every other
  schema are compared before and after, and any difference aborts it);
- its rollback is to drop the new schema and role (see the rollback table), which needs no restore;
- restoring a production backup would also discard every production write made after the backup, so it
  is not a sensible rollback for this change anyway.

That reasoning is the owner's to accept or reject. The workflow makes the acceptance explicit: the two
database writes (`provision-database`, `configure-n8n`) need the phrase to end in
`ACK-NO-RESTORE-PROOF`. On the Coolify terminal path the same acknowledgement is step 0 below.
Running a production-derived restore test is a separate task with its own approval; it is out of scope
here and nothing here does it.

## What cannot go wrong, and why

| Risk | Control | Proven by |
|---|---|---|
| Arbitrary SQL from a dispatch box | The workflow has three inputs: an operation name, a phrase, and an id checked against `^[A-Za-z0-9]{8,32}$`. All SQL is constants in the script or the pinned migrations | `WorkflowFileTests`, `MigrationScanTests` |
| Wrong container | exactly one running container whose name is the Coolify resource uuid and whose image is Postgres; otherwise stop | `ContainerTests` |
| Wrong migration | commit `fce7fd87...` and a sha256 per file; any difference stops; text with `search_path`, `GRANT`, `CREATE SCHEMA`, `DROP`, `public.`, `adapteng_ops.`, `COPY`, backslash commands is refused | `PinnedSourceTests`, `MigrationScanTests` |
| Objects in `public` or `adapteng_ops` | one transaction; search path pinned to the new schema; before and after object counts of every other schema compared inside the transaction, and any difference aborts it | real PostgreSQL 16 test with a deliberately escaping migration |
| The role reaching production data | inside the same transaction: no privilege on any relation or sequence outside its schema, no CREATE anywhere else, no executable SECURITY DEFINER function outside, no database-level CREATE, no membership; any finding aborts and nothing is committed | real PostgreSQL 16 tests (a PUBLIC-readable table and a PUBLIC SECURITY DEFINER function both stop it) |
| A destructive role | `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT`, 5 connections, no DELETE or TRUNCATE anywhere, no UPDATE on approvals, read-only config | `verify` proof and the authenticated client test |
| A half-finished provisioning | the schema, the role, the tables, the grants, the isolation proof **and the password** are one transaction. There is no state in which the schema exists and the password step is still to come. | `test_owner_sql_is_one_transaction_that_also_stores_the_password`, real PostgreSQL 16 |
| A replay creating or changing things | a guard at the start of the transaction raises if the schema or the role already exists; the script then changes nothing | `test_a_pasted_replay_on_a_finished_provisioning_prints_nothing_and_changes_nothing` |
| A password shown that was never applied | psql does not stop a *pasted* script on an error; the password is printed only if a marker set by the last statement before `COMMIT` exists, otherwise the script prints `NOT APPLIED`. | `test_a_failure_in_the_one_transaction_leaves_no_role_no_schema_and_no_password` |
| A write without a recovery point | the Coolify backup check must show a successful backup no older than 36 hours | `OperationTests` |
| A write by accident | exact confirmation phrase, protected environment with a required reviewer, `main` only | `WorkflowFileTests` |
| A leaked password | generated in memory (or on the database server), masked for GitHub before it exists elsewhere, registered for redaction, sent on stdin and in one request body; no file, argument or output | `test_the_password_never_reaches_the_output` |
| The wrong n8n credential | see "What the credential check proves, and what it cannot" below | `ImportTests`, real n8n 2.31.5 run |
| An existing workflow overwritten | only the six exact names; an active one, one that has executions, or two with one name stops the run | `ImportTests` |
| Drift in an imported workflow | after the import every node, parameter, position, connection and setting of all six is compared with the pinned definition; any difference fails | `test_readback_rejects_every_kind_of_drift` |

## What the credential check proves, and what it cannot

The credential name written in a workflow JSON is only a label; n8n decides what an id is. So
`import-workflows` does not trust the name:

1. It saves the first staging workflow with the Postgres nodes bound to the given id under a name no
   credential has. n8n, on saving, replaces that name with the real name of the credential that has that
   id and is of type Postgres (measured on n8n 2.31.5). If the id is unknown, belongs to another credential
   type, or belongs to a credential with another name (for example the production one), the check fails
   and the workflow is put back with no credential at all.
2. n8n skips that lookup for a node that is disabled, so the probe node must be enabled. It is kept
   harmless: the workflow is inactive, has only the manual trigger, and is saved with **no connections**,
   so the Postgres node cannot be reached from the trigger and cannot run. It exists for a moment.
3. It refuses an id that any non-staging workflow already uses.
4. It imports all six with the Postgres nodes **disabled**, then reads every workflow back and compares
   every node, parameter, position, connection and setting with the pinned definition. Only the
   credential references and the disabled flag may differ, and those are checked separately.

What this proves: the id is a real Postgres credential in this n8n whose name is
`ADAPTENG_MARKETING_STAGING_POSTGRES`, no other workflow uses it, and the six workflows are exactly the
reviewed ones.

What it **cannot** prove: which database user, host and database that credential connects as. The n8n
public API has no endpoint that returns credential data, and n8n allows two credentials to have the same
name. A credential renamed to the staging name but connecting as another user would pass every automated
check. That is why the Postgres nodes stay **disabled** until a person looks at the credential in the
editor and runs `enable-db-nodes`.

On the SSH path (`configure-n8n`) the job itself set the password of the restricted role and created the
credential with that user, then asked n8n what the returned id is, so identity holds by construction and
the nodes are imported enabled.

## Approval gates

| # | Gate | Who | What it covers | What it does not cover |
|---|---|---|---|---|
| 1 | Review and merge of this pull request (the workflow only runs from `main`) | owner | the code, tests, runbook | running anything |
| 2 | GitHub environment `marketing-staging-provision`: required reviewer, deployment branch `main`, administrators cannot bypass | owner, on every write dispatch | that a person released that one write | the content of the write |
| 3 | Confirmation phrase typed at dispatch (table in "Operations") | owner | the right operation was chosen on purpose | anything else |
| 4 | Recovery point: newest successful Coolify backup at most 36 hours old | automated check, and the owner's look in Coolify on the terminal path | that a recent backup job succeeded | restorability |
| 5 | Restore proof: **open**; acknowledged by `ACK-NO-RESTORE-PROOF` in the two database phrases, or by step 0 below | owner | an explicit, recorded acceptance | it is not a restore test |
| 6 | Editor attestation of the credential, then `enable-db-nodes` with its own phrase | owner | the credential's user, database, host and type | n8n cannot be asked these |
| 7 | **Not covered by anything here**, each needs its own approval | owner | activating a workflow; executing a workflow; pointing anything at production; changing credentials, DNS or the SSH firewall; a production restore test; merging | - |

## Operations

| Operation | Writes | Needs SSH | Phrase | What it does |
|---|---|---|---|---|
| `preflight` | no | yes | none | container, state, pins, scan, recovery point, n8n instance; reports restore proof as not established |
| `provision-database` | yes | yes | `PROVISION marketing_staging ACK-NO-RESTORE-PROOF` | the one transaction: schema, role, 009, 010, grants, isolation proof |
| `configure-n8n` | yes | yes | `CONFIGURE n8n marketing_staging ACK-NO-RESTORE-PROOF` | sets a new role password, creates the n8n credential, imports six inactive workflows bound to it (nodes enabled), reads back |
| `import-workflows` | yes | **no** | `IMPORT workflows marketing_staging` | identity check, then imports six inactive workflows with the Postgres nodes **disabled**, exact read back; input `credential_id` |
| `enable-db-nodes` | yes | **no** | `ENABLE database nodes marketing_staging AFTER-EDITOR-CHECK` | after the owner's editor check, enables the Postgres nodes of the six workflows (still inactive); input `credential_id` |
| `verify` | no | yes | none | proves the role reaches nothing outside its schema, and the n8n read back |
| `owner-sql-state` (local file) | no | no | none | writes a read-only SQL file that prints the staging state |
| `owner-sql` (local file) | no | no | none | writes the one provisioning transaction for the Coolify terminal |
| `owner-sql-rotate` (local file) | no | no | none | writes a script that only replaces the role password |

The three `owner-sql*` operations only write a file on your computer. They are not dispatchable from
GitHub and refuse an output path inside the git checkout.

## Procedure through the Coolify terminal (SSH is down)

Do this once, in this order. Nothing is pasted into chat. Stop at the first thing that does not match
what is written here, and send only the text of the error.

**Step 0. Read the two gates.** In Coolify open `adapteng-ops-db`, Backups, and confirm the newest
successful backup is less than 36 hours old (the recovery point). Read "Two different backup questions":
no production restore proof exists. Proceeding means you accept that, knowing the change is additive and
its rollback is a drop.

**Step 1. Merge and environment.** Merge this pull request (your decision). The environment
`marketing-staging-provision` must exist with you as required reviewer and branch `main`.

**Step 2. Make the three SQL files** on your computer, in a checkout of this repository on `main`, with
`gh` signed in. These are Windows PowerShell commands; `$env:USERPROFILE` is how PowerShell names your
user folder (cmd.exe-style variables do not expand there). The files go **outside** the checkout, and the
tool refuses a path inside it. None of them contains a secret.

```
python scripts/marketing_staging.py --operation owner-sql-state --out "$env:USERPROFILE\marketing_staging_state.sql"
```

```
python scripts/marketing_staging.py --operation owner-sql --out "$env:USERPROFILE\marketing_staging_provision.sql"
```

```
python scripts/marketing_staging.py --operation owner-sql-rotate --out "$env:USERPROFILE\marketing_staging_rotate.sql"
```

To put a file on the clipboard for pasting:

```
Get-Content -Raw "$env:USERPROFILE\marketing_staging_state.sql" | Set-Clipboard
```

**Step 3. Look before you change anything.** In Coolify open `adapteng-ops-db`, Terminal, and start
`psql -v ON_ERROR_STOP=1 --no-psqlrc -U "$POSTGRES_USER" -d "$POSTGRES_DB"`. Paste the **state** file.
The last line is `staging_state: <verdict>`. Act on the verdict:

| Verdict | Meaning | What to do |
|---|---|---|
| `absent` | neither the schema nor the role exists | go to step 4 |
| `provisioned-no-password` | the schema and role exist in the exact expected shape, the role has no password (for example created over SSH by `provision-database`, or left by an older two-step script) | **do not run the provision script**; go to step 5 and use the rotate file instead |
| `exact` | provisioned and a password is set | **do not paste the provision file again**; if the password is lost, use the rotate file (step 5); otherwise go to step 6 |
| `drifted` | something exists but not in the expected shape | **stop**; nothing is repaired automatically; send the output of the state file, and do not drop anything to "fix" it |

**Step 4. Paste the provision file** (only on `absent`), whole, in the same terminal. It is one
transaction. Possible outcomes:

- success: no `ERROR` lines; `COMMIT` appears; then the server prints the role's password **once**.
  Do not screenshot or share it. It goes into the n8n form in step 6 and nowhere else;
- failure: an `ERROR` line, then a block saying `NOT APPLIED`. The transaction was rolled back: no schema,
  no role, no password exists. psql does not stop a pasted script, so later lines produce
  `current transaction is aborted` errors; ignore those and read the **first** error only.
  Then paste the state file; it must say `absent` again. Send the first error text.
- the guard `do not replay this script` appears: something already exists. Do not run it again; paste the
  state file and follow its verdict.

Do not close the terminal before the password is in the n8n form. If you lose it, nothing is harmed:
use the rotate file.

**Step 5. Rotate (only when needed).** Paste the rotate file. It changes only the role password, inside
one transaction, and refuses (`nothing to rotate`) if the schema or role is missing. A success prints a
new password once; the previous one stops working at once, so update the n8n credential if it exists.
Then `\q` and close the tab.

**Step 6. Check the result.** Paste the state file again. It must say `staging_state: exact`.

**Step 7. Create the credential** in the n8n editor: Credentials, new, Postgres. Name
`ADAPTENG_MARKETING_STAGING_POSTGRES`; host `pgdgbwzsuuxhw55g8v5opjgn`; port 5432; database
`adapteng_ops`; user `marketing_staging_app`; the password from step 4 or 5; SSL disable; maximum
connections 5. Run the credential test; it must pass. Copy the credential id from the address bar.

**Step 8. Import the workflows with the database nodes disabled.** GitHub, Actions, **Marketing staging
provision**, Run workflow on `main`: operation `import-workflows`, confirm
`IMPORT workflows marketing_staging`, `credential_id` from step 7. Approve the environment prompt. A
refusal naming "credential identity check" means the id is not the credential you think it is; fix that,
do not retry with another id blindly. The job replaces the one workflow that was imported with the
production credential and creates the other five, all inactive, all Postgres nodes disabled.

**Step 9. Editor attestation.** In the n8n editor open the credential and confirm, with your own eyes:
type Postgres, name `ADAPTENG_MARKETING_STAGING_POSTGRES`, host `pgdgbwzsuuxhw55g8v5opjgn`, database
`adapteng_ops`, user `marketing_staging_app`. Open `STG MM-20 Discover` and confirm no staging workflow is
active. This is the one check no tool can do for you.

**Step 10. Enable the database nodes.** Run workflow: operation `enable-db-nodes`, confirm
`ENABLE database nodes marketing_staging AFTER-EDITOR-CHECK`, the same `credential_id`. The workflows stay
inactive. Do not run anything yet; running and activating are separate approvals.

When SSH is restored, run instead: `preflight`, `provision-database`, `configure-n8n`, `verify`.

## Rollback for every step

Only the owner, only after looking at what exists. No operation here drops or deletes anything.

| Step | What it changed | Rollback |
|---|---|---|
| 0, 1 | nothing in the systems | none needed; to stop, simply do not continue |
| 2 | three local files without secrets | delete the files |
| 3 | nothing (read-only) | none needed |
| 4 | one transaction: schema, role, seven tables, one view, grants, password | on `NOT APPLIED`: nothing to roll back, it was atomic. After a success: first paste the state file; run the block below only on `exact` or `provisioned-no-password`, and only after the n8n credential and the six workflows are removed (steps 7 and 8 below) |
| 5 | the role password | none needed; run the rotate file again for a new one |
| 6 | nothing (read-only) | none needed |
| 7 | one n8n credential | delete the credential `ADAPTENG_MARKETING_STAGING_POSTGRES` in the editor, after no workflow uses it |
| 8 | six inactive workflows; the earlier `STG MM-20 Discover` is replaced in place, so the production-credential binding it had is not restored (that binding was the defect) | delete the six workflows named `STG ...` in the editor. They never ran and were never active, so nothing else depends on them |
| 10 | the Postgres nodes of the six workflows enabled | run `import-workflows` again (it returns them to disabled), or disable the nodes in the editor; the workflows stay inactive throughout |

The database rollback, to be pasted in the Coolify terminal after the state file reported `exact` or
`provisioned-no-password`. It is one transaction and fails closed: if the role or the schema is missing,
or the role still owns or holds something, nothing is dropped.

```sql
-- rollback-database
BEGIN;
DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'marketing_staging') THEN
        RAISE EXCEPTION 'nothing to roll back: the schema does not exist';
    END IF;
END
$check$;
DROP SCHEMA marketing_staging CASCADE;
DROP ROLE marketing_staging_app;
COMMIT;
```

Afterwards paste the state file; it must say `absent`. The restore of a production backup is **not** a
rollback for this change, for the reason given in "Two different backup questions".

## Not done

No dispatch, no provisioning, no import, no activation, no merge, no execution. The staging workflows
were never executed. Production, credentials, DNS and the SSH firewall were not touched.
