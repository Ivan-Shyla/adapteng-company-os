# Platform v1 — release command center

**observed_at:** 2026-08-13T11:40Z (UTC)

**Evidence scope.** GitHub only, read via the REST/GraphQL API and `gh` as the
authenticated account `PalinaRuban`: repository metadata, branch heads, open pull
requests and issues, Actions run conclusions and check-run outputs, plus tracked
files on `adapteng-company-os` `main` at `bc70a89`. Six repositories were
inspected; all six were accessible.

**Provider runtime was NOT inspected.** No Coolify, Hetzner, n8n, Backblaze B2,
Google, Cloudways or WordPress console, API or host was contacted. Nothing below
is a statement about what is actually running. Where GitHub cannot prove runtime
state, the row reads `UNVERIFIED` — that is a real verdict, not a placeholder.

**Deliberately not decided here.** `adapteng-automation-platform` is frozen for
the rollout receipt ceremony and was read only: no branch, push, rebase, rerun,
comment or edit of PR #121 occurred.

---

## 1. Release verdict

| Scope | Verdict | Controlling reason |
|---|---|---|
| Repository development | **GO** | All five active repositories are green on `main`; `main-protected` rulesets active; only one open PR company-wide. |
| Controlled internal pilot | **CONDITIONAL GO** | Baserow and n8n self-hosted are recorded live, but B-3 (n8n deploys from a non-`main` branch) means the running code is not provably the authoritative source. |
| AI pilot | **NO-GO** | B-1: the first model proof has never run, and its whole predecessor chain is gated behind PR #121, which is unauthorized. `Migrate Approved Assets` has zero runs. |
| Full n8n cutover | **NO-GO** | B-3 plus B-6: n8n Cloud remains the authority for MM/LM, MCP exposure is unresolved, and no producer cutover proof exists. |
| Autonomous external actions | **NO-GO** | B-2 and B-4: an unrotated compromised credential and no production backup. No rollback floor exists for an autonomous writer. |

---

## 2. Repository matrix

Heads read 2026-08-13T11:40Z. "Latest CI" is the newest completed run on the
default branch.

| Repository | Vis. | Authoritative role | Default | Current `main` SHA | Open PRs | Open issues | Latest CI on main | Relation to Platform v1 | Current blocker | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| `Ivan-Shyla/adapteng-company-os` | public | Index + control plane; not implementation | `main` | `bc70a896357147fd278f999646a086a6dc3d17ca` | 0 | 2 (#32, #18) | `CI` **success** 2026-08-13T10:28Z | Governs the release; owns backup/restore + deploy drivers | B-5 (restore guard literal mismatch) | [repo](https://github.com/Ivan-Shyla/adapteng-company-os) |
| `Ivan-Shyla/adapteng-automation-platform` | private | Platform implementation authority (**FROZEN, read-only**) | `main` | `6ecdd5fb224eae878ed49a522857bc5a21c32b9f` | 1 (#121) | 0 | `Validate Repo` / `Adapter Tests` / `Rollout Policy` / `Secret Scan` all **success** on `6ecdd5f` | Owns the rollout ceremony, AI gateway, adapters, n8n deploy spec | B-1 (PR #121 unauthorized) | [PR #121](https://github.com/Ivan-Shyla/adapteng-automation-platform/pull/121) |
| `Ivan-Shyla/adapteng-website` | private | Public site + Fluent Forms lead capture | `main` | `ae8073085964aa761252138ba739bc5efa24d49f` | 0 | 0 | `Validate Website` **success** 2026-08-12T19:24Z | Lead intake producer (WEB-001) | B-6 (producer merged, not deployed) | [repo](https://github.com/Ivan-Shyla/adapteng-website) |
| `Ivan-Shyla/adapteng-marketing` | private | Marketing media/content worker | `main` | `9afaf96db1024685652383bbf825fc2994da13bc` | 0 | 0 | `validate` **success** 2026-07-31T09:59Z | Media intake; live worker on a legacy credential binding | Later backlog L-2 | [repo](https://github.com/Ivan-Shyla/adapteng-marketing) |
| `Ivan-Shyla/ai-dev-loop-control-plane` | private | Generic agent lifecycle contract | `main` | `327fc4b63ec60afc8a8a6c3169d062a58d9eb4da` | 0 | 0 | `CI` + `Gitleaks` **success** 2026-08-03T15:11Z | Repository contract only; no business runtime | Not on the v1 critical path | [repo](https://github.com/Ivan-Shyla/ai-dev-loop-control-plane) |
| `PalinaRuban/adapteng` | private | **Legacy, non-authoritative, read-only** | `main` | `9c8acd166bf57dc416ed6de86ced8f0b26ac3eb5` | 0 | 0 | `Legacy containment` **success** 2026-07-28T10:43Z | June-2026 WordPress/Azure snapshot; excluded from Company OS authority | B-2 (historical credential exposure unrotated) | [PR #3](https://github.com/PalinaRuban/adapteng/pull/3) |
| `Ivan-Shyla/Kraken` | private | **OUT OF SCOPE** — personal trading project | `main` | not inspected beyond classification | — | — | — | Excluded by `decisions/0002`; no Company OS resources | n/a | `registry/environments.yaml` `excluded_personal_projects` |

No additional `Ivan-Shyla` repositories are visible to this account. No repository
was inaccessible.

---

## 3. Deployment relationship matrix

Derived **only** from repository evidence. A recorded status is what a tracked
file or an Actions run asserts; it is not a runtime observation.

| Service | Owning repository | Declared deployment source branch | Recorded status | Live verification state |
|---|---|---|---|---|
| `adapteng-website` (theme) | `adapteng-website` | `main`, auto-deploy under `wp-content` | `live` | **UNVERIFIED** — last accepted post-deploy evidence is run `31329017343`; no runtime check this round |
| `adapteng-core` (lead producer) | `adapteng-website` | manual `Deploy to Cloudways`, confirmation-gated | `repo-merged-not-live` | **UNVERIFIED, negative** — last successful plugin deploy `30720691975` predates the WEB-001 merge `6770e749` |
| `baserow-self-hosted` | `adapteng-automation-platform` (`deploy/coolify`) | not recorded in company-os | `live` | **UNVERIFIED** |
| `n8n-self-hosted` | `adapteng-automation-platform` (`deploy/coolify`) | **`palinaruban-repo-status-review`** (branch exists at `4b67fa47`, ≠ `main` `6ecdd5fb`) | `live-partial-authority` | **UNVERIFIED** — and the declared source is provably not `main` |
| `n8n-cloud` | n/a (SaaS) | n/a | `live` (current MM/LM authority) | **UNVERIFIED** |
| `postgres-adapteng-ops` | `adapteng-automation-platform` | Coolify, internal-only | `live` | **UNVERIFIED** |
| `ai-gateway` | `adapteng-automation-platform` | Coolify (target), driven by company-os `Coolify deploy` | **CONFLICT** — see C-1 | **UNVERIFIED** — `Coolify deploy` run `31542579590` succeeded 2026-08-11T22:28Z; a workflow success is not a service observation |
| `adapteng-media-worker` | `adapteng-marketing` | Coolify | `live-legacy-binding` | **UNVERIFIED** |
| `adapteng-drive-adapter`, `adapteng-run-ledger`, `integrity-reconciler` | `adapteng-automation-platform` | none (no deployed service) | `repo-merged-not-live` | n/a — no deployment claimed |
| `ai-dev-loop-control-plane` | `ai-dev-loop-control-plane` | none | `repo-merged-not-live` | n/a |
| legacy WordPress/Azure | `PalinaRuban/adapteng` | `Deploy WordPress to Azure App Service` | retired | **UNVERIFIED, negative** — last three recorded runs failed; workflow not run since 2026-06-30 |

---

## 4. The five Platform v1 release gates

| # | Gate | State | What decides it |
|---|---|---|---|
| G1 | **Owner/access continuity** | **NOT MET** | No second company administrator or break-glass operator is evidenced; the `ISO-1` isolation waiver expired 2026-08-08 and the shared deploy key is still shared. Blockers: B-2. |
| G2 | **Production source from authoritative `main`** | **NOT MET** | `n8n-self-hosted` deploys from `palinaruban-repo-status-review`, and `adapteng-core` has no deploy carrying the merged producer. Blockers: B-3, B-6. |
| G3 | **Security P0** | **NOT MET** | The Baserow primary API token was committed to Git history and is not evidenced rotated; systemic n8n MCP exposure is open; legacy credential rotations are `false`. Blockers: B-2, B-4. |
| G4 | **Production backup + isolated restore** | **NOT MET** | No production backup exists. The nightly `PostgreSQL backup and restore rehearsal` (run `31668917675`, success 2026-08-13T05:01Z) is by its own design isolated from production and uses no production database credential. B2 connectivity (`30752237109`) proves object-store reachability only. Blockers: B-4, B-5. |
| G5 | **One controlled end-to-end business flow** | **NOT MET** | The first model proof has never run, `Migrate Approved Assets` has zero runs, and its four-phase predecessor chain is gated behind PR #121. Blockers: B-1. |

**Zero of five gates are met.** No gate is met by a document alone.

---

## 5. Release blocker queue

Each row prevents at least one gate. Severity: **P0** blocks the gate outright;
**P1** blocks it pending owner action that is already identified.

| ID | Sev | Gate | Exact evidence | Owner repo/service | Executable by | Acceptance criterion | Rollback | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| **B-1** | P0 | G5 | PR #121 head `4c4a2f00`: check `Base-trusted rollout authorization` → **FAILURE**, output "The exact current head is not externally authorized"; `Verify exact current head from merged base` → **FAILURE** (2×). 29 other checks green. `mergeStateStatus=UNSTABLE`. `migrate-approved-assets.yml` has zero runs. | `adapteng-automation-platform` | **owner-only** (script requires repo admin, POSIX host, phase-env authorization secret) | PR #121 head carries a valid external authorization and both trust-anchor checks pass, then phases `db_status → preflight → import → replay_verify` each record a successful run | Do not merge; PR #121 stays open at its current head. No production state is touched until `db_status` succeeds. | Owner authorizes the exact head `4c4a2f00`. **Do not rebase, retitle or rerun** — the freeze holds. |
| **B-2** | P0 | G1, G3 | `owner/action-items.md` 🔴: Baserow primary API token literal committed to history, "rotation is not complete or evidenced"; legacy `PalinaRuban/adapteng` `credential_rotations_complete: false`, `history_clean: false`; shared deploy key not isolated; `ISO-1` waiver expired 2026-08-08. | provider consoles / `PalinaRuban/adapteng` | **owner-only** | Each named credential revoked at its provider, replacement installed, and the **old value verified to fail**; second admin/break-glass operator nominated and tested | n/a — rotation is forward-only; keep the replacement in the secret store | Owner performs provider-side rotation and records old-value failure. Do not rewrite Git history. |
| **B-3** | P0 | G2 | `registry/services.yaml` `n8n-self-hosted`: "Coolify still deploys from branch `palinaruban-repo-status-review`". That branch exists in `adapteng-automation-platform` at `4b67fa4704ea05e6e63de3e22d69f66779f84499`; `main` is `6ecdd5fb`. The two are different commits. | `n8n-self-hosted` / Coolify | **owner-only** (Coolify console) | Coolify source is `main`; an auto-deploy from `main` is observed and recorded | Repoint back to the prior branch; the branch is retained and not deleted | Owner repoints the Coolify source to `main` and records the deploy run. |
| **B-4** | P0 | G3, G4 | `owner/action-items.md` ⚪: "the backup itself is still not configured"; rollout-authorization literal `BLOCKED_ON_UNCONFIGURED_PRODUCTION_BACKUP`. Restore manifests (`postgres_restore_*_manifest.json`) are `NOT_CONFIGURED` and stop execution by design. The nightly rehearsal proves isolation, not backup. | `adapteng-company-os` + provider | **owner-only** for the provider steps; manifest population is agent-executable | A fresh full pgBackRest backup of `adapteng_ops` exists, post-backup `check` passes, parsed selected-set `verify` passes, and one isolated restore completes on a disposable host | Delete the disposable restore host/volumes; revoke the read-only key | Owner approves the provider quote and dedicated backup host scope. **Do not claim a backup from B2 connectivity or the rehearsal.** |
| **B-5** | P1 | G4 | company-os issue [#32](https://github.com/Ivan-Shyla/adapteng-company-os/issues/32): `scripts/postgres_restore_guard.py:441` pins `repo_path != "/adapteng-ops"` fail-closed, while `main` documents that "nothing reads a literal". A restore wired to `PGBACKREST_REPO1_PATH` as the runbook describes stops before it starts. | `adapteng-company-os` | **agent-executable** | Guard and configured variable agree; a test asserts the two cannot diverge; `runbooks/backup-and-restore.md` and `owner/action-items.md` no longer carry the false claim | Revert the single commit; the guard is fail-closed either way | Fix in a bounded PR — see task T-1. Not fixed in this run. |
| **B-6** | P1 | G2 | `owner/action-items.md`: WEB-001 merged as `6770e749` (2026-08-01T22:54Z); last successful `Deploy to Cloudways` plugin run `30720691975` is 2026-08-01T22:10Z — **44 minutes earlier**. No plugin deploy carries the lead-intake code. | `adapteng-website` / `adapteng-core` | **owner-only** (confirmation-phrase-gated deploy) | Exact-head review-clean on `6770e749`, then producer T1–T4, atomic no-dual-write mode switch and rollback proof; MM-18 retired last | Revert the mode switch; MM-18 remains active until retirement is proven | Owner runs the producer cutover sequence in `runbooks/n8n-operations.md`. Keep MM-18 active. |
| **B-7** | P1 | G3 | `owner/action-items.md` 🔴: audited n8n workflows still have **Available in MCP** enabled because the update API rejected the field; live state remains 89 non-archived / 31 active / 58 inactive. Merged containment (platform PR #85, `99f4d88e`) changed no live availability. | `n8n-cloud` + `n8n-self-hosted` | **owner-only** (n8n console) | Instance-level MCP disabled, or effective exposure proven to be an explicit allowlist only, verified against an authenticated session | Re-enable per-workflow availability; normal triggers are unaffected either way | Owner closes MCP exposure at the instance level and records the verified allowlist. |

---

## 6. Later backlog (non-blocking)

These do not prevent any of the five gates. They are recorded so they are not
re-promoted into the blocker queue.

| ID | Item | Source |
|---|---|---|
| L-1 | Issue #18 — `scheduler_records()` fails closed on symlinked systemd units on a normal Linux host. Blocks the inventory exporter, which is downstream of B-4. | company-os [#18](https://github.com/Ivan-Shyla/adapteng-company-os/issues/18) |
| L-2 | `adapteng-media-worker` still runs on the legacy `GDRIVE_SA_JSON` service account; not redeployed with the company SA. | `registry/services.yaml` |
| L-3 | Baserow off-host export/restore completion. | `owner/action-items.md` |
| L-4 | Rotate the Coolify API token post-launch. | `owner/action-items.md` |
| L-5 | n8n Cloud inventory drift: 14 live-only / 7 repo-only workflows vs 82 repository exports. | `registry/services.yaml` |
| L-6 | Legacy `Approval_Log` / `Publish_Plan` forensic reconciliation; 19 pending `Content_Drafts` await staged migration-003 cutover. | `owner/action-items.md` |
| L-7 | INT-001 integrity-reconciler live wiring, deferred by ADR-0011 to approved PRs; writes forbidden permanently. | `registry/services.yaml` |
| L-8 | `ai-dev-loop-control-plane` is `repo-merged-not-live` with `readiness_verdict: REJECT_LIVE`; not on the v1 path. | `registry/services.yaml` |
| L-9 | Record vendor commercial baselines (Hetzner, Cloudways, n8n Cloud); GitHub Actions has a $10/month hard stop. | `owner/action-items.md` |
| L-10 | 45 stale branches on `adapteng-automation-platform` and 19 on `adapteng-company-os` await deliberate classification. | branch listing, 2026-08-13 |

---

## 7. Evidence conflicts

Listed, not investigated recursively.

| # | Contradiction | Which evidence is stronger | Disposition |
|---|---|---|---|
| C-1 | `registry/services.yaml` (updated 2026-08-10) says `ai-gateway` is `implemented-tested-not-deployed`, `deployed: false`, `deployment_evidence: UNVERIFIED`. `control-plane/current-state.md` §9 D-3 says the gateway **is** deployed and `/ready` answers 200, citing run `31542579590`. | **current-state.md is newer.** Run `31542579590` is real: company-os `Coolify deploy`, `workflow_dispatch`, conclusion `success`, head `f5726cb2`, 2026-08-11T22:28Z — one day after the registry's `updated:` date. | The registry entry is stale. A successful deploy workflow is still not a runtime observation, so the live state stays **UNVERIFIED**. Registry not edited in this run (out of allowed paths). |
| C-2 | `owner/action-items.md` and `runbooks/backup-and-restore.md` on `main` both state that nothing in `scripts/` reads a literal repository prefix. | **The code is stronger.** `scripts/postgres_restore_guard.py:441` pins `/adapteng-ops` and raises `GuardError`. | Documents are wrong; tracked as B-5. |
| C-3 | D-1/D-2 in the drift register rest on the owner's manual production check that all nine migration units are exact. | **UNKNOWN from GitHub.** current-state.md already records these as owner-attested and not reproducible from the API. | Leave as owner-attested. Zero runs of `Migrate Approved Assets` is **not** evidence of an unapplied database — that inference produced D-1 originally. |
| C-4 | `registry/services.yaml` `updated: 2026-08-10` predates several 2026-08-11..13 platform and company-os merges (through company-os `bc70a89`, platform `6ecdd5fb`). | **Live GitHub state is stronger** for every repository fact. | Registry lag is structural; §2 above supersedes it for heads, PRs and CI. |
| C-5 | `PalinaRuban/adapteng` `main`-protection state cannot be read: the rulesets API returns 403 (below GitHub Pro). | **UNKNOWN.** Not settleable from GitHub on this plan. | Legacy repository is non-authoritative; classification unchanged. |

---

## 8. Next implementation batch (proposed — not started)

Bounded, agent-executable, one repository each. **No branch below was created in
this run.**

### T-1 — Correct the restore-prefix claim (closes B-5)
- **Repository:** `Ivan-Shyla/adapteng-company-os`
- **Proposed branch:** `fix/restore-repo-path-literal-20260814`
- **Allowed paths:** `runbooks/backup-and-restore.md`, `owner/action-items.md`, `scripts/postgres_restore_guard.py`, `scripts/test_postgres_restore_rehearsal.py`
- **Acceptance tests:** `python scripts/validate_sensitive_references.py`; the `main` unittest module list from `README.md`; a new test asserting the guard literal and the configured `PGBACKREST_REPO1_PATH` cannot diverge; issue #32 closable by evidence
- **Forbidden:** touching any other repository; contacting B2, Coolify or any provider; weakening the guard to pass; claiming a backup exists

### T-2 — Refresh the stale registry entry (closes C-1)
- **Repository:** `Ivan-Shyla/adapteng-company-os`
- **Proposed branch:** `docs/registry-ai-gateway-deploy-evidence-20260814`
- **Allowed paths:** `registry/services.yaml` only
- **Acceptance tests:** `python scripts/validate_sensitive_references.py`; full `main` unittest list; the entry must cite run `31542579590` and keep live state `UNVERIFIED`
- **Forbidden:** upgrading the status to `live`; inferring runtime health from a workflow conclusion; editing `current-state.md`, `ARCHITECTURE.md` or `owner/action-items.md`

### T-3 — Make the symlinked-unit exporter defect testable (closes L-1)
- **Repository:** `Ivan-Shyla/adapteng-company-os`
- **Proposed branch:** `fix/scheduler-records-symlink-20260814`
- **Allowed paths:** `scripts/postgres_restore_inventory_exporter.py`, `scripts/test_postgres_restore_scheduler_surface.py`, `scripts/test_postgres_restore_rehearsal.py`
- **Acceptance tests:** `scripts.test_postgres_restore_scheduler_surface` green on Linux CI (POSIX-only); full `main` unittest list green; issue #18 reproducer passes
- **Forbidden:** removing the `O_NOFOLLOW` protection; skipping the test to make CI green; touching backup manifests or any provider

---

## 9. What this run did not do

No issue, comment, release or deployment run was created. No repository other
than `adapteng-company-os` was modified. `adapteng-automation-platform` was read
only: PR #121 was not rebased, retitled, rerun, approved, merged or closed, and
no `approval.json` or `approval.sig` was added. No finding above was fixed.
