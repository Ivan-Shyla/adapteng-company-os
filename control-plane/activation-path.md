# Platform v1 — activation path

- **observed_at:** 2026-09-07 08:30 (UTC)
- **Evidence:** live unauthenticated probes of the Coolify, n8n and website
  edges, two live object-store inventory runs (`34100219296`) and one full
  disposable backup rehearsal (`34100426430`) in `adapteng-company-os`, one
  read-only internal Coolify probe (`34099598085`), and the GitHub API for five
  repositories.
- **Purpose:** state what actually runs today and give the shortest honest route
  to a platform that processes real work. This is a build plan, not an audit.

---

## 1. What actually runs today

The governed pipeline is **built, deployed and correct**. It is simply not
receiving anything.

| Component | State | Evidence |
|---|---|---|
| Baserow adapter | Deployed and serving | Commit `f9daf1b5`; five live checks passed 2026-09-06 |
| Governed read path (L1) | **Operational** | Workflow `65ATNbi5sColtnp0`, execution `26`, `200/200/200/401/403` |
| AI Gateway | **Answering on the private network** | `health` 200, `ready` 200, alias resolves, 2026-09-06 10:43 |
| Producer plugin on Cloudways | Deployed and current | `lead-intake.php` byte-identical at deployed `ce1a200b` and at `main` |
| Lead delivery configuration | Never switched on | `configure-lead-intake.yml` dispatched once ever, 2026-07-19 |
| WEB-002 lead intake | Enabled, **zero traffic** | 16 nodes, 0 disabled, 0 executions in the retained self-hosted log |
| AUT-001 systems upsert | Enabled, **zero traffic** | 2 nodes, 0 executions in the retained self-hosted log |
| WordPress producer | Merged and deployed | Website PR #78 merged 2026-08-01; Cloudways deploy succeeded 2026-08-14 on `854f5971` |
| Lead delivery configuration | **Never switched on** | `configure-lead-intake.yml` last dispatched 2026-07-19 |
| WEB-002 evidence lane | **Passed T1–T4 in full, returned to inactive** | Run `34058980920`, lane `6t0GJrZjfMMOMNVo` version `c6f95c40`, 4 effect keys, 1 physical row each |
| Coolify control-plane API | **Degraded** | `GET /projects` answered 502 on two authorized probes, 2026-09-06 20:54 and 20:55; unchanged since 2026-09-05 |

**The gap is one configuration switch and one backup, not missing code.** The
consumer, the producer and the transport all exist, and the consumer contract has
now been exercised end to end rather than only read.

### Preflight status — closed 2026-09-06

The two-repository lane fix in §5 is done. `adapteng-automation-platform` PR #131
moved the content-type check above the body-shape checks
(`9957271a267ececd41abd09c22243d35fe87416f`), `adapteng-website` PR #187 moved the
pinned artifact digest to match (`cd17a2969041ed68cb336150182df544c73c6a46`), and
the live lane was updated in place with no structural difference from the
reviewed artifact. The T1–T4 run then passed in full and the three attestation
variables are set. Full detail is in `registry/workflows.yaml` under
`cutover_state_2026_09_06_2046z`.

### Correction to previous status

`registry/workflows.yaml` recorded website PR #78 as *draft, unmerged and
undeployed*. That has been false since 2026-08-01. The corrected entry is in the
same file. Anyone planning from the old line was planning around a blocker that
no longer existed.

---

## 2. The shortest route to a working business flow

One owner-dispatched GitHub Actions run activates the end-to-end lead flow:

> **Repository:** `Ivan-Shyla/adapteng-website`
> **Workflow:** `.github/workflows/configure-lead-intake.yml`

### Answers to the dispatch form

| Input | Value | Basis |
|---|---|---|
| `mode` | `self_hosted` | Routes the site to WEB-002 on `n8n.adapteng.com` |
| `confirm_cutover` | `true` | Owner decision |
| `consumer_idempotency_approved` | **`true`** | Verified 2026-09-06; see below |
| `preflight_evidence_ref` | `WEB002-T1-T4:cd17a2969041ed68cb336150182df544c73c6a46:34058980920` | Set 2026-09-06 from a T1–T4 run that passed in full; equals repository variable `AE_LEAD_WEB002_PREFLIGHT_EVIDENCE_REF` and binds the dispatch commit |

**A fourth gate exists and is not an input.** Verified live on 2026-09-07 in the
workflow at website `main`: an active mode also requires the repository variable
`AE_LEAD_INTAKE_CUTOVER_APPROVED=true`, checked separately from the three
preflight attestation variables, which do **not** substitute for it. It is
currently absent, which is correct. It is a one-shot switch: set it immediately
before the approved dispatch and return it to `false` or remove it immediately
afterwards.

### Cutover approval package — prepared 2026-09-07, not yet executable

Presented for approval only. **Do not** set the approval variable, dispatch the
workflow or submit a canary until items 1–4 of the consolidated owner queue in
[`../owner/action-items.md`](../owner/action-items.md) are done and Ivan replies
with the exact phrase below.

| Item | Value |
|---|---|
| Website revision to dispatch from | `cd17a2969041ed68cb336150182df544c73c6a46` (current `main`, unchanged since the evidence run) |
| Preflight evidence | Run `34058980920`, T1–T4 all passed, artifact digest `29f53422…`, approved at `2026-09-06T20:46:15.072Z` |
| Consumer | `WEB-002 Lead Intake (governed)`, `05ytz5If9kHUOYuA`, 16 nodes, active, **0 executions to date** |
| Trusted backup | **MISSING.** No pgBackRest repository exists in the bucket under any prefix (measured, run `34100219296`). This is the one hard blocker. |
| Baserow token rotation | **NOT DONE.** `baserow-company-os-primary` is still the compromised token. |
| Planned dispatch inputs | The four in the table above, plus `AE_LEAD_INTAKE_CUTOVER_APPROVED=true` set immediately before and cleared immediately after |
| Expected production writes from one canary | Exactly one identity reservation and one effect per governed entity — Organization, Person, Opportunity, one-day Action — all four carrying one idempotency key. Four rows, no more. |
| Duplicate submission | Same identity replayed must return `409` and create no second effect |
| Monitoring window | 60 minutes after the canary, watching for 4xx/5xx, held or dead-lettered items and delivery ambiguity |
| Abort thresholds | Any lost lead, any duplicate effect, any ambiguous hold, or any write/response contract violation |
| Rollback | Re-dispatch the same workflow with `mode=legacy`, which restores the MM-18 route. Evidence rows and synthetic records are **not** deleted by rollback; they are reported for separately approved cleanup. |
| Source-entry safety | Fluent Forms retains the source entry if delivery fails, so a failed cutover does not lose the lead |

**Required approval phrase:** `APPROVE WEB-002 CUTOVER + ONE NON-PII CANARY`

### Why idempotency can be approved

WEB-002 does not rely on retry etiquette. It reserves identity in Postgres
before any write:

1. `Validate` reads `form.submission_id` from the body and enforces the `N:N` shape.
2. `Reserve` calls `public.reserve_lead_identity('lead.created','wordpress', …)`.
3. A duplicate returns outcome `conflict`, which routes straight to `Respond409`.
4. Only a fresh reservation reaches the four governed upserts, each carrying an
   idempotency key.

The WordPress producer emits the same value in both the `X-AE-Submission-ID`
header and `form.submission_id`. **One caveat worth recording:** WEB-002 reads
only the body field. Dedupe is consistent today, but a proxy that strips the body
field, or a future producer that sends only the header, would silently disable
deduplication. The body field is load-bearing.

### Fresh consumer verification, 2026-09-06

The consumer contract was re-read live from the self-hosted n8n API rather than
inferred from the July artifact. `WEB-002 Lead Intake (governed)`
(`05ytz5If9kHUOYuA`, version `fa199c8e-ad42-4681-bd5b-123fcefeab65`, definition
last changed 2026-07-25) is active with 16 nodes, and every contract element is
present in the running definition:

| Element | Result |
|---|---|
| `POST` webhook, header authentication | present, bound to the existing webhook token credential |
| Webhook path matches `web002-lead-[0-9a-f]{8}` | yes |
| Reads `form.submission_id` | yes |
| Builds `sitelead_wp_<id>` | yes |
| Calls `reserve_lead_identity(...)` before any write | yes |
| Conflict path responds `409` | yes |
| `idempotency_key` occurrences | exactly 4, one per governed upsert |
| Governed upserts | `AE-ORG-`, `AE-PER-`, `AE-OPP-`, `AE-ACT-` |

The self-hosted instance holds four workflows: WEB-002 and AUT-001 (both
enabled, both still with no production traffic) and the two completed L1 proofs
(both inactive).

### Cutover prerequisites, resolved 2026-09-06

Four of the five items recorded earlier as blockers are now closed. The owner
placed the shared webhook secret at 18:00:29Z; the remaining three secrets and
the isolated evidence lane were configured by the agent between 18:26Z and
18:29Z.

| # | Item | State on 2026-09-06 |
|---|---|---|
| 1 | `AE_N8N_LEAD_WEBHOOK_TOKEN` | present, owner-placed 18:00:29Z, verified by metadata only |
| 2 | `AE_N8N_SELF_HOSTED_LEAD_WEBHOOK_URL` | present, 18:28:28Z, read from the running webhook node and shape-checked against the workflow's own pattern |
| 3 | `AE_N8N_WEB002_EVIDENCE_URL` / `AE_N8N_WEB002_EVIDENCE_TOKEN` | present, 18:28:29Z and 18:28:59Z |
| 4 | Evidence lane deployed | imported to self-hosted n8n as `6t0GJrZjfMMOMNVo`, version `22485020-691e-431d-afed-991145e672ab`, 22 nodes, serving `/webhook/web002-lead-a11ce001`; test table `WEB002_Evidence_Test_Effects` (`P4COpnElwU9pg20P`) created |
| 5 | Four attestation variables | still unset, correctly — no valid evidence reference exists yet |

Website `main` moved from `0214cfa1` to `b188e9ef742e0d6342a56b273190f5169fb7658e`
across eight content-only commits; `configure-lead-intake.yml` is byte-identical
across that range, so no gate logic changed. `configure-lead-intake.yml` has
still run exactly once in its history, on 2026-07-19, so the producer carries
that run's configuration and re-dispatching with the previous mode remains the
rollback.

### First live T1–T4 attempt, run 34051896721

`web002-t1-t4-evidence.yml` was dispatched against website `main`
`b188e9ef742e0d6342a56b273190f5169fb7658e` at 18:29:48Z and failed at 18:30:11Z.
Context validation passed, so the endpoint, its authentication and the exact-main
check are all sound. The run reached T1 probe 7 of 11, which means probes 1–6
passed live:

| Probe | Result |
|---|---|
| Accepted canonical lead | success, exactly one committed effect written to the isolated test table |
| Wrong authentication | rejected as required |
| Missing authentication | rejected as required |
| Missing field | `422`, `invalid_envelope` |
| Extra field | `422`, `invalid_envelope` |
| Malformed field | `422`, `invalid_envelope` |
| Content-type rejection | **mismatch — run stopped here** |

The isolated test table held exactly one row afterwards, with `effect_count` 1
and status `committed`, which is the intended single-effect result. No
production table was involved at any point: the lane's declared write boundary
is that one n8n Data Table.

### The one remaining blocker

The failure is a real defect in the pinned lane artifact, not a configuration
gap. The deployed lane is byte-faithful to
`n8n/workflows/experimental/MM-WEB002-self-hosted-lead-evidence-lane.json`,
whose SHA-256 `fb70720fc5679e6bd81e8a67b4088567d41d86a86d737c7e8e00ea43010cc0f6`
matches the constant pinned in `tools/contracts/web002-evidence-runner.mjs`
exactly, so the imported artifact is the authoritative one.

Inside the lane's validation node the body-shape checks are evaluated before the
content-type check, and the failure helper keeps only the first code it is
given. n8n does not parse a `text/plain` request body into an object, so such a
request reaches the body checks as an empty object and is rejected as
`invalid_envelope`. The runner requires `invalid_content_type` for that probe.
Reproduced directly against the lane on 2026-09-06: a `text/plain` request
returned `422` with `invalid_envelope`, while the same body sent as
`application/json` reached the header checks and returned `422` with
`header_identity_mismatch`. Every other probe therefore works; only this
ordering makes one rejection code unreachable.

The fix is one line — evaluate the content-type check before the body-shape
checks — but it changes the artifact, and the artifact's digest is a governance
control pinned in the website repository. Editing the control that an agent must
satisfy is exactly what that pin exists to prevent, so this run stops here. It
needs an owner-authorized, two-repository change: reorder the check in
`Ivan-Shyla/adapteng-automation-platform`, then update the pinned digest in
`tools/contracts/web002-evidence-runner.mjs` in `Ivan-Shyla/adapteng-website`.
Re-import and reactivate the lane afterwards, and rotate the evidence-lane
credential together with its matching repository secret, because both were
created for this bounded attempt.

The lane was left inactive after the attempt. `WEB-002 Lead Intake (governed)`
was not modified: it is still active on version
`fa199c8e-ad42-4681-bd5b-123fcefeab65`, and the existing route stays in place.

### Backup before production writes

The cutover turns on four governed upserts against `adapteng_ops`, so a
recoverable backup should exist first. There is no agent-executable path to one.
`postgres-backup-rehearsal.yml` is real in every respect except the database:
it deliberately backs up and restores a disposable cluster, and a guard fails
the job if production connection configuration appears at all. Host-side
execution has no working route — the production host does not accept SSH from
GitHub-hosted runners, which is precisely the gap `ops-runner.yml` exists to
close. **A rehearsal is not a backup of production, and this document does not
treat it as one.**

This gate was not reached on 2026-09-06. The T1–T4 lane writes only to an
isolated n8n Data Table, so it needs no production backup; the backup
requirement applies to the cutover itself, which did not start.

**Configuration agreement closed 2026-09-07; the backup itself did not.** Two
facts were established by measurement rather than inference:

- `PGBACKREST_REPO1_PATH` was corrected from the underscore spelling to
  `/adapteng-ops`, which is what `scripts/postgres_restore_guard.py` line 472,
  the stanza pinned at line 470 and the runbook configuration block all require.
  The guard was not relaxed. Run `34100426430` re-ran the full disposable
  rehearsal afterwards and concluded success.
- The change was proven safe first. Run `34100219296` counted `0` objects under
  the previously configured prefix and listed every populated top-level prefix
  in the bucket: `data/coolify`, one self-created probe object, and
  `postgres-physical-backup/validation`. **None has the `archive/` or `backup/`
  children that identify a pgBackRest repository**, so nothing was orphaned —
  and, more importantly, no production backup exists anywhere in that bucket
  under any spelling. The previous "no evidenced backup" position is now a
  measurement rather than an absence of evidence.

What remains is the backup itself, and it is owner-only for the reason stated
above: no production database credential exists in this repository, so no
workflow can reach the production cluster. The Backblaze lifecycle rules and the
application-key prefix restriction also still need re-scoping to the corrected
value; a rule left on a stale prefix silently stops expiring hidden versions and
no pgBackRest command reports it.

---

## 3. The AI layer

The same pattern holds on the AI side: built, deployed, and until today
unverified. A read-only probe issued from inside the shared network on
2026-09-06 returned `health` 200 and `ready` 200 from `http://ai-gateway:8081`,
and the `ai-gateway` alias resolved. Because `/ready` touches the database, that
single result proves the container is up, the name resolves and the gateway
reaches `postgres-adapteng-ops`. The alias gap measured on 2026-08-12 — when the
Baserow adapter resolved and this service did not — is closed.

The registry status moves from `deployed-live-unverified` to
`live-internal-verified`. Cost, model pricing and FX configuration were already
not blockers.

**What remains unproven** is the first real model call: the EU Vertex client,
Drive adapters, orchestration and canonical approval composition have never been
exercised against the running service. That is the next AI milestone, and it is
bounded — one schema-valid, side-effect-free draft, which is the service's
declared first operation.

It is also, today, owner-only. `/health` and `/ready` are answered before the
`Authorization` header is read, which is why the reachability probe needed no
secret; `POST /v1/gateway` is the only authenticated route, and it requires a
bearer token from `AI_GATEWAY_BEARER_TOKENS`, whose value exists only in the
Coolify secret store. The service also ships its own one-shot proof
(`app/company_os_model_proof.py`), but it runs inside the container behind an
explicit owner acknowledgement, and no execution path into the container is
available from here.

---

## 4. Proportionality: what is a real risk and what is not

The user-visible complaint is that the platform is over-guarded relative to what
it delivers. Separating the two honestly:

**Genuine, keep:**

- **No verified production backup (B-4).** Enabling live lead writes into
  Baserow with no restore-tested backup is a real business risk. This is the one
  control that should gate cutover, and it is owner-only.
- **WEB-002 fail-closed responses.** `Respond409`/`Respond500` are what make a
  producer retry instead of losing a lead. Removing them would lose business data.

**Over-weighted relative to delivered value:**

- **The 13 frozen n8n Cloud workflows.** They were frozen because they had
  ungoverned reach. The answer is not to unfreeze them in place, and not to keep
  auditing them: it is to migrate the ones that still matter onto the governed
  adapter path already proven at L1. Anything not worth migrating should be
  archived rather than carried as permanent open risk.
- **Repeat inventory audits of n8n Cloud.** The split question is now settled
  (see `registry/workflows.yaml`). Further census work adds no capability.

---

## 5. Ordered next actions

**Owner-only (cannot be automated from here):**

1. ~~Authorise the two-repository lane fix.~~ **Done 2026-09-06.** Platform
   PR #131 and website PR #187 are merged, the live lane carries the corrected
   validator with no structural difference from the reviewed artifact, and the
   T1–T4 evidence run passed in full.
2. ~~Correct the pgBackRest repository prefix.~~ **Done 2026-09-07 by the
   agent.** `PGBACKREST_REPO1_PATH` is now `/adapteng-ops`, matching the
   fail-closed guard, the stanza and the runbook; the guard was not relaxed and
   the disposable rehearsal still passes (`34100426430`). What remains of B-4 is
   the backup itself: run one production full backup of `adapteng_ops` and one
   isolated restore per `runbooks/backup-and-restore.md`. This is owner-only
   because no production database credential exists in this repository. Run
   `34100219296` established by direct measurement that **no pgBackRest
   repository exists in the bucket under any prefix**. The Backblaze lifecycle
   rules and application-key prefix restriction must also be re-scoped to
   `/adapteng-ops`. See issue 32.
3. Restore the Coolify control plane. **Re-diagnosed 2026-09-07: the
   application container is down, not merely its API.** Every path on
   `coolify.adapteng.com` returns 502, including the web UI root and a static
   asset, while the edge proxy answers and every workload behind it stays
   healthy — `n8n.adapteng.com/healthz` 200, `/rest/login` 401, `adapteng.com`
   200. The fix is a container restart on the host; diagnose before upgrading
   and do not assume the deployed applications are down. This now precedes the
   source repoint below, because deployment revision, build context and network
   membership are all unreadable until it is back.
4. Repoint the Coolify application for `n8n-self-hosted` from
   `palinaruban-repo-status-review` to `main` (B-3) — **but not blindly.** The
   histories have diverged rather than simply fallen behind: `main` carries 83
   unique commits and the old branch 35. `deploy/coolify/docker-compose.n8n.yml`
   and `.env.example` are byte-equivalent between the refs, which is not enough
   to prove the whole build context is. Identify the resource type, build
   context and deployed revision first, classify every branch-only runtime
   change as merged, obsolete or still required, snapshot the rollback ref, and
   confirm the n8n database and its separately stored encryption key are
   recoverable before redeploying. An export without the encryption key is not
   a credential backup. Never merge the old branch wholesale into `main`.

**Agent-executable once authorised:**

- Dispatch `configure-lead-intake.yml` with the inputs in §2 once the backup
  gate passes. The preflight side is already satisfied.
- After the first real leads land, verify the flow from the self-hosted
  execution log and report duplicates, 409s and 500s.
- Exercise the first bounded model call once a gateway bearer token is
  reachable by reference.
- Migrate a named Cloud workflow onto the governed adapter path, one at a time,
  each with its own proof.

---

## 6. What this document does not claim

- It does not claim any service is healthy right now beyond what §1 and §3
  record from live probes.
- It does not claim a production backup exists.
- It does not claim the cutover has happened. `configure-lead-intake.yml` has
  not been dispatched since 2026-07-19, and the three attestation variables
  record a passed preflight rather than an authorised cutover.
- It does not authorise the cutover. It supplies the answers the cutover form
  asks for so the owner can decide in one step instead of reopening the question.
