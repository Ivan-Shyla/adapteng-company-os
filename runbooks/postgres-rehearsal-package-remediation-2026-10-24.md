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
