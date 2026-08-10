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
| #120 | `adapteng-automation-platform` | **MERGED** 2026-08-10 19:03:23Z (`3bddeeeab05b`) | Restored the waiver-horizon CLI's only failing test, lost when #118 retired the waiver. Tests only, mutation-verified. See §13. |
| #45 | `adapteng-company-os` | **MERGED** 2026-08-10 17:28:54Z (`cb87521039bf`) | Promoted `n8n isolation` to a required check in `bootstrap_rulesets.py`, completing WS-1. See §11. |
| #46 | `adapteng-company-os` | **MERGED** 2026-08-10 17:39:06Z (`48fa67cad42e`) | Recorded the gate as enforcing rather than advisory. |
| #47 | `adapteng-company-os` | **MERGED** 2026-08-10 18:20:41Z (`7b148f5e4b47`) | Corrected the trust-anchor root cause in F-3 and §10, repaired three mojibake characters, and closed the README↔CI drift (F-7) with an enforcing test. |
| #48 | `adapteng-company-os` | **MERGED** 2026-08-10 18:29:36Z (`cd751a642d80`) | Recorded the trust-anchor repair (§12) and the eliminated waiver (§11a). |
| #49 | `adapteng-company-os` | **MERGED** 2026-08-10 18:45:24Z (`124e04642d42`) | Corrected #111's disposition and recorded the `-fresh` rebuild pattern (§11b). |
| #50 | `adapteng-company-os` | **MERGED** 2026-08-10 | Completed this table, which had stopped at #47. |
| #51 | `adapteng-company-os` | **MERGED** 2026-08-10 19:26:32Z (`7ede8c6d439e`) | Recorded F-8, the nondeterministic required check, and dispatched WS-9. |
| #52 | `adapteng-company-os` | **MERGED** 2026-08-10 | Added the #51 row. The tail lags by construction; see below. |

This table is self-referential, and that is the structural reason it drifts: the
row recording any given change can only be written by a later change. #48 and
#49 were absent from it for the same reason D-1 and D-2 went stale elsewhere —
not neglect, but a record that cannot close itself. Verify the tail of this
table against `gh pr list --state merged` rather than trusting it; the merge
SHAs above are what make that check cheap.

**The SHAs are also self-checking, which caught a live error.** While recording
#120 I carried its short SHA as `3bddeea`; the true value is `3bddeee`. A single
transposed character. `gh api repos/<owner>/<repo>/commits/3bddeea` answers
**HTTP 422, "No commit found for SHA"** — a wrong short SHA cannot silently
resolve to a plausible commit. The error was caught before it was published, but
the general point is the reason to record SHAs at all: a timestamp can be wrong
and still look reasonable forever, whereas a wrong SHA is mechanically
detectable by anyone, at any time, with one call. Prefer identifiers that fail
loudly over prose that degrades quietly.

**Demonstrated immediately.** #50 closed the gap and #51 reopened it within the
hour, which #52 then closed again. Chasing the tail with another pull request
is a treadmill and will always leave exactly one row outstanding — the one
being written. Do not read a missing final row as evidence of unrecorded work,
and do not open a pull request solely to add one. The `gh pr list --state
merged` check is the remedy; this table is a convenience.

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

**Update, 19:03Z.** Platform **#120** is the first ordinary pull request to
complete after the repair, away from any merge boundary, and **both** anchor
checks passed on it — `Verify exact current head from merged base` SUCCESS and
`Base-trusted rollout authorization` SUCCESS, alongside all five required
checks. That is stronger evidence than the earlier successes, which all sat
close to merges. The re-evaluation above now has its first clean data point.

**Update, 20:44Z — the merge-boundary race above was a real defect, and this
section was wrong to file it under "not a defect".** The paragraph beginning
"Still not required" told the next reader to treat a single merge-boundary
failure as a race rather than a fault. Platform **#122** investigated instead of
accepting that, and found a **third** conflation site that #116 had not reached:
`fetch_live_pull_request` required `state == "open"` and `merged is False`, and
raised `TrustError("pull_request.state_invalid")` — *authorization* class, exit
1 — otherwise. The observed timeline on #119 is unambiguous:

```
18:26:54Z  ready_for_review    triggers the run
18:26:57Z  merged              3s later, job still starting
18:27:05Z  verifier reads the API -> state="closed"
```

So the check accused an author of an unauthorized act consisting entirely of
merging their own approved pull request, and the same head SHA produced SUCCESS
at 18:23:46Z and FAILURE at 18:26:57Z. A well-formed but no-longer-open pull
request is now `UndeterminedError` / `pull_request.no_longer_open` at exit 75,
narrowly: a malformed `state` or non-boolean `merged` still raises `TrustError`
at exit 1, so this cannot be used to downgrade a suspect API response, and a
number mismatch stays an integrity failure regardless of state. Undetermined
still exits non-zero, so closing a pull request mid-run cannot turn the check
green.

**The lesson is mine, and it is the §13 rule pointed at my own writing.** I saw
one commit produce two verdicts, reached for "race, not a defect", and wrote it
into the record as guidance. It was a race *and* a defect — the race was the
trigger, the misclassification was the fault. It is the same
authorization-versus-infrastructure conflation I had already documented twice in
F-3, and I failed to recognise its third instance because I had a comfortable
explanation. **Two verdicts from one commit is a defect until proven otherwise,
whatever the timing looks like.**

Consequence for promotion: the precondition is unchanged and still unmet. The
count of clean ordinary-pull-request runs resets against the repaired verifier,
because the version observed on #120 still contained this third site.

## 13. A control that cannot fail is not a control

Three instances landed on 2026-08-10, in three different mechanisms. They are
recorded together because the pattern generalises and the next agent will
otherwise meet it a fourth time without recognising it.

1. **The trust anchor's worktree assertion.** `test "$(git status
   --porcelain=v1)" = ""` discarded git's exit 128 and read empty stdout as a
   clean worktree. It printed alarming `fatal:` noise while asserting nothing.
   Failed **open**. (§12, F-3.)
2. **The trust anchor's verdict logic.** Testing whether approval material was
   *present* rather than *introduced* meant no ordinary pull request could pass
   and no owner-signed receipt could authorize. Jammed in both directions for
   four days while appearing live. (§12.)
3. **The n8n waiver-horizon test suite.** When #118 retired the ISO-1 waiver,
   the test asserting the CLI exits non-zero was removed with it and replaced
   only by a passing case. The daily early-warning job would have kept passing
   even if `return 1` were deleted. Closed by platform **#120**, which proved
   the gap by mutation: narrowing `FAILING_STATES` and forcing `return 0` were
   both green before the change and both caught after.

A fourth sits open as **F-8**, and it is a distinct member of the family. The
`2>/dev/null` on the `select-queued-run` call discards the error code needed to
diagnose the nondeterministic required check — but the control does not merely
fail silently. It prints `lifecycle.run_selection_failed`, which is correct for
at most 2 of the 33 failure codes it is emitted for; 23 of them are
`github_metadata.*` and 7 are `runner_selection.*`. So this is **a control that
goes red and says something false about why**, which is worse than one that says
nothing: silence invites investigation, a confident wrong label redirects it.
Both sessions that looked at F-8 went to run-selection logic first, because the
message told them to. (See F-8 for the verified breakdown.)

**The rule.** A green check is evidence only if you know it can go red. When a
control changes — or when the condition it guarded is removed, which is what
happened in case 3 — verify by mutation that it still fails when it should.
Deleting the last failing test is indistinguishable from deleting the control,
and both leave the suite green.

**The corollary, from F-8.** A red check is actionable only if what it says about
the failure is true. When a handler collapses many error classes onto one
message, the message stops being a diagnosis and becomes a guess with the
authority of a log line. Check the width of the `except` before trusting the
label attached to it.

**The corollary for this record:** never infer that a mechanism works from the
absence of failures. Two of the three above looked healthy for days.

## 14. Where the required-check list is allowed to be duplicated

Platform **#117** rewrote §3 of that repository's
`docs/github-governance-checklist.md`, which had claimed `Validate repository
structure and content` was the only required check with the rest "pending". It
now enumerates all five, with the workflow each comes from, and documents the
`unauthorized` versus `undetermined` split.

The session that wrote it offered to cut the table back to a bare pointer,
since a second copy of a list is a drift source. **Decision: keep the table.**
Verified against the live ruleset on 2026-08-10 — all five names match exactly
and `required_approving_review_count` is `0`, as the document states. It is
accurate, it is useful to someone working in that repository who should not
have to open another repository to learn what will block them, and it already
names [`scripts/bootstrap_rulesets.py`](../scripts/bootstrap_rulesets.py) as
the authority and tells the reader to propose changes there.

**What was missing was the other direction.** The mirror knew about the
authority; the authority did not know about the mirror. That is exactly the
shape of F-7, where the README and the CI module list each failed to mention
the other and drifted apart. `bootstrap_rulesets.py` now carries a comment at
the platform target naming the mirrored table, so anyone editing the tuple is
told where the copy lives.

**This is a weaker guarantee than F-7 got, and deliberately so.** F-7 is
enforced by a test because both surfaces live in one repository. This pair
spans two repositories, so a test would need cross-repository read access at CI
time — more machinery, and a new credential path, than a documentation mirror
is worth. A comment that travels with the line being edited is the proportionate
control here. If the pair is ever observed to have drifted, revisit that
judgement.

## 15. An unsettled policy: may an advisory refusal be merged past?

**This is the one open governance question in the programme, and it needs one
sentence from the owner.** It is recorded here because two sessions reached
opposite conclusions about it within four minutes, and both are defensible from
the documentation as written.

The situation is identical in both cases. A pull request changes a path in
`PROTECTED_EXACT_PATHS`; all five *required* checks pass; the trust anchor
correctly returns `rollout_trust_anchor.unauthorized.approval.commit_delta_invalid`,
because a protected change arrived without an owner-signed receipt commit; and
the two anchor checks are advisory, so GitHub permits the merge.

| | Platform #122 | Platform #121 |
|---|---|---|
| Protected paths touched | `verify_rollout_trust_anchor.py`, `authorize-rollout-policy-change.md` | `authorize_approved_assets_phase.sh`, `migrate-approved-assets.md` |
| Anchor verdict | `unauthorized.approval.commit_delta_invalid` | `unauthorized.approval.commit_delta_invalid` |
| Required checks | all green | all green |
| Outcome | **merged** 20:44:08Z | **left open** |

All four paths verified present in `PROTECTED_EXACT_PATHS`, read from
`verify_rollout_trust_anchor.py` on `main` rather than from either report.

**The reading under which #122 is right.** The anchor checks are advisory
*precisely because* the required signature cannot be produced inside a pull
request — the platform's own governance checklist says so, and says an
`unauthorized.<code>` verdict is actioned by "an approver, or the author". If a
protected change could never merge without a receipt, "advisory" would be
indistinguishable from "required". This reading has real force, because **the
anchor protects its own source file**: every repair to it is a protected change.
Under the opposing reading, the four-day anchor outage could not have been fixed
by any agent, and #116 — which ended it — would also have been improper.

**The reading under which leaving #121 open is right.** A refusal that is
routinely merged past is decorative. Each such merge also forecloses promoting
these checks to required, which this audit has been arguing toward. #121 is not
a repair to the anchor's own machinery; it is an ordinary reliability fix that
happens to touch a protected path, so the bootstrap argument does not apply to
it.

**The distinction that probably resolves it, though the owner should decide.**
Separate *bootstrap* changes — to the anchor's own verifier and runbook, which
cannot self-authorize — from *ordinary* protected changes, which can wait for a
receipt. That is a one-line policy, and it makes #116 and #122 correct while
keeping the boundary meaningful for everything else.

**Interim stance: #121 stays open.** When the meaning of a control is unsettled,
the conservative default is not to spend it. This is deliberately *not* a
criticism of #122, whose engineering is sound, whose reasoning was stated openly
in its own description, and which corrected a wrong prediction it had published
about its own checks rather than quietly editing it away. The gap is in the
policy, not in that work.

**Tested against both live cases, 2026-08-10 — the distinction holds.** WS-6
offered to revert #122 if I meant the stand-down more broadly. I do not, and the
proposed one-line policy is why: applied to the two real pull requests it does
not merely sound reasonable, it separates them correctly and for the right
reason.

| | Touches | Can it self-authorize? | Verdict under the proposed policy |
|---|---|---|---|
| #122 | `verify_rollout_trust_anchor.py` — the anchor's own verifier | **No.** Any fix to the verifier is judged by the broken verifier | Bootstrap; merging is correct |
| #121 | `authorize_approved_assets_phase.sh` — an ordinary protected script | **Yes.** A receipt lands it unchanged, with no circularity | Ordinary; waits for the signature |

A policy that gives the same answer for both would be wrong in one of them:
demanding a receipt for #122 leaves a live false-accusation bug in place and is
unsatisfiable in principle, while waiving one for #121 concedes that any
protected path may be changed by whoever is willing to click merge. **A reverted
#122 would restore a check that accuses authors of unauthorized acts consisting
entirely of merging their own approved work.** That is a worse state than the
policy ambiguity, so it stays.

This does not settle the question — the owner still owns the sentence — but it
narrows it usefully. The proposal is no longer abstract; it has been checked
against the only two cases anyone has, and it discriminates.

**If it is adopted, it does not have to live only in prose.** WS-6 offered the
implementation and correctly declined to build it without the decision: the
verifier can already see the difference between a change confined to the anchor's
own machinery and one touching other protected paths, so it could **emit distinct
codes** rather than making a reader open the file list and re-derive which case
they are in.

That is the same principle as the rest of this programme. §13 says a control that
cannot fail is not a control; F-8 adds that a control which reports the wrong
cause is worse than one that reports none; F-7 is a whole register of things that
drifted because a distinction lived in prose that something else had to remember.
A policy encoded in the check cannot drift from the check. **So the owner's
sentence has a durable form available, and the choice is worth making
deliberately rather than defaulting to prose** — which is the option that decays.

Not built, and correctly so: it is both a protected-path change and an encoding
of a policy that has not been decided. It waits for the decision and for someone
with the authority to ask for it.

**A test for whether the sentence is good enough, from WS-9.** Whichever way the
owner rules, *the rule must be unambiguous enough that an agent does not have to
be cautious by temperament to apply it correctly.*

That is not a stylistic preference; it is the thing that actually failed here.
Both sessions reasoned honestly from the same governance text and took opposite
actions on an identical verdict, and WS-9's own assessment of its stop is the
decisive evidence: **"a judgement call, not a rule I could point at."** It
happened to be the conservative call, and conservatism happened to be right — but
a policy that only works when the agent reading it is temperamentally careful is
not a policy, it is a filter on personnel.

So the acceptance criterion for the owner's sentence is not "is it correct" but
"could an incautious agent apply it correctly without judgement". If the answer
is no, the sentence needs to be sharper regardless of which reading it adopts.
This is also the strongest argument for the encoded form above: a code emitted by
the verifier requires no temperament at all to read.


