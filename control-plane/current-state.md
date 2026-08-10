# Company OS — verified current state

**Reconciled:** 2026-08-10. **Last updated:** 2026-08-10, after the first
execution round landed. **Method:** GitHub API reads against every repository
owned by the account, plus the failing CI logs behind each red check. Production
runtime was not reachable from the reconciling workstation; every claim that
would require it is marked `UNVERIFIED` and names what would settle it.

> **Execution round 1 landed.** Platform PR #110 cleared the repository-wide
> merge lock; company-os PRs #35, #39, #40 and #41 merged. Coolify deployment
> automation now exists and its read-only `inspect` has been run once. Sections
> 2, 6, 9 and 10 are updated below; the rest still holds as reconciled.

## 1. Repositories

Discovered by enumeration, not from the handoff list. Six exist; five are in
Company OS scope.

| Repository | Role | Open PRs |
|---|---|---|
| `adapteng-company-os` | Control plane, registry, governance | 0 |
| `adapteng-automation-platform` | Implementation: AI Gateway, adapters, migrations | 1 (#109, now mergeable) |
| `ai-dev-loop-control-plane` | Agent execution patterns, skills, admission | 0 |
| `adapteng-website` | Public website | 0 |
| `adapteng-marketing` | Marketing assets | 0 |
| `Kraken` | **Out of scope.** Personal trading project. | 0 |

`Kraken` is not Company OS. Its exclusion follows the boundary already recorded
in [`decisions/0002-…`](../decisions/0002-personal-projects-remain-outside-company-os.md).
It is the only repository with no `main-protected` ruleset, which is consistent
with it being outside the governed set. No action.

Two repositories the handoff did not mention were found by enumeration
(`Kraken`, `adapteng-marketing`). Neither changes the plan.

## 2. The single blocking fact — CLEARED 2026-08-10

> **Resolved by platform PR #110**, which scoped the isolation check out of the
> required job without weakening it, and completed by company-os PR #45, which
> promoted the new `n8n isolation` job to a required check so the data boundary
> actually blocks n8n changes instead of merely reporting on them.
> `adapteng-automation-platform` merges normally again: PR #109 moved from
> `BLOCKED` to `MERGEABLE` and has merged, as has #112. The isolation finding is
> **still open and still reported**; only its blast radius was removed.
> Renewing the waiver remains an owner decision, and is now the live blocker for
> n8n work specifically — see §11.
>
> The diagnosis is kept in full because the failure mode is worth recognising
> again: a governance control with a date in it became a repository-wide outage,
> silently, with no warning before the fact.

**What was wrong.** The `main-protected` ruleset then required four status
checks. Three passed. The fourth, `Validate repository structure and content`,
failed on a condition that had nothing to do with any code under review:

```
n8n/isolation-waivers.json:waiver[0]: waiver expired isolation_ref=ISO-1
```

The repository holds one time-boxed waiver permitting a company-to-personal
resource crossing. Its `expires_on` is **2026-08-08**. The waiver lapsed on
2026-08-08 and the check that reads it runs on every push and every pull request
to every branch, so from 2026-08-09 the repository was sealed.

Evidence that this was a date lapse and not a regression: the same workflow
succeeded on `main` at `23a23f0` on 2026-08-08, and failed at 2026-08-10 against
a pull request that touches only `services/ai-gateway/` and `docs/`.

The waiver is deliberately double-locked. The validator pins the approved
tuple in code, so the date in the JSON must equal the date in
`scripts/validation/validate_n8n_isolation.py` **and** be unexpired. Editing
one without the other fails with `does not match the approved ISO-1 tuple`.
That design is sound: it makes extending a data-boundary waiver a reviewable
code change rather than a quiet JSON edit. It is kept.

What was wrong is the blast radius, not the check. A lapsed waiver on an n8n
resource crossing halted unrelated engineering across the whole repository, and
it did so silently, on a date nobody was warned about. PR #110 separated the
two concerns and added a scheduled job that now warns 14 days before the next
expiry, so the recurrence is visible before it is blocking.

## 3. What is actually complete

Verified from `main` and CI, not from narrative.

- **AI Gateway is implemented.** Provider abstraction, EU Vertex adapter,
  `PostgresBudgetStore`, call/run identifiers, replay and idempotency,
  fail-closed configuration, sanitized logging. Present on `main` under
  `services/ai-gateway/`.
- **Its tests pass.** On PR #109's head: unit tests green on Linux and Windows,
  PostgreSQL-backed semantics green, supply-chain gates green.
- **The image is production-shaped.** Digest-pinned base, hash-locked
  requirements, dedicated non-root user.
- **Migration runners exist** for all logical units, brought onto a fail-closed
  contract through PRs #105–#107, with the allocator schema-qualification fix
  in #108.
- **Vertex readiness passed** without an inference call. Credentials live in a
  dedicated GitHub environment in the platform repository.
- **Model inference count is 0.** Nothing in this reconciliation called a model.

## 4. What is not built

- ~~**No Coolify deployment automation exists anywhere.**~~ **Built** by
  company-os PR #41: `deploy/ai-gateway.json` (committed declarative spec),
  `scripts/coolify_deploy.py` (stdlib-only driver with `inspect`, `reconcile`,
  `deploy`, `status`) and a SHA-pinned dispatch workflow. `reconcile` re-reads
  after writing and fails on residual difference; deletion is unreachable by
  construction. The platform repository still holds only Coolify *specifications*
  and manual runbooks, which is now correct — the API driver belongs here,
  with the credential.
- **The AI Gateway is not deployed.** Confirmed, no longer inferred: a read-only
  `inspect` run on 2026-08-10 found project `adapteng-ops` and environment
  `production` present, containing `adapteng-baserow-adapter` (running, healthy)
  and `n8n-selfhosted` (running). **`ai-gateway` is absent**, so the first
  `reconcile` will create rather than update. This resolves the `UNVERIFIED`
  mark previously carried here.
- **The approval writer is unbound.** `external_draft_dispatcher` is `None` at
  construction. This is a deliberate open seam, not an omission — see §7.

## 5. Two deployment blockers nobody had recorded

Both were found by reading `main`, and both would have produced a confusing
failure during the first deployment attempt.

**a. The container binds to localhost.** `services/ai-gateway/Dockerfile` ends
with:

```dockerfile
ENV AI_GATEWAY_HTTP_HOST=127.0.0.1 \
    AI_GATEWAY_HTTP_PORT=8081
```

A process bound to `127.0.0.1` inside a container is unreachable from the
container network. Unless the deployment sets `AI_GATEWAY_HTTP_HOST=0.0.0.0`,
the health check cannot succeed and no consumer can reach the service. The
Dockerfile also deliberately omits `EXPOSE`. The default is correct as a
safe default; it simply has to be overridden at deploy time, and that fact was
written nowhere.

**b. `/health` is not a readiness signal.** It is defined in
`services/ai-gateway/app/http_app.py` and returns `{"status":"ok"}` without
touching the database. A green health check therefore proves the process is
listening — not that PostgreSQL, credentials or budget accounting work. Treat
it as liveness only. Database and credential readiness need their own check.

## 6. Where the credentials actually are

This is the finding that unblocks Coolify automation, and it contradicts the
assumption that the work belongs in the platform repository.

**The Coolify credential and URL are repository secrets and variables of
`adapteng-company-os` — this repository — not of the platform repository.**
This repository also already holds production SSH key material and known-hosts.
The platform repository holds no Coolify credential at all.

So the owner's statement that a working Coolify credential already exists is
correct, and no new credential is needed. The automation simply has to live
where the credential already is. That happens to be the architecturally correct
home anyway: cross-repository deployment control belongs to the control plane,
not to one implementation repository.

The platform repository's four GitHub environments carry `branch_policy`
protection only — **no required reviewers and no wait timers**. Every "owner
approval" in that repository is therefore self-imposed in workflow YAML, not
enforced by GitHub. That matters for the friction audit: those gates can be
changed by ordinary pull request.

## 7. The approval writer boundary

`external_draft_dispatcher=None` is a real architectural boundary, and the
previous investigation was right not to close it with one line.

The gateway exposes exactly one outbound side effect, `external_draft.create`,
through a narrow typed port. Binding it in-process would require the gateway
image to import the write adapter, which in turn depends on the approval outbox
schema. That drags an unrelated service's internals into the gateway image and
couples two deployables that are currently independent.

The approval ledger already enforces single-use tokens and replay rejection in
the database, and the outbox is already transactional. The decoupled consumer
therefore does not need to re-implement any safety property — it needs to read
a table that is already correct.

**Recommended:** the asynchronous outbox consumer. It preserves idempotency and
replay, keeps the gateway as provider execution infrastructure, and matches the
stated architecture where PostgreSQL is operational truth and n8n orchestrates.
It is also the only option that does not require redeploying the gateway to
change approval behaviour. This is deferred: it blocks nothing on the path to
first inference.

## 8. FX configuration — already solved

The handoff asked for the simplest correct mechanism and warned against
inventing values or building disproportionate governance. Both concerns are
already addressed on `main`; no new mechanism should be built.

`services/ai-gateway/.env.example` states the contract directly: FX is
**operator-configured and never looked up live**, and the real values belong in
the deployment platform's secret store. The gateway fails closed at
construction if any of the three is missing or invalid.

The price version is a pinned audited constant, `2026-07-27`, asserted against
`app/pricing.py`. It is not a deployment choice.

That leaves exactly one genuine owner input: the USD→EUR rate, its timestamp
and its source label. Three values, entered once, at deployment. **No FX
workstream is needed.** It is a field on the deployment configuration.

## 9. Drift register

Documentation that contradicts a stronger source. Recorded, not silently fixed.

Reconciled in place by company-os PR #40
(`f36be5e64e410b050d6b45dfc0a578b52b054030`, merged 2026-08-10). The register is
kept as the audit trail of what was wrong and what settled it; the "Verdict"
column now records the outcome rather than an outstanding instruction.

| # | Claim | Where | Contradicted by | Verdict |
|---|---|---|---|---|
| D-1 | Migrations 002, 003, 005, 006, 007 and both 008 units "remain repo-only and unapplied" | [`owner/action-items.md`](../owner/action-items.md) | Owner's post-rollout manual production check: all nine logical units exact | **Corrected in PR #40.** The item now records the verified state and forbids replay; [`registry/data-stores.yaml`](../registry/data-stores.yaml) carries all nine units as `live: true` with `replay: forbidden`. |
| D-2 | Rollout authorization blocked pending an automation-evidence lifecycle PR | [`owner/action-items.md`](../owner/action-items.md) | The referenced chain merged through platform PRs #93, #94, #98 | **Closed in PR #40**, each PR re-verified merged with its SHA. Status literal is now `BLOCKED_ON_UNCONFIGURED_PRODUCTION_BACKUP` on all five status surfaces. |
| D-3 | AI Gateway readiness reads as cost-and-runtime blocked | `ai/` notes | Gateway tests and supply-chain gates green on `main`; only deployment is missing | **Narrowed in PR #40** to "implemented and tested, not deployed", citing AI Gateway Tests run `31214858400` (5/5 jobs green). |
| D-4 | Coolify deployment assumed to be manual console work | platform runbooks | Credential for API automation exists in this repository | **Obsolete.** Out of scope for PR #40, which touched only this repository; closed instead by PR #41, which added the API driver and committed spec. The platform runbooks still describe console work and are now the fallback path, not the standard one. |
| D-5 | Migration 001 allocator schema incident open | prior narrative | Fixed and merged in platform PR #108 | **Root cause closed in code**, recorded in PR #40. Narrower than this row's original "Closed": #108's body states "No production changes in this PR", so the live disposition of the misplaced copy is `UNVERIFIED`. |

D-1 was the most damaging: it invited an agent to re-apply migrations that are
already exact, which is the one class of mistake this system is built to
prevent. That invitation is now removed at every surface that carried it,
including a step in [`ai/ai-001-pilot-intake.md`](../ai/ai-001-pilot-intake.md)
that had instructed an agent to apply migration 005.

`UNVERIFIED` — D-1 and D-2 rest on the owner's production check, which neither
this reconciliation nor PR #40 could repeat; PR #40 records them as
owner-attested and not reproducible from GitHub rather than as GitHub-verified.
A read-only schema verification run through the existing migration runner would
settle both permanently, and is exactly the kind of check that should be
automated rather than remembered. Note also that `Migrate Approved Assets` has
zero runs, and that zero runs is **not** evidence of an unapplied database —
that inference is what produced D-1 in the first place.

## 10. Pull requests

| PR | Repository | State | Verdict |
|---|---|---|---|
| #35 | `adapteng-company-os` | **MERGED** 2026-08-10 (`c75127d60e4cc61f0bb4ed44c53b3d73dfe39b93`) | Was ready and waiting on nobody. Merged, then PR #40 branched from the updated `main`. |
| #39 | `adapteng-company-os` | **MERGED** 2026-08-10 | Established this control plane. |
| #40 | `adapteng-company-os` | **MERGED** 2026-08-10 (`f36be5e64e410b050d6b45dfc0a578b52b054030`) | Reconciled D-1, D-2, D-3 and D-5 in place, plus the two registry surfaces. Both checks green. |
| #41 | `adapteng-company-os` | **MERGED** 2026-08-10 | Coolify deployment automation, closing D-4. |
| #110 | `adapteng-automation-platform` | **MERGED** 2026-08-10 | Cleared the §2 repository-wide merge lock. |
| #109 | `adapteng-automation-platform` | **MERGED** 2026-08-10 | Was blocked solely by §2; unblocked by #110. |
| #112, #113, #114 | `adapteng-automation-platform` | **MERGED** 2026-08-10 | AI Gateway deployment contract: bound host/port logging, readiness split from liveness, contract documented. |
| #116 | `adapteng-automation-platform` | **MERGED** 2026-08-10 17:49Z | Repaired the trust anchor. See §12 — this is the one that ended a four-day outage nobody had noticed. |
| #117 | `adapteng-automation-platform` | **MERGED** 2026-08-10 17:54Z | Records the required checks and how their verdicts are to be read. |
| #118 | `adapteng-automation-platform` | **MERGED** 2026-08-10 18:08Z | Removed the MM-25 cross-scope write. Supersedes #115, which was **closed unmerged** and rebuilt on a fresh branch. Empties the waiver list — see §11. |
| #111 | `adapteng-automation-platform` | **CLOSED unmerged** — content shipped as **#119** | Was 8 behind / 6 ahead; its red marks were a stale tree, not a defect. Abandoned and rebuilt on `…-evidence-lane-fresh`, merged 18:26:57Z. See the note below on `-fresh` rebuilds. |
| #119 | `adapteng-automation-platform` | **MERGED** 2026-08-10 18:26:57Z | WEB-002 self-hosted evidence lane — the content of #111. |
| #45, #46, #47 | `adapteng-company-os` | **MERGED** 2026-08-10 | Required the n8n gate; recorded it as enforcing; corrected the trust-anchor diagnosis (§12) and enforced README↔CI equivalence (F-7). |

PR #109 adds credential-file validation that checks existence, readability and
non-emptiness without ever reading contents, its tests, and a least-privilege
runtime-role runbook. The role grants execute on the required definer functions
and no direct table access. Nothing in it warranted the delay it had.

Two red checks on #109 are **not** required by the ruleset and do not block
merge: `Verify exact current head from merged base` and `Base-trusted rollout
authorization`.

**The mechanism recorded here on 2026-08-10 was wrong and has been corrected.**
I attributed these to a partial clone plus a scrubbed environment holding no
credential. Those `fatal:` lines are real but they are not the cause of the
verdict: the git calls sit outside the scrubbed block, and the assertion
`test "$(git status --porcelain=v1)" = ""` discarded git's exit 128 and read
its empty stdout as a clean worktree, so that step **failed open**.

The real defect is that `verify_rollout_trust_anchor.py` tests whether approval
material is *present* in the head tree rather than whether the pull request
*introduced* it. PR #104 merged the approval receipt onto `main` at
`2026-08-06T15:42:06Z` — eleven minutes after the last green anchor run at
`15:30:57Z`, which was #104's own final run. Every branch cut since inherits
the receipt and trips `approval.unexpected`; **55+** consecutive failures
followed. The same presence test on the subject tree
(`approval.circular_or_stale`) means no owner-signed receipt can authorize
anything either.

So this was not a noisy gate misreporting its failure class. It was a gate
unable to reach either terminal state — it could neither pass a pull request
nor accept an authorization — for four days, while looking like a live control.
Full analysis and attribution in the friction audit, F-3.

## 11. The n8n isolation waiver is now a live, scoped blocker

Recorded 2026-08-10, after the gate was made enforcing.

Company-os PR #45 promoted `n8n isolation` to a required check on
`adapteng-automation-platform`. Platform PR #110 had scoped the job correctly
but left it advisory at the ruleset level, and said so in its own comment: the
job always starts, so it is safe to promote later. Only this repository could
finish that, because the ruleset is managed here in
[`scripts/bootstrap_rulesets.py`](../scripts/bootstrap_rulesets.py).

Until that landed the boundary was **reported but not enforced**: a change
touching `n8n/` while the waiver is expired failed the job and could still
merge. That is the precise outcome the gate exists to prevent.

**Verified behaviour after applying it**, which is the part that matters, since
requiring the wrong check is what caused the original outage:

| Pull request | Touches `n8n/` | `n8n isolation` | Outcome |
|---|---|---|---|
| platform #112 | no | `SUCCESS` | Merged normally. Unrelated work is unaffected. |
| platform #111 | yes | `FAILURE` | `BLOCKED`. Correct: it crosses a boundary under an expired waiver. |

The job carries no workflow-level `paths:` filter and runs on every push and
pull request, so it always reports a status. That is what makes requiring it
safe rather than a repeat of the trap in §2.

**Consequence for the owner — superseded the same day, see below.** Renewing
the ISO-1 waiver was briefly the blocker for n8n work specifically.

### 11a. The waiver decision was eliminated, not deferred

Platform PR **#118** merged at `2026-08-10T18:08:29Z` and removed the MM-25
cross-scope write outright. `n8n/isolation-waivers.json` on `main` is now:

```json
{
  "schema_version": "1.0",
  "waivers": []
}
```

There is no expired waiver, because there is no waiver. `n8n isolation` passes
on `main` with the gate fully required. **The owner decision recorded above no
longer exists** — it was not deferred, postponed or delegated; the crossing it
governed was deleted.

Note for the record: this landed as #118, on a fresh branch. **#115 was closed
unmerged**, so anyone tracing this through #115 will conclude the change was
abandoned. It was not.

### 11b. Two pull requests were rebuilt on `-fresh` branches, not rebased

This happened twice within twenty minutes and is now a pattern rather than an
accident:

| Original | Fate | Replacement | Merged |
|---|---|---|---|
| #115 `…fix-mm25-isolation` | **closed unmerged** | #118 `…fix-mm25-isolation-fresh` | 18:08:29Z |
| #111 `…web002-evidence-lane` | **closed unmerged** | #119 `…web002-evidence-lane-fresh` | 18:26:57Z |

Both originals had fallen far behind `main` — #111 by 8 commits — while the
repository was being unblocked by #110, #116 and #118 in quick succession. A
branch that stale has its required checks evaluated against a tree that no
longer resembles the target, so the red marks describe the old world and cannot
be reasoned about directly. Rebuilding was the correct call.

**The trap this leaves behind.** An audit that walks pull request numbers sees
two `CLOSED`, unmerged pull requests and concludes the work was dropped. Both
shipped in full. Any future reconciliation of this repository against `main`
must resolve outcomes by *content on `main`*, not by pull request state — the
same discipline already recorded in §9 for the drift register.

**Related:** this is also why a stale branch must never be diagnosed from its
current check-runs. Check `compare/<branch>...main` for `behind_by` first. An
earlier entry in this very document made that mistake about #111 and had to be
corrected here.

This is the outcome to prefer whenever it is available. A waiver is a standing
promise that someone will revisit a boundary violation later; removing the
violation retires both the promise and the mechanism that tracked it. The
double-lock pinned-tuple design in `validate_n8n_isolation.py` remains in place
for any future crossing, unused and harmless.

## 12. The trust-anchor gate is repaired and green

Platform PR **#116** merged at `2026-08-10T17:49:36Z`.

The gate had failed on every pull request since `2026-08-06T15:30:57Z` — 55+
consecutive runs — and, as F-3 now records, was jammed in *both* directions:
it could neither pass an ordinary pull request (`approval.unexpected`) nor
accept an owner-signed receipt (`approval.circular_or_stale`). For four days it
looked like a working control while being incapable of reaching a verdict.

**Verified green after the repair**, which is the only evidence that counts:

| Run | Branch | Result |
|---|---|---|
| `2026-08-10T17:52:05Z` | `palinaruban-document-check-verdicts` | **success** |
| `2026-08-10T18:03:19Z` | `palinaruban-fix-mm25-isolation-fresh` | **success** |

Those are the first successes in four days.

The repair also splits the verdicts, which was the part of the original brief
worth keeping: **exit 75** and `rollout_trust_anchor.undetermined.<code>` for
"could not determine", **exit 1** and `rollout_trust_anchor.unauthorized.<code>`
for "not authorized", with distinct check-run titles. Both still fail closed.
An infrastructure fault can no longer be read as an authorization refusal — or,
as the pre-repair code did on one line, as success.

**One failure after the repair, and it is not a regression.** Run
`31417567517` at `2026-08-10T18:08:29Z` exited 1 at the exact second #118 was
merging. `pull_request_target` evaluates `main`'s verifier against a head and
base that both moved underneath it. The two runs before it passed and #118
merged cleanly. Non-required, so it blocked nothing. If this recurs *away* from
a merge boundary, treat it as real; a single instance timestamped to the second
of a merge is a race, not a defect.

**Still not required, deliberately.** Promotion is a company-os decision made
in [`scripts/bootstrap_rulesets.py`](../scripts/bootstrap_rulesets.py), and the
precondition is the one that made requiring `n8n isolation` safe: the job must
always start and always report. That has now been observed green twice and
red once under a known race — enough to trust the mechanism, not yet enough to
promote a check whose failure mode includes a merge-boundary race. Re-evaluate
after a run of clean pull requests, and only then.
