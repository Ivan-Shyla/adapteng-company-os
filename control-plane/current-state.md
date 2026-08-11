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

**Update — the race has four production occurrences, not two, and the two extra
ones were found by paying for a surface nobody had queried.** WS-6's full-population
census (above, §12a) surfaced two *pre*-#116 runs carrying the bare code
`pull_request.state_invalid`, and flagged them as unresolved rather than folding
them into the count — correctly, because before #122 that code covered three
distinct causes and the code alone cannot discriminate. WS-6 named the exact
evidence that would settle it: the two pull requests' `merged_at` against the run
times. Run here:

| Run | Head | Run created | PR | Merged at | Gap |
| --- | --- | --- | --- | --- | --- |
| `31014078147` | `a6b32681` | 14:13:18Z | #101 | 14:13:19Z | **+1s** |
| `31017261728` | `3f27e140` | 14:50:42Z | #102 | 14:50:42Z | **same second** |

Both on 2026-08-05. **The workflow's trigger types are
`[opened, edited, reopened, synchronize, ready_for_review]` — `closed` is not among
them** (verified at `824b4238`), so the merge did not cause these runs; it overtook
them, exactly as on #119. Cause established to the same standard as the #119 case,
which is timing plus code rather than a distinguishing code, since the
distinguishing code is what #122 created. So the defect #122 repaired was firing
five days before the run that exposed it, and this is now a recurring pattern
spanning both sides of the category split rather than a two-instance oddity.

**Why the earlier sweep could not have found them, which is the transferable part.**
The signature used was *the same head SHA carrying opposite verdicts* — a success
beside a failure. Both pre-#116 heads do have a sibling run, and both siblings are
**failures**:

| Head | First run | Second run |
| --- | --- | --- |
| `a6b32681` | 14:02:21Z `json.too_many_nodes` | 14:13:18Z `pull_request.state_invalid` |
| `3f27e140` | 14:48:49Z `approval.commit_delta_invalid` | 14:50:42Z `pull_request.state_invalid` |
| `36902fc7` | 18:03:19Z **success** | 18:08:29Z `unauthorized.pull_request.state_invalid` |
| `4d6e70ba` | 18:23:46Z **success** | 18:26:57Z `unauthorized.pull_request.state_invalid` |

A success/failure comparison is blind to a race that converts one failure into a
*different* failure, and blind again to a race that fires on a run with no sibling
at all. It can only find occurrences where an earlier attempt happened to go green.
That is not a lapse in how the sweep was run; it is what the signature selects, and
no amount of re-running it reaches the other two. The instrument that does reach
them is the emitted code plus the merge timestamp. Recorded in §13 as the third
case of a population excluded by an instrument's design.

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
   `d799b5bb`, the run the count rested on when this was written, whose verdict is
   `{"outcome":"not_applicable"}` (verified in the job log). It has since been
   joined by a second — see the 2026-08-11 update below.
2. **`undetermined.pull_request.no_longer_open` is not a fault.** WS-6's message
   closes by calling any `undetermined.*` code a real defect — which restates the
   #117 claim that its own #123 retired. #122 *created* that code for the benign
   merge-underneath-the-job race documented above. It is neither evidence for the
   mechanism nor against it: the job legitimately could not reach a decision.
   Treat it as **neutral** — it does not advance the count and does not break it.
   Any other `undetermined.*` is an infrastructure fault and breaks it.
3. **The count needs a version anchor, and that is why it stood at one.** All
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
and a run's "version" is not a single thing that a timestamp can name. **WS-6's
widening is right and this document had it too narrow:** re-runs are the vivid
case, not the mechanism. Because line 31 resolves `refs/heads/main` at checkout
time, *any* run that starts before a verifier merge and reaches checkout after it
straddles the same way. That is the error window recorded earlier in this section,
now with a named consequence rather than a caveat, and it makes the straddle a
property of ordinary scheduling rather than of an unusual operator action.
**Inert today, and verified inert rather than assumed:** #122 changed three files
— `docs/runbooks/authorize-rollout-policy-change.md`,
`scripts/validation/verify_rollout_trust_anchor.py` and
`tests/test_rollout_trust_anchor.py` — and **not** the workflow, so every
revision of the verifier since the reset has run against the same workflow. The
hazard becomes live the first time the workflow itself changes, at which point
runs silently mix revisions and no run field records it. This is the same
root as the `created_at` correction above: re-run identity is absent from the
fields anyone reaches for. **And the negative is unobservable in the same way the
version is** — nothing on a run records whether its two halves came from one
revision, so a straddle cannot be detected after the fact, only excluded by
knowing the workflow did not change. The criterion is unaffected, since a straddle
is neutral either way; what changes is that this section verified the pair and not
the pair's coherence.

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

**Half of that gap closed on its own, one day later, and the closure was not
deliberate — measured 2026-08-11.** The blockquote above is now wrong on its
middle term. WS-6 opened platform **#124** (`fix(ci): report a broken trust root
as undetermined, not unauthorized`, head `36119231`), which fired the anchor on
`pull_request_target` three seconds later. Run `31466827525` emitted
**`rollout_trust_anchor.unauthorized.approval.commit_delta_invalid`** at
06:54:39Z, exit 1 — taken from the job log, not inferred from the exit code.
Post-reset runs enumerated rather than assumed: exactly **two** postdate
20:44:08Z, the `not_applicable` at 20:45:58Z and this one. So the census under
the verifier now on `main` reads:

> **zero `authorized`, one `unauthorized`, one `not_applicable`.**

**And the mechanism corrects a stronger claim two paragraphs below.** The
statement that the gap "cannot close by waiting" because ordinary traffic yields
only `not_applicable`, so "any N will be reached with the let-work-through path
still unexercised unless someone deliberately produces the input", is true of the
`authorized` half and false of the `unauthorized` half. #124 touches
`scripts/validation/verify_rollout_trust_anchor.py` and
`tests/test_rollout_trust_anchor.py` — both protected rollout paths — and carries
no receipt, which is exactly the input the passage says must be manufactured. It
was not manufactured; it is what a workstream fixing the verifier necessarily
produces. The asymmetry is the point: an `unauthorized` observation arrives from
any pull request that edits the machinery, and the workstreams edit the machinery
routinely, whereas an `authorized` one still requires a signature and remains
unobtainable by waiting.

**What this does to the promotion argument, in both directions.** It removes the
need for any deliberate action to obtain the `unauthorized` half, so the
`edited`-trigger manoeuvre recorded in `execution-program.md` item 6 should not
be performed for that purpose — see the correction there. **And it advances the
promotion count, which I first wrote up backwards.** My draft of this paragraph
said a failing run "does not accumulate toward N". That is exactly the error the
adopted criterion at line 553 was written to prevent: the criterion is *N
consecutive runs that each reach a terminal verdict, of any class, with no
infrastructure fault*, and line 551 says in terms that counting only greens would
rebuild the thing that broke. `unauthorized` is a terminal verdict of a counting
class. Both post-reset runs qualify and they are consecutive, so **the count is
two, not one** — and the second is a real refusal rather than a `not_applicable`,
which is the stronger of the two observations. I caught this by re-reading the
criterion instead of recalling it, one paragraph after recording that the
register's own remedy is to re-derive rather than remember.

What it does **not** do is close the validation gap. The four-day outage was
`approval.circular_or_stale`, so `_approval_material_introduced` on its
authorizing branch is still validated only by unit tests. An observed refusal
does not exercise the let-work-through path, and that is the path that was
broken. `authorized` remains unobtainable by waiting.

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
that was never reached. WS-6's distinction is the operative one: **a wrong label
is fixed by a rename, a false assertion by a different sentence**, so this is not
a naming quibble deferred to the same edit.

**The second site, and a correction to the reason this document first gave for
it.** `operator.live_ref_changed` at 3087–3088 also compares live state, and this
document said it "is not the same fix, because it compares the whole
`LivePullRequest` object rather than two SHAs, so it cannot be split". True, and
the wrong reason — it makes the obstacle structural, which invites exactly the
harmful repair: make it splittable, then split it. WS-6 supplied the real reason
and it is functional. The check sits between `verify_signature` (3079) and
`staging.replace(output)` (3089): it is a **signing-time freshness guard**, asking
whether anything moved while a signature was being minted. The receipt binds
`base_sha` at 2966, so a base that moves genuinely invalidates the receipt about
to be published. Any movement must fail it, base included.

**A second and independent reason, from the audiences.** These two sites do not
have the same reader. 2653–2659 publishes a check-run verdict on somebody else's
pull request, which is why a wrong class there is an accusation. 3087–3088 exits a
command the operator is running themselves, and the `create` handler at 3192–3201
prints `rollout_trust_anchor.unauthorized.operator.live_ref_changed` to stderr —
the exact code, to the person who just ran it, who then retries. **The cost that
makes base-moved worth reclassifying at the first site does not exist at the
second**, and this holds whatever the guard protects, so it survives even if the
freshness argument were disputed. Any ruling on 2653–2659 must say the second site
is deliberately excluded, or the next reader harmonises them and puts a hole in
the signing path.

Strength, stated: **zero occurrences across the whole population, checked.** This
previously read "no production occurrence observed", resting on a reason that was
wrong twice over. The old sentence said the check-run rendering collapses while
"the `create` path names its code exactly", concluding that
`pull_request.live_ref_changed` is unobservable by check-run inspection and
`operator.live_ref_changed` is not. Both halves fail, and in opposite directions.

WS-6 found the first: `_report_failure` line **3132** is
`print(f"rollout_trust_anchor.{category}.{code}", file=sys.stderr)` and the create
path line **3196** is `print(f"rollout_trust_anchor.{category}.{exc.code}", file=sys.stderr)`
— same shape, same stream, same prefix. Verified at `824b4238` (blob `ec4c82d6`,
sha256 `07f1dafb`). **Neither path's code is hidden.** The second half is this
document's alone and is an inversion: `complete_check_run` is called at exactly two
sites, 3120 and 3156, both inside the verify path, and the create path
(`_construct_command`, 3181) publishes **no check run at all**. So the sentence
awarded check-run observability to the one path that has none.

WS-6's replacement structure is right and the correction is that these are
**surfaces, not sites**. WS-6 then corrected its own table upward, from two paths
to four (adding `validate-trust-root` at 3221 and `main`'s catch-all at 3266).
Both additions verified exact. **Enumerating every emitting site in the verifier
rather than extending the table again gives six, not four** — blob `07f1dafb`,
3274 lines, byte-identical to the cached copy. The qualifier "in the verifier" was
absent when this table was first written, and its absence was the next error in the
sequence; see *A seventh emitter, outside the file the census was bounded by* below.

| Site | Command / condition | Token | Stream | Segments |
| --- | --- | --- | --- | --- |
| 3129 | verify — check-run publication failed | `…undetermined.check.failure_update_failed` (fixed) | stderr | **4** |
| 3132 | verify — the verdict | `…{category}.{code}` | stderr | 3 |
| 3196 | construct — failure | `…{category}.{exc.code}` | stderr | 3 |
| 3202 | construct — **success** | `…receipt_created` (fixed) | **stdout** | **2** |
| 3221 | `validate-trust-root` — failure | `…{category}.{exc.code}` | stderr | 3 |
| 3266 | any command — uncaught `OSError`/`UnicodeError`/`ValueError` | `…undetermined.internal_failure` (fixed) | stderr | 3 |

`validate-trust-root` additionally prints `{"trust_root":"unconfigured"}` (3211–3216)
or `{"trust_root":"configured"}` (3227) to stdout, carrying no dotted token at all.

**The check run is a separate surface and is the lossy one.** It exists on the
verify command alone (`ensure_check_run` at 3141, `complete_check_run` at 3120 and
3156 — no other call sites in the file), and it carries the outcome only: five
fixed title/summary pairs at 2771–2787, with the payload at 2788–2800 having
`summary` and `title` but **no `text` field**. So no dotted code reaches it.

**3129 is inside `_report_failure` itself** — the function begins at 3102 and
contains both 3129 and 3132 — so that function has two emitting sites, not one,
and WS-6's "not `_report_failure`, not `_construct_command`, a third emitter"
frame is right about 3266 and wrong about this one. The consequence is the part
worth carrying: when check-run publication fails, the run prints **two dotted
codes**, and their categories can disagree — 3129 is hard-coded `undetermined`
while the verdict at 3132 may be `unauthorized`.

**Which turns an unstated assumption in the census below into a checked fact — but
only jointly, and this document originally claimed it singly.** That census deduped
per run and reported eight codes summing to 64 across 64 failure runs. The sentence
that stood here said `check.failure_update_failed` is absent from the census, so no
run took the 3129 branch, "and the one-to-one holds on evidence rather than by
assumption." **That is half an argument.** Absence of the 3129 code establishes that
no run emits *twice* — an upper bound of one. It says nothing about a lower bound.
The census arithmetic, 64 codes over 64 runs, establishes that the *mean* is one,
which is equally satisfied by one run emitting twice and another not at all. WS-6
identified the gap precisely: neither half alone establishes the claim, and jointly
they are decisive — 64 values each at most one, summing to 64, are each exactly one.
The conclusion survives unchanged; the warrant for it was misattributed to the
mechanism when the mechanism supplies only one of its two bounds. **This is the
count-versus-population distinction landing on the very claim it was invoked for**,
which is the third time in this section that a tool has been applied everywhere
except to the sentence deploying it.

**The token namespace is not uniform, and nothing enforces that it should be.**
Three shapes exist — two segments at 3202, three at the interpolating sites, four
at 3129 — so any consumer parsing on "`rollout_trust_anchor` plus category plus
code" is wrong in both directions. The census regex used here,
`rollout_trust_anchor\.[A-Za-z0-9_.]+`, is shape-agnostic and so was unaffected;
that was luck rather than design, since it was chosen to tolerate the pre-#116
bare codes and not for this.

The collapse is real and is exactly where this document put it. What was wrong is
that it is **additive**: verify has two surfaces of which one is lossy, create has
one that is not. Verify is therefore *better* observed, not worse. **The accurate
generalisation is WS-6's:** check-run publication is a property of the *verify
command*, not of an exception class or of a site — which is what neither "both
sites name their code" nor "create has one surface" managed to say. The ruling on
2653–2659 does not depend on any of this — the reader and cost asymmetries in the
other two rows are untouched — but the observability row was doing rhetorical work
it could not support.

### A seventh emitter, outside the file the census was bounded by

**WS-6 applied the remedy this document had just prescribed and it returned a
seventh emitter.** The remedy above — re-derive the population from source rather
than editing the list — was run against the verifier, because the table was about
verifier call sites. The frame that survived the correction was not the row set but
the **file**. `.github/workflows/rollout-trust-anchor.yml` line **56** emits the
same token namespace and is not in the file: blob verified, 5028 bytes, 121 lines.

| | |
| --- | --- |
| Helper | `undetermined()`, lines **55–59** |
| stderr | **56** — `echo "rollout_trust_anchor.undetermined.$1" >&2` |
| annotation | **57** — `echo "::error title=Rollout trust anchor undetermined::$1"` |
| exit | **58** — `exit 75` |
| Call sites | **12** — 67, 68, 69, 70, 71, 88, 89, 91, 100, 102, 105, 106 |
| Distinct codes | **11** — `trust_head_malformed` occurs twice, at 89 and 91 |

The eleven: `anchor_verifier_absent`, `anchor_verifier_not_regular`,
`anchor_trust_root_absent`, `anchor_trust_root_not_regular`, `anchor_home_unusable`,
`trust_head_unreadable`, `trust_head_malformed`, `anchor_head_unreadable`,
`anchor_head_stale`, `anchor_worktree_unreadable`, `anchor_worktree_dirty`.

**A fourth surface, and it belongs to the emitter outside the file.** Line 57 is a
GitHub annotation — rendered in the pull request UI without opening a log. The six
verifier sites have stderr, stdout and the check run; none of them annotates. So the
surface inventory is stderr, stdout, check run and annotation, and the *best*
surface — the only one a reviewer sees without deliberate action — is the one that
was outside the population. (The verifier's `{"trust_root":…}` JSON, recorded above,
is a fifth thing on stdout carrying no dotted token at all; it is a format, not a
surface, but it is invisible to any token census by construction.)

**The two emitters cannot both fire in one run, and this is structural rather than
observed.** All twelve call sites precede **`exec`** at **109–120** —
`exec /usr/bin/env -i … /usr/bin/python3 -I "$verifier" verify` — inside a single
`run:` block (`shell: bash` 41, `run: |` 47). `exec` replaces the shell process, so
control never returns. A workflow emission and a verifier emission are mutually
exclusive by construction. The one-to-one argument above therefore no longer needs
the census to rule out cross-emitter double counting; it needs the census only for
the within-verifier case at 3129.

**Two disjoint vocabularies render as one token shape, and the names are adjacent.**
Nothing in `rollout_trust_anchor.undetermined.<code>` says which emitter produced it.
The collision is not hypothetical: the census below carries **11×
`trust_root_not_configured`** — verifier, raised inside `validate_allowed_signers`
(def **2451**) at **2475** for an empty file and **2493** for a file with no
non-comment principal lines — while the workflow's neighbouring code is
**`anchor_trust_root_absent`**. Same subject, opposite conditions: *present but
unconfigured* versus *not present at all*.

**And one guard shadows a verifier code outright, which WS-6's account did not
reach.** `validate_allowed_signers` raises a third trust-root code —
**`trust_root_file_missing`** at **2460**, its only raise site, on the `lstat`
failing. That is the same condition the workflow tests at line 69. Because line 69
runs before `exec` and exits 75, **`trust_root_file_missing` is unreachable on this
workflow's path**: the guard renames it to `anchor_trust_root_absent`. Line 70
(`anchor_trust_root_not_regular`) shadows the not-a-regular-file branch at 2464 the
same way. So of the five pre-flight guards, two do not merely duplicate verifier
checks — they systematically re-label a class of verifier errors before the verifier
can name them.

**That sentence is true, correctly qualified, and the qualification is where the
defect was.** "Unreachable on *this* workflow's path" scopes the claim exactly
right and never asks what the other paths do. They do the wrong thing: the same
`trust_root_file_missing` is live and reported as `unauthorized` on two required
checks that have no guard (§ the live misclassification, below, reproduced by
execution). The boundary was stated and then not crossed — which is this document's
own rule about search boundaries, met halfway. Stating a scope makes the claim
honest; it does not discharge the question the scope raises.

**A third meaning for the same token, inside the verifier.**
`_validate_trust_root_command` (**3206**) special-cases `trust_root_not_configured`
at **3210**: it prints `{"trust_root":"unconfigured"}` and **returns 0**. So the
identical code is a failure verdict on the verify path and a *successful* answer on
the `validate-trust-root` path. `trust_root_file_missing` gets no such treatment and
falls through to 3221. The token's category is a property of the command, not of the
code.

**The documented contract records the shape and is silent on the emitter — in both
places, and they do not carry the same columns.**
`docs/runbooks/authorize-rollout-policy-change.md` **147–148** (24489 bytes, 454
lines) gives shape, **exit code**, meaning and owner; `docs/github-governance-checklist.md`
**93–94** (9708 bytes, 212 lines) gives shape, meaning and owner and **omits the exit
code**. Both are accurate. Neither discriminates the emitter, and neither could: the
exit code does not discriminate it either, because `undetermined()` exits 75 and so
does `UNDETERMINED_EXIT_CODE` (**159**).

**Zero occurrences — established here through the annotation surface rather than
through logs, and the result is stronger than the claim it was checking.** WS-6
reported that none of the eleven workflow codes appears in the 81-run census, and
withdrew, correctly, its own earlier statement that line 69 is a load-bearing
detector. Re-derived independently: every run of the workflow was enumerated from the
API (81 runs — 64 failure, 12 success, 5 cancelled, reproducing the population figure
below exactly), each run's single job resolved to its check run, and every annotation
retrieved — **151 annotations across all 81 runs, zero fetch failures**. No
annotation carries the title `Rollout trust anchor undetermined` or a bare code as
its message. The messages are 78 Node-20 deprecation warnings, 64 × "Process
completed with exit code 1.", and 7 cancellations.

That last figure decides more than it was asked to. `UNAUTHORIZED_EXIT_CODE = 1` and
`UNDETERMINED_EXIT_CODE = 75` (**158–159**), and the runbook table at 147–148
documents exactly that mapping. **All 64 failures exited 1; none exited 75.**

**Correction, from WS-6, and it removes 58 of the 64 from the inference.** Exit 75
did not exist for most of this history. Measured at `1a5d84e^` = `8a4d87e7`, the
verifier is 3168 lines and contains **zero** occurrences of `UNDETERMINED_EXIT_CODE`,
`UndeterminedError` and `_report_failure` — and, extending WS-6's check, zero of
`UNAUTHORIZED_EXIT_CODE` as well. At `1a5d84e` all four appear. The same boundary
holds for the workflow: `rollout-trust-anchor.yml` at `8a4d87e7` has no
`undetermined()` helper, no `exit 75` and no `::error` annotation. So before #116
there was no category system at all — 1 was the only failing value the binary could
produce, and a run exiting 1 carried no information about a path that did not exist.

Therefore the four conclusions below hold, but on **6** runs rather than 64:

- the workflow emitter has never fired. True — but for the 58 it is true because
  the emitter was not in the file, not because its twelve `exit 75` sites went
  untaken. The conclusion is unchanged and the reason is different, which matters
  here for the reason this document keeps giving: reasons carry forward and
  conclusions do not;
- the verifier has never returned an `undetermined` verdict either;
- `internal_failure` (3266, returns 75) has never fired;
- every one of the 64 failures was `unauthorized`, and — since `exec` leaves only
  the verify path reachable — was emitted at site **3132** specifically. **This one
  is false for the 58**, and doubly so: 3132 did not exist when they were emitted,
  and neither did the word `unauthorized`. Calling them `unauthorized` projects a
  taxonomy backwards onto observations that predate it.

**The refutation was already on this page, four lines below the claim.** The census
table splits 58 bare / 6 prefixed — 21 + 12 + 11 + 7 + 5 + 2 = 58 against 4 + 2 = 6 —
and the sentence immediately after it says *bare codes predate #116, when the
category prefix did not exist*. Both halves were recorded correctly and neither was
carried up to the four bullets that needed them. WS-6 found it from outside by
checking the parent commit; the arithmetic needed to find it from inside was already
written down. The control is free and should have been standing: **filter to heads
after `1a5d84e` before drawing any conclusion about the undetermined path.**

So the eleven-code vocabulary, the annotation surface, and the runbook's entire
exit-75 row are latent: correct, unexercised, and owned by "An operator" who has
never been paged by them. This is the same epistemic status as `live_ref_changed` —
mechanism read at source, no production occurrence — and it is recorded here so the
promotion argument in §15 is quoted with the caveat attached rather than without it.

**And the conclusion is no longer resting on an instrument at all.** WS-6 paid the
cost it had previously declined and censused every run of
`rollout-trust-anchor.yml`. Re-run independently here rather than accepted:
81 runs total — 64 failure, 12 success, 5 cancelled — with the failed step
retrieved for all 64 and codes extracted by regex. The distribution reproduces
WS-6's exactly, and the eight counts sum to **64**, matching the failure count with
no run unaccounted for:

| n | code |
| --- | --- |
| 21 | `approval.circular_or_stale` |
| 12 | `approval.unexpected` |
| 11 | `trust_root_not_configured` |
| 7 | `approval.commit_delta_invalid` |
| 5 | `json.too_many_nodes` |
| 4 | `unauthorized.approval.commit_delta_invalid` |
| 2 | `unauthorized.pull_request.state_invalid` |
| 2 | `pull_request.state_invalid` |
| **0** | **any `live_ref_changed`** |

Bare codes predate #116, when the category prefix did not exist. All 64 failures
fail at the same step, `Verify signed protected-boundary receipt` — checked
independently through the jobs API — so no run failed before reaching the verifier.
`live_ref_changed` lies entirely inside the character class used, so its absence is
a result rather than an artefact. **`pull_request.live_ref_changed` has zero
production occurrences in the whole population.** Latent, not live, unchanged as a
conclusion and now resting on a query that returns the set.

**One note on the completeness check, which is weaker than it looks and happened
not to matter.** WS-6 argued the retrieval was complete because
`rollout_trust_anchor.py` appears in 64/64 failure logs. That token appears in the
step's command trace whether or not the verifier emits anything, so it evidences
that the step *ran*, not that a verdict was *printed* — the same defect WS-6 had
just corrected in itself when it found six rows of bare
`rollout_trust_anchor.undetermined.` coming from the workflow helper's own source
(`echo "rollout_trust_anchor.undetermined.$1" >&2`), which this run reproduces at
exactly six. The output-keyed check is the one that settles it: **0 runs with no
verdict, and per-code counts summing to the failure count.** Both were run here and
both hold, so the conclusion is unaffected; the check is worth replacing anyway,
because it would have passed just as confidently on an incomplete retrieval.

**WS-6 sharpened this against itself, and its version is stronger than what is
above.** The problem is not merely that the check is weak: **it cannot fail in the
direction it claims to test.** `--log-failed` on a run whose log was not retrieved
returns empty, empty contains no script name, and a genuine retrieval gap therefore
makes the *count* short rather than making the check fail — and short is what would
have been read as the answer rather than as an error. A control whose failure mode
is indistinguishable from its success mode is a tautology dressed as a control. The
output-keyed sum was the real evidence and was available the whole time.

**Note what defeated the original search:** the collapse documented in F-8 is one of
the two instruments WS-6 had to use to investigate a defect in the same file, so the
finding's own subject suppressed its evidence — but only on that surface. The
logs held the codes the whole time under a greppable prefix, which is what makes
WS-6's own re-diagnosis the right one and is recorded in §13.

**A live misclassification on two required checks, found by WS-6 and reproduced
here by execution.** §12's premise — that a broken trust root is announced as
`undetermined` — holds only on the anchor path. `validate_allowed_signers` is
invoked at four sites (2586, 2660, 3026, 3208; a grep that counts the `def` at 2451
returns five), and the `validate-trust-root` subcommand at 3208 is hosted by two
workflows: `validate.yml:88` and `secret-scan.yml:43`. Both are **required and
merge-blocking** — confirmed independently against the active `main-protected`
ruleset, whose five required contexts include *Validate repository structure and
content* and *Fail on unencrypted secret-like content*. Neither has a pre-flight
guard. Run against the real script at `824b4238`:

```
missing trust root  →  rollout_trust_anchor.unauthorized.trust_root_file_missing   exit 1
empty trust root    →  {"trust_root":"unconfigured"}                               exit 0
```

An infrastructure fault announced as an authorization refusal, on checks that block
merge — the exact conflation #116 was written to remove, surviving where #116 did
not reach. All **15** raises in `validate_allowed_signers` (2451–2523) and all **6**
in `_validate_executable` (2524–2541) are bare `TrustError`; neither function reads
the pull request, and on the anchor path the trust root is read from `$anchor_root`,
the base-trusted checkout of `main`, which a pull request cannot influence. So
`unauthorized` there is not merely the wrong word, it is a verdict about a subject
the code never consults.

**And the reason nobody saw it is the mitigation.** Line 69 of the anchor workflow
reports the identical condition as `anchor_trust_root_absent` — undetermined, and
correct. The workflow and the verifier disagreed about the same file, and the guard
renamed the condition before the verifier could misclassify it. The annotation sweep
recorded above returned zero trust-anchor codes; **that zero was produced by the
mask, not by absence of the defect** (§13, twentieth sub-shape).

**One qualification against WS-6's own framing, and it is the correction WS-6 had
just made to me.** WS-6 cites the census's 11 × `trust_root_not_configured` — third
most common failure in repository history — as the same function "misclassified the
whole time". Those 11 are **bare** codes, so they predate the category system
entirely; there was no classification for them to be wrong. What the 11 establish is
*frequency* — the condition recurs and is not hypothetical — while the
misclassification is a property of the **current** code, which would render the same
condition as `unauthorized`. Frequency and misclassification are both real and they
are established by different things. This is the measurement-window correction WS-6
opened the message with, applied one section later to the message itself.

**A third instance of the collapse, found while enumerating F-8's discard sites,
and it is the cleanest of the three because nothing else is wrong with it.**
`cleanup_resources` in `authorize_approved_assets_phase.sh` (43–84 at `824b4238`)
runs eight guarded operations, each followed by `[ "$?" -eq 0 ] || cleanup_failed=1`,
and returns that one boolean at line 83. The *fact* of a cleanup failure survives
and propagates correctly. What is destroyed is which of the eight failed — a
residual authorization secret (48, 59), a residual reviewed-evidence secret
(53, 64), a **still-registered self-hosted runner** (68, 72) and a leftover temp
directory (76, 78) all arrive as the same bit, though the first three are security
residue and the last is housekeeping. The three instances now span an exception
class, a set of lifecycle labels and a shell status variable, which is enough to
state the shape independently of any of them: **wherever N distinguishable causes
are reduced to one carrier, the reduction is invisible at the site that performs
it and only shows up at the site that reads it.** It is also worth separating from
the discard it was found beside: un-discarding the eight streams would not fix it,
and naming the resource would fix it without touching a redirect.

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

**The general rule, with twenty sub-shapes and fifty-one instances.** WS-1 proposed the
right corollary after its own second miss: state not only the boundary you
searched, but whether the search you chose *could have returned the answer*. That
generalises everything in this family, and the instances now sort cleanly by how
the instrument's range fails to match the question:

**On the arithmetic, since this list has twice been the subject of its own
"correct count beside a list that does not sum to it" entry.** Three sub-shapes
below are *cross-cutting*: they re-describe instances already counted
under earlier shapes rather than adding new ones. The phantom five-run gap is
counted once, under unchecked arithmetic, and reappears under "a plausible cause"
as a second reading of the same event; the race proxy is counted once and
reappears under "a detector defined by a coincidence". Only one genuinely new
instance was added there — WS-6's four-row table proving short — so that round moved
the count by one and not by six. Sub-shapes and instances are different populations, and
adding a shape is not evidence of a new failure.

**The current round adds one sub-shape and three instances, derived by
enumeration rather than by increment.** The bullets were counted directly and
stood at sixteen before this edit. New sub-shape: *a rule that reproduces the
defect it governs*. New instances: (i) my "two mechanisms exist" in §15 — the count
of mechanisms I had examined, asserted as the count that exists, filed under
*a sample read as a census*; (ii) the promotion rubric's two-bin sort, the new
shape's own instance; (iii) the lossy `path.name` projection at
`test_rollout_trust_anchor.py` 5259–5271, filed under *wrong axis*. Sixteen plus one
is seventeen; thirty-one plus three is thirty-four. **The first of the three is
worth naming as mine:** it is the identical error I had just corrected in WS-6's
emitter table, committed by me in the next document I edited, which is the argument
for the register being mechanical rather than remembered.

**The count above went stale one round later, in exactly the way this list
documents, for the third time and again by me.** The round after it added a second
instance to the thirteenth sub-shape — WS-2 reverting to a withdrawn reason — and
did **not** move the header, which continued to read thirty-four. Derived from that
round's own diff rather than from memory: it appended exactly one failure instance
to §13, plus one entry to the positive-results subsection, which is not a failure
and is not counted here. So the true figure entering the current round was
seventeen sub-shapes and **thirty-five** instances. The lesson is narrower than
"check the arithmetic": the round in question opened by declaring *no new
sub-shape*, and that true statement is what made the header look settled. **A
header carries two numbers, and confirming one of them is not evidence about the
other.**

**This round: nineteen and forty-four, and the increment is unusual in that every
new instance is mine.** Bullets re-counted directly in the edited file: **nineteen**.
One new sub-shape — *a generalisation that supplies its own corroborating
instances*. Three new instances, all committed by this document: (i) "the shell
script's version is the `EXIT` trap", and (ii) "`closure.dynamic_import` collapses
three", both under the new shape and both in the same paragraph; (iii) the
`from None` recoverability claim, under *wrong axis*. Eighteen plus one is
nineteen; forty-one plus three is forty-four. **No instance in this round was
found by me.** WS-2 brought (i) and (iii); (ii) surfaced only because verifying (i)
required re-reading the sentence that carried both. That is the load-bearing
observation about the register itself: three of my own errors sat unchallenged
through several rounds of my own re-reading, and the thing that moved them was
another reader with the source open. The list documents instrument-question
mismatches, and its own most reliable instrument turns out to be *someone else*.

**One instance later, forty-five, and the header moved with it this time.** WS-9
withdrew its own "the workflow that runs this script" — repo-wide there are six
references to `authorize_approved_assets_phase` and none is an invocation, which
independently confirms what this document had recorded — and in checking it found
a claim of mine resting on an antecedent neither of us had tested. Filed under
*unstated validity interval*, second instance. No new sub-shape: the population of
shapes is unchanged at nineteen, and saying so is now a required step rather than
an observation, because the last three rounds each moved one number and left the
other.

**WS-9's extension, which belongs to the unifying form rather than to any bullet.**
Every shape below describes a *claim* whose instrument answered a neighbouring
question. WS-9 found the same structure in an **artefact**: the shell script's
working-directory bound is real, load-bearing, and established by nothing — it
falls out of two clauses written to validate inputs. The cause is identical to the
prose cases and it is this document's own overdetermination rule: **nothing
downstream needed the property to be stated, so nothing tested whether it was.** A
property no one wrote down is not thereby absent; it is merely unowned, and it
breaks when someone improves the line that incidentally holds it.

**Twenty and forty-eight, and this round's arithmetic is worth showing because the
new shape is not mine.** Bullets re-counted in the edited file: **twenty**. New
sub-shape, WS-6's — *a mitigation that converts its own success into evidence that
nothing is wrong*. Three new instances: (i) my four exit-75 conclusions drawn from
64 runs when 58 predate the mechanism, under *unstated validity interval*; (ii)
WS-6's 11 pre-category codes cited as evidence of misclassification, the same shape
one section after correcting me on it; (iii) the line-69 guard masking a live
misclassification on two required checks, the new shape's own instance. Nineteen
plus one is twenty; forty-five plus three is forty-eight.

**Twenty and fifty-one — and the number that matters this round is the one that did
not move.** Three further instances, **no new sub-shape**: (i) the corrected exit-75
inference surviving in two further copies for a round, under *unstated validity
interval*, fourth instance; (ii) WS-6's "five required checks green on both" applied
to a pull request no ruleset governs, same bullet, fifth instance, where the domain
is a branch scope; (iii) WS-6's citation of the root `.gitattributes` reader as a
bound on the trust-root pins it does not assert, under *instrument answering a
neighbouring question*, seventh instance. Twenty stays twenty; forty-eight plus three
is fifty-one. **This is the first round in which a full exchange produced no new
shape**, which is the first evidence the population is converging rather than
accumulating — and the reason to say it explicitly is that a register which grows by
one every round is indistinguishable from one that is being padded.

**The register's own result this round is that both directions now work.** The last
entry recorded that every new instance was mine and none was found by me. Here one
of the three is WS-6's, found by me, in a message whose opening correction named the
exact shape it went on to commit. The list is no longer only a record of what other
readers catch in me; it is now symmetric, which is the first evidence that the
shapes are properties of the work rather than of one writer.

**The current round adds one sub-shape and two instances, again by enumeration.**
The bullets were counted directly and stood at seventeen before this edit. New
sub-shape: *a relation asserted with its terms transposed*. New instances: (i) this
programme's own citation of `CLOSURE_SYS_PATH_ALLOWED_SOURCE_SHA256` as living in
`validate_repo.py`, when that file is one of the dict's governed entries; (ii)
WS-1's citation of `migrate-approved-assets.yml` as "the workflow that runs this
script", when no workflow runs it. Seventeen plus one is eighteen; thirty-five plus
two is **thirty-seven**. Both instances are citations, and both were produced by
parties who verified the components and not the relation between them.

**This round adds two instances and no sub-shape, so eighteen stands and
thirty-seven becomes thirty-nine.** Both are extensions of existing bullets rather
than new forms, and both are named here so the figure is auditable rather than
incremented: *a correction that inherits the scope of the thing it corrects* gains
its fourth instance — the bullet's own remedy, which said "in the file" and was
therefore the defect it described — and *too narrow, empty* gains its fourth — two
false zeros from one instrument in one measurement, caught by a positive control.
Eighteen plus zero is eighteen; thirty-seven plus two is **thirty-nine**. The bullet
count was re-derived by enumeration, not carried forward, because the previous round
of this section is where a header count went stale by being incremented.

**This round adds two further instances and again no sub-shape, so eighteen stands
and thirty-nine becomes forty-one.** Both land on *wrong axis — the artefact
described instead of the decision*, and both are mine, in the paragraph where I
recorded that bullet's third instance. Named so the figure is auditable: (i) I called
`tests/test_rollout_trust_anchor.py` 5259–5271 "anchored correctly and projected
lossily" and instructed that it be cited for the anchor — but the hazard lives in the
sparse-checkout pattern string and the assertion inspects the tree, so the anchor is
on the wrong object too; (ii) I warranted its truth with a **tracked-tree**
enumeration against a claim about a **filesystem** walk. A candidate sub-shape was
considered and rejected: *wrong object* is not a new form, it is this bullet with the
axes one step further apart, and coining a nineteenth shape for it would have counted
a sharper reading as a new phenomenon. Eighteen plus zero is eighteen; thirty-nine
plus two is **forty-one**. Bullets re-enumerated, not incremented.

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
  **A fourth instance, mine, caught by a positive control:** measuring whether the
  workflow emitter has ever fired (§12a), my first sweep reported "960 annotation
  rows" — every one of which was the five-line body of a `404` response, iterated as
  though it were data, because the jobs endpoint had been given a path I had not
  checked. Corrected, the second sweep round-tripped its results through
  `ConvertTo-Json`/`ConvertFrom-Json`, which silently flattened the records, and
  reported `matches: 0` — a clean, plausible, entirely empty negative that agreed
  with the answer I expected. **Two different mechanisms produced the same
  false zero within minutes**, and neither announced itself: one dressed an error as
  a large count, the other dressed a lost field as a small one. What caught it was
  refusing to accept a negative without a positive control on the same instrument —
  re-running until the sweep returned known-present rows ("Process completed with
  exit code 1." × 64) alongside the absent ones. **The rule this yields is narrower
  and more usable than "verify the artefact, not the call":** a null result is
  uninterpretable until the same query has demonstrably returned a non-null one.
  Expecting the answer you get is the condition under which both of these survive.
- **Too narrow, non-empty — a sample read as a census.** WS-6 reported the two
  `authorized` verdicts in the anchor's history; there are seven (§12a). Every
  element it named was true and one was genuinely the last — the quantifier was
  false, not the data. This is the more durable of the two narrow shapes, because
  an empty result at least invites the question "is that right?", while a
  plausible non-empty list looks like a finished enumeration and so is *less*
  likely to be challenged than the silence is. **A second instance, mine, is
  recorded in §15:** the eleven-entry `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256` was
  read from its head and described by its first four members. **A third, also
  mine and worse, is now recorded in §15 too:** the census of Python hosts that
  could carry the invariant's constant enumerated two, and there is a third in
  neither digest map — an undercount that raised the guard's apparent price to
  the point of deferring it. **A fourth is the sharpest of the four, because
  nothing was read too quickly:** the four-row `.gitattributes` experiment ran on
  a worktree that already existed, so the only observations it could physically
  produce were transitions between states of that worktree. The population where
  the attribute works — a materialization that has not happened yet — was excluded
  by the *design* of the instrument, not by a careless reading of its output. That
  is the version of this shape that the standard remedy does not reach: "is that
  the whole set?" is answerable by looking harder at a query, and unanswerable by
  looking harder at an experiment that cannot emit the missing rows. It needed a
  different instrument, a clone into an empty directory, and WS-2 supplied it.
  **A fifth, and the second of the design-excluded variety, is the strongest
  demonstration of it because the missing rows were later recovered by other
  means:** the test for the merge-boundary race was *the same head SHA carrying
  opposite verdicts*. Applied to the whole anchor history it found two occurrences.
  There are four (§12a). The two it missed both have sibling runs on the same head,
  and both siblings are failures, so a success/failure comparison cannot see a race
  that converts one failure into a *different* failure — nor one that fires on a run
  with no sibling at all. The signature selects for races where an earlier attempt
  happened to go green, which is a property of the accident, not of the defect.
  Re-running it harder reaches nothing; the emitted code plus the merge timestamp
  reaches both. **What separates this from the ordinary narrow miss is that the
  instrument returned a non-empty, internally consistent, correct answer** — every
  pair it named is a genuine occurrence — so nothing about its output invites the
  question that would have exposed it.
- **Too wide — hits read as presence.** WS-1's repository-wide enumeration of
  "importers" returned sixteen sites of which **three** are imports; the other
  thirteen are string literals. WS-9's count of `raise` sites had the same defect:
  every site is real, but the set is wider than "reachable". **A third instance was
  committed by WS-6 and by this document simultaneously**, which is what makes it
  worth recording: both wrote that "every `TrustError` collapses to one summary
  line" to explain why no production `live_ref_changed` could be found. The
  collapse belongs to one *surface* — the check run, which maps five outcomes to
  five fixed strings (2771–2787) and carries no `text` field — and only the verify
  path publishes one at all; **both** paths print their full dotted code to stderr
  (3132 and 3196). The true statement is about one rendering of one path, and it
  was generalised to a class of exception. Neither party checked it because it was
  offered as the reason for an absence, and an absence invites no second look — the
  same incentive gap as the flattering-error entry below, in the opposite
  direction. **The repair went one step too far in this document and is recorded
  in §12a:** narrowing the claim to the check-run surface, it then asserted that
  the create path *is* check-run-observable, which inverts the truth, because the
  create path emits no check run. Narrowing a too-wide claim is itself a claim and
  needs the same query.
  **A fourth instance, mine, and it is the pure form of the shape:** §15 said the
  `allowed_signers` literal "exists in exactly one place in the repository". The
  scoped fact — it appears nowhere in the verifier — was verified and is still
  true; the sentence names no population and reads as repository-wide. It is
  fourteen occurrences in ten files. Two things make it the reference case. It
  was written *in the same document that had just adopted* the rule that a
  quantifier claim needs a query returning the whole set, one round after
  agreeing it. And it is simultaneously an instance of the flattering-error entry
  below: uniqueness meant no second copy could disagree, which made the hazard
  sound unrecoverable and so strengthened the argument it appeared in. **A fifth,
  also mine and also joint with a workstream, is the cheapest of them to have
  avoided:** "CI runs on Linux, so `autocrlf` is off" was written by WS-2 and
  repeated here to price the `.gitattributes` question. Sixteen workflows exist at
  `824b4238` and `ai-gateway-tests.yml` runs a `windows-latest` matrix leg (66).
  The conclusion survives on that leg's scope, but the statement was generalised
  from the one workflow the whole discussion was already reading — the population
  was never enumerated because a sample was already open on the desk. **A sixth
  moves the shape up one level, from a claim to a group label.** Scoring the
  stderr-discard predicate across `authorize_approved_assets_phase.sh`, WS-6 sorted
  eighteen sites into groups and put 108, 151 and 238 under "self-evident". It is
  true of 108 and 238, which print `lifecycle.tool_missing` and
  `lifecycle.dispatch_failed`. It is false of 151, which has no `||` handler at
  all, sits under `set -e`, and so exits printing *nothing*. The label was
  evidently formed from the members that fit and then applied to the group, and the
  member it does not fit is the one the predicate convicts hardest. **A group name
  is a quantified claim over its members and inherits every failure mode above** —
  this one is too-wide with the population being the group rather than a query
  result, which is worth separating because a wrong group name is invisible to the
  check that catches a wrong sentence: nothing in the list is misstated.
- **A correct count beside a list that does not sum to it.** The same enumeration
  was headed "18 sites" and followed by four groups totalling twenty. Both are
  right: three of the twenty carry `>/dev/null` without `2>&1`, so they do not
  discard stderr and are not in the population the eighteen counts — they are the
  control group, and their presence is what shows the predicate discriminates.
  **The near-miss is mine and it is the one worth recording.** Reading the header
  against the list, an arithmetic contradiction was drafted as a correction and
  would have gone out had the eighteen not been re-derived from the blob first —
  where the count came back exactly 18 and the resolution was immediate. The error
  would have been mine in full: a true observation about the presentation asserted
  as a defect in the finding. It is also the same shape as the frame collision
  below, one level up: a number that is correct for a population the neighbouring
  text does not identify. **A second instance, also mine, is worse because the
  arithmetic was the only unchecked step in the chain.** Verifying the 81-run
  anchor census, the eight per-code counts were added mentally, came to 59 against
  64 failures, and a five-run retrieval gap was inferred. A mechanism was then
  constructed for it — and the mechanism was *correct*, since the completeness
  check greps a token that appears in the command trace whether or not the verifier
  prints anything. The gap did not exist: one row was dropped while adding. Every
  expensive part of that verification was done properly and the cheapest part
  carried the error, which is the whole point — **the step nobody writes down is
  the step nobody checks.** The general observation survives on its own merits and
  is recorded in §12a; what would have been published is a defect report about a
  phantom symptom, arriving with a plausible cause already attached, which is the
  hardest kind to refuse.
- **A chain whose every link was checked except the one that inverts it.** WS-6
  classified `.gitattributes:4` — the LF pin on the trust root — as silent on
  drift, "no CI effect on Linux". That scoping invites the correction this document
  had just made in #84: CI is *not* only Linux, and `ai-gateway-tests.yml` runs a
  `windows-latest` matrix leg. The chain built from there was that no `pytest.ini`,
  `pyproject.toml`, `setup.cfg` or `tox.ini` exists anywhere in the repository — all
  four confirmed absent — so a bare `python -m pytest -q -m "not postgres"` collects
  the whole tree, so the Windows leg runs `tests/test_rollout_trust_anchor.py`, so
  the LF pin is load-bearing in CI and belongs in the loud class. Every link was
  verified. The conclusion is false: **`defaults.run.working-directory:
  services/ai-gateway` at lines 67–69 is job-level and governs the pytest step at
  87**, so the leg cannot reach the anchor tests on any platform. WS-6's row stands,
  and on a better reason than the one it gave — the only non-Linux leg is
  directory-scoped away from the anchor, so the verdict does not depend on the
  operating system at all.
  **Three properties make this the reference case for the family.** The unchecked
  link was not obscure, it was *categorical*: every other link was a question about
  the contents of a file, and the missing one was "where does this command run
  from", which is not a property of any file that was read. It ran in the
  flattering direction — the payoff was a dramatic correction to a workstream on
  the very leg this document had corrected it about a round earlier, which is the
  third time in this correspondence that the error pointing at the more satisfying
  conclusion is the one that survived furthest. And it was caught by continuing to
  check *after* the conclusion was already available, which no rule in this list
  mandates and which is the only thing that would have caught it.
- **A cost declined, reported as a limit of the instrument.** WS-6 wrote that the  absence of `live_ref_changed` was "not observable by the means I used". The
  codes were in the run logs the whole time under an exact, greppable prefix; what
  actually stopped it was 64 log downloads against one check-runs API call. It
  re-diagnosed this itself, unprompted, and the re-diagnosis is the entry: not a
  claim that was too wide, but **an unpaid cost written up as an epistemic
  boundary**. The two are indistinguishable in the prose — "I could not see it"
  reads identically either way — and they have opposite remedies, since a real
  limit needs a different instrument and a declined cost needs only the decision to
  pay. This is the more common of the two and the more comfortable to write, which
  is why it needs its own name. **The diagnostic is a question about the world, not
  about the writer: not "was my method adequate?" but "does a surface exist that
  carries this, and what does reading it cost?"** Asked here, it returned the
  census, and the census also handed over the #122 diagnosis outright — a result
  previously reached by reconstructing a timeline, and one of the missed race
  occurrences was sitting in the same output. The declined cost was larger than the
  claim it was declined for, which it always is, because an unpaid query answers
  every question it would have answered and not only the one that prompted it.
- **An internal-consistency assertion read as verification.** `tests/test_rollout_trust_anchor.py`
  1935–1948 asserts that one hard-coded trust-root path string appears in both
  `validate.yml` and `secret-scan.yml`. It is correct, it is on a required check,
  it genuinely prevents those copies from diverging — and it says nothing about
  whether the path exists. Three copies going stale *together* is a passing state,
  so the assertion supplies positive reassurance about wiring while the thing it
  names is unbound (§15). This belongs beside the exit-status entry above and
  generalises it: **an exit status is a claim about the command, and an
  internal-consistency assertion is a claim about the set of copies. Neither is a
  claim about the world.** It is the more dangerous of the two, because an exit
  status is at least known to be a weak signal, whereas a cross-file assertion is
  usually cited — as it was here — as the reason a literal is safe to duplicate.
  The discriminator is one question: **is any member of the compared set checked
  against something outside the set?** For these three, no. For `REQUIRED_FILES`,
  yes, at 272 — which is why the guard's assertion has to anchor there.
- **A number that is correct in two frames and means two different things — the
  collision.** WS-6's earlier record cites `f0a2d17 388` and this document cites
  `main 388`. Both are accurate and they are **different sites**: at `f0a2d175`,
  388 is `--expected-name … 2>/dev/null`, the verify-staged-runner capture, which
  on `main` is 379; at `824b4238`, 388 is `"$runner_start" … >/dev/null 2>&1`.
  #121's +9 shift is precisely what puts two records on one integer. Verified in
  both files. This is not the granularity failure below — the coordinate is exact
  and the reading is right — it is a *frame* failure, and it has a property none of
  the others have: **it presents as a flat contradiction between two correct
  records**, so the natural response is to re-check the readings, which confirms
  both and resolves nothing. The remedy is mechanical and cheap: **a line number is
  meaningless without its ref, and a table of them must carry the ref in its
  header.** The reason to enforce it here rather than treat it as tidiness is that
  the shift is large enough to collide and small enough to look like a mistake.
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
  mechanisms" above; the sets are real, and they are not what decides. WS-6 gave
  the sharper form: *the population was not wrong, the dimension was* — true
  statement, wrong axis, invisible in the output either time. **Third instance, and
  it is an assertion rather than a report.** `tests/test_rollout_trust_anchor.py`
  5259–5271 projects `path.name` over `ROOT.rglob` — which discards location, the
  one dimension the assertion appears to constrain. Its comment says it guards
  against a *nested* `.gitignore` reaching the sparse checkout; a nested `.gitignore`
  passes it. Found only by executing the projection against a synthetic tree.
  **My own summary of that instance was itself an instance, and this is the fourth.**
  I wrote that the assertion was "anchored correctly and projected lossily" and
  should be cited for the anchor. Measurement reverses the first half: the hazard
  lives in the sparse-checkout *pattern string* — the configuration that decides how
  the tree is read — and the assertion inspects the *tree*, which is downstream of
  it. Anchoring on the filesystem is the right instinct for a claim about the world,
  and this claim was never about the world. So the axes are one step further apart
  than this bullet recorded, and the artefact I praised for being anchored was
  anchored to the wrong object (§ the sparse-checkout precedent, withdrawn).
  **Fifth instance, same page, my instrument.** I warranted the assertion's truth
  with a tracked-tree enumeration (`truncated: false`) against a claim about a
  *filesystem* walk. The two populations differ by every ignored file, and
  `.gitignore:115` ignores the directory `pytest` writes into. An instrument
  answering a neighbouring question, cited as though it answered this one.
  **Sixth instance, and it is the cheapest of all to have avoided.** This document
  argued that `raise … from None` at three of six sites in `_fixed_migration.py`
  made those messages unrecoverable even if the swallowing handler were rewritten.
  `from None` does exactly what was claimed — it clears `__cause__` and sets
  `__suppress_context__` — and the six messages were never in `__cause__`. They are
  the `RuntimeError`'s own `args`, a channel `from None` does not touch. Executed on
  3.11.9: `str(exc)` returns the message, and `__context__` survives besides. A real
  property of the adjacent channel, measured correctly, describing nothing about
  where the payload lived. The cost was a mispriced remedy — three of six declared
  beyond reach when one line recovers all six.
  **Seventh instance, WS-6's, and it is the shape applied to a test rather than to a
  measurement.** Asking who else reads root `.gitattributes` returned
  `tests/test_migrate_approved_assets.py:2060` —
  `test_migration_files_are_forced_to_lf_checkout`, an unconditional
  `(ROOT / ".gitattributes").read_text()` on the required `root-rollout-tests`, so
  deleting the file does fail a required check pre-merge. Correct, and it is cited to
  bound a hazard about the **trust root's** byte pins. The two `text eol=lf` pins it
  asserts are the two **`database/migrations/*.sql`** pins. The three trust-root pins
  — `allowed_signers`, `approval.json`, `approval.sig` — are asserted **nowhere**:
  zero occurrences across all four files on `root-rollout-tests`. The right file, on
  the right required check, answering about a different subject. Finding the reader is
  not reading it.
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
  hunk. **Second instance, mine, and the domain was held by something I never
  looked for.** Recording that the shell script's ungoverned `python -c` and stdin
  sites expose "the working directory — the repository root", I verified that the
  repository root holds zero `.py` files. True, and it is the *consequent*. The
  antecedent — that the working directory **is** the repository root — I never
  checked, and the script never states: it contains no `cd`, `BASH_SOURCE`,
  `dirname`, `REPO_ROOT`, `pushd`, `realpath` or `$PWD`. The confinement is a side
  effect of two clauses in an input-validation conjunction (124–125, the only two of
  seven testing hard-coded relative paths). Verifying the consequent is what made
  the claim feel checked, which is the trap: **the easy half of a conditional is
  usually the half that is not load-bearing.** WS-9 found it by asking what pins the
  working directory — a question the sentence presupposed had an answer.
  **Third instance, mine, and WS-6 named the variant better than "domain":
  the measurement window spanned the introduction of the thing being measured.**
  From "all 64 failures exited 1; none exited 75" this document concluded that the
  undetermined path had never been taken across the check's whole history. Exit 75
  did not exist for 58 of those runs — `UNDETERMINED_EXIT_CODE`, `UndeterminedError`,
  `_report_failure` and `UNAUTHORIZED_EXIT_CODE` are all absent at `1a5d84e^`, as are
  the workflow's `undetermined()`, `exit 75` and `::error` — so 1 was the only
  failing value available and those runs cannot bear on the question. A negative
  result changed meaning partway through the population. **What makes this the
  sharpest instance in the list is that the disproof was already written on the same
  page:** the census table splits 58 bare / 6 prefixed, and the next sentence gives
  the reason. Neither was carried up to the four bullets that depended on it. WS-6
  then committed the same shape one section later, citing 11 pre-category
  occurrences as evidence of misclassification, so the round contains the error, its
  correction, and its recurrence.

  **Fourth instance, mine, and it is the recurrence rather than the error.** The
  correction above was applied to one of **three** copies of the same inference in
  this file — §12a — while §14 (the 79 % passage) and the
  `anchor_trust_root_absent` caveat kept it, in different words, for a further round.
  Both are now struck in place with their repairs. The adopted remedy was *grep your
  own corpus for the entity before citing it*; it never occurred to me to grep for
  the **claim**, which is the thing that recurs, and which cannot be found by
  searching for its wording because the three copies share no sentence. The search
  term for a withdrawn claim is its *inference*.

  **Fifth instance, WS-6's, where the domain is a branch scope rather than a time
  interval.** WS-6 reported platform #124 and #125 as "five required checks green on
  both". The checks are green on both — verified, 10 runs on #125's head. But
  `main-protected` is the repository's **only** ruleset and its `ref_name.include` is
  **`~DEFAULT_BRANCH`** alone. #125's base is `fix/trust-root-undetermined`, so no
  ruleset governs it and **none of those checks is required there**. Requiredness is a
  property of a *check on a target branch*, not of a check; carried onto a stacked
  pull request it describes a gate that does not exist. #125 is in fact ungated
  end-to-end — no ruleset, and no anchor verdict either, since the workflow's own
  scope is `branches: [main]`. What holds it is the standing hold, which is a
  discipline, not a control.
- **Success mistaken for effect — an action that reports success while changing
  something other than what its name promises.** The six above are all *read*
  instruments, misread. This one is a write, and the remedy is different in kind.
  Deciding whether `.gitattributes` could remove the CRLF hazard for item 5, I ran
  a scratch repository at `core.autocrlf=true`: committing `*.py text eol=lf`,
  then `git add --renormalize .`, then `git checkout -- .` all reported success
  and all left the file byte-identical, still CRLF. `--renormalize` renormalizes
  the *index*; `checkout -- .` restores files it considers modified, and the file
  is not modified relative to the index. Only a command that forces
  re-materialization converted it. The command whose name most exactly describes the
  intended effect is the one that does not produce it. **A second instance is my
  own, one command earlier in the same session:** the first run of that experiment
  wrote the sample file directly and never had git materialize it, so the CRLF
  condition was never created and all four rows came back identical — output that
  reads as clean confirmation of whatever was being tested. The setup step and the
  remedy step failed the same way: an action assumed to have landed rather than
  measured. **The remedy does not generalise from the rest of this list.** For a
  read you ask what the output is true of; for a write, "it succeeded" is never
  evidence, and the only check is to measure the artefact you wanted changed —
  here, count the `CR` bytes. **WS-2 read the same experiment the other way and
  the reading is better:** of those four commands, three quietly did nothing and
  one worked, and *none of the four printed anything that distinguished them*. So
  the generalisation is not confined to writes — **an exit status is a claim about
  the command; the check has to be a claim about the world.**

  **A third instance, mine, committed while verifying the F-8 round itself, and it
  extends the rule from writes to reads.** Fetching the shell script WS-1 had
  quoted, I ran `gh api …/contents/<path>?ref=<sha> 2>$null` with a path that does
  not exist in that tree. The `2>$null` discarded the error; the command produced an
  empty artefact; nothing on screen said so. **Had I then probed that content for
  `on_exit`, I would have found nothing and concluded WS-1 had fabricated a
  verbatim quotation** — a false accusation, from a fetch that never happened,
  against a correct report. What caught it was measuring the artefact rather than
  trusting the call: the SHA-256 came back `e3b0c442…`, the hash of the empty
  string. **The irony is the finding.** I destroyed an error channel with a stderr
  redirect while verifying a report about sites that destroy error channels with
  stderr redirects — the F-8 defect, self-inflicted, inside the F-8 audit, by the
  auditor. So the bullet's rule is not a rule about writes. For a read, "it returned"
  is no better evidence than "it succeeded", and the artefact needs measuring either
  way; a digest or a byte count costs one line and converts a silent empty result
  into a loud one.

  **And the conclusion drawn from that experiment was wrong, which is a separate
  failure from the one it illustrates.** The four rows were reported accurately and
  used to conclude that `.gitattributes` "is not effective". WS-2 supplied a fifth
  row — `git rm --cached -r .` then `git reset --hard` converts the file, clean
  tree, blob unchanged — and, more importantly, a sixth measurement the table had
  no row for: **a fresh clone with the attribute already committed checks the file
  out at `CR=0`.** Both reproduced here. The attribute is effective immediately for
  every future materialization; what it cannot do is retroactively rewrite a
  worktree that already exists. §15 and owner-decision item 5 are corrected.

  The reason this belongs in the register is *why* the table could not have found
  it. The experiment mutated a worktree that already existed, so every row it could
  produce was a transition between states of that worktree. The population where
  the remedy works — a materialization that has not happened yet — was outside what
  the instrument could observe, not something missed within it. **That is the
  sample-as-census shape with the sampling done by the experimental design rather
  than by the reader**, and no amount of adding rows reaches it; it needs a
  different instrument, a clone into an empty directory. The same reading applies to
  the two earlier misses: WS-2's `git status --porcelain` probe and my own
  never-materialized setup both read clean because the condition they were meant to
  observe had never been created.

  **A third instrument artefact, mine, committed while writing this correction up.**
  Re-running the experiment I printed a "stored blob CR count" of 3 for a blob that
  is pure LF — `git cat-file blob | Out-String` routes bytes through PowerShell's
  text pipeline, which inserts CRLF. Verified properly two ways: the blob is 18
  bytes with zero CRLF pairs when redirected binary-safe, and its SHA-1 equals the
  independently derived hash of the LF content (`a9aeef04`). A probe that reads the
  world through a converting layer reports the layer.

- **A sufficient cause named, and then dropped when the case changed.** Deciding
  whether a repo-wide `*.py text eol=lf` could break CI, §15 gave two independent
  reasons the Windows leg is unaffected — it is scoped to `services/ai-gateway`,
  *and* its repo-file readers normalize line endings — and then, three lines later,
  concluded that a repo-wide entry "inherits none of this inertness". That is only
  true if scope were the sole mechanism. The second reason survives the change of
  scope untouched, and is sufficient by itself. **This is not a check I failed to
  run: I ran it, wrote the result down, and then reasoned past my own sentence.**
  Nothing was missing from the instrument's range; a narrower test was applied to
  the variant than to the base case, using material already on the page. WS-2
  settled it by measurement — 221 passed / 1 skipped under both a CRLF and an LF
  checkout of the same commit — and the enumeration behind it is now in §15: all
  four repo-file readers on that leg normalize, and the two `read_bytes()` sites in
  the whole tree either normalize explicitly or digest a canonical re-serialization.
  **The direction is the familiar one.** The dropped reason was the one that would
  have removed a live CI consequence from a decision I wanted the owner to weigh
  carefully; keeping it made the decision look costlier than it is. Fourth instance
  in this register where the error's direction favours the writer's existing
  position, which is now frequent enough that direction is worth checking on its
  own. **Diagnostic:** when a conclusion rests on two or more sufficient reasons,
  a claim about any variant has to be tested against each reason separately —
  conjunctions are where a sound argument silently narrows to an unsound one.

  **Second instance, and the party is the one who established the distinction.**
  WS-2 is the session that corrected "CI is Linux" as false-as-stated, supplying the
  two-source structure this bullet records. Reporting the `_fixed_migration.py`
  finding two rounds later, it wrote that the failure is "invisible on CI because CI
  is Linux". The conclusion is true and the reason is the withdrawn one: at
  `824b4238`, `ai-gateway-tests.yml:66` still carries `windows-latest`, and what
  actually protects the root tree is the job's `working-directory:
  services/ai-gateway` at 67–69 scoping it out of collection. So a correction can
  fail to propagate to its own author's later prose, in a different file, about a
  different defect — which is the strongest argument yet that this register has to be
  a mechanical check rather than a remembered one. Neither party was careless; the
  correction simply had no instrument attached to it. **Diagnostic, and it is cheap:**
  when a reason has been formally withdrawn, the withdrawn phrasing is a string. Grep
  for it before sending.

- **A correction that inherits the scope of the thing it corrects.** **Four**
  instances in one thread, the fourth being this bullet's own remedy, which is what
  makes it a shape rather than an accident. This document framed the anchor's failure
  reporting as two paths; WS-6 corrected the claim *within* that frame and produced a
  four-row table; enumerating every emitting site in the verifier returns **six**.
  Each participant verified the part being changed and adopted the surrounding frame
  untested. WS-6 diagnosed its own case exactly — "I checked the half I was
  correcting and adopted the rest" — and the mechanism is that a correction arrives
  with its scope already fixed by the error, so the one thing it never questions is
  the boundary it inherited. **The tell is structural, not statistical:** a table
  produced by correcting a table is evidence about the corrected cells and about
  nothing else. **Remedy, as originally written:** when correcting an enumeration,
  re-derive the population from the source rather than editing the list — here,
  listing every `rollout_trust_anchor.` literal in the file took one command and
  returned two sites that three rounds of careful correction had not.
  **And the remedy was the fourth instance.** It says *in the file*. WS-6 ran it
  repository-wide instead and found a seventh emitter in
  `.github/workflows/rollout-trust-anchor.yml` (§12a). The row set had been
  re-derived; the frame that survived every correction, including the correction that
  named this failure mode, was the **file** — because the table had been about
  verifier call sites and nobody restated what the population was *of*. **Corrected
  remedy:** re-derive from the boundary of the thing named, not of the artefact being
  edited. The population here is every emitter of a token namespace, and a namespace's
  boundary is not a file. The interval between describing this shape precisely and
  committing it inside the description was one paragraph.

- **A plausible cause makes its symptom less likely to be re-checked.** WS-6's
  formulation, and it inverts the order this register otherwise assumes. When I
  reported a phantom five-run gap, the mechanism I built to explain it was
  *correct*; that is precisely why nothing prompted a recount, and why it would
  have survived review — a defect report with a real mechanism attached to a
  nonexistent symptom is refused only by re-deriving the symptom, and the
  mechanism's plausibility argues against doing so. WS-6 supplies the mirror
  instance: its timeline reconstruction for #122 was correct, and that is why it
  never looked for the emitted code that stated the same conclusion outright.
  **So explanatory power raises rather than lowers the need for the raw number**,
  which is the opposite of how explanation is normally treated as evidence.

- **A detector defined by a coincidence detects only instances that share the
  coincidence.** Also WS-6's, and the strongest member of the family because
  nothing in its output can be interrogated. The race proxy — "one head SHA
  carrying opposite verdicts" — selects for races where an earlier attempt
  happened to be green. That is a property of the accident, not of the defect, so
  failure-to-different-failure is structurally invisible and running the detector
  harder never reaches it. The output was non-empty, internally consistent, and
  every element true. **Check:** state the detector's selection criterion as a
  sentence and see whether it mentions the defect. "Two runs on one commit
  disagreed in conclusion" does not mention races at all. This is the census
  error with the flattering direction removed — the narrowing lived in the
  instrument's *design* rather than in the query.

- **A rule that reproduces, at the level of the rule, the defect it was written to
  govern.** The promotion criterion above (line 553, point 2) sorts every
  `undetermined.*` code into two bins: the benign merge race, which is neutral, and
  "any other `undetermined.*`", which is an infrastructure fault and breaks the
  count. `undetermined.anchor_trust_root_absent` is neither. The job honestly
  reached no decision and the cause is a **misconfigured repository** — so a correct
  detection of a real defect is scored as an environmental failure, and scored
  against promoting the only check that can detect it. That is the
  authorization-versus-infrastructure conflation of F-3 and #122 exactly, one level
  up: the same missing third bin, now in the rubric instead of the verifier, written
  by me in the same section that documents the verifier's version of it. **Distinct
  from "a correction that inherits the scope of the thing it corrects":** there the
  repair carries the original's blind spot forward; here a *governing* artefact
  independently re-derives the fault it governs, from the same pressure toward two
  bins that produced it the first time. **Check:** when a rule classifies outcomes
  of a mechanism, apply the rule to the mechanism's own known defect classes and see
  whether any lands in a bin that contradicts its meaning.

- **A relation asserted with its terms transposed — every component true, the
  composition inverted.** This document cited
  `CLOSURE_SYS_PATH_ALLOWED_SOURCE_SHA256` as living "in `validate_repo.py`
  (427–440)". The constant is at `verify_rollout_trust_anchor.py` **427**, and
  `validate_repo.py` — which contains the string `SYS_PATH` zero times — is one of
  the four paths that dict *governs*, at 431–433. WS-1 then produced the mirror
  image in the same message: `migrate-approved-assets.yml` does pin
  `python-version: "3.11"` at 449 and 1006, but it is not, as claimed, "the workflow
  that runs this script" — none of the sixteen workflow files references it, and the
  script is operator-run out of `scripts/operations/`. **What distinguishes this from
  every other entry above is that the instrument was not at fault.** The searches
  returned the right artefacts; the sentence assigned them the wrong roles. So no
  amount of widening the query reaches it, and the two standard defences both fail:
  the line number *corroborates* — `validate_repo.py:427` is itself a trust-closure
  line — and the entity is *genuinely related*, as a governed entry in one case and
  the runbook's namesake in the other. **Relatedness is what defeats the
  spot-check.** One causal link is worth recording because it runs between the two
  instances: the transposed pin sat beside a claim this document had scoped to
  "false on Python 3.11", which made a *version* look like the thing needing
  evidence and sent the next reader to a workflow with a version pin. An imprecise
  reason selects the next reader's search. **Check, and it is cheap because the
  corpus already holds the answer:** state the relation as a sentence with subject
  and object, then grep your own corpus for both terms — `execution-program.md` line
  872 already named `validate_repo.py` correctly as a pinned entry, so the two
  statements were contradicting each other in the same directory.

- **A generalisation that supplies its own corroborating instances.** Having read
  one file where the erasure genuinely sat in a wide handler
  (`_fixed_migration.py:712`), this document generalised the family as *the
  outermost handler of an entry point* and then wrote that "the shell script's
  version is the `EXIT` trap, which is the same structural position" — a factual
  claim about a file it had not opened, produced by the generalisation rather than
  by a read. Source refutes it: `authorize_approved_assets_phase.sh` defines
  `on_exit` at 86–101, installs it at 102, and prints only
  `lifecycle.cleanup_failed` from it, while `lifecycle.run_selection_failed` sits at
  262, inline in the retry loop 245–266. **The same paragraph did it twice.** Its
  other clause, "`closure.dynamic_import` collapses three", names a number matching
  nothing in the corpus: `execution-program.md` 956–962 describes *two* constants
  each collapsing *two* causes, and the token is raised at **46** sites in
  `verify_rollout_trust_anchor.py`. "Three" is the length of the sentence's own list
  of examples. Both errors were generated by the frame the sentence was already in.
  **This is the mechanism by which a wrong generalisation becomes self-confirming:**
  state the pattern, then populate it from the pattern instead of from the sources,
  and the instances read back as evidence. It is distinct from *a sample read as a
  census*, where the sample is real and only the extrapolation is unwarranted; here
  the extrapolated instance did not exist. The defence is positional rather than
  epistemic — **the instances of a generalisation must be gathered before it is
  written, because afterwards the writer is no longer sampling.**

- **A mitigation that converts its own success into evidence that nothing is
  wrong.** WS-6's, and it is the only shape here where the instrument was adequate
  and the *world* had been altered to be quiet. The anchor workflow's line 69 guard
  reports a missing trust root as `anchor_trust_root_absent` — undetermined, and
  correct — before the verifier can reach it. The verifier's own answer for the same
  condition is `unauthorized.trust_root_file_missing`, still live on two required
  checks that have no such guard. Because the guard suppressed the symptom on the
  surface anyone would look at, an annotation sweep across the whole run history
  returned zero occurrences, and that zero read as absence of the defect when it was
  a product of the fix. **Distinct from *too narrow, empty*, where the query was the
  wrong shape:** here the query was right, the population was right, and the silence
  was real — manufactured by a partial repair. The tell is cheap and available: a
  mitigation that renames a condition creates *two* names for it, so the question is
  not "does this still occur" but **"how many code paths reach this condition, and
  does the mitigation cover all of them"** — a question about the call graph, which
  no quantity of production evidence answers. The general form is uncomfortable:
  **the better a partial fix is at hiding the symptom, the stronger the evidence it
  manufactures that the remainder needs no fixing.**

Unifying form, and the reason the twenty belong together: **every one of these
instruments returned a true statement, and in no case was the true statement about
the question being asked.** The helper list truly contained no such script. The
two `authorized` verdicts were truly `authorized`. The sixteen hits truly
contained the module name. Line 24 truly is not an import statement. The two
mechanisms truly exist — and a third existed alongside them, which is the point:
the true statement was about the mechanisms I had read, not about the mechanisms
there are. The net delta truly is +9. `git add --renormalize` truly
renormalized the index. Eighteen sites truly discard stderr. Line 388 truly is a
discard site in both trees. Every opposite-verdict pair the race test named is
truly a race. `validate_repo.py:427` truly is a line about trust closure, and
`migrate-approved-assets.yml` truly pins Python 3.11 at 449 and 1006. Not one of
these is a wrong answer; each is a right answer to a
question nobody asked. **Three of them extend the form past outputs.** A
group name and a line number are not instrument readings at all — they are labels
placed on findings afterwards — and they fail identically, which suggests the rule
is not about instruments but about anything that carries a claim while omitting the
population or frame it is true of. The eighteenth extends it once more, to the
*relation between two artefacts*: there the reading is not merely unlabelled but
correct and complete, and the error is entirely in the sentence built from it —
which is why it is the only member whose remedy is grammatical rather than
investigative.

**The tenth extends it in the other direction, to instruments never run.** A
declined cost reported as a limit produces no output to be true of the wrong
question, because nothing was queried; the sentence describes the writer's reach
rather than the world, and it is the only member of the family that cannot be
caught by asking what the output is true of. Its diagnostic is the mirror image:
ask what surfaces exist and what each costs, *before* reporting that a thing could
not be seen. And it is the member with the largest expected loss, because an
unrun query is not just an unanswered question — it withholds every answer it
would have carried, which is why paying it here returned a census, a corroboration
of #122 on independent evidence, and two occurrences of a race whose count was
believed settled.

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

**One structural note, offered by WS-6 and adopted, about what this register is
evidence of.** This document recorded the too-wide shape and then instantiated it
— in the same section, inside the sentence narrowing the very claim the entry was
about, within a day. That is not an embarrassment to be softened; it is the
strongest available evidence that the failure mode is real, because **a failure you
can describe precisely and still commit is one that does not yield to knowing about
it.** It follows that the value of this register is not in its readers remembering
the entries. It is in the mechanical checks the entries produce — the ref in the
header, the sum against the count, the surface-and-cost question — each of which
works without being remembered at the moment it is needed. Entries that have not
yielded such a check should be treated as unfinished rather than as lessons.

**One positive instrument-design result, which the register otherwise has none
of.** WS-2 needed to know what `core.autocrlf` a GitHub-hosted `windows-latest`
runner uses, could not observe it, and did not assert it. Instead it ran the leg
under *both* possible checkouts and got the same verdict from each, which makes
the unobservable setting irrelevant to the conclusion rather than a caveat on it.
**When a condition you cannot observe has finitely many states, measuring every
state beats determining which one holds** — and it is cheaper here, since
determining the runner's setting would have required a CI round-trip. This is the
constructive form of the rule everything above states negatively: instead of
narrowing the claim to what the instrument saw, widen the instrument until the
unknown drops out. Recorded because the same move is available for the two
unresolved items in §15 and owner item 5, both of which currently carry
"representative rather than identical to CI" caveats that a two-endpoint design
would remove.

**Second positive result, and it is the same move used to refuse an inference
rather than to complete one.** Running the root tree at both endings, WS-2 hit a
failure present under LF and absent under CRLF —
`test_runner_executes_pinned_absolute_client_and_rejects_identity_drift`. The
available inference, "LF breaks a test", was the round's whole subject and would
have landed in this document. Instead of reporting it, WS-2 ran the 2×2:

| location | endings | result |
| --- | --- | --- |
| worktree | CRLF | pass 3/3 |
| temp clone | LF | fail 5/5 |
| temp clone | **CRLF** | **fail 3/3** |

The variable is the checkout *location*, not the endings; the one-sided run had
confounded the two, and a detached-HEAD control ruled out the remaining
alternative. Deterministic in both directions, so not a flake either. Mechanism
traced by patching a scratch clone to re-raise:
`RuntimeError("psql executable path changed")` out of
`_revalidate_trusted_executable`, which compares ancestor-directory filesystem
entries under a POSIX trust model and is therefore location-sensitive on Windows —
a local artefact, not a repository defect. **The design point is that the second
positive result has the opposite sign from the first.** The two-endpoint move
completed a claim there; here the added cell *destroyed* one, and destroyed it
before it was written down rather than after. An instrument that can only confirm
is not an instrument, so this is the better evidence that the register has started
paying for itself.

**A related family that is not an instrument failure at all, and needs separating
because the remedy differs.** In every case above the instrument was consulted and
its answer misread. In these the answer came *first* and the citation was
recruited afterwards to support it — so the citation is **true, and its truth is
independent of the conclusion it is offered for**. Six instances, all from this
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
- Mine, and the worst of them because it was load-bearing. Correcting WS-9's
  audit predicate for F-8, I proposed *is the failure of this command observable
  anywhere downstream?* and supported it with "at `main` 254/379 and `f0a2d17` 388
  there is no downstream name". That sentence is false — all three sites print a
  label and exit non-zero (262, 384, 392). The predicate does not merely admit one
  false positive; **it clears every real site**, so an auditor applying it would
  have concluded #121 fixes nothing. What separates the sites is the *cause*, not
  the failure. Two features make this the sharpest instance in the register: the
  fabricated support sat in the same paragraph as the claim it supported, and the
  paragraph's whole purpose was correcting somebody else's predicate — the act of
  correcting is not itself a check, and it supplies exactly the confidence that
  suppresses one.
- Mine, with a distinguishing feature the other four lack: **the recruited reason
  invited the harmful repair.** Recording the second `live_ref_changed` site
  (§12a), I wrote that it "is not the same fix" because it compares whole objects
  rather than two SHAs "so it cannot be split at all". True; and it makes the
  obstacle *structural*, which tells the next reader that splitting is blocked by
  a data shape rather than forbidden by purpose — an invitation to restructure and
  then split, which would hole the signing path. The real reason is functional and
  WS-6 supplied it. Where the other instances cost a reader some wasted effort,
  a true-but-structural reason for a decision that is actually about purpose
  **points at the dangerous change and calls it merely inconvenient**.
- Mine, and the one that shows the family is not confined to facts about code.
  §15 concluded that the negative invariant should be taken in the same sitting as
  owner-decision item 5 — correct — and supported it with a *cost*: the constant
  must live in a `.py` file and "both plausible hosts" are digest-pinned, so it
  needs a re-pin wherever it is put. Each named host really is pinned; the
  quantifier is false, there is a third host in neither digest map, and the true
  reason the conclusion holds is the opposite one — the classifier must live in
  the verifier anyway, so the pins are spent by the encoding and the invariant's
  marginal cost is zero. A recruited *cost* is more dangerous than a recruited
  *fact*, because it is acted on by whoever schedules the work rather than by
  whoever reads the file.

**The tell is checkable and cheap, and it has a hole my own instance falls
through.** Ask whether the citation would still be true if the conclusion were
false. For the first three, the fifth and the sixth it would; a supporting citation
should *fail* when the claim fails, and one that cannot is decoration. It costs the
implementer who reads "the anchor already blocks this" and skips writing the
guard. The remedy for those is stating the conclusion and the evidence in that
order, and checking the arrow between them points the way it is drawn. **The
fourth is not that.** "There is no
downstream name" is not a true statement recruited for the wrong conclusion — it
is simply false, and no question about the arrow between claim and evidence
detects it, because the evidence was never checked at all. So the family splits:
five cases of a real citation aimed wrongly, one of a citation that was asserted
because the conclusion required it to exist. The second kind is cheaper to catch —
read the lines — and easier to commit, because writing a sentence about what the
code does feels indistinguishable from having looked. **The fifth case adds a
severity axis the split does not capture:** among the aimed-wrongly cases, what
matters is not how wrong the reason is but what the wrong reason *recommends*.
Three of them merely waste a reader's effort; the `live_ref_changed` one names a
structural obstacle where the real objection is purpose, and so points the reader
at the harmful repair. Rank these by the action the bad reason invites, not by the
distance between the reason and the conclusion. **A sixth case supplies the null
value of that axis and it belongs on the same scale.** §15's cost for the negative
invariant — "a digest re-pin wherever it is put" — was an undercount that
overstated the price of a guard whose real marginal cost is zero. It recommends
neither a good action nor a harmful one; it recommends *deferral*. That is the
hardest entry on this axis to detect, because a harmful repair eventually produces
a failure and an inert reason eventually produces a confused reader, whereas a
guard that was never written produces nothing at all. Cost claims therefore need
the same treatment as quantifier claims: enumerate the hosts, do not estimate
them.

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
any confidence that a fact once written down is a fact available. **WS-2 endorsed
this over the weaker reading and gave the better reason:** the rule-doesn't-fire
limit still leaves something for the reader to get right at recall time, whereas
here there was nothing — the note was accurate, the retrieval query would have had
to be phrased in terms of the very knowledge the note existed to supply. An index
by symptom is only searchable by someone who already has the diagnosis.

**One aggravating factor, from the §12a instance.** Correcting WS-6's census from
two to seven made WS-6's own argument *stronger* — a well-attested pre-outage
`authorized` path makes the post-repair silence more striking, not less. So this
was an error its author had no incentive to find. That is worth stating as its
own caution, because the intuitive guard against motivated error is to check
hardest where a finding flatters you: **an error whose repair helps the arguer
will not be caught by that guard.** Self-interest is not an error detector in
either direction, and undercounts in your own favour are as invisible as
overcounts are tempting. **A third instance, mine, closes the pattern across both
agents.** §15's claim that the `allowed_signers` literal occurs exactly once made
the hazard unrecoverable — no second copy could disagree — and so strengthened
the argument it sat in. Three instances now, two agents, and in every one the
undercount was the direction that helped. That is not coincidence: an overcount
weakens your own case and gets re-checked in the writing, so the errors that
survive to publication are selected for flattering the argument.

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

   **Correction, from WS-6, and it removes the "from memory" qualifier.** This was
   written as a thought experiment about someone reconstructing the set unaided.
   It is not hypothetical: `scripts/operations/authorize_approved_assets_phase.sh`
   is **line 144 of `ROLLOUT_ONLY_PATHS`**, a real, pre-existing, protected
   constant that an implementer would reach by copying rather than by
   remembering. The uncaught direction is not a risk someone runs by being
   careless; it is the default outcome of the most available shortcut.

*A negative half is available for the two cases where the confusion is worst, and
those two are mechanical.* WS-6's form, taken:

```
ANCHOR_MACHINERY ∩ (APPROVAL_PATHS ∪ {allowed_signers}) == ∅
```

The positive assertion catches unprotected additions; this catches the receipt and
the trust root; the semantic middle stays with the add-is-never-bootstrap rule.

**The cost claim recorded here was wrong, and wrong in the direction that deters
the guard.** It read: `allowed_signers` has no path constant, so the invariant must
name it, the constant has to live in a `.py` file, and "both plausible hosts, the
verifier (digest 408) and its test (digest 423)", are digest-pinned — therefore "a
digest re-pin wherever it is put". The enumeration was short. WS-6 found a third
Python host, `scripts/validation/validate_rollout_ci_policy.py`, which already
holds the literal at 137 and is protected at 96; checked here against **both**
digest maps, not just the one WS-6 cleared it against — `CLOSURE_PROCESS_…`
(392–426) and `CLOSURE_SYS_PATH_…` (427–440) — and it is in neither. "Wherever it
is put" is false.

**But the conclusion inverts rather than merely survives.** `is_protected_path` is
verifier behaviour (1107), so a bootstrap-versus-receipt classifier is too, and
`ANCHOR_MACHINERY` has to live where it is consumed. The re-pin is therefore spent
by *encoding the classifier at all*, and the negative conjunct adds nothing to it.
One refinement to WS-6's version: it is **two** pins, not one — the verifier (408)
and its test (423), because new verifier behaviour needs a test and the test is
pinned as well. Both are spent by the encoding; the negative invariant's
**marginal cost is zero**. The recommendation — take it in the same sitting as
owner-decision item 5 — stands, on a different reason.

Worth naming what the wrong reason did. It did not point at a harmful repair; it
pointed at *nothing*. An overstated cost recommends deferral, and deferring a
zero-cost guard produces no event, no failure and no reader who notices. That is
the null case of the severity axis in §13: rank a wrong reason by the action it
invites, and include inaction, which is the cheapest error to commit and the only
one with no symptom.

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

**Retracted: the uniqueness claim.** This section previously said the literal
`.github/trust/rollout-policy/allowed_signers` "exists in exactly one place in the
repository — the sparse-checkout list an implementer would copy from", and drew
from it that there is "no second copy for a wrong one to disagree with". False.
WS-6's census is exact, and re-run here across both path separators at
`824b4238`: **14 occurrences in 10 files.**

| File | Lines |
| --- | --- |
| `.gitattributes` | 4 |
| `.github/workflows/validate.yml` | 89 |
| `.github/workflows/secret-scan.yml` | 44 |
| `.github/workflows/rollout-trust-anchor.yml` | 35, 63 |
| `scripts/approval/create_rollout_trust_receipt.sh` | 23 |
| `scripts/approval/create_rollout_trust_receipt.ps1` | 21 (backslashes) |
| `scripts/validation/validate_repo.py` | 34 |
| `scripts/validation/validate_rollout_ci_policy.py` | 137 |
| `tests/test_rollout_trust_anchor.py` | 1920, 1939, 1969 |
| `docs/runbooks/authorize-rollout-policy-change.md` | 36, 371 |

The scoped half is still true and was re-verified: the literal appears **nowhere**
in the verifier, which contains only `allowed_signers_path: Path`,
`args.allowed_signers` and error-code strings. The defect is that a true statement
about one file was written in a form that reads as a statement about the
repository — the same quantifier failure recorded in §13, committed while writing
up the round in which it was agreed. And the direction is the familiar one: the
undercount made the hazard sound unrecoverable, so it flattered the argument and
nothing prompted a re-check.

**A correction to the correction, because the fourteen do not do the work either.**
WS-6 concludes that "the cross-copy check you said would not fire has more places
to fire from than almost anything else in the repo". That does not follow. The
fourteen sit on six unrelated axes — a git attribute, two CLI arguments, a
sparse-checkout, a required-file list, a routing exclusion, prose and test
fixtures — and **not one of them classifies the path**. Drift between them would
signal a rename, never a misclassification, which is the hazard this section is
about. So the corrected fact is right and the inference from it is not: I claimed
one copy and concluded no safety net; WS-6 counted fourteen and concluded a strong
one; the copies are silent on the question in both cases.

**What is genuinely load-bearing, and neither of us named it.** The occurrence at
`validate_repo.py:34` is inside `REQUIRED_FILES`, consumed at 270–272 as
`check(path.is_file(), …)`. The exact path is **existence-asserted in CI**. So the
F-7 drift WS-6 originally priced — the invariant hard-codes the literal, the file
is later renamed, the intersection goes vacuously empty and the guard silently
stops guarding — cannot happen quietly: the rename fails `validate_repo.py`, which
is itself protected (anchor 95). Hard-coding the literal is therefore materially
safer than either of us costed it, and the "or introduce a constant" horn is not
forced by drift. It remains preferable for legibility, and it is free anyway under
the corrected cost above.

`.gitattributes` is genuinely undecided rather than wrong. It governs LF
normalization and therefore the digests, so it has a real claim; it is also
repo-wide, and classifying it bootstrap would let a normalization change merge
receiptless. That one wants the owner. **One input from WS-6, verified and
deliberately narrow.** The file is six lines and three of them are the trust
material — `allowed_signers` 4, `approval.json` 5, `approval.sig` 6 — and
signature verification is byte-exact, so on a CRLF checkout without those pins a
receipt does not verify. But the anchor workflow is `runs-on: ubuntu-latest`
(line 20), where `autocrlf` is off, so removing the pins would not move a single
CI verdict. They are load-bearing for local reproduction, not for the gate's
output. That is a checkable distinction and a weaker claim than "it is machinery",
which is what the argument would have been without the check.

**A correction to the supporting sentence, not the conclusion.** "CI runs on
Linux" was written by WS-2 and repeated here, and it is not exactly true. Sixteen
workflows at `824b4238`, none setting `core.autocrlf`; the self-hosted runners in
`migrate-approved-assets.yml` carry `linux`/`x64` labels; but
`ai-gateway-tests.yml` runs its `unit` job on `[ubuntu-latest, windows-latest]`
(line 66), so a **Windows CI leg exists**. Confirmed independently by WS-2: that
matrix is the only `windows-latest` in the repository. Filed here because "CI is
Linux" is precisely the kind of load-bearing background fact that gets reused
without re-checking — it was asserted from the workflow everybody was already
reading, and one of the fifteen others contradicts it.

**And the consequence drawn from it was wrong, which WS-2 then measured.** This
previously read that the leg's survival "is a fact about that leg's scope, not
about the platform", so a repo-wide `*.py text eol=lf` "would change the Windows
leg's checkout and **inherits none of this inertness**". The last clause is false,
and the refutation is three lines above it in this document's own text: the
inertness has **two independent sources**, and the sentence that named both then
reasoned as though only one existed.

| Source | Mechanism | Does a repo-wide `*.py` entry escape it? |
| --- | --- | --- |
| (a) collection scope | `defaults.run.working-directory: services/ai-gateway`, `ai-gateway-tests.yml` 67–69, governs the pytest step at 87 | **yes** — `.py` files inside that directory *are* collected |
| (b) reader normalization | every repo-file reader on the leg normalizes line endings | **no** |

(b) is sufficient on its own, so the entry changes the leg's checkout bytes and
does not change its verdict. WS-2 measured exactly that, on one machine at one
commit, running the leg as CI runs it: **221 passed / 1 skipped under a CRLF
checkout and 221 passed / 1 skipped under an LF checkout of `824b4238`.**

**The population behind (b) was enumerated here rather than sampled, because a
single normalizing reader would only have made the result luck.** Every repo-file
read on that leg, across all 22 test modules and all 19 application modules:

- `tests/migration_support.py:20` — the only `read_bytes()` in the test tree, and
  it feeds `decode_utf8_normalized` (12–16), which maps `\r\n` and lone `\r` to
  `\n`.
- `tests/test_deployment_contract_doc.py:44`, `tests/test_migration_008_static.py:48`,
  `tests/test_postgres_ai_gateway.py:57` — `read_text(encoding="utf-8")`, which
  opens with `newline=None` and so applies universal newlines. Verified by
  execution on Python 3.11.9, the leg's version: `write_bytes(b"a=1\r\nb=2\r\n")`
  then `read_text` returns `'a=1\nb=2\n'` while `read_bytes` still shows the `CR`.
- `app/company_os_model_proof.py:165` — the only `read_bytes()` in application
  code, and its digest at 177 is taken over a **canonical re-serialization**
  (171–176: `sort_keys=True`, `separators=(",",":")`, `ensure_ascii=True`), so the
  source file's endings cannot reach it. The second digest, at 276, is over
  `source["utf8_json"]`, an in-memory string that was never a checkout file.
- Zero `open(…, "rb")` and zero `newline=` overrides anywhere in either tree.

`tests/test_migration_blob_portability.py` is the confirming case: it exists for
this exact question and compares an `origin/main` blob against the worktree file
with **both** sides normalized (29–32).

So the two-path form and the repo-wide form are equally inert *as to CI verdicts*.
**Scope remains a real decision** — blast radius, review surface, and the
transition cost on existing worktrees all scale with it — but not on the ground
this document gave, and the "inherits none of this inertness" clause is withdrawn.

**One asymmetry survives, and it is a latent condition worth recording on its own.**
At the repository root, `tests/test_migration_digest_pins.py:53` is
`hashlib.sha256(migration.path.read_bytes()).hexdigest()` — a raw byte digest with
**no normalization at all**, over seven migrations that carry no `eol=lf` pin. That
tree has source (a) and no (b). It has never gone red because the only Windows leg
is directory-scoped away from it, which is protection by accident of collection
rather than by design, and nothing in the test expresses the dependency. Move that
job's working directory, or add any Windows job at repository root, and seven
subtests fail immediately. **But it does not bear on the `*.py` scope question**:
`ALL_MIGRATIONS` are `.sql`, so no `*.py` entry of any breadth can reach them. It
is an adjacent `.sql` question — WS-2's point that `*.sql text eol=lf` would retire
the class is right, and is a wider edit than owner item 5 contemplates.

**Update — the `.sql` scoping is now enumerated rather than asserted, and the
exposed population is two readers in two files, not one.** The paragraph above
asserted that `ALL_MIGRATIONS` are all `.sql`. WS-2 checked it and I re-derived it
independently at `824b4238`: `tests/test_migration_digest_pins.py` **36–46** holds
nine entries, and every one of the nine `apply_*.py` modules was fetched and its
`path=` expression extracted across line breaks — all nine end in `.sql`, spanning
`001`–`008` plus `008_drive_bridge_replay_reservations.sql`. **Nine of nine.** No
`*.py` entry of any breadth reaches that test, so the scope decision in owner item 5
is untouched by it.

WS-2 also ran the whole root tree at both endings on one machine at one commit —
**8 failed under CRLF, 0 of those 8 under LF**, and **zero `.py`-caused failures at
either ending**. That converts the inertness claim from the two readers we happened
to inspect into a census, which is the stronger form and the one that should be
cited.

**The eighth failure is a second raw-byte reader I had not recorded, and it is in a
different file.** `scripts/validation/validate_ai_gateway.py` **405–406** reads
`database/migrations/008_ai_gateway_runtime_hardening.sql` with `.read_bytes()`,
digests it at **209**, and emits at **212–213** the exact text WS-2 extracted from
its failing run. It is asserted by `tests/test_ai_gateway_hardening.py` **18–19**
(`test_repository_contract_passes`), which is also in root `tests/` and so sits in
the same collection-scope-protected population. So the latent condition is:

| Reader | Normalization | Reached by |
| --- | --- | --- |
| `tests/test_migration_digest_pins.py:53` | none | 7 migration subtests |
| `validate_ai_gateway.py:405–406` → digest at 209 | none | `test_ai_gateway_hardening.py:18` |

**And the same validator reads the same file both ways**, which is the detail that
makes this a defect rather than a choice: `read_text(encoding="utf-8")` at
**380–381** normalizes, while `.read_bytes()` at **405–406** does not, over one
migration file in one module. The identical inconsistency appears in
`tests/test_rollout_trust_anchor.py`, where 4058, 4064, 4238, 4332 and 4553 all read
`.read_bytes().replace(b"\r\n", b"\n")` and **4243** alone reads raw — safe there,
because normalization happens downstream, but the same unexplained asymmetry.

**The general pattern, restated on five sets, and the earlier version of it was
too weak.** This previously read: *a wrong candidate set is dangerous exactly when
it contains the verifier*, on three data points. Two more sets exist. Scored at
`824b4238` against the four machinery files and the trust root:

| Candidate set | Keyed on | n | Machinery | Trust root | Extra |
| --- | --- | --- | --- | --- | --- |
| workflow sparse-checkout, 32–37 | what the anchor **reads** | 4 | 1/4 | **in** | 2 |
| `CLOSURE_PROCESS_…SHA256`, 392–426 | what it **loads** | 11 | 2/4 | out | 9 |
| `CLOSURE_LOADER_ALLOWED_MODULES`, 378–391 | what may be **imported** | 10 | 0/4 | out | 10 |
| `ROLLOUT_ONLY_PATHS`, 131–160 | what must not **trigger** | 27 | **4/4** | **in** | 22 |
| `REQUIRED_FILES`, 25–55 | what must **exist** | 29 | 3/4 | **in** | 25 |

**The real pattern is not that these sets contain the verifier — it is why.** Each
is keyed on a mechanism the verifier *participates in*: being read, being loaded,
being importable, being routed, being present. None is keyed on *being part of the
decision procedure*, which is the only thing `ANCHOR_MACHINERY` means. The verifier
therefore appears in four of the five for four unrelated reasons, and the one
spot-check every reader runs — "is the anchor in it?" — is **uninformative by
construction**, not by carelessness. That is a stronger and more durable argument
for writing the enumeration fresh than counting confirmations, because it predicts
that any future set will have the same defect.

Two consequences worth stating separately. **Three of the five contain the trust
root**, so the single most severe misclassification is the majority behaviour of
the available enumerations, not an outlier. And `ROLLOUT_ONLY_PATHS` is the only
set that is *complete* on machinery (4/4, nothing missing) while also being wrong
— the most attractive candidate is one of the wrong ones. WS-6 is right that a
pure superset survives review where a two-directional error does not, because
"is everything in here rollout-related?" answers yes for all 27. The mechanism is
worse than a coincidence: `ROLLOUT_ONLY_PATHS` has exactly one consumer, line 509,
`if routed_paths & ROLLOUT_ONLY_PATHS` → "rollout-only files must not trigger
credential-aware Adapter Tests". Its criterion is rollout *scope*, and
rollout-scoped strictly contains anchor-machinery by definition. It is a superset
**permanently**: pruning it would break the routing rule, and every future
rollout-scoped file enlarges it again. Two further reasons not to reuse it: it
mixes a glob (`tests/fixtures/approved-assets-rollout/**`, 156) so it cannot be
consumed as an exact-path frozenset without transformation, and the row filed here
as harmless points straight at it — `CLOSURE_LOADER_ALLOWED_MODULES` line 388 is
`scripts.validation.validate_rollout_ci_policy`, the module that holds it. The set
that is safe because nobody would adopt it is a pointer to the set that is
dangerous because everybody would.

**This also promotes the negative invariant from speculative to demonstrated.**
It was proposed as a guard against a mistake nobody had made.
`ROLLOUT_ONLY_PATHS` contains `allowed_signers` at 137 and `REQUIRED_FILES`
contains it at 34, so the invariant fires on two of the three most complete
enumerations available — on the first attempt, before review, without anyone
having to be careless.

Recorded, not built. It is a protected-path change encoding an undecided policy,
and it waits for both the ruling and the authority to ask for it.

**What binds the trust-root literal, checked rather than assumed — and it is one
mechanism, not fourteen and not two.** Two competing intuitions were in play. The
first, this document's, was that fourteen scattered copies of
`.github/trust/rollout-policy/allowed_signers` mean no cross-copy check can fire.
The second, WS-6's, was that fourteen copies mean fourteen chances to disagree.
Both are wrong, and the second is wrong twice: **none of the six axes classifies
the path**, so drift among them means *rename*, never *misclassification*; and
most of the copies are silent on a rename anyway. WS-6 established both points and
its classification is adopted. The one thing worth correcting is the conclusion it
drew, because it is the part the guard's encoding depends on.

WS-6 named two binding mechanisms, both on required checks, and called that
sufficient. The count is right and **what they bind is not the same thing**:

| Mechanism | Asserts | Claim about |
| --- | --- | --- |
| `validate_repo.py:34` in `REQUIRED_FILES`, consumed at 268–272 as `check(path.is_file(), …)` | a file exists at the literal | **the world** |
| `tests/test_rollout_trust_anchor.py` 1935–1948 | the literal string appears in *both* `validate.yml` and `secret-scan.yml` | **the set of copies** |

The second was verified by running it rather than reading it: the test's expected
string is assembled starting mid-invocation, at `verify_rollout_trust_anchor.py`,
which is what lets one string match `python -I …` at `validate.yml:87–89` and
`python3 -I …` at `secret-scan.yml:42–44`. Executed against both blobs at
`824b4238`, both `assertIn` calls return true. It is a real cross-copy consistency
check and WS-6 is right that it exists for this literal.

**But consistency is not currency, and the gap is reachable in one step.** Rename
the trust root and do only what turns CI red:

1. `validate_repo.py:34` fails — `path.is_file()` is false. The renamer fixes that
   one line. This is the only thing that fails.
2. The test still **passes**: its hard-coded old path still matches both workflows,
   because all three went stale together.
3. Both workflows still pass the old path to `validate-trust-root`, which reaches
   `_validate_trust_root_command` 3210–3217 — on `trust_root_not_configured` it
   prints `{"trust_root":"unconfigured"}` and **`return 0`**. Deliberate, since an
   unarmed anchor is a legitimate state, and silent by consequence.

End state: **green, with both trust-root validations validating a file that does
not exist**, and a passing test whose subject is trust-anchor wiring. The test does
not merely fail to catch this; it supplies positive reassurance while the thing it
names is unbound. So WS-6's summary — "they can't drift apart, and they can't drift
from the test" — is exactly right and is exactly the limit for those two.

This is WS-2's rule one level up. An exit status is a claim about the command, not
the world; **an internal-consistency assertion is a claim about the set of copies,
not about the thing they name.** Both read as verification and neither is.

**Correction — there is a third mechanism, it does catch the coordinated case, and
it is on the check nobody must read.** The paragraph above ended "nothing binds the
trio to the filesystem. Two mechanisms exist." Two was the count of mechanisms *I
had looked at*, and I generalised it to the count that exists — the §13 shape, in
the section that documents the §13 shape. WS-6 supplied the missing one and it
survives verification exactly as given, at `824b4238`:

| Mechanism | Asserts | Claim about | Required? |
| --- | --- | --- | --- |
| `validate_repo.py:34` in `REQUIRED_FILES`, consumed at 268–272 as `check(path.is_file(), …)` | a file exists at the literal | **the world** | yes — `Validate repository structure and content` |
| `tests/test_rollout_trust_anchor.py` 1935–1948 | the literal appears in *both* `validate.yml` and `secret-scan.yml` | **the set of copies** | yes — `root-rollout-tests` |
| `.github/workflows/rollout-trust-anchor.yml` **69** — `test -f "$allowed_signers" \|\| undetermined anchor_trust_root_absent` | a file exists at the workflow's own literal (**63**), materialised by the sparse-checkout list at **32–37** whose entry **35** is the same path | **the world** | **no** |

So the coordinated rename *is* detected, loudly and by name: stale literal at 35 →
sparse-checkout materialises nothing → `test -f` at 69 fails → `undetermined()`
(**55–59**) prints `rollout_trust_anchor.undetermined.anchor_trust_root_absent`,
emits an `::error` annotation, and exits **75**. A message that says exactly what
is wrong. The corrected statement is therefore not "two mechanisms, both required"
(WS-6's, withdrawn by WS-6) nor "one about the world, one about copies" (mine, and
incomplete) but: **three mechanisms, and the only one that catches coordinated
staleness is the only one that is not required.**

The required set was read from the live ruleset rather than inferred — ruleset
`20236725` `main-protected`, enforcement `active`, five contexts: the hard-fail
scan job defined at `secret-scan.yml:14`, plus `independent-rollout-policy`,
`n8n isolation`,
`root-rollout-tests`, `Validate repository structure and content`. The anchor's job
is `Verify exact current head from merged base` in `Base-Trusted Rollout
Authorization`. It is not among them.

**And it is worse than non-required: the detection lags by one pull request, onto
an innocent author.** The job has exactly one checkout step (**23–38**), and it is
pinned to `repository: Ivan-Shyla/adapteng-automation-platform` / `ref:
refs/heads/main` (**30–31**) — the whole point of a base-trusted design. So the
anchor machinery is read from `main`, never from the head under test. During the
pull request that performs the rename, `main` still carries the old path, line 69
passes, and **the offending change goes green.** The alarm first fires after that
merge, on the *next* pull request, whose author changed nothing related. Combined
with `cancel-in-progress: true` (**13–15**) and `pull_request_target` (**4–6**), the
observable is: an unrelated pull request suddenly red on a non-required check with
a code nobody recognises. WS-6 predicted this would be read as flakiness; the
lag makes that reading close to inevitable, because the evidence pointing at the
rename is one merge back and on a different pull request.

**Sharpened by WS-6 and verified: "lags by one pull request" understates it. The
alarm is not lagging, it is permanent.** The same two facts that produce the lag also
fix it in place. Line **35** hard-codes
`.github/trust/rollout-policy/allowed_signers` as a sparse-checkout entry, and the
single checkout is pinned to `refs/heads/main`. So once the rename merges, the
sparse pattern selects a path that no longer exists, line 69 fails, and it fails
*identically on every subsequent pull request* — not once on the next author, but on
every author thereafter, until somebody edits a protected workflow to repair it.
The guard's failure mode therefore reconstructs the exact disease this programme was
convened to remove: a non-required check stuck red on unrelated pull requests,
teaching every reviewer to ignore it. That is the strongest argument for making
`root-rollout-tests` the venue, and neither WS-6 nor this document had made it: a
pre-merge block on a required check is not merely *earlier*, it is the difference
between one correct refusal with the cause visible in the diff, and an unbounded red
streak charged to innocent authors.

**And the condition it would create already largely obtains — measured, from the
runs.** Of the **81** recorded runs of `rollout-trust-anchor.yml`, **64 failed, 12
succeeded, 5 were cancelled**: the check is red on **79 %** of its runs today, before
any rename. ~~Every one of those 64 exited **1** (`unauthorized`), so the exit-**75**
pathology described above has provably never occurred~~ — **struck; see the
measurement-window correction at §12a.** Exit 75 did not exist for 58 of those runs,
and neither did the word `unauthorized`. The conclusion survives — the pathology has
not occurred — but on the 6 post-`1a5d84e` runs, and for the 58 it survives *a
fortiori*, because the code path was absent rather than untaken. The 79 % figure and
the red/green split are unaffected: those rest on run conclusions, which exist for the
whole window. This cuts both ways and both are worth holding. It weakens any claim
that a rename would *introduce* habitual redness, since the habit is already available
to be formed; and it strengthens the promotion argument, because a check at 79 % red is
one whose signal is already discounted, so adding a permanent failure mode to it costs
almost nothing in attention and buys nothing in detection.

**That strike is itself the finding.** The identical inference was corrected at §12a
in the same edit that left this copy and one more (below, on
`anchor_trust_root_absent`) standing — three sites, one corrected. The remedy adopted
two rounds ago was *grep your own corpus for the entity before citing it*; it was
applied to **entities** and never to **corrected claims**, which are the thing that
actually recurs. A withdrawn claim is not retired by being withdrawn once. **When a
claim is corrected, grep the corpus for the claim, not only for the correction's
subject** — and the search term is the inference, not its wording, because the three
copies here share no sentence.

**A separate defect in the same population, found by WS-6 building a stacked pull
request.** `rollout-trust-anchor.yml` **4–5** is `pull_request_target:` with
`branches: [main]`, so the workflow fires only when the pull request's *base* is
`main`. Verified against the check-runs API rather than inferred: platform **#124**
(base `main`) carries **12** check runs including both anchor checks, and platform
**#125** (base `fix/trust-root-undetermined`) carries **10** with the anchor checks
**absent entirely** — not pending, not neutral, never created. So a stacked pull
request receives **no anchor verdict at all**, and in the checks list that is not
distinguishable at a glance from a passing one. Two consequences for the numbers on
this page: the 81 is a count of pull requests **targeting `main`**, not of pull
requests; and the population excludes stacked work *silently*, in the same surface
where the failures are counted.

**And it prices the promotion, which is the part that is actionable.** Promotion is
safe against this only because two independently-written scopes happen to coincide:
the workflow's `branches: [main]` and the ruleset's `ref_name.include` of
**`~DEFAULT_BRANCH`** — verified, and `main-protected` is the repository's only
ruleset. Because they coincide, a promoted anchor would be required exactly on the
pull requests where it fires, and stacked pull requests would remain ungoverned
rather than blocked. **Nothing asserts the coincidence.** Widening the ruleset, or
narrowing the workflow to a subset of governed branches, produces a required check
that never reports — and `execution-program.md` line 115 already states what that
does, in the n8n item, for a different check: *a required check that never starts
blocks a pull request forever*. The rule needed to price this promotion was already
written down for another one. So the promotion is sound and its soundness is
contingent: **if the anchor is promoted, the two scopes must be pinned to each
other by a test**, or the next edit to either one is a repository-wide merge stop
with no failing assertion to explain it.

**The consequence WS-6 draws is correct and it indicts the criterion I wrote.** By
the adopted criterion above (line 553 and its point 2 at 563–569),
`undetermined.pull_request.no_longer_open` is neutral and **"any other
`undetermined.*` is an infrastructure fault and breaks it."**
`anchor_trust_root_absent` is not `no_longer_open`, so a correct detection of a real
repository defect **resets the promotion count** — and resets it toward never
promoting the only check that can see the defect. WS-6's formulation is right: a
check that detects a real defect and is ignored *for* detecting it is worse than one
that detects nothing.

**With one caveat that belongs on this argument wherever it is quoted, and that WS-6
raised against its own earlier report.** `anchor_trust_root_absent` has **never
fired**. §12a now establishes this structurally rather than by log absence: across
all 81 recorded runs every failure exited **1**, and every path that reaches this
code exits **75**. **That support is invalid for 58 of the 81 and the conclusion is
nonetheless stronger than stated** — the third copy of the corrected inference, kept
here with its repair rather than deleted. `undetermined()`, `exit 75` and `::error`
are all absent from `rollout-trust-anchor.yml` at `8a4d87e7`, so for the pre-#116
runs the code did not exist to fire. "Never fired" holds across the whole window:
by the exit-code argument for the 6, and by non-existence for the 58. The guard is
therefore load-bearing *in principle* and has never
been load-bearing *in practice* — the same status as `live_ref_changed`, mechanism
read at source with no production occurrence. This does not weaken the argument,
because a guard's correctness does not depend on its having fired, and the promotion
criterion is defective whether or not the reset has yet been triggered. It does mean
the sentence "the only mechanism that catches coordinated staleness" describes a
capability, not an observed event, and it was first stated here without that
distinction.

**But the deeper fault is that my criterion repeats the defect it was written to
police.** Point 2 sorts `undetermined.*` into exactly two bins, benign-race and
infrastructure fault. `anchor_trust_root_absent` is neither: the job honestly could
not reach a decision, and the cause is a **misconfigured repository**. That is the
authorization-versus-infrastructure conflation of F-3 and #122, one level up — the
same two-bin error, now in the promotion rubric rather than in the verifier, written
by me in the same section that documents the verifier's version of it. The rubric
needs a third bin: **`undetermined.*` whose cause is a repository defect is evidence
the mechanism works.** It should advance the count, not break it.

**And the remedy does not require promotion at all, which neither of us saw.**
`root-rollout-tests` — a *required* check — runs `tests/test_rollout_trust_anchor.py`
directly (`rollout-policy.yml:24`, in the explicit four-file pytest list), from an
ordinary `actions/checkout` (**13–16**) with no `ref:`, i.e. against the pull
request's own tree. So a filesystem assertion placed in that file runs **on the
required set, pre-merge, against the head that contains the rename.** It blocks the
offending pull request instead of alarming the next one. Promotion of the anchor
remains desirable for its own reasons, but coordinated staleness specifically is
closed by one assertion in a file a required check already executes.

**Which improves WS-6's third option and changes the instruction that implements
it.** WS-6 proposes neither hard-code-and-hope nor a cross-file constant, but
*hard-code plus a consistency assertion*, justified by the precedent at 1935–1948.
The proposal is right and the justification is the wrong half of it. Followed
literally — assert the verifier's new literal appears in `validate.yml` — the guard
joins the **silent** class, because 1935–1948 is the pattern that permits
coordinated staleness. What makes the proposal work is its *anchor*.

**Superseded — the anchor should be the filesystem directly, not `REQUIRED_FILES`.**
The instruction previously read "anchor the assertion on `REQUIRED_FILES`, and the
reason is line 272, not line 1935". That works, but it *inherits* 272: if
`REQUIRED_FILES` ever stops being existence-checked, the guard silently rejoins the
silent class, and the change that did it would be nowhere near the guard. WS-6's
sharper form has no such dependency — assert `(ROOT / literal).is_file()` in the
test. One line, bound to the world with no intermediary. Every set is one refactor
away from being a claim about copies again; the filesystem is not a set.

**The precedent must be withdrawn, not qualified. It does not guard what this
document said it guards, and the hazard it names cannot occur.** The claim recorded
here was that `tests/test_rollout_trust_anchor.py` **5259–5271** is filesystem-anchored
via `ROOT.rglob` and guards the *other two entries of the same sparse-checkout list*
(`.gitattributes` and `.gitignore`, at `rollout-trust-anchor.yml` **33–34**), so it
should be cited for its anchoring and never copied for its shape. The projection
defect below was right. Everything else about it was wrong, on three further axes,
and each was established by execution rather than by reading:

```python
{path.name for path in ROOT.rglob(".gitignore") if ".git" not in path.parts}
| {path.name for path in ROOT.rglob(".gitattributes") if ".git" not in path.parts}
== {".gitattributes", ".gitignore"}
```

**Projection — recorded correctly before, and it is worse than "lossy".** Every
member of `rglob(".gitignore")` has `.name == ".gitignore"` by construction, so the
union is a subset of the target and equals it **iff at least one file of each name
exists anywhere under `ROOT`**. The assertion is therefore not a lossy exhaustiveness
check; it is *logically equivalent to two existence checks*, and no tree can make it
fail except one missing a name entirely.

**It does not guard the sparse-checkout entries.** Executed against a tree holding
**only** `services/ai-gateway/.gitattributes` and `services/ai-gateway/.gitignore`,
with no root copies at all, the assertion **passes**. So it does not establish that
the two files named at 33–34 exist. It would not notice their deletion. Since the
root `.gitattributes` is what pins `.github/trust/rollout-policy/allowed_signers`,
`approval.json` and `approval.sig` to `text eol=lf` (lines 4–6 of a 337-byte file),
the assertion that appears to protect the trust root's byte content is indifferent to
the removal of the file that fixes it. "Guards the other two entries" was this
document's sentence and it is false.

**Population — the instrument answered a neighbouring question, and the instrument
was mine.** This document justified the assertion's current truth by "the tree happens
to contain exactly two such files (enumerated at `824b4238`; `truncated: false`)".
That enumeration is the **tracked tree**, from the Git tree API. `ROOT.rglob` is a
**filesystem** walk. The two populations differ by every ignored and untracked file,
and `.gitignore` line **115** is `.pytest_cache/` — which `pytest` creates, together
with a `.gitignore` inside it. WS-6 found the third file present in a working tree
rather than in a synthetic one. A tracked-tree listing was used to warrant a claim
about a filesystem glob.

**And the repair inverts.** Comparing relative paths instead of names — the obvious
fix — fails on any machine that has run `pytest` and passes in CI, because
`rollout-policy.yml:24` invokes pytest with `-p no:cacheprovider` (and
`-c /dev/null`, `--noconftest`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`), so CI never
creates the file that breaks it. A test green in CI and red locally trains people to
dismiss local red, which is the same corrosion as a permanently red check one venue
over. **The repair that has neither defect is to draw the population from
`git ls-files`** — the tracked set is what a sparse-checkout pattern actually selects
from, so it is the population the claim was always about.

**Object — the hazard is in the pattern string, and the assertion inspects the tree.**
The comment at 5253–5256 says the assertion exists because `--filter=blob:none` plus
`persist-credentials: false` (line **29**) means "every file Git must read has to be
in the sparse set, or the step dies on an unauthenticated promisor fetch". With
`sparse-checkout-cone-mode: false` (**37**) the patterns are gitignore-style, and a
pattern with no slash matches at **any depth**. Verified in both directions on real
Git, against a filtered credential-free clone of a synthetic repository:

| patterns at 33–34 | `services/ai-gateway/.gitattributes` |
| --- | --- |
| `.gitattributes`, `.gitignore` (the current form) | **materialised** |
| `/.gitattributes`, `/.gitignore` (anchored) | **absent** |

So nested dotfiles enter the sparse set automatically and their blobs are fetched
during the credentialed checkout. The nested addition the comment exists to catch is
**already foreclosed by the pattern form**, and the assertion is not merely blind to
it — it is inert.

**Which relocates the load-bearing property onto something nothing asserts.** What
protects the anchor is that 33–34 are *bare basenames*. Anchor them and the negative
control above shows the nested files drop out, restoring the unauthenticated-promisor
failure — through an edit that reads as tidying. The only guard on pattern form is
5257–5258, `assertIn(materialised, checkout_block)` for each of the two names, and
that is substring containment: `"/.gitattributes"` contains `".gitattributes"`, so it
**passes on the anchored form**. Every existing assertion passes on the change that
reintroduces the failure.

**Conclusion, replacing the instruction that stood here.** 5259–5271 is not to be
cited as a precedent at all — not for anchoring, not for category. WS-6's rule is the
right one and it is this document's own *wrong axis* entry one step further out: the
assertion inspects the artefact (the tree) rather than the decision (the pattern
string that determines how the tree is read). **The `(ROOT / literal).is_file()`
instruction is untouched by all of this** — a single literal, no glob, no projection,
no population, so none of the four defects can reach it — and it needs no precedent
to stand. What the above adds is a *second* assertion for the same required check,
on the pattern form rather than on the tree: assert that the sparse-checkout entries
for `.gitattributes` and `.gitignore` are exactly those two bare basenames, with no
leading slash. That is the guard that keeps F-2 from returning.

Placement is now load-bearing rather than incidental: `root-rollout-tests` is a
required check and runs this file on the pull request, so the assertion blocks the
rename pre-merge — see the three-mechanism analysis above. **Mechanical correction
to that analysis, from WS-6 and verified:** `rollout-policy.yml` triggers on
`[push, pull_request]` (line 3) and its checkout at 13–16 sets
`persist-credentials: false` with **no `ref:`**, so on a `pull_request` event
`actions/checkout` takes `refs/pull/N/merge` — the **merge commit**, not the PR head.
The conclusion survives unchanged, because the merge ref contains the rename. It is
worth stating precisely because the two refs diverge whenever the base has moved, and
"the pull request's own head" is the wrong sentence to reason from next time.

**Cost re-checked and it is zero, as WS-6 says.** Only pins 408
(`verify_rollout_trust_anchor.py`) and 423 (`tests/test_rollout_trust_anchor.py`)
are touched, both already spent by the encoding; both digests were re-derived
from the blobs and are current. No third pin: the test already reads repository
files as text at 1941–1946 and already names `validate_repo.py` at 2025, 4048 and
4063, so the assertion needs no import, and `validate_repo.py`'s own pin at
431–432 is unaffected by being read.

**One live consequence in the silent class, low severity and conditional.**
`ROLLOUT_ONLY_PATHS:137` is consumed only at 509 as a set intersection. After a
rename in which only the loud site is repaired, the stale entry matches nothing and
the routing rule quietly stops covering the trust root, so a workflow could route
on the new name into credential-aware Adapter Tests. That makes the constant this
document already scored as the most dangerous candidate **also** a silent-drift
site — two independent defects in one enumeration.


