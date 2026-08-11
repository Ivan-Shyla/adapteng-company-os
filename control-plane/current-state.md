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

> **Superseded twice — do not act on this paragraph.** Its "known race" reading
> was wrong (see the 20:44Z update: it was a real defect), and "a run of clean
> pull requests" was ambiguous (see the 22:29Z update, which replaces it with the
> adopted criterion). Both corrections are below. Kept because the record of how
> the criterion was got wrong is part of the evidence for the criterion.

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

**Update, 22:29Z — the precondition was ambiguous, and the sharper version is
adopted.** WS-6 swept every anchor run and found nothing live, but flagged that
"always starts and always reports" and "runs green" are different criteria that
have not yet diverged only because no anchor run has fired since 20:45:58Z. Once
protected-path traffic resumes they point in opposite directions.

**The words are right and "green" is wrong.** A gate that has never been observed
refusing is a gate with no evidence it *can* refuse — which is §13 exactly, and
the failure mode this entire repair existed to remove. A correct refusal is the
check working, and counting only greens would rebuild the thing that broke.

Adopted criterion: **N consecutive runs that each reach a terminal verdict, of
any class, with no infrastructure fault.** Three corrections to the proposed
wording, each of which would otherwise miscount:

1. **`not_applicable` counts.** WS-6 proposed "of either class", meaning
   authorized or unauthorized. There are three terminal outcomes, and
   `not_applicable` is the most common — it is what any pull request touching no
   protected path produces. Read strictly, "either class" would exclude
   `d799b5bb`, the single run the count currently rests on, whose verdict is
   `{"outcome":"not_applicable"}` (verified in the job log).
2. **`undetermined.pull_request.no_longer_open` is not a fault.** WS-6's message
   closes by calling any `undetermined.*` code a real defect — which restates the
   #117 claim that its own #123 retired. #122 *created* that code for the benign
   merge-underneath-the-job race documented above. It is neither evidence for the
   mechanism nor against it: the job legitimately could not reach a decision.
   Treat it as **neutral** — it does not advance the count and does not break it.
   Any other `undetermined.*` is an infrastructure fault and breaks it.
3. **The count needs a version anchor, and that is why it stands at one.** All
   four failing runs on the board — `f0a2d175` ×2 (#121) and `fd96060f` ×2
   (#122) — were created between 20:21:21Z and 20:43:43Z, before #122 merged at
   **20:44:08Z** (`merged_at`; corrected from the committer date 20:44:07Z by
   point 5 below). `pull_request_target` takes its workflow and code from the base
   branch, so every one of them ran the *pre-repair* verifier. They are excluded
   on **code version, not verdict class**, which is a different reason from the
   one WS-6 gave. Without a version anchor written into the criterion, a later
   sweep will scoop up old runs and over-count — but see point 4: the anchor
   cannot be read off the run, and point 6: it must not be a constant.

**Current count: 1** — run `31430619706`, head `d799b5bb`, 20:45:58Z, the only
anchor run to date executing post-#122 code. WS-6's forward-looking consequence
stands: if #121 is rebased or re-triggered, its refusal *will* count, because
declining correctly is the mechanism working.

**Update, 01:05Z — three further corrections, all verified.** WS-6 conceded the
first two above and then supplied a finding that changes the third.

4. **The version anchor is not observable on the run at all.** Correction 3 above
   said the criterion needs "runs of the verifier at or after `3e9b9ef4`". WS-6
   checked whether that is *checkable* and it is not. Run `31430619706` records
   `event=pull_request_target`, `head_sha=d799b5bb…`, `head_branch=palinaruban-undetermined-not-always-broken`
   — the **pull request head**. `pull_request_target` executes base-branch code
   while recording the PR's head, so no field on the run says which verifier ran.
   All 81 anchor runs to date use that event, so this is universal for this
   workflow, not incidental. The only available proxy is a run timestamp against
   the merge time, and the record must say so rather than implying the version is
   readable. **The field must be `run_started_at`, not `created_at`** — WS-6's
   correction, verified. `created_at` freezes at attempt 1 while `run_started_at`
   moves with each re-run, and the checkout resolves `refs/heads/main` at
   execution time (workflow line 31, `fetch-depth: 1`), so a re-run executes
   whatever verifier is on `main` when it runs while still carrying an old
   `created_at`. Under `created_at` the criterion would exclude genuine runs of
   the current verifier and could admit a run whose first attempt predated a
   reset. **Scope, checked across all 81 runs rather than a recent slice:** exactly
   one run has `run_attempt > 1` — `31116200705`, the 08-06 one — and it is also
   the only run where `created_at` and `run_started_at` differ. Today's count is
   unaffected either way; the fix is forward-looking.
5. **Name the timestamp field, not just the value.** I wrote 20:44:07Z and WS-6
   wrote 20:44:08Z. Both exist: 20:44:07Z is the merge commit's committer date,
   20:44:08Z is the pull request's `merged_at`. Since `pull_request_target` takes
   code from the base branch, the boundary that matters is **when the ref moved**,
   which is `merged_at` — the committer date precedes it. So WS-6's field is the
   correct one and the criterion should name it. **The comparison also has an
   error window:** a run created shortly before the merge may check out shortly
   after it, so a run whose lifetime straddles the boundary cannot be classified
   from metadata. Here nothing straddles — the nearest failure is 25 seconds
   before and the survivor 110 seconds after — so the exclusion holds, but the
   window is a property of the criterion and not of this data.
6. **The anchor must move, or the criterion decays into the #120 mistake.**
   Written as the constant `3e9b9ef4`, it would still be the anchor after the
   *next* verifier change, and the count would absorb runs of a verifier nobody
   had validated — which is exactly what counting #120's run would have been. So:
   **the anchor is the `merged_at` of the most recent change to
   `verify_rollout_trust_anchor.py`, and any such change resets the count.** That
   converts my one-off #120 reset into a rule. **One addition WS-6 did not make:**
   a change to `.github/workflows/rollout-trust-anchor.yml` must reset it too,
   because that file decides both what triggers a run and what code gets checked
   out — the "always starts" half of the precondition lives there, not in the
   verifier.
7. **Count distinct head SHAs, not runs.** Two runs of one tree are one
   observation repeated, and under a raw-run bar, re-triggering a single pull
   request N times would satisfy promotion without ever exercising a second input.
   The data supports this strongly: the board carries `f0a2d175` ×2,
   `fd96060f` ×2, `36902fc7` ×2, `4d6e70ba` ×2, `83cfa563` ×2, `835c92c6` ×2,
   `d9766d6f` ×2 and `05255308` ×3. It is "one clean run is not a run of them"
   applied one level down. Verified: after 20:44:08Z there is exactly **one**
   distinct head SHA, so the count is 1 under either rule.

**The reset pair is complete, and that is now a checked claim rather than an
assumed one.** Read at `824b4238`: the workflow uses exactly one action —
`actions/checkout` pinned to `11bd7190…` at line 24 — and no local composite
actions; its sparse-checkout is four paths (`.gitattributes`, `.gitignore`, the
`allowed_signers` trust root, and the verifier). Verifier plus workflow is
therefore the whole of what determines run behaviour. Worth recording as checked,
because the failure mode is a third input added later while the criterion
silently stops covering it.

**But the pair is not guaranteed to come from one revision, and that weakens the
claim in a way worth stating.** A re-run re-executes the *original* workflow file
— the run is bound to it — while the checkout step at line 31 fetches
`refs/heads/main` live. So a re-run pairs an old workflow with today's verifier,
and a run's "version" is not a single thing that a timestamp can name. **Inert
today, and verified inert rather than assumed:** #122 changed three files —
`docs/runbooks/authorize-rollout-policy-change.md`,
`scripts/validation/verify_rollout_trust_anchor.py` and
`tests/test_rollout_trust_anchor.py` — and **not** the workflow, so every
revision of the verifier since the reset has run against the same workflow. The
hazard becomes live the first time the workflow itself changes, at which point
re-runs silently mix revisions and no run field records it. This is the same
root as the `created_at` correction above: re-run identity is absent from the
fields anyone reaches for.

**One correction to WS-6's reason for leaving the trust root out of the pair.**
It argued that `allowed_signers` "determines *what* verdict, not *whether* a
verdict". It does both: workflow lines 69–70 attach two undetermined codes to it,
`anchor_trust_root_absent` and `anchor_trust_root_not_regular`, so deleting it or
replacing it with a symlink stops any verdict being reached at all. The
conclusion survives for a different reason — those failure modes are *loud*. They
break the count through the existing `undetermined.*` rule instead of silently
invalidating runs, and the reset exists to catch silent invalidation. The wrong
reason would have had a future: someone triaging `anchor_trust_root_absent` while
believing the trust root cannot break the check.

**A weakness in the count that neither of us had named.** The single counted run
is `not_applicable` — the verdict produced when the verifier finds no protected
change at all, which exercises less machinery than any other outcome. So "1" is
not merely a small number, it is one observation of the cheapest path. A count
composed entirely of `not_applicable` runs would be weak evidence for exactly the
proposition being tested, which is that the gate *can* refuse. Whatever N is
chosen, it is worth requiring that at least one counted run reached a real
authorization decision.

### 12a. The authorization path has never run since the repair

WS-6 returned with the evidence that settles the weakness above, and the true
position is worse than either of us had written. Verified independently, with one
correction that makes the finding stronger rather than weaker.

**The verdict census.** The check writes its outcome as the check-run title, and
the map is explicit in the verifier — `_check_run_payload` admits five outcomes
and gives each a distinct title. Reading that title off every successful run in
the check's entire history:

| Window | Distinct runs | Verdict |
|---|---|---|
| 2026-08-05T14:56:30Z → 2026-08-06T15:30:57Z | 7 | `authorized` — "Exact signed subject authorized" |
| 2026-08-10T17:52:05Z → 20:45:58Z | 5 | `not_applicable` — "No protected rollout boundary change" |

Twelve successes in total, as WS-6 said. **WS-6 listed two `authorized` runs;
there are seven.** Both of its two are real and one of them is genuinely the
last, but the set was offered as the whole and it is under a third of it. The
conclusion is untouched: all seven precede the outage, all five post-repair
successes are `not_applicable`, and **no `authorized` verdict exists after the
repair.**

**The escalation — WS-6's own rule, turned on WS-6's aside.** WS-6 consoled the
criterion by observing that an `unauthorized` refusal would satisfy "reached a
real authorization decision", and that refusals exist in quantity. They do:
fifteen distinct heads were refused on 2026-08-10 alone. **But every one of them
predates the reset anchor.** Point 6 above — which WS-6 proposed and I extended —
resets the count at the `merged_at` of the most recent verifier change, and that
is #122 at 20:44:08Z. After that instant exactly one run exists, `d799b5bb` at
20:45:58Z, and it is `not_applicable`. So under the verifier now on `main`:

> **zero `authorized`, zero `unauthorized`, one `not_applicable`.**

Neither real decision has been observed even once. WS-6 imported its consolation
across the boundary it had itself just drawn — the same move as counting #120's
run, which is precisely what its own refinement was written to prevent. The gap
is twice as wide as the note claimed.

**Why that is not merely a thin count.** The four-day outage was
`approval.circular_or_stale` on the subject tree — the failure that made it
impossible for *any* owner-signed receipt to authorize anything. So
`_approval_material_introduced` on its authorizing branch is code that was broken
for four days, is the reason the gate could reach no terminal state at all, and
has since been validated **only by unit tests**. That is a gap in the repair's
validation, not only in the promotion criterion.

**And it cannot close by waiting.** An `authorized` verdict requires a pull
request that touches a protected path *and* carries an owner-signed receipt.
Ordinary traffic yields `not_applicable` — which is what all five post-repair
successes are. So any N will be reached with the let-work-through path still
unexercised unless someone deliberately produces the input.

**The input already exists, and both missing observations can be taken from it.**
Platform #121 changes `scripts/operations/authorize_approved_assets_phase.sh` and
`docs/runbooks/migrate-approved-assets.md` — both verified in
`PROTECTED_EXACT_PATHS`, at lines 84 and 74 — and carries no receipt, so its
verdict is a genuine refusal. But its only two anchor runs are at 20:21:21Z and
20:27:27Z, **both before 20:44:08Z**, so the red mark displayed on #121 today was
produced by a verifier that no longer exists. Therefore:

1. **Fire an `edited` event on #121** — a title or body edit, no push. This
   re-runs the anchor against the current verifier and yields the first
   post-anchor `unauthorized` observation. Verified safe:
   `rollout-trust-anchor.yml` is the **only** workflow in the repository that
   lists `edited`, and `edited` is not in the default trigger set, so nothing else
   re-runs, no green check is invalidated and no review is dismissed. #121 is
   already red and stays red; the observation merely becomes current.
2. **Then sign the receipt.** That turns the same pull request `authorized` and
   discharges owner-decision item 2 at the same time.

So the promotion-evidence gap and the outstanding receipt are one action, and its
cheaper half is free and can be taken first.

**A third option was offered and should be declined, for a reason that is the
first correction's own consequence.** WS-6 noted that `gh run rerun` on #121's
existing run would re-execute against the verifier fetched live from `main`,
producing the same post-anchor observation without touching another session's
pull request. The mechanism is right — line 31 fetches `refs/heads/main` at
execution time, and the objection I went looking for turns out to be void, since
#122 left the workflow unchanged and so no mixed-revision hazard exists for this
particular re-run. Decline it anyway: **a re-run replaces the run's conclusion in
place, and an `edited` event adds a run beside it.** The pre-reset `unauthorized`
on #121 is evidence in the reconstruction this section is built from, and the
`created_at` defect above is exactly the shape of what a re-run does to the
record — it makes the headline fields describe an execution that no longer
matches them. The additive path costs a body edit and leaves both observations
readable. Cheaper is not the same as safer when the artefact being produced is
evidence.

**One latent defect in the anchor, recorded because it is in code that was
written under this programme.** `verify_pull_request` at 2653–2659 compares the
event-time head and base SHAs against the live API values in a single condition
and raises `TrustError("pull_request.live_ref_changed")` on either. The two halves
are not alike. **Head** moved between event and read is a TOCTOU signature and is
defensibly authorization-class. **Base** moved is somebody merging to `main` in
those seconds; it says nothing about the pull request's author.

The verifier already contains the argument, written for the sibling race. At
975–982 a pull request that closed or merged mid-flight raises
`UndeterminedError("pull_request.no_longer_open")`, and the comment above it says
refusing it "would accuse its author of an attempt nobody made, which is the
confusion this verifier exists to avoid." The base-moved case is that situation
exactly, and it gets the opposite class. **That makes this less a matter of taste
than WS-6 allowed** — it is an inconsistency with the author's own stated
reasoning, available in the same file.

Two refinements to how it presents. The mislabel is not merely generic: outcome
`failure` renders title "Base-trusted verification failed" (2773) with summary
"The exact current head is not externally authorized" (2780), and that summary is
*affirmatively false* when the base moved — it reports a verdict about the head
that was never reached. And there is a **second** site, `operator.live_ref_changed`
at 3087–3088, which WS-6 did not name; it is not the same fix, because it
compares the whole `LivePullRequest` object rather than two SHAs, so it cannot be
split into head and base at all. Strength, stated: mechanism read at source, **no
production occurrence observed** — every `TrustError` collapses to one summary
line, so the code survives only in logs. Latent, not live, and under the hold.

**A corroboration that fell out of the same query.** The last `authorized`
verdict in the repository's history was written at 2026-08-06T15:41:39Z — the
check-run `completed_at` on head `a74b6c67`, which is #104's own head. #104 merged
at 15:42:06Z, **27 seconds later**. The receipt authorized itself, and that same
merge is what made every subsequent run fail the presence test; the diagnosis and
the timeline now close on each other from independent directions. WS-6's
arithmetic is exact. One field note, which is point 5's discipline applied to
WS-6's own citation: 15:41:39Z is the check's `completed_at`, not the run's
`created_at`, which is 15:30:57Z on attempt 2. Eleven minutes separate the two
candidate readings, so the field has to be named.

**A consequence of the timestamp comparison, which is an independent argument for
the neutral ruling.** Because classification is now a timestamp comparison, a run
straddling the anchor is unclassifiable from metadata — and the window in which
that happens is precisely a merge landing underneath a running job, the same race
that produces `no_longer_open`. Those runs are therefore unclassifiable for two
unrelated reasons at once, and neutral is right on both. Operational note, WS-6's:
because neutral runs neither advance nor break the count, a spell of them makes
"not promoting yet" indistinguishable from "no traffic". The stall should be
surfaced, or the count goes quiet in a way that reads as a decision.

**And the second consequence generalises past this check.** WS-6's argument for
why a green streak cannot be the criterion is that a long red stretch is
ambiguous between "the anchor is broken" and "several protected-path pull
requests landed in a row" — and only the verdict string distinguishes them, not
the conclusion. That is the F-8 finding one layer up: there, a CI conclusion of
`failure` was ambiguous across nine transport codes and two genuine ones, and
only the discarded error code distinguished them. Two workstreams, different
subsystems, the same structural result. Recorded as a rule in §13.

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
at most 2 of the **11 distinct codes** that can produce it; the other **9** are
`github_metadata.*` transport and pagination failures. So this is **a control
that goes red and says something false about why**, which is worse than one that
says nothing: silence invites investigation, a confident wrong label redirects
it. Both sessions that looked at F-8 went to run-selection logic first, because
the message told them to. The same shape sits at the runner call site, where
`lifecycle.runner_registration_invalid` is wrong for 9 of the 13 codes that
reach it. (See F-8 for the reachability arithmetic — and for the correction of
the larger, wrong figure this paragraph carried for five commits.)

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

**The worked example, supplied by WS-1 against itself.** Having been corrected,
WS-1 traced exactly how the label misled it: it examined `select_run`'s own raise
sites, established that `run_selection.multiple` is unreachable under a stub that
always returns exactly one run, and concluded the failure was therefore "not a
`MetadataError` at all" — an uncaught exception or a crash. The reasoning inside
that boundary was sound. The boundary was wrong: the `except` at 510 spans the
whole operation, and three `fetch_all` call sites sit inside it. Its own
diagnosis of the miss is the part worth keeping — *"I stopped at the function
boundary while the `except` spans the whole operation. I never stated that
assumption, which is exactly why I didn't test it."*

That is the mechanism by which a wrong label does damage. It did not merely fail
to inform; it selected the subsystem, and careful reasoning within the wrong
subsystem produces a wrong answer carrying all the visible marks of a good one —
specific line numbers, a ruled-out alternative, a stated conclusion. Silence
would have been safer, because silence does not come with a suggestion. An
unstated assumption is untestable by construction; naming the boundary you
searched is what makes the miss visible to the next reader, including yourself.

**The corollary for this record:** never infer that a mechanism works from the
absence of failures. Two of the three above looked healthy for days.

**The same error recurred one level up, which is what promotes it to a rule.**
Tracing the second F-8 fixture site, WS-1 excluded a file from the executed path
because the shell's helper list does not name it — while the shell *does* run a
script that imports the deciding function from it. The first error stopped at a
function boundary that an `except` crossed; the second stopped at an invocation
boundary that an `import` crossed. Both have the form *I checked the named list
and the mechanism was not in it*, and in both cases the list was real, correctly
read, and the wrong list.

So the rule is not about exception scope. **The boundary you stop searching at is
an assumption, and it is invisible precisely because stopping feels like
finishing.** A conclusion reached inside too small a boundary looks identical to a
correct one — same line numbers, same ruled-out alternatives. The only cheap
defence is the one WS-1 named itself: state the boundary you searched, so the
unstated assumptions become visible as the unstated ones.

Worth recording that in this instance the *conclusion* was right and only the
reason was wrong. That is the harder case to catch, and the reason it still
matters is that reasons carry forward while conclusions do not: "not executed"
and "executed but unfailable by construction" imply different future behaviour
the moment anyone reorders the code.

**The generalisation, arrived at twice independently.** A pass/fail conclusion is
a lossy projection of the verdict that produced it, and the loss is exactly the
part you need when deciding whether a control is healthy. F-8 reached this from
below: a CI conclusion of `failure` is ambiguous across nine transport codes and
two genuine run-selection outcomes, and only the discarded error code separates
them. §12 reached it from above: a stretch of red anchor runs is ambiguous
between a broken gate and a run of protected-path pull requests being correctly
refused, and only the verdict string separates *those*. Different subsystems,
different layers, same result — **count verdicts, not colours.**

**Stated more narrowly than I first wrote it, on WS-6's correction.** The claim
was "any health metric defined over conclusions will eventually mistake a working
control for a broken one". That over-reaches: for a plain test suite, red really
does mean broken and the conclusion loses nothing. The property actually doing the
work is that **the control has more terminal states than the conclusion field has
values** — three verdict classes squeezed into two conclusions in §12, eleven
error codes squeezed into one `failure` in F-8. Where that inequality holds the
collapse is guaranteed and the conclusion cannot be trusted as a health signal;
where it does not, the conclusion is faithful.

That version is more useful because it says *when to look* rather than asserting
the problem always exists — count the control's terminal states, compare against
the field you are reading, and you know immediately whether you are reading a
projection or the thing itself. The refusing controls in this programme all fail
the test, which is why they all needed verdict strings; an ordinary test suite
passes it, which is why nobody has ever needed one there.

**Read the predicate, not the sets it consumes.** Recording this against myself
the same day: §15 asserted that a path is protected by "both" mechanisms, having
counted the two frozensets the worked examples cite. Reading `is_protected_path`
itself showed **three** mechanisms and an exemption that fires before all of
them. The sets were the visible artefact; the function was the decision, and I
described the artefact.

**The general rule, with seven sub-shapes and twelve instances.** WS-1 proposed the
right corollary after its own second miss: state not only the boundary you
searched, but whether the search you chose *could have returned the answer*. That
generalises everything in this family, and the instances now sort cleanly by how
the instrument's range fails to match the question:

- **Too narrow, empty — silence read as absence.** WS-1 searched a shell helper
  list for a module. A helper list enumerates *invoked scripts* and structurally
  cannot contain an *imported* one, so the empty result was a true negative about
  the wrong category, read as a finding. Its earlier instance was a function
  boundary searched against an `except`. **A third instance, mine, caught before it
  was published:** verifying WS-1's coordinate table I grepped `f0a2d17` for
  `2>/dev/null`, found nothing at line 255, and was one step from telling WS-1 its
  correct table was wrong. The redirect is at 255; #121 changes its *target* to
  `2>"$selection_error_file"`. I had searched for the old form of the construct in
  the tree whose entire purpose is to change that form. **The trigger is worth
  naming because it recurs: when verifying a change, do not search for the string
  the change changes** — search for the construct's position, or for what it was
  changed *into*. What saved it was reading the region rather than trusting the
  grep, which is the same remedy as everywhere else in this list.
- **Too narrow, non-empty — a sample read as a census.** WS-6 reported the two
  `authorized` verdicts in the anchor's history; there are seven (§12a). Every
  element it named was true and one was genuinely the last — the quantifier was
  false, not the data. This is the more durable of the two narrow shapes, because
  an empty result at least invites the question "is that right?", while a
  plausible non-empty list looks like a finished enumeration and so is *less*
  likely to be challenged than the silence is. **A second instance, mine, is
  recorded in §15:** the eleven-entry `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256` was
  read from its head and described by its first four members.
- **Too wide — hits read as presence.** WS-1's repository-wide enumeration of
  "importers" returned sixteen sites of which **three** are imports; the other
  thirteen are string literals. WS-9's count of `raise` sites had the same defect:
  every site is real, but the set is wider than "reachable".
- **Right range, wrong granularity — a member misclassified because the unit of
  analysis is smaller than the construct.** In that same enumeration, WS-1's
  line-oriented classifier called
  `tests/test_approved_assets_rollout_readiness.py:24` a string literal: line 23
  is `from scripts.validation import (` and 24 is the continuation carrying the
  module name, so the line holding the name is not itself an import statement.
  Verified at source. This is not a range failure — the site was found — and it is
  worth separating because the *same instrument on the same run* was
  simultaneously too wide across the set and wrongly narrow at one member. **Note
  how it surfaced:** not by re-inspection but because two independent counts
  disagreed, three against two. Neither party could have found it alone by looking
  harder, which is a different remedy from every other entry in this list.
- **Wrong axis — the artefact described instead of the decision.** My "both
  mechanisms" above; the sets are real, and they are not what decides.
- **Right answer, unstated validity interval — applied outside its domain.**
  WS-9 derived #121's line offsets from `git diff --numstat`: 10 added, 1 removed,
  net **+9**. Correct, and exact at the runner site — 379→388 and 384→393, three
  coordinates, three confirmations. It is exact there because that site lies below
  every line #121 inserts. At the select site the local offsets are +1, +1 and +7,
  so the same rule is wrong by eight at the call and by two at the label
  (F-8). Nothing about the number `9` states the condition under which it
  transfers. **This is the most dangerous member of the family**, because the
  other five produce answers that are wrong where you look; this one produces
  answers that are *right* where you look and wrong elsewhere. Checking it more
  carefully at the runner site would have raised confidence in it, correctly, and
  changed nothing about the select site. The remedy is not verification — it is
  asking what the instrument measures: `--numstat` answers *how many lines
  changed*, never *where line N went*, and the two coincide only below the last
  hunk.
- **Success mistaken for effect — an action that reports success while changing
  something other than what its name promises.** The six above are all *read*
  instruments, misread. This one is a write, and the remedy is different in kind.
  Deciding whether `.gitattributes` could remove the CRLF hazard for item 5, I ran
  a scratch repository at `core.autocrlf=true`: committing `*.py text eol=lf`,
  then `git add --renormalize .`, then `git checkout -- .` all reported success
  and all left the file byte-identical, still CRLF. `--renormalize` renormalizes
  the *index*; `checkout -- .` restores files it considers modified, and the file
  is not modified relative to the index. Only deleting the file and checking it
  out again converted it. The command whose name most exactly describes the
  intended effect is the one that does not produce it. **A second instance is my
  own, one command earlier in the same session:** the first run of that experiment
  wrote the sample file directly and never had git materialize it, so the CRLF
  condition was never created and all four rows came back identical — output that
  reads as clean confirmation of whatever was being tested. The setup step and the
  remedy step failed the same way: an action assumed to have landed rather than
  measured. **The remedy does not generalise from the rest of this list.** For a
  read you ask what the output is true of; for a write, "it succeeded" is never
  evidence, and the only check is to measure the artefact you wanted changed —
  here, count the `CR` bytes.

Unifying form, and the reason the seven belong together: **every one of these
instruments returned a true statement, and in no case was the true statement about
the question being asked.** The helper list truly contained no such script. The
two `authorized` verdicts were truly `authorized`. The sixteen hits truly
contained the module name. Line 24 truly is not an import statement. The two
mechanisms truly exist. The net delta truly is +9. `git add --renormalize` truly
renormalized the index. Not one of these is a wrong answer; each is a right answer
to a question nobody asked.

That is why "check it more carefully" is the wrong remedy and why it failed
visibly in the sixth instance, where more checking *increased* confidence,
correctly, in a rule that was already outside its domain. The check that works
costs one sentence and is not about correctness at all: **say what the output is
true of, then compare that to the question.** Range failures answer about the
wrong set, granularity failures about the wrong unit, axis failures about the
wrong artefact, and domain failures about the wrong region of the file. Five of
the seven produce *confident* wrong answers, and the two narrow shapes produce a
confident wrong answer that looks like diligence — which is why, until the sixth,
they were the dangerous members of the family. The seventh is the exception that
proves the rule is about reads: there is no output to say anything true of, only
an exit status, and an exit status is true of the command rather than of the
world.

**A related family that is not an instrument failure at all, and needs separating
because the remedy differs.** In every case above the instrument was consulted and
its answer misread. In these the answer came *first* and the citation was
recruited afterwards to support it — so the citation is **true, and its truth is
independent of the conclusion it is offered for**. Three instances, all from this
programme:

- WS-6 argued `allowed_signers` belongs outside the reset pair because it
  "determines *what* verdict, not *whether* a verdict". Workflow lines 69–70 say
  otherwise. The conclusion — leave it out — was defensible on the loud-versus-
  silent argument; the reason given was reached for afterwards.
- WS-6 scoped the `APPROVAL_PATHS` hazard inert by citing `commit_delta_invalid`
  at 2703. True, and irrelevant: 2701/2703 describe the two-commit shape that
  produced **all seven** historical `authorized` verdicts, so the cited rule
  mandates the hazardous shape rather than blocking it. The hazard is inert for a
  different reason — no classifier exists yet.
- Mine: §15's second caveat was illustrated with a hand-picked hypothetical while
  `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256` sat in the same document, real and
  stronger, already labelled a dead end.

**The tell is checkable and cheap.** Ask whether the citation would still be true
if the conclusion were false. For all three it would. A supporting citation should
*fail* when the claim fails; one that cannot is decoration, and it costs the
implementer who reads "the anchor already blocks this" and skips writing the
guard. The remedy is not a better search — the search worked — it is stating the
conclusion and the evidence in that order, and checking the arrow between them
points the way it is drawn.

**And a limit on this section, established by the section itself.** The
`CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256` error above was committed *after* the
"sample read as a census" sub-shape was written down, in the same document, about
the same file, two entries later. So the register does not work by being read —
having named a failure mode confers no protection against it, because the failure
occurs at the moment of *reading source*, not at the moment of recalling a rule.
That matches what WS-6 said about F-3: what changed its behaviour was a claim
cheap to check and expensive to have wrong, not a rule stated as a rule. **The
operative form of every entry here is therefore an action, not a caution** — read
to the closing brace; name what the instrument enumerates; check where the cost
of being wrong lands. A reader who takes this section as a list of things to be
careful about has taken the inert half.

**A second limit, and it is about filing rather than finding.** WS-2 had held the
evidence for the CRLF hazard in its own session notes for the whole exchange:
"SQL-migration `sha256` pin tests fail locally, green on Linux CI", recorded under
environment quirks beside PowerShell syntax problems. The observation was correct
and correctly written down. It was the *same failure mode* as item 5, already
firing, on files one directory away — and it stayed invisible while both of us
reasoned about the hazard in the abstract, because it had been filed as an
artefact of one machine rather than as a fact about how a digest is computed under
authority. Nothing about the note was wrong; retrieving it required already
knowing it mattered. **This is the sharper form of the limit above:** the first
says a named rule does not fire at the moment of reading source, and this says a
recorded fact does not fire either, because the misfiling happens at the moment of
observation, when the thing genuinely does look like housekeeping. The only
mechanism that surfaced it was an unrelated question — "is `sha256sum` the right
function?" — reaching the same evidence from the decision side. Which argues for
indexing this register by *decision affected* rather than by symptom, and against
any confidence that a fact once written down is a fact available.

**One aggravating factor, from the §12a instance.** Correcting WS-6's census from
two to seven made WS-6's own argument *stronger* — a well-attested pre-outage
`authorized` path makes the post-repair silence more striking, not less. So this
was an error its author had no incentive to find. That is worth stating as its
own caution, because the intuitive guard against motivated error is to check
hardest where a finding flatters you: **an error whose repair helps the arguer
will not be caught by that guard.** Self-interest is not an error detector in
either direction, and undercounts in your own favour are as invisible as
overcounts are tempting.

**And a second, from WS-6's own diagnosis of its tree-delta claim.** It observed
that of the four claims in that note it verified three by reading source and
pulling file lists, and asserted only the one about *feasibility* — "the verifier
can already see the difference". Its reading of why is worth keeping verbatim in
substance: **feasibility is the kind of claim whose cost lands on the implementer
rather than on the claimant.** Being wrong about a fact is discovered by the next
person to read the file; being wrong about whether something can be built is
discovered by whoever tries, after committing to the design. So the check should
be weighted the other way from instinct — the claims to verify hardest are not
the ones you would be embarrassed to get wrong, they are the ones whose cost you
will not personally pay. Note that this and the paragraph above are the same rule
seen twice: **look hardest where the cost of being wrong lands somewhere other
than on you** — either because the error flatters you, or because someone else
does the work of discovering it.

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

### The encoding is a security design, not a labelling exercise

WS-6's closing handover is the part most easily lost between a ruling and whoever
implements it, so it is recorded in full here.

**Bootstrap must not be a sub-code under `unauthorized`.** The obvious encoding,
`unauthorized.approval.bootstrap_self_change`, is wrong for the same reason the
original defect was wrong: `unauthorized` is an accusation and this is not one.
Three cases demand three different human responses — which is the test that has
held all day:

| Class | What it means | What a human does |
| --- | --- | --- |
| `unauthorized` | someone attempted something they should not | refuse |
| `undetermined` | the check could not decide | retry or repair infrastructure |
| bootstrap | the check is **not competent** to decide, because the change alters the machinery that would judge it | escalate |

It should fail closed with its own exit code. The slot is natural: the verifier
already carries `UNAUTHORIZED_EXIT_CODE` and `UNDETERMINED_EXIT_CODE` as named
constants, so a third is an extension rather than a new concept.

**The hazard, which is the reason this note exists.** If the bootstrap class is
auto-detected from the file list *and* treated as mergeable-with-reasons, it
becomes **self-selecting**: anyone wanting the softer class adds a trivial edit to
`verify_rollout_trust_anchor.py` and inherits it. That converts the repair into a
bypass, and it would look entirely reasonable in review. WS-6's own words are
worth keeping: under a naive encoding, **#122 would have been the template for
the exploit** — a real verifier fix, honestly argued, that also happens to be the
shape an attacker wants. That is not an argument against encoding; it is an
argument that whoever builds it needs this paragraph.

**The defence: strictest-applicable, not most-lenient.** A pull request touching
the verifier *and* another protected path is ordinary, not bootstrap. Bootstrap
requires that the protected paths touched are a subset of the anchor's own
machinery and nothing else.

**Checked against both live cases, and it discriminates correctly.** #122 touched
`scripts/validation/verify_rollout_trust_anchor.py`, `tests/test_rollout_trust_anchor.py`
and `docs/runbooks/authorize-rollout-policy-change.md` — all three in
`PROTECTED_EXACT_PATHS` (lines 99, 114, 73) and all three anchor machinery →
bootstrap. #121 touched `scripts/operations/authorize_approved_assets_phase.sh`
and `docs/runbooks/migrate-approved-assets.md` (lines 84, 74) — protected,
neither anchor machinery → ordinary. So the rule does not make its own repair
unsatisfiable, which was the failure WS-6 warned about in another context: *a
rule that makes its own repair unsatisfiable is not strict, it is inert.*

Two things the implementer needs that the proposal does not yet state.

**"The anchor's own machinery" does not exist as a set.** Verified against the
verifier at `824b4238`: `PROTECTED_EXACT_PATHS` is one flat frozenset of forty-odd
paths with no sub-classification, and `PROTECTED_PREFIXES` covers `.github/workflows/`
wholesale — every workflow in the repository. So the encoding requires **defining
a new set that does not exist**, and that set is itself security-critical: whoever
widens it widens the bootstrap class. It must therefore live inside the verifier,
which makes it protected, which makes editing it itself a bootstrap change. That
is the same self-reference as the digest pin, and the third instance of this shape
today. An implementer who puts the set in a separate config file for tidiness has
created an unprotected lever on a security boundary.

**The defence constrains only protected paths, and the payload need not be in
one.** "Touched protected paths ⊆ anchor machinery" says nothing about the
unprotected files in the same pull request. A change weakening the verifier and
shipping arbitrary unprotected code alongside it still classifies as bootstrap.
This only bites under exactly the condition WS-6 named — bootstrap being lighter
in *permission* rather than only in *label* — but the cheap guard is to say it
now: the escalation reviews the whole diff, not the protected subset, or
bootstrap requires the pull request to touch nothing but anchor machinery at all.

**Resolved in favour of the strict form, on evidence.** Of those two options, the
strict one — *the pull request touches nothing but anchor machinery* — costs
nothing against either real case: #116 changed four files and #122 three, and
**neither touched anything outside anchor machinery**. So the strict form is free
on all observed evidence, it is checkable inside the verifier, and it closes this
gap as a side effect rather than delegating it to a human process. Whole-diff
review is prose, and a distinction living in prose drifts from the thing it
describes — which is F-7, and the argument the encoding rests on in the first
place.

### Four things the implementer must not get wrong

**The obvious implementation fails on the outage repair itself.** #116 — the
commit that ended the four-day outage — changed four files:
`.github/workflows/rollout-trust-anchor.yml`,
`docs/runbooks/authorize-rollout-policy-change.md`,
`scripts/validation/verify_rollout_trust_anchor.py` and
`tests/test_rollout_trust_anchor.py`. All four are anchor machinery, but the first
is protected by **prefix**, not exact path: `PROTECTED_PREFIXES` takes
`.github/workflows/` wholesale at line 65, and `PROTECTED_EXACT_PATHS` contains no
`.github/workflows` entry at all — verified by reading the whole set. So the
implementation anyone will reach for, "anchor machinery ⊆ `PROTECTED_EXACT_PATHS`"
— reached for because that is the set both worked examples cite — classifies
**#116 as ordinary**, demands a receipt for it, and makes the outage permanent
under the rule written to permit its repair. It fails closed, which is the right
direction and the wrong outcome. The anchor-machinery set must span every
protection mechanism, and that is exactly the sort of detail that dies between a
ruling and an implementation.

**Correction to the sentence above, made the same day it was written.** It first
said "**both** protection mechanisms", which is an undercount reached by looking
at the two sets the worked examples cite instead of at the predicate. Reading
`is_protected_path` at line 1107, a path is protected by **three** mechanisms,
and there is also an exemption that fires first:

1. `PROTECTED_EXACT_PATHS`, case-folded (1111–1112);
2. `PROTECTED_PREFIXES`, case-folded prefix match (1113–1114);
3. `_is_protected_python_shadow_path` (1115) — the anti-shadowing mechanism,
   which protects `.py`, `.pyc`, `.pyd` and `.so` forms that would shadow a
   protected module, against `PROTECTED_PYTHON_NAMESPACE_PATHS` (`scripts`,
   `scripts.migrations`, `scripts.validation`, `tests`) and
   `PROTECTED_PYTHON_EXACT_MODULE_PATHS`;
4. and `APPROVAL_PATHS` returns **False** first (1108–1109) — the receipt and
   signature are deliberately *not* protected, because otherwise no receipt could
   ever be added.

The third does not add anchor files, because it is **derived from the first**:
`_protected_exact_python_module_paths` walks `PROTECTED_EXACT_PATHS` at line 134
and projects it into module space, adding every ancestor. It changes the shape of
the question rather than the file list — an implementer who tests "which of the
two sets protects this path" gets a wrong answer for shadow forms. That direction
is safe here (an unlisted shadow form classifies ordinary, which is stricter), so
this is a correctness point about the encoding, not a live hole.

A second-order consequence: because the prefix protects every workflow, the
anchor-machinery set has to name `rollout-trust-anchor.yml` **explicitly** even
though it is already protected. That path will therefore appear in two mechanisms
for two different reasons, and the two can drift apart — an F-7 shape built in at
construction time, worth a comment at the definition rather than a discovery
later. The general form is stronger than the instance: **anchor machinery cannot
be derived from any protection set, because none of the three is about this
question.** It has to be its own enumeration, cross-checked against the union.

**The set that defines bootstrap is itself anchor machinery, so widening it is
classified bootstrap — the lighter class.** An attacker's optimal move is a
one-line addition to that set, arriving under the class whose label says
"escalate, this cannot self-authorize". WS-6's fix is the right shape and matches
the asymmetry already accepted elsewhere: **adding to the anchor-machinery set is
not itself a bootstrap change; removing from it can be.**

*The capability that fix assumes is not the one that exists.* WS-6 justified it
with "the verifier already computes tree deltas, so it can see the difference".
Tree deltas answer *which files changed*, which cannot distinguish an addition
from a removal **inside** a frozenset. What the verifier actually has is a closer
precedent and still not the thing: at 1290–1310 it AST-parses a named constant's
literal keys out of source, for `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256`. That
operates on **one** revision. A membership delta needs the base revision's copy
of the verifier fetched and parsed as well — a new fetch and a second parse, not
an existing capability. And it works only for constants written as literals:
`PROTECTED_PYTHON_EXACT_MODULE_PATHS` is a **function call** at line 154, so its
membership is not readable this way at all.

Two corrections that fix are not safe without.

*Polarity is not uniform across the sets in that file.* For the anchor-machinery
set the dangerous direction is **addition**, because adding widens the lighter
class. For `PROTECTED_EXACT_PATHS` and `PROTECTED_PREFIXES` the dangerous
direction is **removal**, because removing strips protection outright. A blanket
"additions are ordinary, removals may be bootstrap" would therefore let a
protection-stripping deletion arrive under the lighter class — the same hole in
the opposite direction. State it polarity-free: **a delta that weakens any
boundary is never bootstrap, whichever set it edits.**

*Modification is an addition and a removal at once.* A rename, or an edit that
replaces one entry with another, satisfies both halves, and a rule phrased over
the diff's shape can be argued into the wrong branch. Phrase it over the
**effect on membership**, not over the operation.

**One dead end, and the correction to it is more useful than the dead end was.**
`CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256` looks like an existing grouping that could
serve as the anchor-machinery set. It cannot — but the reason recorded here
earlier was wrong, and wrong in the direction that made it sound safe. That text
said the constant "maps `scripts/migrations/_fixed_migration.py` and the three
approved-assets collectors to source digests, and contains **none** of the
anchor's own files". Read in full at 392–426 it has **eleven** entries, and two of
them are the anchor's own files: `verify_rollout_trust_anchor.py` at 408 and
`tests/test_rollout_trust_anchor.py` at 423.

*How that happened, recorded because it is the shape named two entries earlier in
§13.* The dict was read from its head — the first four entries are
`_fixed_migration.py` and the collectors — and generalised to the whole. That is
"too narrow, non-empty: a sample read as a census", committed against the same
file, in the same document, immediately after adding the sub-shape on WS-6's
behalf. Naming a failure mode does not confer immunity to it; the only thing that
does is reading to the closing brace.

*The conclusion survives and gets sharper.* A **disjoint** set would be obviously
unsuitable and therefore harmless — nobody adopts it. This set **overlaps**, which
is the trap: it contains the verifier and the verifier's test, so a spot-check of
"is the anchor in here?" says yes twice; it **omits the workflow and the runbook**,
which are exactly two of the four files #116 needed; and it carries **nine** paths
that are not anchor machinery at all, including a service module and three
unrelated test files.

*And it makes the second caveat above a demonstrated failure rather than a
hypothetical one.* All eleven members are in `PROTECTED_EXACT_PATHS` — lines 81,
85, 87, 88, 92, 99, 102, 111, 112, 113 and 114, checked individually. So
`all(is_protected_path(p) for p in CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256)`
**passes**. Adopt this constant as `ANCHOR_MACHINERY` and the proposed invariant
goes green while the bootstrap class silently widens to include, among others, a
pull request touching only `services/adapteng-drive-adapter/app/approved_source.py`.
The worked example offered above for that caveat was hand-picked; this one was
already sitting in this document, labelled a dead end, which is a better argument
than the invented example and was available the whole time.

**Fourth: the receipt is invisible to `is_protected_path`, deliberately.** This is
the strongest of the three arguments for the strict form, because of what it
admits. `APPROVAL_PATHS` — `.github/trust/rollout-policy/approval.json` and
`.sig`, defined at 53–60 — sit *underneath* the protected prefix
`.github/trust/rollout-policy/` at line 64, and the exemption at 1108 returns
`False` for exactly those two before any mechanism is consulted. So any
classifier that consumes `is_protected_path` is structurally unable to see a
receipt.

Under the naive rule — *touched protected paths ⊆ anchor machinery* — a pull
request that edits the verifier **and adds approval material** has a
touched-protected set of `{verifier}`. The subset test passes and the change is
classified bootstrap, the class whose label reads "cannot self-authorize,
escalate". The payload riding along invisibly is not arbitrary: it is the receipt,
the one artefact the entire gate exists to govern. The strict form —
*the pull request touches nothing but anchor machinery* — is immune, precisely
because it reads the raw changed-file list instead of filtering it through
`is_protected_path` first.

*Correction to the scoping, which matters more than it looks.* WS-6 called this
inert today because "`commit_delta_invalid` requires a receipt commit whose delta
from its parent is only `APPROVAL_PATHS`, so a combined verifier+receipt commit
fails now". The first half is true — line 2703 raises
`approval.commit_delta_invalid` unless `_changed_leaf_paths(subject_tree,
head_tree)` equals `APPROVAL_PATHS` exactly. But the conclusion does not follow,
because **the anchor does not block the hazardous shape; it mandates it.** Read
2701 and 2703 together: the subject tree must introduce no approval material, and
the head commit's delta from that subject must be exactly the two approval paths.
That *is* the canonical authorized pull request — one commit carrying the change,
one receipt commit on top — and it passes today by design. A single combined
commit does fail, but nobody builds that shape. What actually makes the hazard
inert is only that the classifier does not exist yet. The difference has a future:
"the anchor already blocks this" is false comfort that would survive into the
implementation and stop someone writing the guard.

*The proposed invariant is worth taking, and its scope is narrower than claimed.*
WS-6 offers `all(is_protected_path(p) for p in ANCHOR_MACHINERY)` — cheap,
in-repository, no cross-repository access, runs in the existing
`root-rollout-tests`. Verified that it holds on the real set today: the verifier
(99), the test file (114) and the runbook (73) are exact entries, and the workflow
is covered by the prefix at 65. Two caveats it needs to carry.

1. **The English and the code disagree on exactly the pair that motivated the
   finding.** "Protected by at least one of the three mechanisms" is not what
   `is_protected_path` computes; that function returns `False` for `APPROVAL_PATHS`
   *before* reaching any mechanism. The two readings diverge only on the approval
   paths — which is where this whole item started. Write the assertion in terms of
   the predicate and say why, or the first confusing failure gets "fixed" by
   someone removing the exemption.
2. **It is a necessary condition, and it is blind to the more dangerous
   widening.** Adding an *unprotected* file to the set fails the assertion and is
   caught. Adding a *protected but non-machinery* file passes it — and that is the
   direction that hurts, because a pull request touching only that file then
   classifies as bootstrap and merges with no receipt. WS-6 splits the cases as
   deliberate versus accidental and claims the test "stops the accidental kind".
   The operative split is which direction, not which intent, and the most likely
   *accidental* widening lands in the uncaught one, because the confusable names
   live there. Concretely: `scripts/operations/authorize_approved_assets_phase.sh`
   is protected (line 84), is **not** anchor machinery, has "authorize" in its
   name, and is the file in #121. Anyone writing the set from memory could sweep
   it in, and this test would pass. What stops that is the rule already recorded
   above — adding to the set is never bootstrap — so the invariant is a supplement
   to it, never a substitute.

*A negative half is available for the two cases where the confusion is worst, and
those two are mechanical.* WS-6's form, taken:

```
ANCHOR_MACHINERY ∩ (APPROVAL_PATHS ∪ {allowed_signers}) == ∅
```

The positive assertion catches unprotected additions; this catches the receipt and
the trust root; the semantic middle stays with the add-is-never-bootstrap rule.
**The cost is more specific than "not free."** `allowed_signers` has no path
constant, so the invariant must name it, and the constant has to live in a `.py`
file — of which both plausible hosts, the verifier (digest 408) and its test
(digest 423), are members of `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256`. So writing
this invariant requires a digest re-pin wherever it is put. **It therefore shares
its scarce resource with owner-decision item 5 and should be taken in the same
sitting**, not costed separately.

**Why the trust root is the severe case, verified.** Someone asked "what is the
anchor's machinery?" will read the workflow's sparse-checkout at lines 32–37,
because it is literally the list of files the anchor reads. Scored:

| Sparse-checkout entry | Protected | Machinery | Invariant |
| --- | --- | --- | --- |
| `.gitignore` | no | no | **catches it** |
| `scripts/validation/verify_rollout_trust_anchor.py` | 99 | yes | correct |
| `.gitattributes` | 69 | arguable | passes |
| `.github/trust/rollout-policy/allowed_signers` | prefix 64 | **no** | **passes** |

It also omits the workflow, the test and the runbook. `allowed_signers` is not
ambiguous: the anchor *reads* it, but it is what the procedure trusts, not part of
the procedure. Classified as machinery, a **key swap** becomes a change that
"cannot self-authorize, escalate" instead of one requiring a receipt.

WS-6 noted there is no constant for it. Checked, and it is stronger than that:
**the path literal `.github/trust/rollout-policy/allowed_signers` does not appear
in the verifier at all.** Every occurrence of the name is
`allowed_signers_path: Path` (2581, 2647, 3003), `args.allowed_signers` (3151,
3188, 3208) or an error-code string. The literal exists in exactly one place in
the repository — the sparse-checkout list an implementer would copy from. There is
no second copy for a wrong one to disagree with.

`.gitattributes` is genuinely undecided rather than wrong. It governs LF
normalization and therefore the digests, so it has a real claim; it is also
repo-wide, and classifying it bootstrap would let a normalization change merge
receiptless. That one wants the owner.

**The general pattern, now with three data points: a wrong candidate set is
dangerous exactly when it contains the verifier.** Every reader's spot-check is
"is the anchor in it?", so that is the only property a wrong set needs in order to
be adopted.

| Candidate set | Contains verifier | Wrong how | Danger |
| --- | --- | --- | --- |
| workflow sparse-checkout, 32–37 | yes | 2 through, 3 missing | **high** |
| `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256`, 392–426 | yes | 9 extra, 2 missing | **high** |
| `CLOSURE_LOADER_ALLOWED_MODULES`, 378–391 | **no** | disjoint from the anchor | none |

The third is wrong in the largest possible way and is harmless for exactly that
reason — nobody adopts a set that fails the first check. Both sets that pass the
first check are wrong in *both* directions. That is three independent
confirmations that the enumeration has to be written fresh, and it is a stronger
argument than any of them alone.

Recorded, not built. It is a protected-path change encoding an undecided policy,
and it waits for both the ruling and the authority to ask for it.


