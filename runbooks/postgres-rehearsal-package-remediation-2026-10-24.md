# pgBackRest rehearsal package remediation — 2026-10-24

Status: **repo-only draft; not dispatched, not merged, not recovery-accepted**.

## Context
The isolated GitHub Actions job `PostgreSQL backup and restore rehearsal`
passed 11 times in the 2026-09-25..2026-10-05 window but has failed on each of
19 observed runs from 2026-10-06 to 2026-10-24. Both sampled failures terminate
in installation: `PGDG no longer offers 2.59.0 for noble`. No backup or restore
occurred in those failed runs. This is a test supply-chain failure; it does
**not** demonstrate damaged production backups or unavailable B2.

Evidence: [run 40076562988](https://github.com/Ivan-Shyla/adapteng-company-os/actions/runs/40076562988).

## Decision (pending owner approval)
- Eliminate dependency on PGDG retaining an old `pgbackrest=2.59.0-...` deb.
- Download upstream distribution tarball for **2.59.3** (2.59.x patch release
  with October 4, 2026 weak-encryption-subkey fix).
- Verify exact SHA-256 against the GitHub immutable release asset digest
  `14037901db002e5536a948bf9f0fc0ff6cde31f4e675d3e9b46f129071bf2e5f`
  *before extraction or compilation*; fail closed if unavailable/mismatched.
- Build into disposable runner using meson/ninja; check executable version and
  record its measured binary hash. Only upstream *source bytes* are pinned:
  the hosted runner, compiler, PostgreSQL packages and compiled output still
  lack an immutable toolchain manifest. Do not mislabel this as the full
  Phase-0 executable immutability gate.
- Remove `on.push` from the real B2 rehearsal workflow: editing it in a PR
  must never silently run real B2 operations. Existing default-branch daily
  schedule remains unchanged before an approved merge.
- Preserve B2 run-scoped prefix isolation and existing production-connection
  denial logic. No production database credentials or restores are introduced.

Source: [pgBackRest upstream release assets](https://github.com/pgbackrest/pgbackrest/releases);
[upstream security advisory](https://pgbackrest.org/news.html).


## First owner-approved isolated B2 rehearsal — FAILED

- GitHub run: https://github.com/Ivan-Shyla/adapteng-company-os/actions/runs/40128239401
- Branch commit at execution: `6add716eff79dab75f0c9660ad26319caa5163c7`.
- Source tarball SHA verification, PostgreSQL 16 setup, isolation guard,
  B2 capabilities, stanza-create and `pgbackrest check` succeeded.
- The check archived the first WAL segment successfully. Full backup began
  but aborted with pgBackRest error `[082]`: next required WAL segment
  `000000010000000000000002` was not archived within 60000 ms.
- Post-failure B2 probes returned listing 200, archive.info 200,
  absent-key 404. These observations rule out a blanket connectivity denial,
  but do not identify why the PostgreSQL archiver did not deliver that WAL.
- Immediate and WAL replay restore steps were skipped. Cleanup passed:
  rehearsal prefix empty, production prefix object count 0 both before/after.
- No data loss, production outage, or successful restore is evidenced.

**Decision:** block merge and do not increase archive timeout blindly.
Next patch adds a *failure-only, sanitized* local `pg_stat_archiver` query
and counts of archive error categories/exit codes from the ephemeral logs;
it does not print raw log contents and cannot read production state.

**Next gate:** review green CI on the new diagnostic-only patch, then obtain
a **new explicit owner approval** for another disposable B2 test. One failed
test does not authorize unrestricted retries.


## Second owner-approved isolated B2 rehearsal — PASSED (first green)

- GitHub Actions: https://github.com/Ivan-Shyla/adapteng-company-os/actions/runs/40179960217
- Timestamp: 2026-10-25, 09:16–09:19 UTC. Event: workflow_dispatch;
  branch: fix/postgres-rehearsal-pinned-source-20261024;
  tested head: `783608c6e3a86f0e216514cd46055694050759d1`.
- All steps succeeded: source/archive verification, disposable PostgreSQL 16,
  negative-control isolation guard, B2 access/stanza/check, full backup,
  repository verification, independent restore and checksum verification,
  WAL replay recovery and checksum verification, cleanup, sanitized evidence.
- Immediate restore: **4 tables, 45,001 rows**; backed-up/restored set SHA-256:
  `11942a24357a213f00cb469b5c0029f1c2970d2ac2aa2e9a7ce2b065e801abb3`.
- WAL replay: **5 tables, 46,203 rows**; post-backup/restored-to-latest SHA-256:
  `5271c90d227309af11f0d248a178ce13ff2ab747a975009c008ab1a9cc65f847`.
- B2 rehearsal prefix `/restore-rehearsal/40179960217-1` cleaned successfully.
  The checked production prefix `/adapteng-ops` remained **0 objects before
  and after**. This count is only for that exact prefix and does NOT establish
  that the production database has no backups elsewhere.
- Prior run 40128239401 FAILED due WAL timeout; this was not a second green.
  Therefore current independent green count: **1 of 2 required**.
- The test does NOT restore the current production PostgreSQL backup, establish
  production RPO/RTO, or prove production B2 archive health.

**Decision:** keep this PR as Draft. Do not merge, change production backup
configuration, or launch another B2 test without a new explicit approval.
After two independently green disposable rehearsals, separately validate the
actual offsite backup and production-derived restore in an isolated environment.

## Review and acceptance
1. GitHub repo CI tests pass for this PR (static policy and unit tests).
2. Reviewer verifies sha256 against upstream asset and code-level negative
   controls: no on.push, no production DSN, run-specific B2 prefix, full/WAL
   content checksum comparison, failure cleanup guard.
3. **Separate owner approval** to merge the workflow, since default branch's
   existing nightly schedule will subsequently execute the revised job. Merge
   is a runtime change even though it is done from GitHub.
4. After approval, observe a scheduled run or separately approve manual
   workflow_dispatch (this writes disposable test data to a separate B2 prefix,
   and deletes only that prefix).
5. Require **two independent green** full-backup + restore and WAL replay
   rehearsals with content checksums and cleanup evidence; no production data.
6. Independently verify a current offsite **production** backup set and live
   production WAL archive status. Then propose a separate owner-approved,
   isolated production-derived restore with measured RPO/RTO.

## Explicit exclusions
- This patch does **not** install pgBackRest on Hetzner, upgrade production
  PostgreSQL, touch Coolify, set production backup retention, or create a
  recovery copy of production.
- A green disposable rehearsal is **not** a restore drill of `adapteng_ops`.
- This does **not** change the accepted production backup architecture and
  cannot be used to bypass the phase-0 immutable runtime tooling requirement.

## Rollback
Before merge: close PR and delete the feature branch (no production change).
After an approved merge: revert only this PR's workflow commit; do not disable
existing daily backups/freshness jobs on the basis of rehearsal status.
