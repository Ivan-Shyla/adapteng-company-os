# Protection and friction audit

Every material protection mechanism found across the governed repositories,
classified by whether it earns its cost.

The question asked of each one is not "is this safe?" but **"what would go
wrong if this were removed, and is anything currently going wrong because it is
here?"** A control that cannot answer the first question is not protecting
anything. A control that answers the second badly is charging more than it is
worth.

Nothing in P0 is touched.

---

## P0 — keep, hard safety

These defend secrets, money, irreversible state or a real boundary. Keep all of
them. Where they cost owner time, the fix is fewer interactions, never less
protection.

| Control | Where | Why it stays |
|---|---|---|
| Secret scan, hard fail | platform, all PRs | Only thing standing between a careless commit and a leaked credential. Cheap, fast, never falsely blocking in practice. |
| Sensitive-reference validation | company-os CI | Enforces this repository's core promise: no resource identifiers, no credential values. |
| Company/personal isolation boundary | platform | A real data crossing between company and personal resources. The boundary is legitimate — only its blast radius is wrong (F-1). |
| Waiver pinned in code, not only in JSON | platform validator | Makes extending a data-boundary exception a reviewable code change. Good design. Keep exactly as is. |
| Fail-closed gateway configuration | ai-gateway | Missing or invalid FX, price version, credential or DSN stops startup. Prevents unpriced and unattributed spend. |
| Model allowlist, region and host pinning | ai-gateway | Rejects any host, region or model other than the audited ones. Bounds cost and keeps inference in the EU. |
| Single-use approval tokens, transactional outbox | migration 003 | Replay protection enforced in the database, where it cannot be bypassed by a caller. |
| Non-root, digest-pinned, hash-locked image | ai-gateway Dockerfile | Supply-chain integrity. No cost to autonomy. |
| Credential contents never read | PR #109 | Validates existence and readability only. Exactly right. |
| Ruleset denying force-push and deletion of `main` | all governed repos | Protects history. Costs nothing. |

---

## P1 — legitimate, automate it

Real checks that currently involve a human somewhere they add nothing. They
should run on their own and fail closed.

| Control | Current cost | Change |
|---|---|---|
| Unit, integration and PostgreSQL tests | none — already automatic | No change. Working as intended. |
| Schema and migration verification | owner performed the post-rollout production check by hand | Make it a read-only automated verification. This is what would settle drift items D-1 and D-2 permanently instead of re-litigating them each handoff. |
| Health and deployment polling | manual console watching | Belongs in the deploy automation (WS-B). |
| Coolify resource reconciliation | entirely manual console work | Automate against the API. The credential already exists (WS-B). |
| Read-only production inspection | asked for as an owner action | Explicitly AUTO under the autonomy policy. |

---

## P2 — redundant, remove or narrow

The checks are not wrong; their scope, coupling or ceremony is. This is where
the cost is being paid.

> **Status 2026-08-10.** F-1 and F-5 are **remediated** (platform PR #110,
> company-os PRs #41 and #45). F-3 is in progress. F-4 needed no code change —
> it was a false belief, not a control. F-6 needed no work at all. The findings
> are kept in full: an audit that deletes its own reasoning once acted on cannot
> be checked later, and the F-1 failure mode is one worth recognising on sight.
>
> **One correction to this audit's own reasoning.** F-1 framed the isolation
> check as too broad. It was, but it was also *too weak*: scoping it in #110
> left it advisory, so an n8n change under an expired waiver would have merged.
> Company-os PR #45 made it a required check. Narrowing a control's scope and
> strengthening its enforcement are not opposites, and treating "reduce
> friction" as always meaning "relax" would have opened a real hole here.

### F-1 — A lapsed n8n waiver merge-locks the entire repository

**Remediated by platform PR #110**, which scoped the check without weakening it
and added a scheduled job that warns 14 days before the next expiry, and by
company-os PR #45, which promoted the scoped job to a required check so the
boundary is enforced for n8n changes rather than merely reported. The underlying
isolation finding is still open and still reported; renewing the waiver remains
an owner decision. The analysis below is why.

**The single most expensive control in the system.** An expired waiver on one
n8n resource crossing makes `Validate repository structure and content` fail,
and that check is required by the ruleset. Every pull request in
`adapteng-automation-platform` is therefore unmergeable regardless of content,
including one that touches only the AI Gateway.

Worse, it arrived without warning: the waiver lapsed on a date, on a weekend,
and the first symptom was an unrelated pull request turning red.

**Do not** remove the check and **do not** drop it from the required list — it
also performs structural validation, deploy-spec validation, gateway hardening
validation and trust-root validation, all of which must stay blocking.

**Change:** separate the two concerns.
1. Keep `Validate repository structure and content` required and blocking.
2. Move n8n isolation into its own always-running job that is blocking for
   changes touching n8n, and reporting-only otherwise.
3. Add a scheduled run that warns **before** a waiver lapses, not after.

Renewing the waiver itself stays an owner decision — it is a data boundary
(P0). Decoupling it from unrelated engineering is not.

### F-2 — Ruleset-required checks and workflow-level gates duplicate each other

Approval requirements are asserted in workflow YAML *and* in the ruleset *and*
in runbook prose. The GitHub environments in the platform repository carry
`branch_policy` only — no required reviewers, no wait timers — so several
"owner approval" steps are self-imposed ceremony that GitHub is not enforcing.

**Change:** the ruleset is the single source of truth for what blocks a merge.
Workflow-level confirmation phrases are kept only where they guard a P0 action.

### F-3 — A trust-anchor gate that is deadlocked in both directions

**This entry was rewritten on 2026-08-10. The original diagnosis was wrong.**
It is preserved as a correction rather than silently replaced, because the
error is instructive: the loud symptom was not the defect.

`Verify exact current head from merged base` and `Base-trusted rollout
authorization` have failed on **every** pull request, on every branch, since
2026-08-06. Verified: last green run `2026-08-06T15:30:57Z`; **55+** consecutive
failures after it.

**What I originally wrote, and why it was wrong.** I attributed the failure to
a partial clone plus `env -i` scrubbing the credential, citing:

```
fatal: could not read Username for 'https://github.com'
fatal: could not fetch <object> from promisor remote
```

Those lines are real and reproducible. They are also **irrelevant to the
verdict**, on two counts. They do not come from `env -i` — the git calls at
`rollout-trust-anchor.yml` lines 82/84 sit *outside* the scrubbed block, which
opens at line 63 and closes with its command substitution at line 77; the
credential is absent because the checkout sets `persist-credentials: false`.
And they did not fail the step at all. Line 84 reads:

```sh
test "$(/usr/bin/git -C "$anchor_root" status --porcelain=v1)" = ""
```

The command substitution discards git's exit 128 and its empty stdout compares
equal to `""`, so a hard git failure was read as a clean worktree. The check
**failed open**. Line 82 has the same shape but fails *closed*, since empty
output cannot equal a 40-character SHA — the pair looks symmetric and is not.

**The actual defect** is a modelling bug in
`scripts/validation/verify_rollout_trust_anchor.py`. Line 2627 computes
`approval_paths_present = APPROVAL_PATHS & set(head_leaf)` — membership in the
head tree — and raises `approval.unexpected` when a PR carries no protected
change. PR #104 merged `.github/trust/rollout-policy/approval.json` and `.sig`
onto `main` at `2026-08-06T15:42:06Z`, eleven minutes after that last green run.
Every branch cut since inherits the receipt, so every PR trips it. The gate
tests whether approval material is *present*, not whether the PR *introduced*
it.

The same presence test is applied to the subject tree at lines 2649–2650 and
2893–2894, raising `approval.circular_or_stale`. So once any receipt had
merged, no owner-signed receipt could authorize anything either.

**That is the finding that matters, and it is worse than the one it replaces.**
This is not a noisy gate that misreports its failure class. It is a gate that
can no longer reach *either* terminal state: it cannot pass an ordinary pull
request and it cannot accept an authorization. It was non-functional, in both
directions, for four days, while appearing to be a working control.

Neither check is in the ruleset's required list, so they blocked nothing.

**Change:** judge approval material by what a tree *introduces* relative to the
merged base, not by presence; deletion is not introduction, since removing
material can plant nothing. Separate the verdicts — "could not determine" exits
75, "not authorized" exits 1, both still failing closed. Repair the swallowed
exit codes so an infrastructure fault can never again be read as success.

**Credit:** diagnosed by the WS-6 session, which rejected this brief's stated
root cause instead of implementing against it. Verified independently against
`main` before being recorded here.

**Resolved.** Platform PR #116 merged `2026-08-10T17:49:36Z`. The gate returned
its first green runs in four days at 17:52Z and 18:03Z, and now separates
"could not determine" (exit 75) from "not authorized" (exit 1), both failing
closed. Detail, the post-repair merge-race failure that turned out to be a third
conflation site, and the promotion criterion are in
[`current-state.md`](current-state.md) §12. Not promoted to required: the
criterion counts terminal verdicts of any class against the repaired verifier,
and stood at one when this was written — two as of 2026-08-11. Deliberately not
restated here beyond the figure, and even that is one copy too many: §12 is the
single source, and this sentence had to be corrected precisely because it kept a
number it had just declared it would not keep.

### F-4 — Owner approval for ordinary pull requests and CI reruns

All governed rulesets already require **zero** approving reviews. The habit of
waiting for the owner is not enforced by anything.

**Change:** covered by the autonomy policy. Merging a green pull request and
rerunning CI are AUTO.

### F-5 — Manual console deployment as the standard path

Five Coolify runbooks describe console clicking. No automation exists, while a
working credential sits unused in this repository.

**Change:** WS-B. The runbooks become the break-glass path, not the normal one.

### F-6 — FX treated as a governance programme
FX is already specified as operator-set configuration that is never looked up
live, with a pinned price version. It needs three values entered at deployment.

**Change:** none, beyond recording it. Do not build an FX workstream. Removing
work from the plan counts as progress.

### F-7 — The documented local check was weaker than the check that decides

Found 2026-08-10 while validating an unrelated change; fixed in the same pass.

`README.md` names the commands to run before opening a pull request and states
that CI runs the same ones. That sentence had quietly become false: `ci.yml`
had gained `test_rehearsal_contour` and `test_rehearsal_effective_repository`
while the README still listed three suites. Anyone following the documentation
ran a strictly weaker check than the one that decides mergeability, and learned
the difference only after pushing.

This is the cheapest kind of friction to fix and among the most expensive to
leave: it spends a full push-and-wait cycle to deliver information the
contributor could have had in seconds, and it teaches people that the
documented commands are approximate.

**Change:** the README now lists every suite CI runs — and, because a promise
that two files agree is worth exactly what checks it,
`scripts/test_pre_pr_commands_match_ci.py` parses both and fails on divergence
in either direction. Registered in `ci.yml` and in the README, including a case
asserting it lists *itself*, since a check nobody runs cannot fail. Verified
non-vacuous by deleting a module from the README and confirming the failure
names the missing suite.

**A related trap, deliberately left alone.**
`test_postgres_restore_scheduler_surface` fails hard on Windows — 3 failures
and 15 errors from `os.O_NOFOLLOW`, `os.mkfifo` and `os.symlink`, none of which
Windows supports. Every agent in this programme runs on Windows, so this looks
exactly like a broken repository and cost time twice in one day.

It is not a defect and it must not be "fixed". The module's own docstring
records the reasoning: `skipUnless` was rejected as *an invisible control*, so
the POSIX-only cases were isolated into a named module that CI runs
unconditionally on `ubuntu-latest`, the platform the exporter actually runs on.
Adding a skip marker would trade a loud, honest failure for a silent one. The
README already flags the line `# только POSIX`; the correct response is to run
the documented commands rather than `unittest discover`, which the README also
warns against and which is what produced the confusing result here.

**The general lesson, which cost two sessions today:** a test failing on a
developer's machine but green in CI is evidence about the machine until proven
otherwise. The other instance was a report that all seven migration digests
mismatched — alarming, and entirely a line-ending artifact of a Windows
checkout, with `.gitattributes` pinning `eol=lf` on only six paths. Check the
required check's status on `main` **before** reporting a data-integrity
problem.

**Third sighting, 2026-08-10:** the AI-gateway session reported that
`validate_ai_gateway.py` cannot pass on a Windows checkout, because migration
008's SQL is checked out CRLF and its sha256 never matches the LF digest
pinned in `apply_ai_gateway_008.py`. Same root cause, third session. The
digests are correct and CI is green; the local check is what is unusable. The
pins must not be "fixed" — they are the control. Widening `.gitattributes` to
pin `eol=lf` on the migration set is the only safe remedy, and it is cosmetic
relative to the confusion it keeps causing.

**Fourth sighting, and this one was my own tooling, 2026-08-10.** The
pre-commit scan I have been running before every control-plane pull request —
`Select-String -Path "*.md" -Pattern ([char]0xFFFD)` — reported mojibake on
nearly every line of `autonomy-policy.md`. It was a false positive on all of
them. Windows PowerShell 5.1's `Select-String` decodes files using the ANSI
code page, not UTF-8, so every `—` and `§` in a correctly-encoded file becomes a
replacement character *in the scan's own reading of it*. The file on disk is
clean. Verified by counting the actual `EF BF BD` byte sequences:

```powershell
$bytes = [System.IO.File]::ReadAllBytes($path)   # 0 hits across all five documents
```

**Use the byte check, not the text check.** A grep for a character cannot be
trusted when the tool doing the grepping is guessing the encoding. This is the
same failure class as the CRLF sightings above — a local tool misreading bytes
and reporting a content problem — and it is the reason the rule in §13 of the
state document is stated as *a control that cannot fail is not a control*: its
mirror image is equally dangerous. A check that fires regardless of the truth
trains you to ignore it, and I would have ignored it, and the one time it was
real I would have shipped the corruption.

**Fifth sighting, and this one exonerates the documentation — 2026-08-10.**
Running the pre-PR suite on Windows produced 18 failures in
`scripts/test_postgres_restore_scheduler_surface.py` (symlink chains, symlink
loops, group- and world-writable bits, absolute host unit roots). I confirmed
they are pre-existing by stashing my changes and re-running against the
pristine tree: byte-identical 3 failures and 15 errors, so nothing I had
touched was implicated.

They are also entirely expected, and **the repository already says so.** That
module's docstring states it is POSIX-only *by subject, not by choice* —
`scheduler_file_record` opens with `os.O_NOFOLLOW`, which does not exist on
Windows, and the fixtures need `os.symlink`, which Windows refuses without
`SeCreateSymbolicLinkPrivilege`. The author deliberately declined a
`skipUnless` marker, calling it "an invisible control", and isolated the cases
in their own named module instead. The README correspondingly splits that
module onto its own line, annotated `# только POSIX`.

**The defect was mine: I ran CI's single-command form instead of the documented
local one.** CI can run everything in one invocation because it runs on
`ubuntu-latest`; the README deliberately does not. I nearly "fixed" this by
adding platform skips — which would have overridden a correct, documented
decision with precisely the invisible control its author had rejected, and
would have silently deleted the only coverage `scheduler_file_record` has ever
had.

**Recorded deliberately as a negative result.** Every other entry in this audit
is drift, and a register that only ever finds fault trains its author to expect
fault. Here the documentation was already right, the design was already right,
and the operator was wrong. Read the docstring before you improve the test, and
run the procedure as written before you conclude it is broken.

**Sixth sighting — the fourth one recurring, which is the point.** Reading §13 of
`current-state.md` back through `Get-Content` on 2026-08-10 showed `Â§12` and
`â€”` throughout. This is the *same* PowerShell 5.1 ANSI-decoding artifact as the
fourth sighting, in a different cmdlet, against a file whose bytes I had already
verified clean. It is recorded not because it is new but because it recurred
within hours of being written down, against an author who knew about it — which
is the strongest available argument that the mitigation belongs in tooling rather
than in memory. **Always confirm with a byte-level `EF BF BD` count via
`[System.IO.File]::ReadAllBytes`; never trust a PowerShell 5.1 text read for a
mojibake verdict.** A file that displays wrongly and a file that *is* wrong are
indistinguishable on this machine without that check.

**Sixth sighting, different mechanism, same lesson — 2026-08-12.** A probe
assigned the repository to `$R` and then the run object to `$r`. **PowerShell
variable names are case-insensitive, so those are one variable**, and every
subsequent `"repos/$R/..."` interpolated a serialised hashtable into the URL. The
failure surfaced as six identical `unsupported protocol scheme ""` errors far
from the assignment, with the real output — `total=`, `failures=0` — looking like
a legitimate empty result. **Never distinguish two variables by case alone**, and
treat a `failures=0` that arrives alongside any error text as unread rather than
zero. Same family as the entries above: the shell did something other than what
the code says, silently, and the wrong answer was well-formed.

**Seventh sighting, within the hour, same shape — 2026-08-12.** Measuring the
bound in F-25, `[datetime]$run.run_started_at` parsed the API's `Z`-suffixed
timestamps into **local** time (CEST, UTC+2) while the comparison instant had
been converted with `.ToUniversalTime()`. Every figure came out shifted by
exactly 7200 s: the after-side gap read `+7528 s` instead of `+328 s`, and the
margin read `94.5×` instead of `117×`. **Both were plausible, neither was
flagged, and the qualitative conclusion was unchanged** — which is why it would
have shipped. **Normalise every timestamp with `.ToUniversalTime()` at the point
of parsing, never at the point of comparison**, and treat a figure that differs
from a correspondent's by a round multiple of 3600 as a frame mismatch until
proven otherwise. This is F-24's rule in a third substrate: *both sides of a
comparison must be read in the same frame*, where the frame here is a timezone.

**Eighth sighting, and this one attacks the remedy for F-20 — 2026-08-12.**
Verifying an edit by counting occurrences — the rule adopted after a duplicated
block shipped — `[regex]::Matches((Get-Content $f -Raw), '117×')` returned **0**
for a phrase that was present twice. Windows PowerShell 5.1 `Get-Content` decodes
a UTF-8 file in the system codepage, so every non-ASCII character (`×`, `—`, `’`)
becomes mojibake and any pattern containing one silently fails to match. **The
count and the file disagreed, and the count was the instrument of record.**

The hazard is not the encoding, it is the direction of the error: a count of `0`
reads as *my edit did not land*, whose remedy is to apply it again — **which
produces exactly the duplicate that counting exists to catch.** A verification
instrument that fails toward "absent" converts itself into the fault it was
adopted to detect. Use `Get-Content -Raw -Encoding UTF8`, or the ASCII substring
of the phrase, or the `grep` tool, which decodes correctly; and treat a count of
zero for text you just wrote as an instrument failure until the file itself says
otherwise.

---

### F-8 — A required check that is nondeterministic — P1

**`root-rollout-tests` produced two different verdicts for the same commit,
twice.** It is one of the five checks required by the platform ruleset, so
every occurrence randomly blocks a merge that has nothing wrong with it.

> **Superseded, and by 5×.** "Twice" was what two hand-found examples showed.
> A systematic enumeration of every `push`/`pull_request` twin pair finds
> **ten** such commits — see the divergence table below. Six of the ten are
> invisible at conclusion level, and `084c4d17` is one of those six: it was
> found only because the paragraph below this table went to run attempts
> rather than conclusions. That is the method that generalises to ten.

Evidence, from the API rather than from a report:

| Commit | `push` run | `pull_request` run |
|---|---|---|
| `2c9824ba` (PR #112) | attempt 1 **success** | attempt 1 **failure** |
| `084c4d17` (PR #114) | attempt 1 **success** | needed **attempt 2** |

Identical trees, opposite verdicts. Both were re-run to green, which is why
the current check-run conclusions all read `success` and the failures are
invisible unless you look at run attempts.

**Third instance, 2026-08-11. It refutes the folk remedy; it does not measure a
rate, and the sentence that said otherwise is retracted below.** Run
`31532517315` on `fca8f278`
(platform PR #127) carries **three** attempts of the same workflow on the same
bytes:

| Attempt | Started | Conclusion | Failing job |
|---|---|---|---|
| 1 | 20:21:34Z | **failure** | `root-rollout-tests` |
| 2 | 20:42:39Z | **failure** | `root-rollout-tests` |
| 3 | 23:43:34Z | success | none |

~~Two failures in three attempts on one commit. The earlier evidence shows the
check *disagreeing with itself*; this shows how often.~~ It also refutes the
folk remedy directly: attempt 2 **was** a re-run, and it did not clear.

**"Shows how often" is withdrawn, and it was wrong by roughly 14×.** Read as a
rate, 2-of-3 is **66.7%**. The measured per-attempt flake rate is **4.2–4.4%**
on the paired, decontaminated population — see the attempt-1 amendment in F-10,
which also shows why the 9.47% attempt-1 figure is not this quantity. At that
rate, `P(≥2 of 3) ≈ 0.5%`, about **1 in 180**. Unusual; not a measurement of how
often anything happens.

> **Corrected before this entry was merged: the first draft said 4.66%, about
> 1 in 158, and that denominator was selected on the outcome.** It pooled the
> five days of a peer's table, which were the days that *had* failures. Across
> all 11 days with runs, 5 carry zero failures and 93 runs sit on them. The
> defensible denominators are all 285 runs (3.51%) or the 119 decontaminated
> pairs (4.20% direct, 4.39% by inversion). The conclusion is unchanged and
> slightly strengthened — 1 in 180 rather than 1 in 158 — but the number was
> arrived at by inheriting somebody's table as a population.

A peer proposed 1-in-27 instead, using 08-11's local 11.5%. That is the better
correction of the two but it still conditions on the day the event occurred,
which selects on the outcome; and 08-11's 11.5% is 3/26 with a 95% interval of
[4.0%, 29.0%], which contains the pooled rate. Either way the conclusion is the
same: this run is an unusual draw, not a frequency.

**Three attempts is a sample of size three.** A ratio computed from 3 trials is
not a rate however carefully it is written, and "shows how often" is precisely
the phrase a reader would have carried off. What the run does establish needs no
rate at all and is unaffected by this retraction: **one re-run, still red.** The
folk remedy is refuted by a single counter-example, which is the correct
instrument for refuting a universal claim — and that was always the load-bearing
half of this paragraph.

The run's top-level conclusion now reads `success`, which is the second and
independent way conclusion-reading undercounts this defect. The known one is
that a re-run to green hides the occurrence. The other is that **a run carries
one conclusion however many attempts failed beneath it** — here one field
stands in for three attempts and two failures. Every census in this register,
including the ones taken to correct the first bias, used the *run* as its unit.
The correct unit is the **attempt**, and no census has yet used it, so every
occurrence count on record should be read as a lower bound.

**Re-verified 2026-08-10 against the API, and one alternative explanation is now
ruled out.** WS-6 asked — correctly, and without asserting it — whether the
`2c9824ba` split might be its newly-found `pull_request.state_invalid`
conflation rather than nondeterminism, since a `pull_request` run reading a PR
that merged underneath it is deterministic and merely timing-dependent. It is
not. The three runs on that commit separate cleanly:

| Run | Workflow | Event | Result |
|---|---|---|---|
| `31412597674` | Rollout Policy | `push` | success |
| `31412621141` | Rollout Policy | `pull_request` | **failure** |
| `31412620854` | Base-Trusted Rollout Authorization | `pull_request_target` | failure |

F-8's evidence is the first two: **the same workflow, on the same tree, at the
same attempt number, disagreeing.** The anchor conflation lives in
`verify_rollout_trust_anchor.py`, which runs only in the third — a different
workflow, on a different event, emitting a `rollout_trust_anchor.*` verdict
rather than a pytest assertion. The F-8 failure is `assert 1 == 90` inside
`test_production_lifecycle_cleanup_status_is_fail_closed`, driving the bash
script against a **stub** `gh`; it never reads live pull-request state, so
`fetch_live_pull_request` cannot be reached from it.

The third run failing *as well* is not a coincidence and not a complication: on
that date the anchor was permanently red for the presence-vs-introduced reason
(§12). Both defects were live simultaneously on the same commit, which is
precisely why they were worth separating rather than merging into one story.

**Recorded because a ruled-out hypothesis is worth as much as a confirmed one
here.** F-3 exists in this register because a confident, coherent, wrong
mechanism was published and believed. WS-6 had a coherent mechanism and asked
instead of asserting; the answer is no, and F-8 keeps its evidence.

~~The failure is always the same case of
`test_production_lifecycle_cleanup_status_is_fail_closed`: the `ok-fail-0-90`
case exits 1 with `lifecycle.run_selection_failed` instead of reaching the
cleanup path and exiting 90.~~

**"Always the same case" is false, and it pointed at the wrong mechanism.**
The test is always
`test_production_lifecycle_cleanup_status_is_fail_closed`, but the failing
case varies. Read from the attempt logs rather than from a summary.

**It is not a `parametrize`.** The file contains no parametrize decorator at
all. The five cases are a list literal walked by a `for` loop *inside a single
test function*, with a bare `assert` (`tests/test_migrate_approved_assets.py`
lines 886–908 at `feee3166`):

| # | `delete_mode` | `destroy_mode` | `watch_status` | expected |
|---|---|---|---|---|
| 1 | `ok` | `ok` | `0` | **0** |
| 2 | `fail_leave` | `ok` | `0` | **90** |
| 3 | `fail_absent` | `ok` | `0` | **90** |
| 4 | `ok` | `fail` | `0` | **90** |
| 5 | `fail_leave` | `ok` | `37` | **37** |

That structure is load-bearing for every inference below, because **the loop
aborts at the first failing case**. The case named in a log is therefore the
*minimum* failing position, not a sample of which case is broken.

**Every one of the five positions has now been witnessed.** Attempt-level
census of `rollout-policy.yml`, decoded from the job logs of all 26 failed
`root-rollout-tests` jobs:

| # | Case | Expected | Failures | Sole/first witness |
|---|---|---|---|---|
| 1 | `ok-ok-0-0` | 0 | **1** | `31414049256` att 1 |
| 2 | `fail_leave-ok-0-90` | 90 | **1** | `31404500180` att 1 |
| 3 | `fail_absent-ok-0-90` | 90 | **4** | `31027935863` |
| 4 | `ok-fail-0-90` | 90 | **4** | `31203208437` |
| 5 | `fail_leave-ok-37-37` | 37 | **1** | `31488794144` att 1 |

**11 occurrences, 7 branches.** Three of the five positions are singletons, and
each was read back from raw log context rather than a pattern match: position 2
is `assert 1 == 90` at 15:37:15Z on `feature/ai-gateway-owner-decisions`;
position 5 is **`assert 1 == 37`**, an assertion that occurs nowhere else in
the corpus and that only position 5 can produce.

**This table first read 18 occurrences, and the 18 was contaminated.** Seven of
them were a *deterministic* regression on `palinaruban-backup-evidence-lifecycle`,
not this flake, and the correction came from a peer's instrument rather than
mine — see "the both-hit cell" below.

**The sequential loop turns each failure into a batch of passes.** A failure at
position *k* means positions 1…*k*−1 **passed, in the same process, seconds
earlier**. Counting only within the failing runs:

| Case | Passed | Failed |
|---|---|---|
| 1 `ok-ok-0-0` | 10× | 1× |
| 2 `fail_leave-ok-0-90` | 9× | 1× |
| 3 `fail_absent-ok-0-90` | 5× | 4× |
| 4 `ok-fail-0-90` | 1× | 4× |
| 5 `fail_leave-ok-37-37` | 0× | 1× |

**Cases 1–4 each both passed and failed inside the failing runs alone** — no
green run is needed to make the argument. Case 5 was reached exactly once, and
failed. A defect keyed to a case's data cannot pass and fail the same case in
the same corpus, and cannot move between cases on **consecutive attempts of the
same commit 21 minutes apart** (`31532517315` att 1 → position 3, att 2 →
position 4). The pass column is unchanged by the contamination correction: all
seven discarded occurrences sat at position 1 and therefore contributed no
passes.

The invariant is on the other side of the assertion. **The observed value is
`1` in every occurrence; only the expected value changes with the case.** That
`1` is the shell's literal `exit 1`, which fires for any non-zero non-2 helper
status, so it carries no information about what the helper did — the causal
datum appears nowhere in the failure message. What every failing case shares is
the *selection path*, which every case traverses regardless of its data, and
which is where the timing defect lives. `ok-ok-0-0` is decisive on its own: it
injects nothing at all, so there is no fault for a data-dependent explanation
to be about.

This mattered. "Always the same case" invites the reading that one fixture is
malformed, which is a bounded, local, data-shaped problem; the truth is a
timing window on a path shared by every case, which is neither bounded nor
local. The wrong claim was not merely imprecise — it named the wrong class of
defect, and a reader acting on it would have gone looking at the fixture.

**The job name is not the fault, either.** Of the 26 failed
`root-rollout-tests` jobs in the corpus, only **11** are this flake. Eight are
two unrelated failures that happen to share the job:
`test_predecessor_collector_ast_transport_policy_accepts_exact_source`
(6, `assert [...body_invalid] == []`) and
`ApprovedAssetsRolloutReadinessTests::test_offline_constructor_writes_only_mode_0600_receipt`
(2, `repository.git_inspection_failed`). The remaining **7** are the
deterministic regression below. Counting by failing *job* therefore overstates
this flake by **2.4×**. It is the same error as the prefix-based log census
recorded elsewhere in this register — **a job is not an owner** — and it
inflates rather than hides, which is the direction that gets waved through.

**The both-hit cell, and how a peer's instrument corrected mine.** This repo
runs most workflows twice per SHA (`push` and `pull_request`, seconds apart), so
it manufactures independent duplicate trials that nobody has to request. WS-2
built a rate estimator on the *disagreement* cell of those twins —
`P(disagree) = 2p(1−p)` — and inverted it to **p ≈ 2.5–4.5%**. I had a direct
attempt-level count that needed no inversion and no independence assumption,
**18/292 = 6.16%**, which sat inside my own arrival-race prediction of 6–11%
and above theirs. Two instruments, two answers, and mine looked stronger
because it assumed less.

The cell that settles it is the one **neither** of us was using. Under
independence, the number of twin pairs where *both* twins fail is `p²·n` ≈
**0.46** — computed with the contaminated `p = 6.16%`; with the corrected rate
it is **0.23**, so the discrepancy below is larger, not smaller, than first
stated. Observed: **3** — p ≈ 0.011 against independence. All three sit on one
branch:

| SHA | runs | outcome |
|---|---|---|
| `b21b74c5` | 1 | failure |
| `5f5906da` | 2 | **both twins failure** |
| `1b2bc448` | 2 | **both twins failure** |
| `892822b4` | 2 | **both twins failure** |
| `dc93e727` | 2 | both twins **success**, 5 minutes later |

Four consecutive SHAs on `palinaruban-backup-evidence-lifecycle`, **every run
on every one of them failed**, all seven at position 1 (`ok-ok-0-0`, the
success path), on a branch whose subject *is* the lifecycle script — then clean
at the next commit. That is a regression introduced and fixed inside 32
minutes, not a timing window. The position mix is the tell: the cluster is
7/7 at one position, the genuine occurrences spread 1/1/4/4/1 across all five.

**Corrected rate: 11/274 = 4.01% per attempt**, which lands inside WS-2's
inverted range and near their whole-second model's 3.2%. **My arrival-race duty
cycle overestimates by roughly 2×**, and my direct count was the contaminated
one. An independent estimator built below puts it at **4.43%**; the register
carries **≈4% per attempt** as the figure, supported by two instruments that
share no failure mode.

The methodological result is worth more than the number. **Twin disagreement is
intrinsically immune to deterministic failures** — a deterministic fault hits
both twins and registers as *agreement*, so it is discarded automatically.
WS-2 claimed only immunity to the conclusion-overwrite problem; the stronger
property they did not claim is the one that made their estimate right. A raw
count of occurrences has neither immunity, which is exactly how mine absorbed a
regression and read it as flake. **The instrument that assumes less is not
automatically the one that measures better.**

**Correction to the paragraph above: the immunity WS-2 *did* claim is the one
they did not have.** I let stand their claim that twin disagreement is immune to
the conclusion-overwrite problem, on the reasoning that twins "require nobody to
press re-run". That is true of how the trials are *produced* and false of how
they are *read*. A conclusion-level detector compares each twin's final
`conclusion`; re-run either side to green and the pair reads as agreement.
Measured over every twin pair in the corpus:

| detector | reports | true positives | false positives | missed |
|---|---|---|---|---|
| conclusion-level twin | 5 | 4 | 1 | 6 |
| attempt-1 twin | 10 | 10 | 0 | 0 |

**Precision 4/5, recall 4/10.** The false positive is `ce0900dd` on
`agent/vertex-owner-dispatch-fallback`, where the `pull_request` side failed in
a *different job* of the same workflow — exactly the caveat WS-2 disclosed
themselves ("the rate is per-workflow not per-job"). That caveat was
load-bearing, not decorative — **and it is now measured and closed in F-10:**
`ce0900dd` is the only one of the eleven attributable to the second job, so the
per-job rate for the required check is 4.36%, not measurably below the
workflow figure. Load-bearing for *precision*, immaterial for the *rate*.
Note the two errors partly cancel in the reported
count, so the number looks less wrong than the instrument is.

**Every single-commit divergence in the corpus.** Two sessions found two of
these by hand and read them as a matched pair, one preserved and one re-run.
There are ten:

| SHA | push | pull_request | side hit | readable? |
|---|---|---|---|---|
| `8ee55f3f` | 31027935863 **failure** | 31027941321 success | push | yes |
| `836dca62` | 31028319019 success | 31028320004 success | pr | **no** — re-run |
| `f7d4e7e8` | 31203205170 success | 31203208437 success | pr | **no** — re-run |
| `016ee67b` | 31404500180 **failure** | 31404532145 success | push | yes |
| `2af34cf1` | 31410536810 success | 31410587868 success | push | **no** — re-run |
| `2c9824ba` | 31412597674 success | 31412621141 **failure** | pr | yes |
| `084c4d17` | 31414009822 success | 31414049256 success | pr | **no** — re-run |
| `e1d113c8` | 31483724882 success | 31483726386 success | pr | **no** — re-run |
| `bda389d2` | 31488790487 success | 31488794144 **failure** | pr | yes |
| `fca8f278` | 31532511735 success | 31532517315 success | pr | **no** — re-run |

Six of the ten are invisible to `gh run list`, the checks UI and the PR page,
and **all six are invisible because of a re-run**. The corpus holds exactly six
multi-attempt runs, so the correspondence is total: **the hidden-occurrence set
and the hidden-divergence set are the same set** — one defect, not two. Two of
the four readable divergences are `push`-side failures against a green
`pull_request`, visible in the API and invisible to anyone watching the PR.

**Who gets re-run.** Five of the six are on the `pull_request` twin, one on the
`push` twin. People re-run the check that gates the merge, so the `push` twin is
a nearly-pristine control channel — and its single contaminating event,
`31410536810`, is one the owning session disclosed voluntarily and unprompted.
That disclosure is worth more than they claimed for it.

**The estimator with both immunities.** Attempt 1 of each twin cannot be
overwritten by a re-run, and a deterministic fault hits both twins and lands in
the agreement cell. Over 118 clean twin pairs:

| cell | observed | expected under independence |
|---|---|---|
| both twins hit | **0** | 0.23 |
| exactly one | 10 | — |
| neither | 108 | — |

`d = 10/118 = 8.47%`; inverting `2p(1−p)` gives **p = 4.43%**. The both-hit
cell — the anomaly that exposed the deterministic cluster — is now **exactly
zero against 0.23 expected**. Independence holds once the regression is removed,
which retroactively licenses the inversion that the cluster had invalidated.

| instrument | survives re-run | rejects determinism | p |
|---|---|---|---|
| conclusion-level twin (WS-2) | ✗ | ✓ | **2.1%** |
| direct attempt count (mine, #138) | ✓ | ✗ | **6.16%** |
| direct count, hand-decontaminated (#139) | ✓ | ✓ | **4.01%** |
| attempt-1 twin (this entry) | ✓ | ✓ | **4.43%** |

The two instruments carrying one immunity each are wrong in **opposite
directions**, each by roughly the size of its own blind spot: re-run blindness
deletes failures and biases *down*; deterministic blindness adds them and biases
*up*. The two carrying both agree to within half a point.

The part worth carrying: **each session built the instrument whose blind spot
flattered its own hypothesis.** My arrival-race model predicted 6–11% and my
instrument returned 6.16%; WS-2's whole-second model predicted 3.2% and theirs
returned 2.1%. Neither blind spot was chosen — the bias is structural, not
motivated — but the effect is indistinguishable from having chosen one, and
**neither of us could have detected it alone.** The correction lives in the cell
that only exists once both instruments are built over the same pairs.

**The recall defect is confined to this workflow, and that is worse than if it
generalised.** The obvious next question is whether 4/10 recall degrades every
row of the cross-workflow table the detector was used to build. It does not.
All 18 workflows in the repository, every `push`/`pull_request` pair, attempt-1
conclusions:

| workflow | pairs | re-runs | detector reports | real | missed |
|---|---|---|---|---|---|
| **Rollout Policy** | 121 | 6 | 5 | **11** | **6** |
| Adapter Tests | 122 | 4 | 3 | 3 | 0 |
| Validate Repo | 375 | 2 | 1 | 1 | 0 |
| Secret Scan (hard fail) | 374 | 2 | 0 | 0 | 0 |
| 5 others | 54 | 2 | 0 | 0 | 0 |

The 11 counts divergence of *any* cause; **10 of the 11 are this flake**, the
eleventh being `ce0900dd`. So the detector scores **4/10 against F-8** and
**5/11 against divergence generally**. Both are stated because conflating those
two denominators is the error this register already carries twice.

**The prediction that recall loss tracks the re-run count is falsified.**
Adapter Tests has four re-runs and loses nothing. Hiding a divergence needs a
*conjunction* — attempt 1 failed **and** the re-run cleared **and** the twin
passed. What the four Adapter Tests re-runs actually were:

| SHA | attempt 1 | why it hides nothing |
|---|---|---|
| `d4c17314` | **success** | a re-run of an already-green run |
| `983fd124` (push) | failure | **twin also failed** — agreement, not divergence |
| `983fd124` (pr) | failure | **twin also failed** — same SHA, both sides re-run |
| `7066f555` | **cancelled** | not a failure at all |

Against Rollout Policy, where **6 of 6** are `attempt 1 failure → cleared → twin
passed`. That conjunction is precisely the signature of a genuine, clearing,
single-sided intermittent fault — which is to say, **the detector loses
divergences only where there is a real flake to lose.**

So the instrument is not uniformly biased; it is **accurate everywhere except at
the phenomenon it exists to measure**. Perfect recall on the workflows with
nothing to find, 45% on the one with something. A uniform bias is a calibration
problem and a constant fixes it. This cannot be fixed that way, because the
error is *conditioned on the presence of the signal*: the rows that look most
trustworthy are trustworthy for exactly the reason that makes the remaining row
untrustworthy. The published cross-workflow table is right in three rows of four
and wrong in the fourth by 2.2×, and the fourth is not the unlucky row — it is
the only one that had a flake in it.

**A summary field re-denotes on the time axis too.** Re-running leaves
attempt-level records intact — logs, conclusions and byte counts all survive,
and any claim that a re-run destroys evidence is withdrawn. But four top-level
fields move and one of them is a clock. On `31532517315`, read directly:

| field | attempt 1 | after the third attempt |
|---|---|---|
| `conclusion` | failure | **success** |
| `run_attempt` | 1 | **3** |
| `run_started_at` | 2026-08-11T20:21:34Z | **2026-08-11T23:43:34Z** |
| `created_at` | 2026-08-11T20:21:34Z | 2026-08-11T20:21:34Z |

The run object dates a 20:21Z failure at 23:43Z: the remediation clock in the
position where the occurrence clock belongs. `created_at` is the safe ordering
key **at the top level only** — per attempt it moves as expected (attempt 2
reads 20:42:41Z), and attempt 2's `created_at` is two seconds *later* than its
own `run_started_at`, so it is not a creation timestamp in the ordinary sense
either. This defect lands on the **same rows** as the `conclusion` defect, the
re-run ones, so the time axis loses no additional occurrences; it corrupts the
ones already lost, which is worse than losing them, because a corrupted row
still reads as data.

**Three of us hid occurrences by re-running, including me.** Seven of the 11
genuine occurrences sit inside runs whose final conclusion reads `success`, so
a conclusion-level census sees four — it loses **64%** of them. Two of the
seven are mine: I re-ran `31532517315` at 23:43Z to unblock #127, which
converted a run carrying **two** failed attempts into one that reads green. I
did this while holding the register that exists to detect exactly that. The
other sessions each did the same on their own branches — one of them found
their instance only by going looking for who owned the hidden set. Worth
stating plainly rather than attributing the practice to other people's work:
**the census's own authors are in the hidden set.**

**`31488794144` is more load-bearing than anyone holding it realised.** Two
sessions preserved that run red as evidence that the flake is real. It is also
the **sole witness of position 5** in the entire corpus, and the only
occurrence anywhere carrying `assert 1 == 37`. Re-running it would have
destroyed the only proof that the last case is reachable at all — a fact none
of the three of us knew when we agreed to preserve it.

**Why a transient becomes a hard failure.** In
`scripts/operations/authorize_approved_assets_phase.sh` the `select-queued-run`
retry loop treats **only** exit code 2 as retryable:

```sh
if [ "$selection_status" -eq 0 ]; then break; fi
if [ "$selection_status" -ne 2 ]; then
  printf '%s\n' "lifecycle.run_selection_failed" >&2
  exit 1
fi
```

and `approved_assets_github_metadata.py` returns 2 for exactly one condition,
`run_selection.zero`. Every other error — including anything raised by the API
and JSON layers — collapses to exit 1 and kills a required check with no
retry, thirty attempts notwithstanding.

**The trigger is UNCONFIRMED, and deliberately recorded as such.** The helper's
stderr is discarded by `2>/dev/null` in the same command substitution, so the
one datum that would name the cause — the `MetadataError` code — never reaches
the log. What can be ruled out by reading the fixtures:

- **not `run_selection.multiple`** — the fake `gh` returns exactly one run
  (`total_count: 1`, single element)
- **not `run_selection.zero`** — that maps to exit 2 and would retry, and in
  any case the filter is `created_at >= created_after` where both sides
  truncate to whole seconds and `created_at` is always the later real time, so
  truncation cannot invert the comparison

Which leaves an error raised outside the selection filter — most plausibly the
fake `gh` reading `$state/dispatch.json` and the helper then parsing empty or
partial output. **Plausible is not confirmed.** This audit has already been
burned once by a mechanism that was coherent and wrong (F-3); the next owner
should make the failure observable *before* changing behaviour.

**Order of work, and the reason for the order:** stop discarding the helper's
stderr and print the code on the failure path. That is purely additive, changes
no control semantics, and converts an unreproducible flake into a named error
on its next occurrence. Only then decide whether the retry contract should
widen. Widening it first would be changing fail-closed retry semantics in a
lifecycle script on a guess — and a hard failure on an unrecognised status is
defensible behaviour, not obviously a bug.

Dispatched as WS-9.

**Delivered as platform PR #121, and it did the right thing.** The brief ordered
observation before inference, and the pull request separates them explicitly:
an `OBSERVED` section for what was executed or read, an `INFERRED — not
confirmed` section for the mechanism, and a refusal to do the retry-widening
step at all. `2>/dev/null` becomes `2>"$temp_dir/run-selection-error"` — a file,
deliberately not `2>&1`, so the command substitution's stdout stays clean and
`run_id` is uncontaminated — and the non-retryable path now prints the helper's
exit status and error code, bounded to 512 bytes through a printable whitelist.
+19/−1 across two files. Exit code, retry behaviour, attempt count and the whole
success path are unchanged.

It also found a **sufficient** mechanism and quantified it: `fetch_all` performs
a re-verification pass that re-fetches every page and compares a sha256 digest
of the projected items, and `_project_run` includes `created_at`, which the test's
fake `gh` regenerates from the clock on every invocation. Two identical requests
either side of a whole-second boundary therefore differ, and `fetch_all` raises
`github_metadata.pagination_race` → exit 1 → the `-ne 2` branch → the observed
signature. Measured 11/300 (~3.7%) per call with the real clock and simulated
latency; 0/300 with `created_at` frozen. That matches the archived record
field-for-field — and it is still labelled unconfirmed, correctly, because the
code was discarded at the moment of failure and cannot be recovered from the
archive. The pull request is the instrument, not the conclusion.

**The structural finding, which is the part that matters here.** Both files it
must touch are in `PROTECTED_EXACT_PATHS` — verified directly against
`verify_rollout_trust_anchor.py` on `main`:
`scripts/operations/authorize_approved_assets_phase.sh` and
`docs/runbooks/migrate-approved-assets.md`. So do the two paths any deeper fix
would need, including `approved_assets_github_metadata.py` itself. The trust
anchor therefore refuses the change with
`rollout_trust_anchor.unauthorized.approval.commit_delta_invalid`, which is
**correct behaviour, not a defect** — the change really does touch the rollout
boundary.

**Consequence: F-8 cannot be closed by any agent.** A reliability defect in a
*required* check sits entirely behind an authorization gate that only an
owner-signed receipt can open. Nothing is wrong with either control on its own;
the coupling is what bites. All five required checks are green on #121, so
GitHub will permit the merge — the anchor checks are advisory — but merging past
a correctly-functioning authorization refusal would hollow out the control this
program spent a day repairing, and would destroy the argument for ever promoting
it to required. **Left open for the owner.** This is now an owner-action item,
not agent work.

**And the underlying defect is locked three deep, which is the more interesting
finding.** WS-9 identified the harness fault — the fake `gh` regenerates
`created_at` from the clock on every invocation — implemented the fix (record it
once at dispatch and reuse it), verified it against the committed fixture, and
then **reverted it rather than ship it**. That was the right call, and the reason
is structural. Verified directly against `verify_rollout_trust_anchor.py` on
`main`:

1. `tests/test_migrate_approved_assets.py`, which holds the defective fixture, is
   in `PROTECTED_EXACT_PATHS`.
2. It is *also* digest-pinned in `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256`
   (`1f18157989b9…`), so editing it trips `closure.dynamic_import` until the pin
   is updated.
3. The pin lives in `verify_rollout_trust_anchor.py`, which is itself a protected
   path **and pins its own source** (`0cf23bf17d39…`) — so re-pinning the test
   changes the verifier, which then requires re-pinning itself.

`scripts/validation/approved_assets_github_metadata.py` is pinned too, so the
alternative repair site is closed by the same mechanism.

**A digest pin intended to protect a test file also freezes any defect inside
it.** The control is not wrong — pinning reviewed sources is exactly how you stop
a test being quietly weakened — but the consequence nobody designed is that a
known-defective fixture in a *required* check cannot be repaired by anyone
without an owner-signed receipt and a deliberate re-pinning of the verifier
against itself. Both the diagnostic instrument (#121) and the actual fix are now
waiting on the same signature.

This is the sharpest instance yet of the pattern in `current-state.md` §15: the
protections are individually correct and collectively immovable. It should be
weighed when that policy question is settled, because it is no longer academic —
it is blocking two distinct repairs to a check that randomly blocks merges.

**The two locks are not equivalent, and the difference is decision-relevant.**
WS-9 put it better than my paragraph above did, so its formulation stands:
**path-protected means "needs a signature"; digest-pinned means "needs a
signature *and* a verifier change in the same breath."** That is why #121 could
be built at all — its `.sh` and runbook are path-protected only, so a signed
approval lands them unchanged — while the fixture fix cannot be, because the
file carrying the defect is pinned inside the very file that authorizes edits.

An owner reading the decision list should therefore not treat items 3 and 5 as
one act. Signing #121 is ordinary: construct the receipt, merge, done. Unlocking
the fixture requires deciding that the verifier may be re-pinned against itself,
which is a change to the trust boundary's own machinery. They can be taken at the
same sitting; they are not the same decision, and item 5 is the larger one.

### The message names the wrong subsystem — 9 of 11, one label

WS-1, doing evidence-only work under observe-first orders, noticed that
`lifecycle.run_selection_failed` points investigators at selection logic. That
observation is correct and it is the origin of everything below. **The figure I
first attached to it was not** — it was corrected by WS-9 and then corrected
again by checking WS-9, and the arithmetic is set out here in full because this
register is worth exactly as much as its numbers survive being checked.

`approved_assets_github_metadata.py` raises `MetadataError` at **34 sites**, and
the handler at line 510 catches the class, not a code family:

```
except MetadataError as exc:
    print(exc.code, file=sys.stderr)
    return 1 if exc.code != "run_selection.zero" else 2
```

Two things have to be got right to turn that into a number, and my first pass got
both wrong.

**Sites are not codes.** The 34 raise sites carry only **20 distinct codes**
(`github_metadata` 11, `runner_selection` 5, `run_selection` 3,
`secret_selection` 1). Reporting "33 codes" counted duplicated raise sites as
separate causes — `github_metadata.page_invalid` alone is raised at seven sites.

**Exit code is not reachability.** All 33 non-`run_selection.zero` sites do exit
1, but exiting 1 is not the same as printing *this* message. The subcommands are
mutually exclusive branches of one `if`/`elif`/`else`, and each is invoked from a
different line of `authorize_approved_assets_phase.sh`. Only
`select-queued-run` (`.sh:248`) reports through
`lifecycle.run_selection_failed` (`.sh:262`). The `runner_selection.*` family
belongs to `verify-staged-runner` and `assert-runners-absent`, which report
through their own messages — so including those 7 sites inflated the figure with
a family that **cannot reach the label at all**.

Reachable from `select-queued-run`, by call tree — `_repository_endpoint`,
`fetch_all(…, "workflow_runs")` with the default `identity_key="id"`, and
`select_run`:

| | distinct codes |
| --- | --- |
| Reachable | 12 |
| less `run_selection.zero` (exits 2 → retries → `lifecycle.run_not_found`) | **11 print the message** |
| of which genuinely about run selection (`multiple`, `created_after_invalid`) | 2 |
| **`github_metadata.*` transport codes wearing a run-selection label** | **9** |

The nine: `api_failed`, `page_invalid`, `bytes_exceeded`, `pagination_race`,
`item_id_invalid`, `duplicate_item`, `results_exceeded`, `truncated`,
`repository_invalid`.

**Two `github_metadata.*` codes are *not* reachable here**, which is where WS-9's
otherwise-correct recount ran two too generous. `item_identity_invalid` (269) is
in the `elif` branch taken only when `identity_key != "id"`, which is the secrets
path; `collection_invalid` (203) fires only on an unrecognised collection string,
and all three call sites pass a known literal.

**The conclusion never depended on the inflated denominator.** "Correct for at
most 2" was right when the figure was 33 and is still right at 11. The label is
wrong for **9 of the 11** codes it is printed for — 82%, not 94%. It is not
merely uninformative; it actively misdirects, and it has already cost
investigation time here: both sessions that looked at this went to run-selection
logic first, because the message told them to.

### The same defect sits at the runner call site, and it is not smaller

Quantifying the second discard site (the one #121 leaves alone) shows it is the
same shape. `.sh:383` prints `lifecycle.runner_registration_invalid` for **any**
non-zero exit of `verify-staged-runner`. Reachable there: 9 `github_metadata.*`
transport codes plus 4 genuine `runner_selection.*`
(`run_id_invalid`, `labels_invalid`, `release_count_invalid`,
`binding_mismatch`) — **13 distinct codes, 9 of them mislabelled.** The same
nine transport failures, asserting something false about runner registration
instead of about run selection.

So the two sites are near-identical in both defects — discarded stderr *and* an
over-claiming label — and folding line 379 into the #121 receipt now has a
number behind it rather than a symmetry argument.

### That this register carried a false number is the finding, not an aside

The §13 entry warning that a control can "go red and say something false about
why" was itself, for five commits, stating a false number about how often that
happens — inflated by counting raise sites as codes and by including a family
that cannot produce the message. It was checkable the whole time; nobody had
checked it, including me, because it was mine.

It is corrected in place rather than quietly overwritten because F-3 sits in this
same document as the worked example of a confident wrong brief, and a register
that silently repairs its own errors provides no evidence about how often it
makes them. WS-9's reason for pushing the correction is the one to keep: a
smaller figure that survives the owner checking it is worth more than a larger
one that does not.

This belongs to the §13 family but is a distinct member: not a control that
cannot go red, and not a control that goes red without saying why, but **a
control that goes red and says something false about why.** Of the three, this is
the most expensive, because a silent failure invites investigation while a
confidently mislabelled one redirects it.

### Correction: WS-9's hypothesis is *not* ruled out, and no crash is required

WS-1 concluded from the same trace that the observed exit 1 "was not a
run-selection outcome at all — it was an uncaught exception or a crash in the
helper or the stub." The first half is right; **the second half is wrong**, and
recording it unqualified would have retired a live hypothesis.

`github_metadata.pagination_race` — WS-9's proposed mechanism — is raised as a
`MetadataError` at three sites inside `fetch_all` (lines 257, 290, 318). Being a
`MetadataError`, it is *caught* at line 510, and being not-`run_selection.zero`
it returns 1, which bash reports as `lifecycle.run_selection_failed`. No crash,
no uncaught exception, no stub defect is needed anywhere in that chain. The
observed signature — exit 1, empty stdout, that exact message — is exactly what a
**handled** `pagination_race` produces.

WS-1's ruling-out addressed a *different* race: `dispatch_after` is captured
before dispatch truncated to whole seconds and compared non-strictly
(`created_at >= created_after`), so same-second is handled correctly. That is
sound, and it is not WS-9's mechanism. WS-9's race is in `fetch_all`'s
re-verification pass, which re-fetches every page and compares a sha256 of the
projected items — including `created_at`, which the fake `gh` regenerates from
the clock on each call.

So the two sessions ruled on two different races and only one of them was ever
on the table. Net effect on the record: WS-9's hypothesis moves from "a
sufficient mechanism" to "a sufficient mechanism with a verified end-to-end route
to the observed message". **It remains INFERRED, not confirmed** — the stderr
that would name the code was discarded, so this still cannot be settled
retroactively. But it is now the leading candidate rather than one of several.

### The archived attempt is recoverable — a general method for F-8

WS-1 recovered the discarded run: for run `31410536810`, `attempts/1/jobs` and
then `actions/jobs/<id>/logs` still serve the *original* failing attempt even
after a re-run overwrote the surface conclusion. That matters beyond this
instance, because retry-to-green is precisely how this flake has been handled all
along, and everyone involved assumed the evidence was gone with it.

The limit is worth stating so nobody over-trusts it: the archived log recovers
what the job *printed*. It cannot recover what was routed to `/dev/null` before
it ever reached the log. So the attempt survives; the specific error code still
does not.

**Operational instruction for the next occurrence, from WS-9.** Once #121 lands,
the confirming datum is the `lifecycle.run_selection_stderr=` line. **Capture it
before re-running to green.** A re-run erases it from the surface conclusion and
from `--log-failed`, leaving it only in `run_attempt` and the archived log — and
the reflex on a flaky required check is to hit re-run immediately, which is
precisely how this went undiagnosed for as long as it did. The recovery path
above exists; do not make it necessary.

### #121 fixes one discard site of two

Verified in the diff: #121 changes line 254 (`--created-after … 2>/dev/null`) and
leaves **line 379 (`--expected-name "$expected_runner_name" 2>/dev/null`)
untouched.** The runner check discards its stderr by the same pattern, and
reports through its own over-claiming label — `lifecycle.runner_registration_invalid`,
wrong for 9 of the 13 codes that reach it, as quantified above. Both defects, at
both sites.

WS-1 derived the same remedy independently and declined to act on a script under
another session's investigation — which was the right call procedurally, and
which also means two sessions reached the same fix without conferring. That
strengthens the case for the receipt rather than weakening it.

**Practical consequence for the owner:** #121 is already waiting on a signature.
Extending it to line 379 costs nothing extra at signing time and avoids needing a
second receipt later for an identical one-line change. Recorded here rather than
acted on, because amending #121 means touching a protected path — the same lock
described above.

**The two defects are separable, and only two sites carry both** (WS-9's
refinement, verified against `main` at `824b4238`). The helper is invoked from
**eight** sites, in three groups:

| Sites | Redirection | Consequence |
| --- | --- | --- |
| 56, 61, 71 | `>/dev/null 2>&1`, inside the cleanup trap | both streams discarded; defensible for cleanup |
| 156, 161, 168 | `>/dev/null` only | label over-claims, but the real `MetadataError` code is in the log directly above it — **mislabelled yet diagnosable** |
| 248/254, 376/379 | `2>/dev/null` while capturing stdout | **blind**: the code is destroyed, not merely mislabelled |

So the over-claiming *label* is a repo-wide naming problem across five lifecycle
labels, while the *blindness* is exactly two sites — #121 closes one and 379 is
the other. That is a tighter scope than "both discard sites carry both defects",
and it is checkable.

**Superseded on scope, not on substance — see the whole-file scoring below.** Every
sentence above is about the eight *metadata-helper* invocations, which was the
population under discussion at the time. Scored across all eighteen
stderr-discarding sites in the file, the blindness is **seven** sites, not two:
the two helper sites named here plus 353, 359, 367, 388 and 151. The narrow
statement is true of its population and reads as a statement about the file, which
is the failure this document has now recorded five times; it is left standing with
this pointer rather than rewritten, because the sentence is the evidence.

**The definitive coordinates, stated so no convention is implicit.** Two numbers
have been used for the "first" number of each site — the `python -I` invocation
and the `2>/dev/null` line — and citing one against the other's convention makes
two correct records look contradictory. All three lines, both trees, verified by
reading each file:

| Site | Tree | `python -I` call | stderr redirect | label `printf` |
| --- | --- | --- | --- | --- |
| `select-queued-run` | `main` | 248 | 254 | 262 |
| `select-queued-run` | `f0a2d17` | 249 | 255 (`2>"$selection_error_file"`) | 269 |
| `verify-staged-runner` | `main` | 376 | 379 | 384 |
| `verify-staged-runner` | `f0a2d17` | 385 | 388 | 393 |

**A single-number delta is itself a citation error when the insertion is
interior.** WS-9 established the offset mechanically — `main` 409 lines,
`f0a2d17` 418, `--numstat` 10/1, net +9 — and derived 388 = 379 + 9 and
393 = 384 + 9. Both are right, and they are right by luck: the runner site lies
below *every* line #121 adds, so the file-level net happens to be its local
offset. The select site does not. #121 inserts `selection_error_file=` at 245 and
replaces a two-line failure block with a ten-line one at 263–272, so the local
offset is **+1** at the call and redirect (248→249, 254→255) and **+7** at the
label (262→269). Applying the headline +9 to the select site would have been
wrong by **eight** at the call and redirect (257 and 263 against 249 and 255) and
by **two** at the label (271 against 269).

*That sentence previously read "wrong by eight", full stop.* True of two
coordinates out of three, false of the third, and WS-1 independently described the
same error as "off by two" — citing the label while this document cited the call.
Two correct magnitudes, different coordinates, appearing to contradict: the exact
failure this section exists to eliminate, reproduced inside the paragraph
describing it. A single magnitude cannot summarise an interior insertion for the
same reason a single offset cannot, and the fix is the same one — say which
coordinate.

The general form is the §13 one again: a file-level `--numstat` enumerates *net
lines changed*, which cannot answer *where line N moved to*. It was a safe
instrument for the question asked and an unsafe one for the adjacent question,
and nothing in the number says which case you are in.

One consequence for anyone grepping: `f0a2d17` still contains **two**
`2>/dev/null` occurrences, at 264 and 388, exactly as `main` contains two at 254
and 379. A raw count therefore reads *unchanged* while the blind sites have gone
**2 → 1**. The select site did not lose its redirect; it changed target, to
`2>"$selection_error_file"`, and 264 is a new occurrence of the string doing
something structurally opposite.

**The obvious audit predicate does not separate them, and it must.** "Does this
discard a helper's stderr while capturing its stdout" matches 264 as well as the
real sites: `selection_error="$(head -c 512 < "$selection_error_file"
2>/dev/null | tr ... )"` captures `head`'s stdout and discards `head`'s stderr.
Anyone auditing by that rule finds 264, calls it a defect, and "fixes" the
degradation path #121 deliberately built.

**The predicate this document proposed instead was worse, and the error is
mine.** It read *is the failure of this command observable anywhere downstream?*
— and the answer at every site is **yes**. Verified at `824b4238`: the select
site's status is checked at 261 and labelled `lifecycle.run_selection_failed` at
262; the runner-verify site is labelled `lifecycle.runner_registration_invalid`
at 384; the runner-start site is labelled `lifecycle.runner_start_failed` at 392.
All three exit non-zero. So the predicate does not merely admit a false positive
at 264 — **it clears all three real sites**, and an auditor applying it as written
would conclude #121 fixes a non-defect. The sentence that made it appear to work
("at `main` 254/379 and `f0a2d17` 388 there is no downstream name") was false, and
it was written in the same paragraph as the predicate it was needed to support.

**What is actually destroyed is the cause, not the failure.** Every one of these
sites announces *that* it failed and destroys *why*. #121 does not add the failure
label — 269 on the branch is the same label `main` already prints at 262. What it
adds is `lifecycle.run_selection_stderr=`, the account of the cause. So the
working predicate has to be about information, not about visibility:

> **Does discarding this stream destroy the only account of why the failure
> happened?**

Scored against all four sites: `main` 254, `main` 379 and 388 — yes, the label
names the failure and nothing names its cause. `f0a2d17` 264 — no; what is
discarded is `head`'s own complaint about retrieval, the account of the cause
lives in the file and is printed at 271, and the retrieval failure itself renders
as `(none)` via line 268. Four for four.

**That is WS-9's screen, which WS-9 demoted.** Offering *does the discarded stream
carry the diagnostic, or metadata about retrieving it?* as a fast triage
subordinate to the observability test, WS-9 had the ordering backwards, and so did
this document in accepting it. The information question decides; the visibility
question decides nothing, because these scripts are careful about labels and
careless about causes, so visibility is uniformly present and carries no signal.

**A structural consequence for how the audit is run.** None of these verdicts can
be reached at the redirect site. 264 is safe because of lines 268 and 271; 254 is
defective because 261–263 contain no cause. Both judgements live five to seven
lines away, in different commands. A grep for `2>/dev/null` is therefore the only
practical way to *find* candidates and cannot *decide* any of them — the search
must be line-granular and the decision must be block-granular, and that mismatch
is a property of the problem rather than a lapse. Stated as procedure: the grep
yields candidates; each candidate is settled by reading forward to wherever the
captured value is consumed or printed, and asking what the reader of that output
would learn about the cause.

That is the same distinction the whole finding rests on, applied one level down:
what matters is not whether a stream is discarded, nor whether the failure is
announced, but whether anything downstream can still say *why*. Stated as "count
the redirects" the rule is cheap and wrong; stated as "find the failures whose
cause nothing downstream can recover" it is the rule #121 implements.

### Scoring the predicate across the whole file — the scope is seven sites, not two

Everything above scored the predicate against the four sites already in the
discussion. WS-6 then scored it against **every** stderr-discarding site in
`scripts/operations/authorize_approved_assets_phase.sh`, which is the right move
and moves the number a long way. Re-enumerated here from the blob rather than from
the report: `824b4238` blob `251e8218`, 12417 bytes, 409 lines, pure LF, sha256
`4c66e778`. **Eighteen sites discard stderr** — every line carrying a `2>`
redirect. WS-6's six-way scoring is reproduced exactly; two corrections follow.

| Verdict | Sites | Why |
| --- | --- | --- |
| **Destroys the only account** | 254, 353, 359, 367, 379, 388, **151** | labels the *what*, discards the *why* |
| Cleanup trap | 48, 53, 59, 64, 68, 72, 76, 78 | defensible as a redirect; see the collapse below |
| Self-evident | 108, 238 | the label alone identifies the failure |
| Excluded by "only" | 396 | `gh run watch`; the run's own logs are another account |

Three sites — 157, 164, 171 — carry `>/dev/null` **without** `2>&1` and so are not
in the population at all; their `MetadataError` survives on stderr. They are the
control group that shows the predicate is discriminating rather than matching
every redirect, and the same role is played by 396 from the other direction: it
discards both streams and is still not a defect, because the account survives
somewhere else. A rule that excludes nothing would be worthless here, and this one
excludes on two independent grounds.

**Correction one: 151 belongs with the six, making seven, and it is the worst site
in the file.** `gh auth status >/dev/null 2>&1` at 151 has **no `|| { … }`
handler**. `set -Eeuo pipefail` is set at line 2, and 151 falls outside every
`set +e` window (45–82, 246–257, 365–369, 374–382, 387–390, 395–398), so a failure
there terminates the script through `set -e`. Both streams are discarded and no
`lifecycle.*` token is printed. Every other failure in this file prints a name;
this one prints nothing, so the operator sees a bare non-zero exit and can locate
it only by the *absence* of a token. WS-6 filed it as self-evident on the strength
of its neighbours 108 and 238 — but both of those print a label (`lifecycle.tool_missing`
at 109, `lifecycle.dispatch_failed` at 239) and 151 does not. The group label is
true of two of its three members, and the one it is false of is the site where the
predicate bites hardest.

**What the operator actually sees there, traced by WS-1 and verified verbatim at
`824b4238`.** The failure does not simply vanish — it goes to the `EXIT` trap:

```
86  on_exit() {
87    original_status=$?
88    trap - EXIT INT TERM HUP
89    cleanup_status=0
90    cleanup_resources || cleanup_status=$?
91    if [ "$cleanup_status" -ne 0 ]; then
92      printf '%s\n' "lifecycle.cleanup_failed" >&2
93    fi
94    if [ "$original_status" -ne 0 ]; then
95      exit "$original_status"
96    fi
97    if [ "$cleanup_status" -ne 0 ]; then
98      exit 90
99    fi
```

`lifecycle.cleanup_failed` at 92 is the **only** token reachable on the 151 path,
and it fires on cleanup's status while the exit code stays `original_status`. So
when authentication fails *and* cleanup then also fails, the operator gets a token
naming cleanup and an exit code that is neither 90 nor self-describing.

**WS-1 called that "a false what". It is worth being exact, because the label is
true.** `lifecycle.cleanup_failed` prints if and only if cleanup really failed. It
is a true statement, printed on the correct stream, about a real event — and it is
not about the question the operator is asking, which is why the *run* failed. That
is verbatim the unifying form of `current-state.md` §13: an instrument returning a
true statement that is not about the question being asked. **The register was built
from agent reasoning; this is the same shape in the platform's own error
reporting**, which is the first evidence that its diagnostic — say what the output
is true of, then compare that to the question — is a test for artefact design and
not only for investigation.

**The information is not destroyed, it is split across two surfaces**, exactly as
with the trust anchor's stderr-versus-check-run split in §12a. The exit code is the
discriminator: `90` means cleanup was the sole failure, any other non-zero means an
earlier failure occurred and went unnamed. So the *pair* is unambiguous and the
token alone is not — and an operator reading the token as the reason will be wrong
without anything on screen contradicting them. That is the precise sense in which
151 is the worst site: every other member of the group degrades *what and why* to
*what*, whereas 151 degrades to nothing, or to a true statement about something
else standing in the place where the reason would be.

**Correction two: the cleanup group's redirect is defensible and its aggregation
is not.** All eight sites feed one variable — `[ "$?" -eq 0 ] || cleanup_failed=1`
after each — and `cleanup_resources` returns that single boolean at line 83. So
the *fact* of a cleanup failure is preserved and propagated, which is why the
redirect is defensible: suppressing cleanup noise keeps it from masking the real
error. What is destroyed is *which* cleanup failed. A residual authorization secret
(48, 59), a residual reviewed-evidence secret (53, 64), a **still-registered
self-hosted runner** (68, 72) and a leftover temp directory (76, 78) are
indistinguishable in the output. The first three are security-relevant residue and
the last is housekeeping, and they arrive as the same bit. This is the same shape
already recorded twice elsewhere — the `TrustError` outcome collapse in
`current-state.md` §12a, and the lifecycle labels here — a code carrying neither
value. It is a separate finding from the discard and should not be folded into it:
un-discarding the eight streams would not fix it, and naming the resource in
`cleanup_failed` would fix it without touching a redirect.

**Two remediation classes, and the axis that is missing from them.** WS-6's split
is correct and useful: 254 and 379 capture stdout into a variable
(`selection_…="$( … )"`, `runner_id="$( … )"`), so `2>&1` would contaminate the
captured value and the #121 temp-file technique is required; 353, 359, 367 and 388
discard both streams with no capture, so the fix is simply dropping `2>&1` and
letting stderr through. Four of the six are therefore cheap. **But the capture axis
is not the axis that decides whether the change is safe.** 353 and 359 are
`gh secret set` — the only two sites in the file whose *input* is secret material.
Whether that command's stderr can echo anything derived from the value is a
question the capture axis cannot ask, and neither WS-6 nor this document has
checked it. It is very likely fine, since the value is encrypted client-side before
transmission; the point is that "cheap" was concluded from a classification that
does not range over the risk, so those two need one deliberate check that the other
four do not.

**A rule the frame collision forces, and it is not optional.** WS-6 flagged that
its earlier `f0a2d17 388` and this document's `main 388` are **different sites**:
at `f0a2d175` line 388 is `--expected-name … 2>/dev/null` (verify-staged-runner,
which on `main` is 379), and at `824b4238` line 388 is
`"$runner_start" … >/dev/null 2>&1`. Both records are correct. #121's +9 shift is
exactly what makes them land on the same integer. Verified in both files. So:
**a line number in this finding is meaningless without its ref, and every table
must carry the ref in its header** — not as tidiness but because the shift is large
enough to produce a collision and small enough that the two readings look like a
contradiction rather than a mismatch.

**Counts, which are the same in both trees and should not be read as "no change".**
`824b4238` has 18 stderr-discard sites; `f0a2d175` also has 18 — #121 converts 254
from a discard into a capture (`2>"$selection_error_file"` at 255) and introduces
one new benign discard (`head` at 264, already ruled non-defective). The raw count
is therefore flat while the defect count goes **7 → 6**. Anyone auditing this by
counting redirects would conclude #121 changed nothing.

**One unrelated observation from the same enumeration, recorded because it will not
be found by anyone looking for discards.** Line 168 is
`python "$metadata_helper" assert-secret-absent` — the **only** invocation of a
script in this file that omits `-I`. The other seven `$metadata_helper` calls
(56, 61, 71, 156, 161, 248, 376) and both other script calls (274, 347) all use
`python -I`. It sits between two identical siblings that both have the flag, which
is what makes it look like an omission rather than a decision. Unchanged by #121
(still line 168 at `f0a2d175`). Not acted on — the file is protected and under the
hold — and it is not part of F-8; it is recorded here only because this enumeration
is the thing that surfaced it.

**The blast radius was understated here and again by WS-1, and measurement makes it
wider rather than narrower.** This document previously said only that isolated mode
"suppresses `PYTHONPATH` and the user site-packages directory". WS-1 added that
"`sys.path[0]` is the script's directory either way, so this is import-shadowing of
the helper's own dependencies, not of the helper" — narrowing it. **That is false.**
Measured on 3.11.9 rather than recalled, with a third row that separates the
mechanism from the flag:

| invocation | `sys.path[0]` | script dir on path | `flags.isolated` | `flags.safe_path` |
| --- | --- | --- | --- | --- |
| `python -I probe.py` | `python311.zip` | **no** | 1 | true |
| `python probe.py` | the script's directory | **yes** | 0 | false |
| `python -P probe.py` | `python311.zip` | **no** | 0 | true |

So there are three differences at 168, not two, and the third is the one WS-1
denied: the interpreter prepends `scripts/validation/` to `sys.path` **ahead of
everything else**.

**Correcting the version scoping this document gave, because it is the sentence
that misdirected the next reader.** The claim above was first written as "false on
Python 3.11, where `-I` implies `-P`". The measurement is right and the stated
reason is narrower than the truth. The third row shows `-P` alone reproducing the
path effect with `flags.isolated` still `0`, so the path behaviour is separable
from `-E`/`-s` rather than a by-product of them; and the **3.10** documentation —
published before `-P` existed — already states that `-I` "can be used to run the
script in isolated mode where `sys.path` contains neither the current directory nor
the user's site-packages directory". `-P` in 3.11 gave a pre-existing behaviour its
own spelling. **The consequence is not cosmetic: a correct claim advertised as
version-contingent invites the next reader to go looking for a version, and that is
exactly what happened** — see the transposition below.

**In this repository that is not generic import hygiene — it bypasses a control the
repository actually operates.** The helper does not rely on implicit path setup: at
`approved_assets_github_metadata.py` 18–20 it resolves `parents[2]` and inserts the
**repository root**, then imports fully qualified (`from scripts.validation.bounded_json
import …`). That deliberate mutation is what
`CLOSURE_SYS_PATH_ALLOWED_SOURCE_SHA256` at `verify_rollout_trust_anchor.py`
427–440 exists to pin — a four-entry dict in which `validate_repo.py` is one of the
**governed entries** (431–433), not the container. *This document previously named
`validate_repo.py` as the container; the correction is the last subsection here.*
`-I` is what makes that pinning exhaustive, by ensuring the governed insert is
the *only* repo path present. At 168 the interpreter adds an ungoverned entry at
higher precedence, and the 22 modules in `scripts/validation/` become importable by
bare name for that one call — including ahead of the standard library the helper
imports (`json`, `re`, `hashlib`, `subprocess`, `argparse`, `pathlib`). No collision
exists today; the directory already contains `bounded_json.py`, so the naming
convention is one file away from one.

**Two refinements from WS-1, both confirmed by measurement, and they widen the
window rather than bound it.** First, the governed insert does not *displace* the
ungoverned entry. `sys.path.insert(0, str(REPO_ROOT))` at helper line 20 puts the
repository root at index 0 and pushes the script directory to **1** — still ahead
of the standard library. Executed on a synthetic tree reproducing the helper's
prologue: without `-I`, the script directory sits at index 1 with the first
stdlib entry at 4; with `-I` it is absent from `sys.path` entirely. So without `-I`
the pinned mutation stacks on top of the ungoverned path instead of neutralising
it. Second, the helper's own standard-library imports are lines **6–16**
(`argparse hashlib json re subprocess sys dataclasses datetime pathlib typing
urllib.parse`) and execute *before* line 20. The script directory is on `sys.path`
from interpreter startup, so the exposure window is the whole module including its
stdlib imports — not, as the paragraph above could be read, only the region after
the governed insert.

**A measurement neither of us took: the script's other ungoverned interpreters are
a different surface, and it is empty.** Eight calls go through the helper — 56, 61,
71, 156, 161, **168**, 248, 376 — and seven carry `-I`. But the file also runs
`python -c` at 177, 179 and 230 and `python -` (stdin) at 186 and 280, none of them
isolated. Those have a different `sys.path[0]`: measured, `python -c` yields `''`
and stdin yields the working directory as an absolute path.

**Correction, from WS-9, and it withdraws the inference that paragraph invited.**
The two values differ and the *difference is cosmetic*, because `''` **is** the
working directory at import time. A reader taking `''` for "no directory" concludes
`-c` is the safer form. It is not. Executed on 3.11.9 in an empty directory holding
a planted `json.py`:

| invocation | `sys.path[0]` | shadowing `json.py` imported |
| --- | --- | --- |
| `python -c 'import json'` | `''` | **yes** |
| `… \| python -` | the cwd, absolute | **yes** |
| `python script.py` | the script's directory | yes |
| `python -I -c 'import json'` | the stdlib zip | no |

Three spellings, one exposure. Only `-I` changes the answer.

**And the sentence that followed — "the working directory … the repository root" —
asserted a bound the script never establishes.** WS-9 checked what actually pins
the working directory and the answer is: nothing does, deliberately. There is no
`cd`, no `BASH_SOURCE`, no `dirname`, no `REPO_ROOT` in the file; I widened the
search to `pushd`, `realpath` and `$PWD` and all seven tokens return zero hits. The
confinement is a **side effect of input validation**. Lines 119–130 are a
seven-clause conjunction exiting `lifecycle.input_invalid`, and five of those
clauses test caller-supplied positional parameters (`$2`–`$6`) which may be
absolute. Exactly **two** test hard-coded relative paths — **124**
(`scripts/validation/approved_assets_github_metadata.py`, set at line 10) and
**125** (`…/collect_approved_assets_current_run.py`, line 11). Run from anywhere
else, the script dies at 127. So the property holds, it is real, and **nothing was
ever written to establish it** — which is why no one maintaining 119–130 could know
a path-shadowing bound depends on those two clauses. WS-9's generalisation is worth
taking: this document's overdetermination rule is not confined to claims about
artefacts; it appears *in* artefacts, and an unaudited property in code has the
same cause as an untested claim in prose — nothing downstream needed it to be true.

It is also *approximate*. 124 and 125 admit any directory containing both helper
paths — a repository-root-**shaped** tree, not the repository root. The conclusion
survives, since such a tree is what the check is for, but "the repository root" was
stronger than the evidence.

**A second holder, which inverts the obvious remedy.** Lines **190** and **286** —
inside the two stdin heredocs — read `from scripts.validation.bounded_json import
…`, a package path resolvable only with the repository root on `sys.path`. That is
a *functional* dependence on the working directory, not an incidental one, and it
is possible only because those two sites are **not** isolated. So the uniform
hardening — put `-I` on every interpreter in the file — **breaks the script at 190
and 286**. The file has three tiers, not two:

| sites | imports | `-I` addable |
| --- | --- | --- |
| 168 | helper, via path; 7 siblings already isolated | **yes** — the one-character fix |
| 177, 179, 230 | stdlib only (`secrets`, `datetime`, `json`, `sys`) | **yes** |
| 186, 280 | `scripts.validation.bounded_json` via cwd-rooted package path | **no** — requires restructuring |

**And the canonical refactor opens a real window.** The textbook repair for
relative paths in a shell script is to absolutise them. Do that to lines 10–11 and
124/125 stop binding the working directory, while 190/286 still fail without it —
so the script no longer exits at 127 but at 186. Execution order is 177 → 179 →
186, so **two sites run in an unbounded working directory before the first
cwd-rooted import fails**, and the first of them is
`locator="$(python -c 'import secrets; print(secrets.token_hex(16))')"`. A planted
`secrets.py` controls the dispatch locator. What that buys an attacker is **not
traced** and should not be assumed; the point is structural — *the bound exists
because the script depends on the working directory, so removing the dependence
removes the bound*, and the sites it endangers are precisely the five currently
considered safe. Same shape as the sparse-checkout anchoring, in a second
independent artefact: an edit that reads as tidying reinstates the hazard.

**What survives unchanged.** Of the ungoverned invocations, 168 is still the only
one whose exposed directory is populated — but the two bounds are different in
kind. 168's exposure is the *helper's* directory (22 modules), fixed by the
relative path at line 10 and independent of the working directory; the other five
expose cwd, bounded incidentally by 124–125. WS-9's singling-out of 168 stands, and
so does the count.

**Confirmed by execution, and the mechanism is now exact (WS-2, 2026-08-11).** The
tier table was read rather than run. WS-2 ran it at `824b4238`: the heredoc import
succeeds from the repository root, and fails with `ModuleNotFoundError: No module
named 'scripts'` both under `-I` from the root and without `-I` from elsewhere. The
structural reason is now established and is stronger than "cwd-rooted package path":
**there is no `__init__.py` anywhere under `scripts/`** — confirmed against the tree
at `824b4238`, zero under `scripts/` against twenty elsewhere in the repository — so
`scripts.validation.bounded_json` is a namespace package resolvable *only* by the
repository root sitting on `sys.path`. Nothing else can satisfy it.

**Two invocations were missing from every previous count of this file, and they are
not in the 119–130 guard.** Lines **274** (`python -I
scripts/validation/run_rollout_module.py`) and **347** (`python -I
scripts/validation/create_approved_assets_phase_authorization.py`) name their script
by a *literal* relative path, not through a variable, and neither path appears in
the seven-clause precondition block. If the working directory is wrong they fail
when the interpreter cannot open the file — late, and with no `lifecycle.*` token.

**But the ranking WS-2 drew from that is inverted, and the correction matters more
than the omission.** WS-2 filed 274/347 as "the least guarded". Line 274 is the
**most** guarded interpreter invocation in the file. `run_rollout_module.py` — read
in full at `824b4238` — is a launcher whose docstring is *"Run one fixed rollout
module without adding repository roots to `sys.path`."* Before importing anything it
calls `_assert_isolated_search_path()`, which raises unless `sys.flags.isolated` is
set, raises on **any empty `sys.path` entry** — an empty entry *is* the working
directory — and raises on any entry equal to or beneath the repository root. It then
installs `scripts` and `scripts.validation` as synthetic namespace packages bound to
absolute paths derived from `__file__`, checks the resolved spec origin against an
eleven-entry `ALLOWED_MODULES` table, and only then runs the module. **It is a
purpose-built anti-cwd mechanism that refuses to start if the working directory is
reachable from `sys.path` at all.**

**So the file's dependence splits along an axis neither party had separated:
*locating a file* and *resolving an import* are different dependences with different
failure modes, and no site has both.**

| construct | sites | cwd on `sys.path` | cwd needed to **resolve imports** | cwd needed to **locate the file** |
| --- | --- | --- | --- | --- |
| `python -I "$metadata_helper"` | 56, 61, 71, 156, 161, 248, 376 | no | no — helper self-inserts | yes, **pre-checked at 124** |
| `python "$metadata_helper"` | **168** | no | no — helper self-inserts | yes, **pre-checked at 124** |
| `python -c` | 177, 179, 230 | **yes** | no — stdlib only | n/a |
| `python - <<'PY'` | **186, 280** | **yes** | **yes — the only true dependence** | n/a |
| `python -I <literal path>` | **274, 347** | no | no — `__file__`-derived | **yes, unchecked** |

**The self-insert is what actually carries the eight helper invocations, and it is
not `sys.path[0]`.** WS-2 reasoned that a script-file invocation puts `sys.path[0]`
at the script's directory rather than cwd, so absolutising line 10 cannot change
168's exposure. The conclusion is right and the mechanism is insufficient:
`sys.path[0]` would be `scripts/validation/`, which **cannot** satisfy
`from scripts.validation.bounded_json import …` on its own. What satisfies it is
`approved_assets_github_metadata.py` lines **18–20** —
`REPO_ROOT = Path(__file__).resolve().parents[2]` followed by an explicit
`sys.path.insert` — and the same two-line idiom appears at
`create_approved_assets_phase_authorization.py` **16–18**. Under `-I` neither the
script directory nor cwd is on the path at all, so those seven siblings depend on
the self-insert *entirely*. **The repository already contains the cwd-independence
pattern, applied in three different modules, derived from `__file__`.**

**Which prices the remedy differently for each half.** For 274/347 the fix is two
more clauses in the 119–130 block: it converts a late unlabelled exec failure into
`lifecycle.input_invalid` at 128, and it cannot alter behaviour on a correct working
directory. For 186/280 the established pattern is **structurally unavailable** — a
heredoc fed to `python -` has no `__file__` to derive a root from, which is exactly
why those two are the sites that cannot be isolated. That is a sharper statement of
the existing third tier: they resist `-I` not because of how they import, but
because of what they lack.

**And the guard block already contains a clause that guards nothing.**
`current_run_helper` is set at line 11 and existence-checked at line **125**, and
those are the only two lines in the file that mention it. It is never invoked:
line 274 reaches that module through the launcher **by module name**, not by path.
So the seven-clause precondition block **validates one path that nothing ever opens
while omitting the two paths that are opened without validation.** The check is
real, passes, and protects nothing — and it sits four lines from the omission it
resembles.

**WS-1's reclassification, verified verbatim, and it moves 168 out of hygiene.**
The call at 168 is `assert-secret-absent … --name "$reviewed_evidence_name"`,
failing to `lifecycle.reviewed_evidence_secret_present` at 172. It is an **absence
assertion**: subverted import resolution makes it exit 0, and the script proceeds
believing a reviewed-evidence secret is absent when it is present. Its immediate
sibling at **161** makes the *same* `assert-secret-absent` call for
`$authorization_name` **with `-I`**, failing to `lifecycle.authorization_secret_present`
at 165. So the script's two secret-absence gates are governed asymmetrically, and
the ungoverned one is reviewed-evidence. That is an unprotected security assertion
rather than an incidental, and it is now [owner item 7](execution-program.md).

**WS-1's decision not to touch it stands, on grounds independent of the reason it
gave:** the file is under #121's signature hold, and changing it would invalidate
green checks on a PR awaiting signature while smuggling an unrelated change into a
receipt. **WS-1's own refinement of how this document framed that is better than the
framing, and is kept:** this was called the third occurrence of a sound decision
*resting on* a claim that measurement reverses. It did not rest on it. The signature
hold and the don't-smuggle-into-a-receipt argument each suffice alone, so the false
claim was **decoration on an overdetermined decision** — and that is precisely the
condition under which a wrong claim survives. Nothing downstream needed it to be
true, so nothing tested it. The diagnostic follows directly and is mechanical:
**when a decision is overdetermined, its supporting claims are unaudited by
construction; check them separately or drop them.** A decision with three
independent reasons is safer than one with a single reason, and its prose is less
trustworthy — which is not intuitive, and is why it belongs in writing.

### A citation whose subject and object are transposed — twice, in one exchange, in both directions

**WS-1 caught this document naming the wrong file, and the line span it gave was
exact.** `CLOSURE_SYS_PATH_ALLOWED_SOURCE_SHA256` is declared at
`scripts/validation/verify_rollout_trust_anchor.py` **427**, its body running
427–440 and holding four entries: `run_rollout_module.py` (428–430),
`validate_repo.py` (431–433), `approved_assets_github_metadata.py` (434–436) and
`create_approved_assets_phase_authorization.py` (437–439). Verified from the blob
at `824b4238`, size-checked. `scripts/validation/validate_repo.py` — 17131 bytes,
467 lines, fetched and size-checked — contains the string `SYS_PATH` **zero** times.
So the relation was inverted: the file named as the pin's *container* is one of the
things the pin *governs*.

**What makes this worse than a wrong line number, and worse than the wrong-file case
recorded above.** A bad ref fails to resolve and announces itself. A right ref in
the wrong file resolves to something arbitrary, which a careful reader may still
notice. Here the ref resolves to something **on topic**: `validate_repo.py:427` is

```python
        check(False, f"protected set is not closed under import: {exc.code}", errors)
```

— a line about *trust closure*, the very subsystem the pin belongs to, inside the
block that calls `assert_repository_trust_closure` (424–431). A reader who does the
obvious check lands on corroborating material and stops. The association
"`validate_repo` ↔ `sys.path` pinning" is genuine and merely reversed, so every
component of the sentence is individually true and only the composition is wrong.

**The corpus already contained the correct relation, which is what makes the
diagnostic cheap.** `execution-program.md` line 872 says "`validate_repo.py`'s own
pin at 431–432" — naming it correctly as a governed entry. So this was never a
knowledge gap: the two statements sat in the same directory contradicting each
other. **Grep your own corpus for the entity before citing it.** That is the same
instrument added for withdrawn reasons in §13's thirteenth sub-shape, generalised
from *strings a correction retired* to *entities a claim names*, and it would have
caught this one.

**WS-1 committed the mirror image in the message that delivered the correction, and
the verification is a clean negative.** Supporting the argument that the `-I`
divergence "isn't local", WS-1 wrote that `migrate-approved-assets.yml` "pins
`python-version: "3.11"` at 449 and 1006 — the workflow that runs this script". The
pins are exact: both lines verified in a 56978-byte, 1126-line blob. **The final
clause is false.** All 16 workflow files at `824b4238` were fetched, size-checked
and searched; **zero** reference the script. A repository-wide code search returns
six references to `authorize_approved_assets_phase` — a runbook
(`docs/runbooks/migrate-approved-assets.md`), three validators and two test files —
and the script itself lives at `scripts/operations/`, matching this document's
cached copy byte-for-byte at 12417 bytes. It is **operator-run, not CI-run**.

**The conclusion survives on better grounds, and the security reading gets
stronger.** The divergence needs no version pin at all, because isolated mode's
exclusion of the prepended directory is documented behaviour of `-I` predating `-P`.
And an operator's workstation is precisely where `PYTHONPATH` and user
site-packages pollution lives, which a fresh hosted runner does not have — while
line 152 asserts repository-admin before 168 runs. So the ungoverned call executes
in the environment *most* likely to carry the pollution, with admin credentials
live. Being outside CI makes item 7 more serious, not less.

**The shape, since both instances share it and the parties differ.** Each of us
reached for the artefact *nearest* the claim and assigned it a role it does not
have: `validate_repo.py` really is in the pin, as a governed entry;
`migrate-approved-assets.yml` really is about the same subsystem — it is the
namesake of the runbook that documents the script. **Relatedness is what defeats the
spot-check**, because the reader verifying the citation finds a true connection and
reads it as the claimed one. And the two are causally linked in one direction: this
document's over-narrow "false on Python 3.11" is what made a *version* look like the
thing needing evidence, which is what sent WS-1 to a workflow with a version pin. An
imprecise reason does not merely fail to persuade; it selects the next reader's
search. Filed as §13's eighteenth sub-shape.

**Why `2>&1` is the wrong fix at both, structurally.** Each is a command
substitution assigning to a variable the script then depends on — `run_id="$("`
at 247–255 and `runner_id="$("` at 375–380 — with the identical
`set +e` / status / `set -e` / label / exit frame around it. Merging stderr into
stdout would contaminate the captured value. So #121's construct — redirect to a
file under `$temp_dir`, `head -c 512 | tr -c`, print alongside the existing label
— ports **verbatim**. Whoever extends the receipt is doing a transcription, not a
design.

### The mechanism narrows to one raise site, and the instrument cannot say so

Every line WS-1 cited was checked against `main` at `824b4238` and is exact:
`_project_run` returns `created_at` at 149, `_parse_page` projects through it at
198, the digest is taken at 277–284, the re-verification loop runs 291–318, the
`try` opens at 461 and the `except` closes at 510, and the fake `gh` regenerates
`created_at` from `datetime.now()` at 773–775. Three refinements follow that
neither session stated.

**One of the three `pagination_race` sites is reachable here, not three.** Line
257 fires when a later page's `total_count` differs from the first; line 290 when
`expected_total != len(items)`. The stub pins `"total_count": 1` and returns
exactly one item on every invocation, so both comparisons hold unconditionally.
Only **line 318**, the digest comparison, can trip — and the only projected field
that varies between two identical GETs is `created_at`. The mechanism is
therefore not "a pagination race" but one specific comparison failing on one
specific field.

**The re-verification pass is the only success path out of `fetch_all`, not an
extra step for small responses.** `return items` at 319 sits *inside* the
`if len(page.items) < PER_PAGE:` at 288; the only other exit is
`raise MetadataError("github_metadata.truncated")` at 320. So the gate is what
makes the page loop *terminate*, not what makes verification run — there is no
successful return that skips it, at any response size. Every successful metadata
fetch in this system doubles its GETs and re-compares digests. WS-1's phrasing
("the verification pass always runs here") is true but reads as a property of the
one-run fixture; it is a property of the function.

**Production is not exposed to this, and the reason is worth stating rather than
assuming.** Production digests `created_at` too — it is in the projection for
every caller. But a real run's `created_at` is a fixed server-side attribute that
does not change between two fetches, so the digests agree. Catching a genuine
race is what the control is *for*. The defect is entirely that the fixture
regenerates a value production holds constant. That confirms WS-9's "the harness
is the defect" conclusion by mechanism rather than by inference.

**A limit on #121's instrument, worth knowing before the datum arrives.** One
code covers three raise sites, so `lifecycle.run_selection_stderr=` will report
`github_metadata.pagination_race` without saying which comparison failed. Under
the stub, elimination gives 318; on any path where `total_count` can legitimately
vary, it would not. #121 upgrades the label from *wrong subsystem* to *right
code* — a large gain — but a code still is not a site, and 23 `github_metadata.*`
codes spread across more than 23 raise sites means this is general, not peculiar
to `pagination_race`.

**The fixture defect has a second instance, and it is not a second live path.**
The `actions/runs/701` stub regenerates `created_at` from `now()` at line 783 by
the same pattern as 773–775. WS-1 traced its consumer; the trace verifies, and the
conclusion is that 783 is fixture realism rather than a live race:

- the consumer is `collect_approved_assets_current_run.py:72`, a **single**
  `api_call`. `fetch_all` is not imported into that module at all, so there is no
  fingerprint, no re-fetch and no digest comparison that a regenerated clock value
  could make disagree. **783 cannot produce the F-8 signature.** This is the
  load-bearing half and it is correct.
- `created_at` is projected at `collect:103`, read by the shell at 308–313 and
  embedded verbatim into the request at 340.
- the one comparison that consumes it is
  `created_at <= evaluation_time and evaluation_time - created_at <= 86400s`
  (`validate_approved_assets_phase_authorization.py:241–243`,
  `CURRENT_RUN_MAX_AGE_SECONDS = 86400`), reached from
  `create_approved_assets_phase_authorization.py:126`.

So the re-pin only has to fix 773–775 to close the live path. Including 783 while
the file is open is still worth doing — it is free once the edit is authorized,
and the one-attempt constraint is what makes free things worth taking.

**But the reason 783 is safe is not the one WS-1 gave, and the difference has a
future.** WS-1 excluded `validate_approved_assets_phase_authorization.py` on the
grounds that it "is not invoked by `authorize_approved_assets_phase.sh` … so it is
outside the failing test's path", listing shell line 347 among the lines checked.
Line 347 *is* the invocation of `create_approved_assets_phase_authorization.py`,
which imports `validate_current_run_metadata` from that very file (`create:20–28`).
The file is on the executed path — as a library, not as a subcommand — and the
comparison at 241–243 that WS-1 correctly identified as reached lives inside it.
The two statements contradict each other.

The correct reason is stronger: the check **is** executed, on every authorization,
and it passes **by construction**. The current-run fetch is shell 274–278 and the
clock capture that becomes `decided_at` is shell 314, so `created_at` is always
the earlier of the two and the 86400-second window is nowhere near binding.

**And it is reached twice per authorization, not once.** WS-1 offered `create:197`
as "the second call site passing the same `decided_at`", which undersells what is
there. 197 is not a second call of `validate_current_run_metadata`; it is an
argument to `validate_phase_authorization` (opened at `create:180`), and *that*
function calls `validate_current_run_metadata` at
`validate_approved_assets_phase_authorization.py:354`, forwarding
`evaluation_time=evaluation_time` at 361. So the comparison at 241–243 runs on
both paths:

| Path | Entry | Reaches 241–243 via |
|---|---|---|
| Direct | `create:119`, `evaluation_time=decided_at` (126) | immediately |
| Indirect | `create:180`, `evaluation_time=decided_at` (197) | `valpha:354` → 361 |

Both carry the same `decided_at`, so the by-construction conclusion is unchanged
and neither can fail while the shell ordering holds. What doubles is the
consequence of breaking that ordering, and the second site is the more confusing
one: it surfaces from inside receipt validation, so a clock-ordering regression
would present as an authorization-receipt failure rather than a metadata failure.

That distinction is worth the paragraph because "not executed" and "executed but
unfailable" have different futures. The property doing the work is an *ordering in
the shell*, not an invariant of the validator. Capture `now` before the fetch, or
add a retry that re-fetches after it, and `created_at <= evaluation_time` starts
failing with `github_current_run.time_invalid` — against a fixture whose clock
moves. Nobody guards a path they believe is not executed.

**It is also the same error as WS-1's previous one, one level up.** That one
stopped at a function boundary while the `except` spanned the whole operation;
this one stops at the invocation boundary while an `import` crosses it. Both have
the shape *I checked the named list and the mechanism was not in it* — and a
helper list names scripts, so imported modules can never appear in it. The general
form belongs with §13: **the boundary you stop at is an assumption, and it is
invisible precisely because stopping feels like completing.**

### The enumeration that followed is mislabelled, and correcting the label reverses its conclusion

WS-1 then did the repository-wide search it had declined the turn before, and
offered a list of "importers" of `validate_approved_assets_phase_authorization`
as data, drawing one conclusion — that the module is "broadly depended on", which
it advanced as a reason to treat the item-5 edit as conservative. Every cited
line checked at `824b4238`. **Three of the sixteen are imports. Thirteen are
string literals.**

| Site | Reality |
|---|---|
| `create_approved_assets_phase_authorization.py:20` | import |
| `tests/test_migrate_approved_assets.py:28` | import |
| `tests/test_approved_assets_rollout_readiness.py:24` | import |
| `verify_rollout_trust_anchor.py:91` | `PROTECTED_EXACT_PATHS` entry |
| `verify_rollout_trust_anchor.py:387` | module-name allowlist entry |
| `run_rollout_module.py:46,50` | loader registry key and path |
| `run_rollout_module.py:158,161,250,253` | module-name strings |
| `validate_repo.py:46` | path string |
| `validate_rollout_ci_policy.py:151` | path string |
| `test_migrate_approved_assets.py:448,2053` | strings |
| `test_rollout_trust_anchor.py:2023` | path string |

So in production code **exactly one module imports it** — `create`, the one
already known, which is the site that started this thread. The anchor does not
depend on the validator at all; it *protects* it and *allowlists* it, which is a
governance relationship, not a dependency.

**Correctly labelled, the data says close to the opposite of the conclusion drawn
from it.** `run_rollout_module.py` describes itself as "Run one fixed rollout
module without adding repository roots to `sys.path`", and imports
`importlib.machinery`, `importlib.util` and `runpy`: it is a fixed-registry
dynamic loader. These modules are deliberately *not* ambient imports. They are
named as strings, launched through a controlled channel, and their sources are
digest-pinned in `CLOSURE_PROCESS_ALLOWED_SOURCE_SHA256` with
`closure.dynamic_import` raised on mismatch. The breadth is **pinning breadth,
not dependency breadth**.

That inverts the practical caution, which is why the mislabel matters rather than
being pedantry. Broad dependency would mean *many callers may change behaviour* —
an argument for a minimal edit. Broad pinning means *nothing changes behaviour,
and several registries and digests must move in lockstep or the loader refuses
the file*. The second is not a reason to make the edit smaller; it is a reason to
enumerate the pins before starting. The same structure governs the verifier
re-pin in item 5, which is the actual subject of that item.



**Limits, stated.** The consumers traced are those reachable from
`authorize_approved_assets_phase.sh`; no exhaustive repository-wide enumeration
was done, by WS-1 or here. `create:197` is a second call passing the same
`decided_at`, so it does not change the result.

### A stale worktree produced citations that were right by luck

WS-1 reported platform `main` at `3bddeee` while it was at `824b4238` — two
commits behind, with #122 (`3e9b9ef4`) and #123 (`824b4238`) landed in between.
Its line numbers nonetheless verified exactly, because neither commit touched
`approved_assets_github_metadata.py` or `test_migrate_approved_assets.py`. That
was checked against the changed-file lists rather than assumed.

The citations were correct; the method that produced them was not. Line numbers
carry no evidence of the tree they came from, so a stale citation is
indistinguishable from a current one until someone re-derives it — the same
property that makes a wrong timestamp more dangerous than a wrong SHA (§10). The
cheap habit: state the ref you read at, so the reader can check the delta instead
of re-reading the file.

**The second variant is worse, and it appeared within the hour.** WS-9 cited the
runner discard site as "`.sh:388` → `.sh:393`". On `main` at `824b4238` those
lines are **379 → 384**; 388 and 393 are exact at **`f0a2d17`, the head of its own
open pull request**, where #121's added construct has pushed the file from 409
lines to 418. Both citations were produced carefully and neither stated a ref.

The two failures are not the same shape:

- WS-1's was **temporal** — a tree two commits behind. It self-heals on sync, and
  the delta is checkable with `git diff --name-only`.
- WS-9's is **spatial** — a concurrent branch `main` has never contained. Syncing
  does not fix it, because there is nothing to sync to.

And the spatial variant has a property that makes it genuinely treacherous here:
because #121 *inserts* nine lines above that point, **if #121 merges, `main`'s
numbers become 388 and 393.** The citation is wrong today and correct later,
conditional on a merge that has not happened. A reader checking now finds it
wrong; a reader checking after the receipt is signed finds it right; neither can
tell which regime they are in from the number alone.

This is not pedantry about line numbers — it is the same failure as the F-8 label.
A citation, like a lifecycle label, is a pointer that carries no evidence of its
own provenance, and a confident wrong pointer costs more than no pointer, because
it is acted on. The operative consequence: whoever extends the receipt to the
runner site edits **379 on `main`** or **388 on `f0a2d17`**, depending on
sequencing, and writing either number without its ref creates a real chance of
editing the wrong line or failing to find it at all.

**The fix WS-9 proposed is better than stating the ref, and it is adopted.**
Anchor the citation to text — *the `2>/dev/null` on the `verify-staged-runner`
call whose stdout is captured into `runner_id`, and the
`lifecycle.runner_registration_invalid` printf below it* — and attach the
coordinates as a convenience with their provenance. The prose survives both
regimes, so the citation is no longer sequencing-dependent at all, and it
degrades gracefully: a reader who cannot find line 379 can still find the
construct. That is the same reason the port is a transcription rather than a
design — the shape is the stable thing, and the coordinates are a projection of
it into a frame that keeps moving. Owner-decision item 3 is now written this way.

**Why it went unnoticed for an evening, which is the part worth keeping.** WS-9
had read that file at 388 all session. From inside a branch the ref is invisible;
the line number simply *is* the line number, and no amount of care surfaces a
frame you cannot see you are in. So the remedy is not diligence but form: a line
citation without a ref is **malformed**, in the same way "the current head" is
malformed without a SHA. That framing is what makes the habit enforceable rather
than aspirational, because malformedness is checkable by the writer and
carelessness is not.

**Extension — the rule needs the *file* as well as the ref, and the missing-file
case is the more dangerous of the two.** WS-2 reported that
`tests/test_rollout_trust_anchor.py:4243` reads a `.py` raw and is nonetheless safe
"because normalization happens inside `_reviewed_source_sha256` at 1245". The
substance is correct and was verified: at
`scripts/validation/verify_rollout_trust_anchor.py` **1245** is
`normalized = source.replace(b"\r\n", b"\n")`, with a lone `\r` refused at 1246–1247
and the plain digest returned at 1258. But the number was given with no file, in a
sentence whose other coordinate is in the *test*, and `tests/test_rollout_trust_anchor.py:1245`
is `) -> anchor.ApiResponse:` — a test double's return annotation. **An
unresolvable citation announces itself; a citation that resolves in the wrong file
does not.** The missing ref produces a lookup failure the reader notices; the
missing file produces a plausible line the reader may accept. So the malformedness
rule covers both coordinates, and the file is the one whose absence is silent.

### A third control that goes red while discarding what it knew — and it is not a house style

WS-2 found a third instance of the shape F-8 documents, in a third file, and it is
the largest of the three. Verified at `824b4238` from
`scripts/migrations/_fixed_migration.py` (blob fetched and size-checked, 23914
bytes, 729 lines).

The site is **712–714**:

```python
    except (OSError, RuntimeError):
        print("migration status failed", file=sys.stderr)
        return 3
```

Its `try:` opens at **653**, so the handler spans fifty-nine lines covering the
entire migration execution, including a nested `try` at 683–693. WS-2's call chain
reproduces line-for-line: `run_fixed_migration:660` → `_status_snapshot:531` →
`_revalidate_psql:473` → `_revalidate_trusted_executable:387`.

**The measurement WS-2 did not take, and it is what makes this the worst of the
three.** The file raises `RuntimeError` at eight sites carrying **six distinct
messages**, every one of them reachable inside 653–711:

| Line(s) | Message |
| --- | --- |
| 385, 387 | `psql executable path changed` |
| 389 | `psql executable is not executable` |
| 475 | `psql executable identity changed` |
| 542, 544 | `migration status query failed` |
| 550 | `migration status query returned an invalid state` |
| 693 | `migration apply failed` |

All six collapse to one label and one exit code. **The causes were not unknown —
they were composed and then erased three frames later.**

**Sharpened by measurement, and it is worse than "collapsed": four of the six are
actively mislabelled.** Only two of the six messages are about status — `migration
status query failed` and `…returned an invalid state`. The other four are not:
*path changed*, *is not executable*, *identity changed*, *apply failed*. So
`migration status failed` is the wrong sentence for **four of six** reachable
causes. The worst is 693: an `apply` that fails to launch is reported as a
*status* failure, which turns a failed write into what reads as a read-only probe.
That is a static reachability count, not a frequency — which of the six dominates
in practice was not traced.

**Correction, measured on 3.11.9: the `from None` claim below was wrong, and the
remedy is far cheaper than this document priced it.** This section previously said
that three of the six (**385, 542, 693**) raise `from None`, "severing `__cause__`
at the raise site, so even rewriting 712 as `raise … from exc` would not recover
them". The premise is true and the consequence does not follow. The six sentences
are the `RuntimeError`'s **own args**, not its `__cause__`, so they were never in
the channel `from None` closes. Executed rather than recalled:

```
raise RuntimeError("psql executable path changed") from None
  str(exc)            -> psql executable path changed
  exc.args            -> ('psql executable path changed',)
  __cause__           -> None
  __context__         -> OSError('underlying disk error')
  __suppress_context__-> True
```

So `print(str(exc))` at 713 recovers **all six**, `from None` notwithstanding. It
costs only the *rendered display* of the layer beneath, and even that survives
programmatically in `__context__`. The fix is one line and recovers six sentences.
WS-2 established this and it reverses a claim of mine that made the repair look
partial. The file does suppress context at nine sites; that count was correct and
is no longer load-bearing.

**Correction to WS-2's generalisation: the evidence says lapse, not house style.**
WS-2 concluded "the pattern isn't one bad line; it's a house style". The same
function contradicts that in the twenty-five lines immediately above 653 — five
handlers, all narrow, all specific, all with distinct exit codes:

| Lines | Catch | Message | Exit |
| --- | --- | --- | --- |
| 627–631 | `ValueError` | `trusted psql executable is required` | 127 |
| 633–638 | `OSError` | `fixed migration artifact could not be read` | 2 |
| 639–641 | — | `fixed migration artifact digest does not match` | 2 |
| 644–646 | — | `<DATABASE_URL_ENV> must be configured` | 2 |
| 647–651 | `ValueError` | `database URL is invalid` | 2 |

Specificity is this author's local convention; 712 departs from it. The recurring
shape across all three files was therefore recorded here as narrower than "house
style": *the outermost handler of an entry point*, with the shell script's version
said to be the `EXIT` trap, "the same structural position".

**That structural claim is false, and correcting it splits one family into three.**
WS-2 checked the shell script and I verified it at `824b4238` (blob `251e8218`,
12417 bytes, cache confirmed byte-exact). `on_exit` is defined **86–101** and
installed at **102**; the only thing it prints is `lifecycle.cleanup_failed` (92),
and it otherwise preserves `original_status` (94–95).
`lifecycle.run_selection_failed` is at **262**, inline inside the retry loop
**245–266**, immediately after the `selection_status -ne 2` test at 261. It is not
a handler, not outermost, and not a trap. The three sites do not share a position,
and they do not share a mechanism either:

| Site | Mechanism | Where the knowledge dies | Remedy |
| --- | --- | --- | --- |
| shell `254` → `262` | **call-site discard** | callee names the cause; caller sends it to `/dev/null` | stop discarding — #121's `stderr=` capture |
| `_fixed_migration:712` | **in-process erasure** | a 59-line handler has nothing specific left to say | `print(str(exc))`, one line, six sentences |
| `closure.dynamic_import` | **token reuse** | narrow checks decline to encode what they know | discriminate the token; no handler edit helps |
| `github_metadata.page_invalid` | **token reuse** (second member, added 2026-08-11) | nine narrow raise sites share one name | same remedy; see the resolution below |

The helper is not at fault in the first row: `approved_assets_github_metadata.py`
prints the specific code correctly at **510–511** (`except MetadataError as exc:` /
`print(exc.code, file=sys.stderr)`), and its only other handlers, at 92 and 213,
both re-raise with a specific code and `from exc`. **WS-2 placed `metadata:510` in
the erasure column while stating one paragraph earlier that it reports correctly at
511** — the two halves of its own message disagree, and the source settles it in
favour of the earlier half. The metadata helper is clean; the shell is where the
sentence is thrown away. *(Refined 2026-08-11: clean **on the erasure axis**. WS-1
showed it is a token-reuse site at the raise — see the resolution below.)*

**And the third row refutes the property WS-2 proposed to replace position with.**
WS-2 argued the unifier is *width, not position* — a handler wide enough has
nothing specific to say. That is exactly right for 712 and it is the reason the
`print(str(exc))` fix works. It does not hold for `closure.dynamic_import`, which
is raised at **46 sites** in `verify_rollout_trust_anchor.py` and at none of them
from an `except` at all. They are narrow `if` conditions with *maximal* local
knowledge — a module outside the allowlist (1173), a wrong call signature (1198), a
duplicate assignment (1273), a `sys.path` write where none is allowed (1616), an
`importlib` submodule import (1723, 1727), a pin absent or mismatched (1436, 1481).
Each knows precisely what it caught and each emits the same opaque token. So the
family's common property is not width and not position:

> **the granularity of the emitted token is decoupled from the granularity of the
> knowledge available where it is emitted** — and there are at least three
> independent ways to achieve that: width destroys the knowledge, redirection
> discards it in transit, token reuse declines to encode it.

**Which invalidates the practical conclusion this section drew.** It said an
outermost-handler defect "is fixed at one site per entry point, while a house style
implies a campaign". Neither branch survives: none of the three is an outermost
handler, and the three remedies are three different edits in three different files
— one of which, the 46-site one, is precisely the campaign the distinction was
introduced to rule out. #121's `stderr=` remedy generalises to the **first** row
only. That is a narrower claim than "generalises past its own call site", which is
how this document has been putting it, and the narrower claim is the true one.

**One caveat that must travel with the 46-site row, because it may not be a defect
at all.** `verify_rollout_trust_anchor.py` is a trust verifier, and a deliberately
opaque failure code is a defensible security property there: telling an attacker
*which* closure check rejected their input is a disclosure the other two sites have
no equivalent of. Forty-six sites converging on one token is at least as consistent
with an intentional convention as with a lapse — which also means WS-2's original
"house style" reading, which this document corrected to "lapse", has more support
here than that correction allowed, while remaining wrong for `_fixed_migration.py`
where five narrow neighbours prove the author's local practice. ~~**Not classified.**
Deciding it needs the author's intent, and the observation belongs to the owner.~~
**Resolved 2026-08-11 — see immediately below. Classified *lapse*, on measurement
rather than on intent.**

**The caveat is resolved, and it was resolved by evidence rather than by asking the
author.** WS-1 returned with an AST pass; I re-ran every count independently against
blobs byte-verified at `824b4238` (`verify_rollout_trust_anchor.py` `ec4c82d6`,
`approved_assets_github_metadata.py` `f2493a27`, `_fixed_migration.py` `37c802a5`,
each recomputed locally). "Inside an `except`" is an ancestor question that a
line-oriented pass cannot answer, so it was done with a parent map over the tree:
**46 `closure.dynamic_import` sites, zero with an `ExceptHandler` anywhere in their
ancestry**, all eight cited lines present. The wider counts reproduce exactly —
**183 `TrustError` raises carrying 97 distinct tokens** — with one detail worth
keeping: the file holds **190** `TrustError` raises, of which **seven** pass a
non-constant code. So 183/97 is the count *of raises carrying a literal*, the two
figures never disagreed, and the seven are a small separate population nobody has
examined.

Three grounds, in increasing order of force:

1. **The shape appears where security cannot explain it.**
   `approved_assets_github_metadata.py` collects run metadata and has no adversary,
   yet `github_metadata.page_invalid` is raised at **nine** sites (112, 120, 133,
   171, 175, 214, 225, 249, 294) and `pagination_race` at three (257, 290, 318).
   That makes the helper a **second member of the token-reuse row**, and it exposes
   an imprecision in this document's own wording: it was called "clean" without
   saying on which axis. It is clean on *erasure* — 510 prints `exc.code`
   faithfully — and it is a token-reuse site at the raise. Both are true; the table
   now says so.

2. **The local practice is one-token-one-condition, and the exception is not
   marginal.** A repo-wide "96 of 97 tokens are precise" invites the objection that
   different subsystems keep different conventions. Measured inside the closure
   namespace alone — one author, one runbook table, one subsystem — there are **15
   tokens across 65 raise sites**, and `closure.dynamic_import` is **46 of them,
   71 % of the family**. Eleven of the remaining fourteen are used **exactly once**.
   The convention is not merely "precise"; it is one condition per token, eleven
   times over, with a single exception covering more sites than the rest of its own
   family combined.

3. **Decisively, the distinctions are already published — in the operator runbook,
   in prose.** `docs/runbooks/authorize-rollout-policy-change.md` at **218–222**
   enumerates the shapes that raise it — a non-literal or computed argument, a
   `package` keyword argument, `import importlib.util`, `from importlib import` anything but
   `import_module`, `from importlib.X import Y`, and any `importlib.<attr>` other
   than `import_module`. **A token cannot be withholding for security when the
   document written for the operator lists what it is withholding.** This is
   stronger than the allowlist-constants argument, because a constant is machinery
   the reader must interpret, whereas this is the enumeration itself, in English, in
   the same file the decoder table lives in.

**The one reading that would have revived the caveat is checked and does not.** An
external consumer parsing the token would justify keeping it stable regardless of
its coarseness, and that check had been flagged as unperformed. Searched across the
whole tree at `824b4238`: `closure.dynamic_import` appears in exactly **three**
files — the verifier (46), `tests/test_rollout_trust_anchor.py` (**8**) and the
runbook (**2**). No workflow, no script, no schema, nothing outside the repository.
There is no parsing contract to preserve, and the fix's blast radius is bounded by
those three files.

**But the eight test sites are a finding of their own, and they run the other way
from "harmless".** Each is `assertEqual(raised.exception.code,
"closure.dynamic_import")` after constructing one specific dynamic-import shape
(3818, 3840, 3965, 4039, 4080, 4118, 4210, 4347). Because 46 conditions produce that
token, **each of those assertions passes if any of the 46 fires** — including the
wrong one. Eight tests written to distinguish specific shapes cannot distinguish
them, and the suite would not notice if two conditions were transposed. So the
remedy's cost is not 46 edits; it is 46 edits plus **8 assertions that currently
certify only that some closure check fired**.

**A defect neither side went looking for, found while reading the runbook to check
ground 3.** The runbook's operator decoder table (**291–304**) documents **14**
closure codes. The verifier raises **15**. **`closure.import_name_invalid` — raised
at 1151 and 1998 — is absent from the table**, and the set difference is exactly
that one entry in one direction and empty in the other. The asymmetry around it is
the point: the runbook sits in `PROTECTED_EXACT_PATHS`, pinned by
`test_rollout_trust_anchor.py` at ~2000, so **it cannot be modified without a signed
receipt** — and *nothing whatever* checks that its table matches the codes the
verifier can emit. **It is fully guarded against tampering and entirely unguarded
against being wrong.** An operator who meets `closure.import_name_invalid` consults
a document that has been cryptographically protected into a state that does not
mention it.

**Recorded, not acted on.** `scripts/migrations/`, `scripts/operations/` and
`scripts/validation/` are all platform code under the #121 hold, and none of this
is part of F-8's evidence. Every line above was read from blobs fetched and
size-checked at `824b4238` — `_fixed_migration.py` 23914 bytes / 729 lines,
`authorize_approved_assets_phase.sh` 12417 bytes (git blob sha `251e8218`, recomputed
locally), `approved_assets_github_metadata.py` 518 lines — and the `from None`
behaviour from execution on 3.11.9, not from documentation.

### The trust-root exemptions, and a mitigation this document filed in the wrong workflow

WS-9 reported that the masking both of us credited with the trust-anchor silence
covers **one raise out of fifteen**, not the condition. The headline is right and
the census behind it is exact. Verified by AST at `ffc5bc3`, not by reading:
`validate_allowed_signers` raises **15** times across **5** codes —
`allowed_signers.invalid` ×8 (2483, 2489, 2493, 2497, 2499, 2506, 2515, 2525),
`allowed_signers.key_invalid` ×2 (2529, 2537), `allowed_signers.unavailable` ×2
(2478, 2487), `trust_root_not_configured` ×2 (2491, 2509), and
`trust_root_file_missing` ×1 (2476). The second function is `_validate_executable`
with **6**, so the twenty-one is exact too. `UndeterminedError` subclasses
`TrustError` at **485**, which is load-bearing: `_validate_trust_root_command`
catches `TrustError` at **3231**, so severing the hierarchy would convert a
successful answer into an uncaught internal failure. Every one of those numbers
survived checking.

**What did not survive is this document's own account of where the mitigation
lives.** §13's instance (iii) recorded "the line-69 guard masking a live
misclassification on two required checks". Guard 69 is
`test -f "$allowed_signers"` in **`rollout-trust-anchor.yml`**. The two required
checks that run `validate-trust-root` are in **`secret-scan.yml` 40–44** and
**`validate.yml` 85–89**, and both files contain **zero** occurrences of
`test -f`, `test ! -L` and `mkdir -m` — no filesystem guard anywhere in either.
A guard cannot mask anything on a check whose workflow does not contain it. The
mitigation was placed by plausibility rather than by file, and the check that
would have caught it is the one this document keeps recommending to others:
open the artefact the claim names.

**So the two paths have different exemptions in different places, and neither is
the pair WS-9 tabulated.**

| path | workflow | what actually exempts | reachable and misclassified pre-#124 |
|---|---|---|---|
| `verify` | `rollout-trust-anchor.yml` | guard 69 pre-empts `trust_root_file_missing` — **1** site | **14 / 15** |
| `validate-trust-root` | `secret-scan.yml`, `validate.yml` — no guards at all | the code-keyed special case at **3232** exempts `trust_root_not_configured` — **2** sites | **13 / 15** |

WS-9 gave 14 and 14 and summarised it as "one raise each, neither more than one".
The second row is **13**, because the exemption at 3232 tests
`exc.code == "trust_root_not_configured"` — it is keyed on the code, and that code
is raised from two distinct conditions. The asymmetry they ruled out is the one
that holds.

**Their stated reason is also stronger than they made it, and wrong for three of
the fourteen.** The reason offered was that the remaining raises are content
predicates and a filesystem guard cannot mask a content failure. That is true of
eleven. It is not true of 2478 and 2487, which are `OSError` from `lstat` and
`read_bytes` — those are filesystem failures, and they survive the guard because
a guard cannot close the window between itself and the run, not because they are
about content. Nor of 2483, which is a disjunction: its `not S_ISREG` arm *is*
pre-empted (by guard 70, since `path.lstat()` does not follow symlinks), while
its `st_size > 2_048` arm keeps the raise reachable. Masking a raise requires
pre-empting **all** of its arms, which is why the count is still one — the
conclusion holds for two independent reasons where one was claimed.

**A new member of the token-reuse row, and the first whose reuse is load-bearing.**
The row above records three ways to decouple an emitted token from the knowledge
at the emission site, and its two existing members — `closure.dynamic_import` and
`github_metadata.page_invalid` — are both *reporting* coarseness: nothing
downstream branches on them. `trust_root_not_configured` is different. Its two
sites are an **empty file** (2491, `data == b""`) and a file whose principal lines
have all been **commented out** (2509, reached only after every content check has
already passed — well-formed, non-empty, ASCII, newline-terminated). Those are
different world-states: one is "nobody configured this yet", the other is "someone
neutralised what was configured". The shared token is then read at 3232 as a
**branch predicate**, selecting exit 0 and `{"trust_root": "unconfigured"}` on the
two required checks. So the reuse is not merely lossy; it decides a status.

That changes the remedy price, which is the axis this audit has been using. For
the two reporting-only members, splitting a token is free — an author names the
condition they already tested. Splitting this one is not: whichever half keeps
`trust_root_not_configured` keeps exit 0, and that is a policy decision about
whether a commented-out trust root should pass a required check. **A reused token
that reaches a conditional has been promoted from a label into an interface, and
the cost of un-reusing it rises accordingly.**

**And #124's own remedy repeats the shape it was written to fix.** Its commit
message is "pin every trust-root raise, not the two I happened to test", and the
new test does derive the population by AST-walking both functions, does require
every raise to be `UndeterminedError`, and does pin both counts. But
`raised_codes` is populated for both functions at **5306** and compared exactly
once, at **5312**, for `validate_allowed_signers` alone. `_validate_executable`'s
five codes — `ssh_keygen.path_invalid` ×2, `ssh_keygen.not_executable`,
`ssh_keygen.owner_invalid`, `ssh_keygen.permissions_invalid`,
`ssh_keygen.unavailable` — are collected into a set that is never read. A renamed
or added code there passes, so long as the total stays 6. The generalisation
reached the raise type and the counts and stopped one line short of the codes, in
the commit whose thesis is that stopping short is the error. Third occurrence in
one exchange; the finding is WS-9's to fix on an open branch, not this document's
to act on.

### The line-ending pins, and a census of the artefact rather than the subject

WS-9 repaired an assertion that named the trust root's `text eol=lf` pins in its
rationale and then guarded a neighbour. The repair is real and the reasoning
behind it is sound; three of its four supporting claims verify exactly, and the
fourth is an overstatement of its own severity. What the round leaves behind is a
pin that still nothing asserts — and the reason it was left behind turns out to
be an instruction in this repository rather than anything the workstream did.

**The absence claim, confirmed by enumeration over all twenty-nine test modules
at `824b4238`.** Exactly **two** `text eol=lf` assertions exist in the entire
suite, both inside `test_migration_files_are_forced_to_lf_checkout`
(`tests/test_migrate_approved_assets.py` 2060–2070), and both are substring
checks against `database/migrations`. Nothing asserted the three trust-root pins,
and nothing asserts the file wholesale — the test reads `.gitattributes` and then
tests two `in` conditions, so every other line in it is unguarded by
construction.

**Which is where the round's own finding is.** The root `.gitattributes` holds
**six** pins, not five:

| line | pin | asserted on `main` | asserted after `498aad7` |
|---|---|---|---|
| 1–2 | the two `database/migrations` files | yes | yes |
| 3 | the secrets baseline | **no** | **no** |
| 4–6 | the three `.github/trust/rollout-policy` files | no | yes |

`498aad7` takes the count from two to five. The sixth is the secrets baseline,
and it is not a decorative entry: `validate.yml` 79 runs the scanner against that
file, the command string is itself pinned at
`tests/test_ci_n8n_isolation_scope.py` 56, and the anchor suite parses the file
as JSON at 1900.

**And the narrowing is this document's, not the workstream's.** The execution
programme already enumerated all six entries, already established that only the
two migration pins were asserted, and then recommended — in those words — to
*assert the three trust-root pins on `root-rollout-tests`*. `498aad7` implements
that recommendation exactly. So the gap is in the recommendation: it counted six
members of the artefact, identified four of them as unguarded, and prescribed a
remedy for three. **A recommendation that enumerates a population and then scopes
itself to the subject that prompted it hands the omission to whoever implements
it faithfully**, and the implementer has no way to see the difference, because
the instruction and the enumeration sit in the same paragraph and disagree only
in their arithmetic. The owner item is corrected below to four.

**The instrument the repair reached for is the right one, and it settles a
question this document could not otherwise close.** `git check-attr` resolves the
*effective* attribute, so it catches a later line, a glob, or a nested file
overriding an exact pin — none of which a substring search can see. Confirmed
against git 2.53.0 in a scratch repository rather than from documentation.

**The mutation that reported a hole, and why the hole was not there.** WS-9's
harness reported that deleting `.gitattributes` left the new assertion passing.
Reproduced, and the explanation is exact: `check-attr` falls back to the index
when the working-tree copy is absent.

| scratch-repo state | `check-attr text eol` resolves to |
|---|---|
| worktree and index both present | set, and lf |
| worktree copy deleted, index entry intact | set, and lf |
| also removed from the index | unspecified, and unspecified |

A commit removes the file from the index too, so the real change is the third row
and the guard fails correctly against it. The defect was in the perturbation, not
in the guard — **a mutation's verdict is uninterpretable until the mutation is
shown to reproduce what the real change does**, which is the *neighbouring
question* bullet applied to an experiment instead of a query. Worth recording
because it is the first time that bullet has been reached from the direction of a
deliberate test rather than an accidental one.

**And the correction: the over-broad half never had the cost attributed to it.**
The earlier form of the guard forbade a nested `.gitattributes` or `.gitignore`
anywhere in the tree, which would indeed have failed an unrelated session for
adding an ordinary ignore file. But that form existed only on the topic branch,
whose base is not the default branch, so the check it would have failed is not a
gate there — the sole ruleset is `main-protected`, scoped to `~DEFAULT_BRANCH`
with an empty exclude list and five contexts. And `main`'s own version of the
assertion projects each match to `path.name` before comparing, so the compared
set is a subset of the two bare names **whatever nesting exists** — it cannot
fail when a nested copy appears. Two independent reasons the cost was
unreachable: the guard was not on a branch where the check gates, and the version
that is on such a branch is inert in that direction.

The last point carries a caution this document has to apply to itself. The first
search for the over-broad guard on `main` looked for the newer form's signature,
found nothing, and would have supported the sentence *the guard is absent from
`main`*. It is not absent; it is present in an older form that cannot fail. The
conclusion survived and the reason did not, which is this document's own rule
about which of the two carries forward — **verifying the absence of a form is not
verifying the absence of the property**, and the check that distinguishes them is
to search for the behaviour rather than for the text that last expressed it.

---

### F-9 — `Adapter Tests` divergence is at least two mechanisms, not one — P3

`Adapter Tests` gives different verdicts to its `push` and `pull_request` twins
on **3 of 122** commits — 2.46% disagreement, implying p ≈ 1.2% per run. A peer
session reported this as "a second, unrelated nondeterminism" and attributed it
to a `pip install --require-hashes` step. Both halves need correcting, and the
entry exists mainly so a red check on platform #128 is not misread.

**It is not one mechanism.** The three divergences, with failing job and step
read from the API rather than from the check name:

| SHA | date | failing job | failing step |
|---|---|---|---|
| `b5a80341` | 07-26 | `reservation-postgres-semantics` | Exercise reservation SQL against ephemeral PostgreSQL |
| `21e0e096` | 07-31 | `reservation-postgres-semantics` | Initialize containers |
| `36cdc765` | 08-11 | `drive-service-supply-chain` | Build the clean reference Drive service image |

Two jobs, three steps. Two of the three are service-container lifecycle — one
literally "Initialize containers", the other SQL against an ephemeral
PostgreSQL — which is a startup race. The third is an image build and shares
nothing with them. **Quoting 2.46% as a single rate repeats the error this
register already carries as "a job is not an owner":** the unit being counted is
the workflow, and the workflow is not the fault. The honest statement is at most
1.2% per run split across two or three unrelated causes, none individually
characterised.

**The step name was misreported, and the difference is not cosmetic.** The
failing step on `36cdc765` is "Build the clean reference Drive service image",
not a bare `pip install --require-hashes`. The hashed install is presumably
*inside* that image build — but an image build carries layer caching, a base
image and a registry pull, none of which a bare pip install has, so the
candidate causes are a different set.

**Confirmed exactly as reported otherwise:** `36cdc765` push `31533947898` at
20:38:22Z **failure** against pull_request `31533952023` at 20:38:25Z
**success** — three seconds apart, neither re-run, same commit, opposite
verdicts.

**Why P3 rather than P1.** `drive-service-supply-chain` is not in the ruleset's
required set, so this blocks no merge and costs no one a re-run. It is recorded
for one reason: **it is currently red on platform #128, and #128 is the fix for
F-8.** Anyone reading that red as evidence against #128 would be reading an
unrelated, non-blocking fault on a different service. The measurement that made
this visible — attempt-1 twin comparison across all 18 workflows — also confirms
`Adapter Tests` is the *only* other workflow in the repository with any
divergence beyond a single instance.

---

### F-10 — the per-job caveat is worth 0.1pp, and it resolves the other way — P3

**Status:** closed. This entry retires an open caveat carried in F-8 and repeated
by the peer session, and records a near-miss that is more useful than the number.

**The caveat.** Every rate in F-8 was measured per *workflow*. `rollout-policy.yml`
runs two jobs — `root-rollout-tests` and `independent-rollout-policy` — and only
the first is the required check that gates merges. I wrote that the true per-job
rate must therefore be **below** the workflow figure, because a workflow-level
divergence could be caused by either job. The peer session flagged the same
limitation independently, in their words "per-workflow not per-job." Two sessions
agreeing that a number is an upper bound is not a measurement of the number.

**Measured.** Attempt-1 job conclusions across all 120 clean twin pairs, split by
job name:

| job | pairs | divergent | d | implied p |
|---|---|---|---|---|
| `root-rollout-tests` | 120 | **10** | 8.33% | **4.36%** |
| `independent-rollout-policy` | 120 | **1** | 0.83% | 0.42% |

`independent-rollout-policy` contributes **1 of the 11** divergences — the
`ce0900dd` event already isolated in F-8. So the workflow figure was never an
aggregate of two comparable sources; one job dominates completely, and the
workflow rate was the job rate all along.

**All four estimates converge:**

| instrument | n | d | p |
|---|---|---|---|
| attempt-1 twin, workflow-level (F-8) | 118 | 8.47% | 4.43% |
| attempt-1 twin, per-job | 120 | 8.33% | **4.36%** |
| attempt-1 twin, per-job, deterministic pairs excluded | 116 | 8.62% | **4.51%** |

**The caveat was real and immaterial, and I had its direction wrong.** I predicted
"below 4.01%". The per-job rate is 4.36%, and once the four deterministically
broken pairs are removed from the denominator — those job-runs could not have
exhibited a flake, so they do not belong in it — it is **4.51%, above** the
figure it was supposed to bound. A caveat that names a real defect can still be
wrong about both its size and its sign. Stating one is not the same as pricing it.

**The both-hit cell, re-verified at job granularity: still 0.** F-8 licensed the
`2p(1−p)` inversion on the observation that no commit had both twins hit by the
flake, against 0.23 expected. At job level the both-*failure* cell holds 4 pairs,
which looked like a 17× excess and a refutation. It is not. Read by cause:

| SHA | both twins fail with | flake? |
|---|---|---|
| `8a2d4313`, `e2e41726`, `a0adabf7` | `collector.…body_invalid`, 1 failed / 168 passed | no — deterministic |
| `138cafa9` | `repository.git_inspection_failed`, `AssertionError: 1 != 0`, 2 failed / 100 passed | no — deterministic |

Four deterministic faults, correctly absorbed into the agreement cell. That is the
twin design working as intended, not evidence against it. Expected double-hits at
p = 4.4% over 120 pairs is 0.23, and 0 observed is the modal outcome.

**The near-miss, which is the part worth keeping.** I classified `138cafa9` as a
double flake hit and was drafting a retraction of a correct published figure. The
evidence was that its logs contain the string `fail_absent`, the F-8 fixture name,
on *both* twins — while the three deterministic pairs contain it on neither. A
clean discriminating signal, and wrong. The string appears in `138cafa9` as
`("fail_absent", "ok", "0", 90),` — a **source line of the fixture table, printed
as traceback context for an unrelated failure.** The F-8 predicate is not the
presence of the name; it is the assertion `assert 1 == 90` at that position.
`138cafa9` asserts `1 != 0` in a different test entirely.

So the fixture name is present in the failing and non-failing cases alike, and
selects on which test happened to be near the traceback. **I matched a token where
the finding required a predicate** — the same substitution the peer session had
confessed to one message earlier, about reading `datetime.now(...)` surviving in a
diff without reading the branch above it. Recognising an error class in someone
else's work confers no immunity to it; I committed it inside the hour, while
holding the description of it in view.

**Guard:** when a log signature is used to classify a failure, the signature must
be the assertion or the raised error, never a fixture, test or symbol name.
Names appear in source context, in collection output and in unrelated tracebacks;
only the predicate fires when the fault fires. Cheapest possible check, and the
one that saved a published number here: read the assertion, not the identifier.

**The attempt-1 rate is not a bound on this flake, and the both-hit cell is empty
by signature rather than by luck.** A peer session proposed that `2p(1−p)` is
structurally blind to pairs that fail *together*, reported **7** such pairs
against the 4 above, and concluded the published figure is low by 2.3× with the
truth in [4%, 9.5%]. The upper endpoint is 27 attempt-1 failures across 285
`rollout-policy.yml` runs. **That 9.47% reproduces exactly.** The inference drawn
from it does not.

Classifying all 27 by the assertion that fired — never by a name, per the guard
directly above:

| assertion | disagreeing | both-fail | contaminated | test |
|---|---|---|---|---|
| `assert 1 == 90` | **8** | **0** | 0 | `…cleanup_status_is_fail_closed` |
| `assert 1 == 37` | 1 | 0 | 0 | same |
| `assert 1 == 0` | 1 | 0 | 7 | same |
| `<no assertion>` (`Set up job`) | 1 | 0 | 0 | — |
| `collector.…body_invalid` | 0 | **6** | 0 | `…transport_policy_accepts_exact_source` |
| `AssertionError: 1 != 0` | 0 | **2** | 0 | `…writes_only_mode_0600_receipt` |

**The two columns do not intersect.** Every occurrence of the F-8 assertion sits
in a disagreeing pair; every both-fail occurrence carries a different assertion.
The both-hit cell is not merely 0 against 0.23 expected — it contains no flake
occurrence at all. `138cafa9` is the one subtlety and it cuts the same way: the
flake's *test* does fail there, but raising `repository.git_inspection_failed`
rather than asserting `1 == N`. Same test, different predicate — exactly the
near-miss recorded above, and the reason that guard is worth its space.

So 9.47% measures a different population: **16 of the 27 are breakage,
contamination or infrastructure; 11 are disagreements, of which 10 carry the
F-8 assertion** and one (`ce0900dd`) is runner provisioning. Inverting the twin
formula on those 10 over 119 decontaminated pairs gives **p ≈ 4.4%**, direct
counting gives 4.20%, and dropping the deterministically-broken pairs from the
denominator gives 4.82% — against 4.36–4.51% published. Every route lands in
[3.5%, 4.8%]; **none lands near 9.47%**. The figure is not a lower bound
depressed by hidden double-hits, because the double-hits are absent by
measurement rather than by assumption.

**The endpoint that reads the run is not the endpoint that reads its jobs, and
only one of them is leak-free.** `runs/{id}/attempts/1/jobs` returns **18**
entries belonging to a different workflow entirely (`Base-trusted rollout
authorization`). An instrument that aggregates `any(job.conclusion ==
"failure")` therefore inherits another workflow's verdict: on `084c4d17` and
`ce0900dd` the push twin has every real job green and an alien job red. The
run's own `attempts/1` conclusion reads `success` for both, correctly. So the
leak **relocates a genuine flake occurrence out of the disagreement cell and
into the agreement cell** — the one direction that would falsify the
independence argument above.

The censuses in this register read the run-level conclusion and were never
exposed. **That was a consequence of the unit chosen, not a defence anyone
designed** — the same shape as the `fullmatch` immunity examined below, where a
property held for a reason nobody had picked. The guard worth stating is
positive: **an instrument must whitelist its population by name at extraction.
What an endpoint returns is a fact about the endpoint, not about the thing being
measured.**

**Where the 7 comes from, established by enumeration rather than by guessing at
the peer's method.** Crossing the two scoping choices available:

| conclusion read | contamination | pairs | ≥1 fail | disagree | both-fail |
|---|---|---|---|---|---|
| attempt 1 | excluded | 120 | 15 | 11 | **4** |
| attempt 1 | retained | 123 | 18 | 11 | 7 |
| top-level | excluded | 120 | 9 | 5 | 4 |
| **top-level** | **retained** | **123** | **12** | **5** | **7** |

Only the last row reproduces their 12 / 5 / 7, and it requires *both* choices at
once. Their rate was taken at attempt 1 — correctly, and it reproduces — but the
pair analysis offered to correct that rate was taken at top level, where one
conclusion stands for the last attempt however many failed beneath it. **A single
claim was assembled from two units, and the half meant to remove a bias carried
the precise bias this register was opened to record.**

**The discriminator I reached for first failed in the same direction.** Before
the table above, the partition was by recurrence: a signature appearing on more
than one commit is breakage. It labelled `assert 1 == 90` — 8 commits across 7
days — as BREAKAGE. Recurrence over scattered commits is what a *flake* does; the
rule inverted the thing it was built to detect, and would have converted the
strongest evidence for F-8 into evidence against it. What corrected it is the
distinction this entry already turns on: **the discriminator must be same-commit
disagreement, which is the definition, not cross-commit frequency, which is a
proxy.** Third instrument in this entry to encode a population definition
invisibly inside an extraction rule, and the second to do it while the warning
against it was on screen.

**The five-case loop converts this into a per-case hazard, and that is the
number that describes the race.** F-8 establishes that the failing test is a
`for` over five cases that aborts at the first failure
(`tests/test_migrate_approved_assets.py` 890–897 at `feee3166`, re-read to
confirm: the list opens at 890, the loop is 897, the case id at 898–900 is an
f-string, which is why the case names appear in no grep). So a per-*test* rate
`p` implies a per-*case* hazard `q = 1 − (1−p)^(1/5)`:

| input | p | implied q |
|---|---|---|
| all runs, 10/285 | 3.51% | 0.71% |
| **paired decontaminated, 10/238** | **4.20%** | **0.85%** |
| **twin inversion, 119 pairs** | **4.39%** | **0.89%** |
| twin inversion, deterministic pairs dropped | 4.82% | 0.98% |
| attempt-1, conflated | 9.47% | 1.97% |

**Corrected per-case band: [0.85%, 0.98%].** The numerator is **10**, not the 11
disagreements: `ce0900dd` is a disagreement but its cause is `Set up job` —
runner provisioning, one of only two such failures in all 285 runs — so it
belongs in the divergence count and not in the flake numerator. The observed
first-passage positions fit a uniform per-case hazard, which is what a timing
race predicts and what a data-dependent fault cannot: attempt-level
[1, 1, 4, 4, 1] gives χ² = 4.91, attempt-1 [1, 1, 4, 3, 1] gives χ² = 4.00, both
on 4 df. **The two vectors are the same data at different units** — the extra
occurrence at position 4 is attempt 2 of `31532517315`, which F-8 names
explicitly (att 1 → position 3, att 2 → position 4). Neither count is wrong.

**And here the conflated bracket did measurable downstream damage.** A peer
carried a competing timing model at 3.2% per operation. Against the bracket
[4%, 9.5%] it read as 1.6–4× high — a range whose lower end is close enough to
call agreement. Against the corrected band it reads as **3.3–3.7× high**, which
is not a near-miss and admits no reading in which the model was nearly right.
**An inflated uncertainty band does not merely lose precision; it launders
disagreement into compatibility.** Decontaminating *both* ends narrows the
interval from 1.25× wide to 1.15× wide, which is the sharper form of the same
point: **a band wide enough to argue inside is a measurement of the instrument,
not of the model.**

**The contamination set was an enumeration that had drifted from its predicate.**
It was carried as four hard-coded SHA prefixes. The property those four share is
`head_branch == palinaruban-backup-evidence-lifecycle`, and that branch carries
**five** — `138cafa9` is the fifth, and this register had it filed as a
deterministic both-fail pair rather than as contamination. The classification
consequence is nil, since both exclude it from the flake, but the mechanism is
the entry's own subject one more time: **a population fixed by listing its
members cannot notice a new member, while the predicate that generated the list
would have.**

---

### F-11 — a required check numbers lines with the wrong definition — P2

**Status:** fixed in this change. Latent, not live — zero current instances.

**The defect.** `validate_sensitive_references.py` read tracked files with
`path.read_text(encoding="utf-8").splitlines()`, numbered them with
`enumerate(lines, 1)`, and printed `f"{path}:{number}: {rule}"` — a reference the
reader is expected to open. `str.splitlines()` breaks on **eleven** separators,
not one:

| | bytes | str |
|---|---|---|
| `0x0a` LF, `0x0d` CR | split | split |
| `0x0b` VT, `0x0c` FF, `0x1c` FS, `0x1d` GS, `0x1e` RS, `0x85` NEL, `0x2028` LS, `0x2029` PS | **no** | split |

Any of the eight `str`-only separators in a tracked file shifts every subsequent
reported line number **up by one** relative to an editor or `git diff`.
Demonstrated with a form feed on line 2 and a secret literal on true line 5: the
check reported **line 6**, the file shows **line 5**.

**Detection is unaffected.** Identical rules fire in both arms. This is a
position error, not a miss — worth stating precisely, because the alarming
reading (a secret scanner with a false negative) is available and wrong.

**Scale.** Scanned all 92 tracked files: **zero** contain any of the eight, and
`lf_delimited_lines(text) != text.splitlines()` for **0 of 92**. The fix is a
provable no-op on the present corpus and correct on the hypothetical one.

**Fix.** A named `lf_delimited_lines()` helper stating the definition it uses —
split on `\n`, then drop a single trailing empty record so that a terminated
file does not gain a phantom last line — plus five tests pinning the seven
terminator cases, all eight separators, the CR case, the numbering consequence,
and agreement with `splitlines()` on LF-only text. Suite: 107 tests, OK.

**Why it is P2 despite having no instance.** The failure is silent and
self-consistent: a wrong line number looks exactly like a right one, and the
reader who opens `path:6` and finds nothing concludes the *check* is stale
rather than the *number* wrong. The cost is paid in trust of a required gate.

**What it corrects in the register.** The peer session concluded that shipped
code gets a library primitive and a test suite while the measuring instrument
gets neither, and that this is why line-counting defects survive. **This is
shipped code, with a test suite, gating CI, carrying the identical defect** — so
the shipped-vs-instrument boundary does not hold. Their own sharper point
explains why: **a line count reads as a definition, and a definition invites no
question**, so the review that catches a proxy has no trigger. It survives a test
suite for the same reason it reaches the inside of a replacement instrument.

**Method note, against myself.** My first attempt to demonstrate this inserted
the form feed *inside* the secret literal, and both arms then lost a rule —
producing an apparent **false negative in a secret scanner**, which is a much
larger finding than the real one. It is an artefact: inserting the byte changed
the *content*, not only the *splitting*. I had conflated the mutation with the
mechanism. Third instance in one session of a confound that made the finding
**bigger** — never once smaller. That asymmetry is the tell, and it is cheaper to
check than the finding: **if an experiment's confound would have understated the
result, it rarely gets built.**

**Second method note, caught in review of this entry.** The paragraph above
originally read "the terminator semantics already settled in F-7". F-7 is about
a documented local check being weaker than the check that decides; the
terminator question was settled in peer correspondence and **has never appeared
in this register at all**. A cross-reference is the one claim in a document that
carries its own apparent verification — a number invites "measured how?", but
"see F-7" reads as already-checked, and the cost of checking rises with the
length of the file that makes citing attractive. This register is now eleven
entries and 2,400 lines, quoted by three sessions. **Guard: an F-number cited in
prose must be opened, not remembered.**

Every `F-n` mention in this file was then checked against the `### F-n` headings:
all eleven resolve, no dangling references. **That check would not have caught
the error that prompted it** — F-7 exists; it simply says something else. An
existence check is the automatable half, and the half that was never in doubt.
The failure mode worth guarding is a citation that resolves and misdescribes,
and it is not mechanisable from the reference alone.

---

### F-12 — the fail-closed gate fails open on a line boundary — P0

**Status:** fixed in this change. **Live, not latent** — the mechanism works today
and the direction is toward approval.

**The defect.** `rehearsal_isolation_guard.py` — whose docstring opens
"Fail-closed isolation gate for the disposable PostgreSQL restore rehearsal" —
parsed pgBackRest and PostgreSQL configuration with `text.splitlines()`, then
handed the resulting value to `Path(candidate).resolve()` and tested it for
membership of an allow-list of ephemeral directories.

**Measured, both arms, same input bytes, only the splitter varied:**

```
pg1-path = /mnt/ephemeral<FF>/../../etc

splitlines()   -> value '/mnt/ephemeral'                -> allowed -> PASS
LF-delimited   -> value '/mnt/ephemeral\x0c/../../etc'  -> /etc    -> FAIL
```

The gate approves a data directory that is not the one the value names. This is
not a missed detection — the check runs, matches, and returns **true**.

**Why only this one site.** The sibling at `check_pgbackrest_config` screens
lines with `REMOTE_PG_OPTION`, which is start-anchored, so an over-split can
truncate the tail without removing the match: it fails **closed**. The
path-valued site fails **open** because truncation does not remove the value, it
*replaces* it with a shorter one that satisfies the predicate. Same wrong line
model, opposite direction, decided entirely by what the consumer does with the
parsed value.

**The precondition, stated rather than assumed.** This is a divergence only if
the tools that read these files are LF-delimited. I could not verify pgBackRest's
or PostgreSQL's parser from here and did not assume it. **The fix does not depend
on resolving it**: rather than pick a side, the gate now treats a file whose line
structure is parser-dependent as one it cannot establish a property about, which
is exactly what `GuardError` is for.

**Fix.** `config_lines()` refuses the eight `str`-only separators by name and
then splits on `\n`. With those eight excluded, LF-splitting and `splitlines()`
agree on every surviving input — so the choice between them becomes **inert**
rather than merely corrected. That is the stronger of the two remedies available,
and the ranking is the point: repairing the call site fixes the instance; making
the hazardous choice unobservable retires the class.

**Scale.** All 92 tracked files decode and **zero** contain any of the eight, so
the new refusal cannot fire on committed content. Suites: 108 tests, OK.

**Against myself: the constant was wrong on the first run, and its own test caught
it.** I wrote **nine** separators, including CR. `test_every_ambiguous_separator_is_refused`
failed on CR immediately. `Path.read_text` decodes in universal-newline mode, so
a lone CR and a CRLF are **both already LF** before any of this code runs —
measured, not reasoned. CR was unreachable by construction, and the entry
asserted a hazard that cannot arrive.

The instructive part is that **F-11 had it right**: that entry names *eight*
`str`-only separators and puts CR in the both-split row. I widened the set from
eight to nine while writing a new module and never re-derived it — a number
carried across a context boundary and silently re-scoped, which is precisely the
population-definition failure the peer sessions and I have now hit six times.
It was caught only because the constant had a test that enumerated it. **A
constant with a test is a claim; a constant without one is a memory.**

**What it corrects in F-11.** F-11 concluded the defect there was cosmetic — a
position error, detection unaffected, "worth stating precisely, because the
alarming reading is available and wrong." That remains true of F-11. But the
generalisation it invites — that a wrong line model is a reporting concern — is
false, and this entry is the counterexample from the same session and the same
mechanism. **The severity of a line-model defect is a property of the consumer,
not of the defect.** In a reporter it moves a number; in a gate it moves a
verdict, toward approval.

**How it was found, because the route matters.** Not by looking for it. A peer
session's regex audit raised whether a trailing `$` admits a newline; the single
site in this repo where that anchor is observable is this guard's `SETTING`
pattern. Establishing that the anchor is inert there required reading the input
source two lines above the call — and that is where `splitlines()` was. **The
anchor question was a false lead, and it was the false lead that reached the real
defect.** A verification that comes back negative still moved the reader to a
place they had no other reason to stand.

**Amendment — the property that predicts which call sites are exploitable.**
There are **34** `.splitlines()` call sites across 18 modules under `scripts/`.
Exactly one was exploitable. "It depends on the consumer" is true and useless;
executing the *other* guard against the identical attack gives the actual rule.

`postgres_restore_guard.validate_forbidden` builds a set of forbidden production
identifiers from a file and splits it the same way. Same attack, a form feed
inside a canonical identifier, run against the real module:

```
file bytes:  b'adapten\x0cg-ops-db\n'

splitlines() -> {'adapten', 'g-ops-db'}   -> GuardError: omits canonical production IDs
LF-delimited -> {'adapten\x0cg-ops-db'}   -> GuardError: omits canonical production IDs
```

**Both arms refuse, and for the same reason.** That guard's predicate is
`KNOWN_FORBIDDEN.issubset(identifiers)` — a demand that named things be
**present**. Corruption of any kind removes them, so every parse divergence
fails it, and the splitter is irrelevant. (The file is SHA-256 pinned by
`checked_bytes` as well, but the digest is not what saves it here — the
`issubset` requirement alone is splitter-independent.)

The site fixed in this entry asserted the opposite: that **no** `pgN-path` lies
outside an allow-list. Truncation does not remove that value, it *shortens* it —
and a shorter path can land **inside** the allowed set.

So the criterion is not "consumer" but **direction of the predicate**:

| the guard requires | effect of a parse divergence | result |
|---|---|---|
| something to be **present** | corruption destroys it | **fails closed** |
| something to be **absent** | corruption can manufacture absence | **fails open** |

**Corruption cannot manufacture presence, but it can manufacture absence.** A
gate whose predicate is "nothing forbidden is here" is vulnerable to any parse
divergence that shrinks, splits or truncates its input; a gate whose predicate is
"everything required is here" is not. That is checkable by reading a predicate,
it is why exactly one of 34 sites was exploitable, and it is the half of this
entry worth carrying to the next module rather than the fix itself.

---

### F-13 — the FX policy says "or stale" and nothing measures age — P2

**Status:** open, platform-side, **not blocking the first model proof**. Filed
because it is invisible at the moment it would matter and because a fresh rate
today conceals it entirely.

**The claim.** `ai/model-choices.md`, gate 5, requires that a live gateway "use
an explicit operator-configured FX rate with `as_of` and fail closed if it is
missing **or stale**".

**The implementation.** `services/ai-gateway/app/fx.py` (platform, `feee3166`,
102 lines) is the only thing that reads the triple. `build_fx_config` refuses an
absent rate, a non-numeric rate, an absent `as_of`, an `as_of` that is not
ISO-8601, and an `as_of` carrying no UTC offset. It computes nothing from the
date afterwards. A census of the whole gateway service for a staleness bound —
`fx.*stale`, `stale.*fx`, `fx_max_age`, `MAX_FX` — returns **nothing**.

**So the gate is bound to the *shape* of `as_of`, not to its *age*.** A rate from
any year satisfies every check in the file, provided it is spelled with an
offset. The one property the policy names as disqualifying is the one property
nothing reads. `as_of` is stored and propagated into the cost record, so the
staleness is auditable *after* a call and refused *before* none.

**Why it is not blocking, stated as a measurement rather than a hope.** The
deployed value is `2026-08-10T16:00:00+02:00`, roughly two days old, sourced
`ECB eurofxref-daily.xml 2026-08-10 EUR/USD 1.1555 inverted`; `0.865426` is
`1/1.1555` to six places, so the inversion is right. Nothing will fail closed on
FX during the first proof — which is exactly why this entry exists. The gap
cannot be found by running the thing that would expose it.

**Shape.** Ninth instance of the recurring defect, and the cleanest statement of
it yet: the check was bound to the nearest *checkable* property rather than to
the asserted one. Age needs a clock and a threshold; shape needs neither, and
shape is what got written. Compare F-12, where the predicate's direction decided
the failure mode — here the predicate's *subject* is simply a different thing
from the policy's.

**Remedy, not made here.** It is one comparison against a configured maximum age,
failing closed, in `build_fx_config` beside the offset check. It belongs in the
platform repository and to whoever owns `fx.py`; filing it against the wrong
repository is how the last four population errors started.

### F-14 — the clause that holds the evidence guard is not the clause that looks like the check — P3

**Status:** open, company-os, **not a live defect and deliberately not fixed**.
Filed so that the next person to tidy this code knows which half is load-bearing,
because the code does not say so and no test says so either.

**The construct.** Identical in two shipped files —
`scripts/postgres_restore_c_final_assert.py:117` and
`scripts/postgres_restore_transaction_probe.py:160`:

```python
evidence_lines = evidence.splitlines()
if len(evidence_lines) != len(RUNNER_EVIDENCE_PREFIXES) or not all(
    sum(line.startswith(prefix) for line in evidence_lines) == 1
    for prefix in RUNNER_EVIDENCE_PREFIXES
):
    raise ...
```

The surviving lines are then printed as the run's audit evidence — nine fields
including `runner_manifest_sha256=`, the two identity digests, four inventory
digests and `runner_path=`-class values.

**The ingress matters and is not universal-newline.** Both sites read
`completed.stderr.decode("utf-8", errors="strict")` — subprocess bytes, decoded
explicitly. So CR is *not* collapsed on the way in, and `str.splitlines()` here
splits on all nine of the characters that `split("\n")` does not: VT, FF, FS, GS,
RS, NEL, LS, PS and CR. A guard fed by `Path.read_text()` would see eight.

**The measurement.** 9 separators × 9 evidence fields = 81 interior injections
per file, run against the predicate read out of the source with `ast` rather than
retyped, so the harness cannot pass while the subject drifts:

| arm | c_final_assert | transaction_probe |
|---|---|---|
| as shipped | **0 / 81 accepted** | **0 / 81 accepted** |
| cardinality clause removed | **81 / 81 accepted** | **81 / 81 accepted** |

Both fail closed today. That is the whole reason this is P3 and not P0.

**But the per-prefix test is not what holds.** It reads as the check — "each
field appears exactly once" — and it looks to subsume a mere count. It does not.
An interior separator splits one field into two lines; the fragment carries no
prefix, so every prefix still appears exactly once and the `all(... == 1)` test
is satisfied. Only the cardinality test notices that there are now ten lines
where nine were expected. Remove it — the natural tidy, since it looks redundant
— and all 81 pass while the audit record prints a **32-character
`runner_manifest_sha256=`** as though it were a digest.

**Nothing pins it.** A census of `scripts/test_*.py` for `RUNNER_EVIDENCE_PREFIXES`,
`evidence_lines`, or either raise message returns **zero** hits. The guard is
inline in `main()`, behind a live `subprocess.run` and an
`os.open(..., os.O_DIRECTORY)` that does not exist on Windows, so it is not
reachable from a test without extracting it. Deleting the cardinality clause
would break no check in CI.

**Why it is documented rather than fixed.** Both files are digest-pinned in
`scripts/postgres_restore_procedure_manifest.json` — by sha256, by git blob sha,
and inside a `member_tree_sha256` rollup — and both pins were verified exact at
`f7382f5`. The manifest's own digest is asserted at 36 sites across 8 modules.
Editing either file to extract a testable predicate therefore forces a manifest
re-ceremony across a restore trust boundary, in the middle of a production
rollout, to obtain **no change in present-day behaviour**. That is the wrong
trade. The finding is worth more written down than acted on.

**Shape.** A cardinality pin is *separator-set-independent*: it does not need to
know which characters split, so it catches CR here and would catch a tenth
separator added by a future Python. An exclusion list requires enumerating the
set correctly and re-checking it whenever the ingress changes. Where the expected
record count is known, the count is the stronger defence — and this guard already
contains it, for a different stated reason (rejecting extra evidence lines).
Rejecting a boundary move is the same act, because a fragment *is* an extra line.
So unlike the other incidental defences in this audit, this one is not luck: the
guard covers the hazard because the hazard is an instance of what the guard is
for.

**One disagreement, recorded rather than smoothed over — and since resolved on
both halves.** The finding reached this repository from a platform session
working on `palinaruban-observable-run-selection-failure` (platform #121). F-15
recorded the sender as unnamed because inbound routing headers are not retained
in stored turns. The author has since identified themselves **from content**, by
locating the disputed row in their own scratch file along with the two
properties that produced it: a fixture carrying two evidence prefixes instead of
nine, and no trailing newline.

**How that attribution was recovered is the durable part, and it inverts the
instinct.** The finding itself carries almost no authorship information — anyone
measuring the guard correctly obtains the same result, because a correct result
is convergent. The *error* is idiosyncratic: only its author produces that
particular fixture. So the field that survives header loss is not the conclusion
but the defect, which is exactly the part a register is tempted to discard once
it has been corrected. As a rule: **route forward by the id the message carries;
attribute backward by the divergent content.**

Their table reported a trailing separator passing both arms, and the correction
recorded here — that it **refuses** in both arms — is right about the mechanism
and wrong about its scope. Measured across every boundary character
`str.splitlines()` recognises, appending the separator before the final newline:

| separator | entries | verdict |
|---|---|---|
| CR | 9 | **passes** |
| LF, VT, FF, FS, GS, RS, NEL, LS, PS | 10 | refuses |

**CR is the exception and it passes correctly**, so it is a true negative rather
than a hole: `\r\n` is a single boundary, no entry is added, and the field values
are byte-identical to the clean text. Setting aside LF as the trivial case, that
is eight refusals out of nine. So each side was wrong once — the original row for
realistic input, because a hand-written fixture omits the trailing newline that
`print()` always emits; the correction for CR, the one separator on which the two
line models agree.

**Remedy, not made here.** Two lines of comment binding the cardinality clause to
boundary-moves, so it is not read as subsumed — to be taken the next time either
file is opened for a reason that already requires re-issuing the manifest, and
not before.

### F-15 — the control plane replied to whoever spoke last, not to whoever wrote — P2

**Status:** open, company-os, **mine, live until the practice changes**, and the
direct cause of two rounds that a correspondent reasonably read as forged.

**The report.** A platform session (`2cab0595`) stated that two consecutive
messages reached this control plane bearing its routing header with content it
did not author, could not disprove authoring because outbound capture is ~2% on
both sides, and recommended ending the correspondence and escalating the
attribution question to the owner as unresolvable.

**It is resolvable, and the cause is here.** Measured against the session store
and the session list:

| fact | value |
|---|---|
| sessions on `adapteng-automation-platform` | **84** |
| distinct platform session ids in this session's inbound | **2** as of that census — **now 3**, see the correction below |
| what those two ids are | *different* sessions, different names, different branches |
| what this control plane recorded them as | **one peer whose id "has changed three times"** |

The working note that drove the behaviour said, in as many words, *"always take
it from the newest `cross_session_message`."* So when session A wrote, the reply
went to whichever session had spoken most recently — frequently B. **B then
received a reply to a message B had never sent**, which is exactly and only what
was reported, twice. No forgery is required to produce it, and none is evidenced.

**Shape, and it is the eleventh instance of the one this audit keeps finding.**
The identity of a correspondent was bound to the nearest *checkable token* — the
most recent session id in the inbox — rather than to the thing actually asserted,
which is *which session authored the message being answered*. Compare the alias
census, where "is there a route" was bound to a display name that was never a
name. Both share the aggravating feature that separates them from the merely
sloppy: **the wrong reading did not just mislead, it dispatched an action at the
wrong subject.** There it was a redeploy; here it was every reply for several
rounds.

**Why it went unnoticed for so long is the part worth keeping.** Each
mis-addressed reply was *substantively correct* and assembled from material both
parties already held, so it generated no friction on arrival. The correspondent's
own formulation applies to the sender as well as the reader: an attribution
nobody contests becomes a fact precisely because it produces no argument. It took
a recipient noticing it was being answered about something it had never said.

**What this does and does not license.** It does not establish that any specific
past message was correctly attributed — inbound routing headers are not retained
in this session's stored turns. That limit is real but not absolute: the author
of the `splitlines` finding in F-14 **has since been named**, not from headers
but from content, by producing the scratch file containing the disputed row and
the fixture that produced it. Headers route forward; only idiosyncratic content
attributes backward, and it can do so after the headers are gone. F-14 carries
the resolution. It does remove the need to mark peer-derived entries
*provenance-unestablished* wholesale: the register never attributed anything to a
session id in the first place, only to "a peer" or to a workstream label, and
those attributions remain accurate at the resolution they claim.

**Remedy, and it is one line of practice, not code.** Reply to the
`from_project_session_id` **of the message being answered**, read from that
message, never to a remembered "current peer" id. A correspondent id is a
property of a message, not of a conversation. Where a finding is recorded from an
external sender, cite the id carried by the message that delivered it, or record
that it is unavailable.

**Why that field and not the other one, added 2026-08-12 after a correspondent
challenged the mechanism.** The routing header carries **two** ids from **two
namespaces**, and only one of them is a correspondent:

| field | example | namespace |
|---|---|---|
| `from_project_session_id` | `2cab0595` | app-surfaced sessions — the peer |
| `from_session_id` | `cf066a90` | CLI session ids — *not* the peer |

The correspondent measured that this control plane's outbound `from_session_id`,
`376bf683`, is identical to the `creator_chat_session_id` recorded in their
workspace. So a reply addressed to `from_session_id` would land **in the chat
that spawned the recipient**, not with the recipient — a second mis-addressing
channel, and one a rule that only said "read it from the message" would not have
closed. Name the field, not just the provenance.

**The count itself, challenged and independently confirmed.** The same
correspondent argued the two-peer finding was unsound because one session
legitimately carries two ids, so two ids need not mean two sessions. The premise
is correct and is why the table above cites *names and branches* rather than id
count. Checked again against the app-surfaced session list, which is the
namespace that can answer it:

```
c96f72e3  session  Ivan-Shyla/adapteng-automation-platform  "fix: name the run-selection error…"
2cab0595  session  Ivan-Shyla/adapteng-automation-platform  "fix(ci): repair the rollout trust anchor…"
376bf683, cf066a90, 6fcb4f9f  ->  absent, being the other namespace
```

Two distinct sessions, same repository, different names. **The count stood at
two when measured.** Worth recording how the challenge went wrong, because it is
this audit's recurring shape once more and it arrived inside a correction to it:
the correspondent searched a local store that holds only CLI session ids, found
zero rows for three project-session ids, and read that absence as evidence about
the count. A table that *structurally cannot contain* the identifier being
searched returns a clean negative and says nothing. Same family as the check-run
census measuring the API's default, and as an `errno` explained by a mechanism
that did not predict it.

**The count is now three, corrected 2026-08-12 the same evening.** A third
platform session wrote in:

```
b14546cd  session  Ivan-Shyla/adapteng-automation-platform
          "fix(ci): scope the n8n isolation check…"   branch palinaruban-test-waiver-horizon-exit-code
```

It had not appeared in the inbound set when the census above was taken, so the
census was right and is now out of date — which is the distinction this entry
was opened to enforce, arriving against its own number. **A correspondent census
is a measurement with a timestamp, not a standing fact**, and any entry that
cites one should be read as *as-of*, exactly as F-19 says of a value that was
correct when written and does not announce when it stops being. The direction of
the error also matters: an undercount of correspondents is the condition that
produces mis-addressing in the first place, so this number failing upward is the
failure mode the entry exists to prevent, not a harmless drift.

**Amendment, 2026-08-12: the timing tell, and why neither side can settle this
from a store.** A later round reversed the direction — the correspondent
reported receiving a reply bearing this control plane's header, correcting a
message it had not written. Two things were measured in response, and both
outlast the individual dispute.

*First, arrival time cannot identify the message being answered.* The
correspondent located the reply as "four minutes after mine" and reasoned from
that. But the send tool states plainly on return that a message is **queued and
processed after the recipient's current turn completes**. Delivery is therefore
decoupled from authorship: a reply arriving four minutes after message N may be
answering N-3, and the sender may have several in flight. F-15's own rule —
never identify a correspondent by recency — applies unchanged to identifying
*which message* is being answered, and that is the half the original entry did
not state. Both parties reasoned from timing in the same round; only one of them
had a rule against it, and it still did not cover the case.

*Second, the outbound record does not exist, established with a positive control
rather than assumed.* A search for the disputed string returned nothing, and
nothing is what it had to return:

| store | holds this session's outbound text? |
|---|---|
| cloud `tool_requests` — 1309 send rows over 177 sessions | **0 rows** for this session, any tool |
| local store | has the session; **has no `tool_requests` table at all** |
| local `turns` | 13 of 132 turns carry assistant text — **9.8%**, and the turn in question is not among them |

The first row is the positive control: the table is real, populated, correctly
queried, and simply does not cover this session. A zero from it is F-16's
structurally-empty corpus once more, and reporting that zero as evidence would
repeat the error this register exists to stop.

> **Amendment 2026-08-12 — the coverage survey above is itself under-covered,
> and the error is F-21's, committed inside the read that was supposed to be the
> careful one.** A correspondent replicated the assistant-text finding on their
> own session (2 of 71 turns, 2.8% — worse than the 9.8% here, and it *could*
> have come out otherwise, which is what makes it a real control). Then they
> pointed out what the table omits. Re-measured here:
>
> ```
> local turns       138 turns, 13 carrying assistant text            9.4%
> local checkpoints 70 rows, checkpoint 1..70, 89,356 chars of overview
> ```
>
> **A durable first-party record does exist, it is substantial, and it survives
> compaction** — the failure mode named two paragraphs below as fatal. The survey
> established a positive control on `tool_requests` and `turns` and concluded the
> *store* was silent; a third table in the same store held seventy rows about this
> session. Checkpoint **62** is titled *"Withdrawing the #128 blocked claim"* —
> the subject of #166, the exact round in dispute. **The instrument that would
> have answered the question was never pointed at it.**
>
> **The rule, and it is the one this entry got wrong about itself.** A positive
> control proves the instrument works. It does not prove you aimed it at
> everything. *Enumerate the tables before concluding a store is silent* —
> otherwise "I established coverage" means only "I established coverage of the
> places I looked," which is the same sentence as having no coverage at all.
>
> **The limit, supplied by the correspondent against their own point.** A
> checkpoint stores a *summary* of what was sent, not the bytes. It answers *what
> did I do that round*, never *what exactly did I write* — so it does not
> discharge the remedy below, it only refutes the claim that nothing survives.
> Fidelity to a summary is not fidelity to the bytes, which is this register's own
> rule arriving one level up.

> **Amendment 2026-08-12 (second) — three figures above are wrong, unusable or
> stale, and the correspondent reported the first against themselves.** They had
> quoted a size for their own durable record; it counted **index rows**, and the
> store writes up to six rows per checkpoint. Their record was **19 artefacts, not
> 114**. That figure never entered this file — but two defects related to it did.
>
> *"Replicated" is withdrawn.* This entry says a correspondent *replicated* the
> assistant-text finding at 2.8% against 9.8% here. Re-measured in one store, one
> query, one instant:
>
> ```
> this session   144 turns, 13 carrying assistant text   9.0%
> theirs          78 turns,  2 carrying assistant text   2.6%
> ```
>
> **3.5× apart.** What replicates is the *qualitative* finding — single-digit
> coverage, unusable as evidence of absence. The quantity does not, and
> "replicated" carries the stronger reading. Both-under-ten-percent is weak
> agreement dressed as a matching one.
>
> *And the rate is a property of a session, not of the store.* It differs 3.5×
> between two sessions doing similar work, so **neither party can quote "the"
> coverage figure**; the 9.8% above was never a fact about the store, only about
> this session that day. Reported by `2cab0595` against their own claim.
>
> *Every figure in the fenced block is stale, and none carried an as-of — this
> entry already disagrees with itself.* It states 9.8% in the table and 9.4% four
> paragraphs later; today the same query returns 9.0%. All three are correct, at
> three different times, and **the numerator never moved.** 13 carrying text
> throughout, against 132 → 138 → 144 turns: the rate fell purely by dilution,
> because writing the entry enlarged the denominator it was measuring. A live
> counter published without its as-of manufactures exactly the "your number
> disagrees with mine" exchange this entry is about. **Publish the query, not the
> value** — already the rule for the receipt base SHA — extends to every figure
> here that names a live store.
>
> *The unit fix does not generalise, which is the same defect one level down.*
> 114 = 6 × 19 holds because that session's checkpoints each indexed six rows.
> This session's do not: 73 checkpoints produce **437** rows, not 438 — checkpoint
> 55 (*"Building the peer reachability probe"*, real, with a 1,255-character
> overview) has no `checkpoint_work_done` row. The divisor is not a constant, so
> row counts are **not invertible** to artefact counts. The remedy is the stated
> one and only that one: **publish the unit.** Publishing a conversion factor
> instead relocates the assumption rather than removing it.
>
> *Why a positive control could never have caught this — supplied by the
> correspondent, and it is the reusable part.* A control is chosen **because it is
> known to be populated**, which selects for tables you already know about. It
> cannot, even in principle, surface the table you did not know existed. So it
> needs pairing with a step that answers a different question: not *does the
> instrument work* but *what is there to aim it at*. One `GROUP BY source_type` is
> that step, and it is what exposed the unit defect — counting never could have,
> because the count was correct. **Enumerate the shape, not just the size.**
>
> *A live F-21 committed inside this very read, and the control that caught it.*
> The join testing which checkpoint lacked a `work_done` row compared `source_id`
> across two `source_type`s — but the id **ends with its own type**, so the
> predicate could not match and all 73 checkpoints came back "missing". It was
> caught in one step, and not by looking: the result claimed **73** missing from a
> population where a count already in hand allowed at most **1**. **A
> structurally-broken predicate tends to return the whole population, so check a
> returned set's cardinality against a count you already hold.** That is a control
> F-21 lacked, and it works exactly where F-21's trap bites — where the output
> looks entirely reasonable.

*What settled it, and what did not.* The described content traces to a real
artefact — company-os #166, *"Withdraw the claim that #128 is blocked"*, merged
`2026-08-12T12:32:39Z`, authored here — so the message is plausibly this control
plane's, but from hours earlier rather than the one just sent. That is exactly
what queued delivery produces, and it requires no misattribution by either
party. The only decisive evidence was first-party: the sent text sits in this
session's own context. **That is not a record, it is a memory that happens to be
honest**, and the verbatim text does not survive the next compaction — the
checkpoint summary of the round does, per the amendment above, but the bytes do
not.

**Second remedy, and this one is a file, not a resolution.** Persist every
outbound cross-session message to the session artifacts directory *before*
sending it. Cost is one write; the return is that any future claim of the form
"I did not send that" becomes checkable by both parties rather than asserted by
one. Adopted here as of this entry. It does not repair the peer's side and it
does not recover past rounds — F-14's lesson stands, that once headers are gone
only idiosyncratic content attributes backward.

### F-16 — a check-run census that says "all" is measuring the API's default — P2

**Status:** open, company-os practice defect, **mine**, and it has already
produced one published wrong statement about another team's pull request.

**The claim.** This control plane enumerated the check-runs on platform #128 at
`36cdc765`, reported **"all thirty check-runs: 28 success, 2 failure"**, and used
that census to tell a correspondent that their report of a failing
`drive-service-supply-chain` was wrong. The census was correct arithmetic over
the wrong population.

**The measurement.** `GET /repos/{repo}/commits/{sha}/check-runs` accepts a
`filter` parameter defaulting to `latest`, which returns the newest run **per
check name** and drops every superseded one. On the same head, unchanged:

| filter | total | non-success |
|---|---|---|
| `latest` (default, and what was measured) | 30 | 2 |
| `all` (what "all" was reported to mean) | **40** | **4** |

And the disputed check's real history on that head:

```
2026-08-11T20:38:25Z  failure   run 31533947898
2026-08-11T20:38:27Z  success   run 31533952023
2026-08-12T06:58:17Z  success   run 31533947898   ← re-run of the failed job
```

**So the correspondent was right.** `drive-service-supply-chain` did fail, on a
transient PyPI `BrokenPipeError` inside `docker build`, and their prescribed
remedy — re-run that job — had already been applied at 06:58:17Z, *between* their
measurement and this one. They were reporting the world as it was; this control
plane reported the world as it had since become, and called the difference an
error in them.

**Shape, and it is the twelfth instance, with a new aggravating feature.** The
population was bound to the nearest *retrievable* set — whatever the endpoint
returns by default — rather than to the set actually asserted, which is *every*
check-run on the head. Compare the secrets census measured against the wrong
repository. What is new here is that the wrong population **did not merely omit,
dismiss or concur — it manufactured a contradiction of a correct external
report**, and did so with an exact-sounding integer that made the claim harder to
question, not easier.

**And the corroboration that made it harder still was not independent — this is
the sharpest form of the entry and it is the correspondent's.** The wrong figure
was confirmed back to this control plane by the very party it contradicted, in
the words *"thirty of thirty… I have no quarrel with any of it."* They have since
established why: their census called the same endpoint and inherited the same
unstated default. **Two instruments agreed because they shared a defect, and the
agreement was presented as corroboration.** The general rule, theirs and adopted
verbatim: **when a second measurement agrees, the question is not whether it
agrees but whether it *could have disagreed*.** A confirmation that was
incapable of dissent carries no information, and it is worse than a lone wrong
number, because a reader weights the second source most. Practically: before
citing a confirming measurement, state what it would have taken for that
measurement to come out differently. If nothing would have, it is one
measurement reported twice.

**The second defect is temporal and is the more general one.** Two observers
measured a moving subject at different times, and the later one treated the
disagreement as the other's error rather than as evidence that the subject had
moved. A check-run census is a timestamped observation, not a property of a SHA.
Any disagreement about CI state should first ask *when* the other party looked.

**Remedy, one line of practice.** Pass `filter=all` explicitly when the word
"all" is going to appear in the finding, report the filter alongside the count,
and quote the observation time. Where a census contradicts an external report,
re-read before publishing the contradiction.

**A third party proposed the better generalisation and it is adopted here.** The
coordinates a CI figure needs are **owner + population + rule + as-of**; drop any
one and the number remains quotable and still wrong. Their worked case is sharper
than this entry's: two of their populations have *identical* cardinality, so a
figure moved from one to the other is **verifiably** correct in the destination
and no audit can catch it. Attaching provenance to a figure is therefore
necessary and not sufficient — the population must travel too.

**A mechanism for the extra four was proposed by a correspondent, and this
register had already measured and filed it — three entries below, as F-17.**
That is recorded here as the entry's own worst instance. The correspondent
reported that the two `filter=all` copies of the anchor failure share an
`external_id` and a check *suite*, and that the suite belongs to an unrelated
**push** run rather than to the anchor's own. **All of that is F-17.1**, measured
here at `36cdc765` with both suite ids, the ten-against-nine job count, and the
`external_id` discriminator. The reply sent was *"the strongest thing in your
message… recorded; I am not verifying it."* **It did not need verifying and it
was not the correspondent's — it was this register's, unrecognised on return.**

**The shape is the entry's own, with the register as the population.** Every
other instance in this file is a query whose corpus could not contain the answer.
Here the corpus did contain it, in the same document, and was not consulted. The
consequences were all one-directional: a finding under-credited to itself,
over-credited to a correspondent, described as *"checkable"* when it was already
checked, and — the costly part — a mechanism this control plane already owned was
not brought to a dispute where it was decisive. **A record that is not re-read
degrades to the same condition as a record that is wrong**, and it fails more
quietly, because nothing contradicts it.

**Applied, which is what was missed.** F-17.1 says an API-created check-run
attaches to the existing suite for `(app=github-actions, head_sha)` rather than
to the run that posted it. So the second anchor check-run was created **by a
POST** — the verifier firing again — and not by a re-run duplicating anything.
The correspondent's causal claim, that re-running an unrelated workflow
manufactured the duplicate, rested on the duplicate's timestamp matching that
run's `updated_at`; their own figures put the run's completion forty-six seconds
earlier, so `updated_at` is a last-modified field that a check-run insertion
would itself write to. **F-17.2 already records the same class of trap** —
`run_started_at` is rewritten by a re-run while `created_at` is not — so treating
a mutable Actions timestamp as an event witness is a filed hazard here, not a new
observation. The objection therefore stands on a mechanism rather than on
arithmetic, and it stands on this register's own prior work.

This matters beyond attribution because the two mechanisms imply **different
inflation models for the same count**: one correlating anchor reds with push
re-run activity, the other with anchor invocations. F-16 is about which
population a number is over, so adopting an unverified inflation model would
reproduce the defect one level up.

### F-17 — two Actions fields that look stable and are not — P2

**Status:** open, measurement practice, **mine and at least two correspondents'**;
each of the two has already produced a published error about platform #128.

Both were found while checking a third party's report, and neither is a defect in
the platform — they are properties of the Actions API that make a careful census
wrong.

**1. A check-run created via the Checks API is filed under another workflow's
check suite, and then reports as that workflow's job.** Measured at `36cdc765`:

| object | id | check suite |
|---|---|---|
| `Adapter Tests` run, event `push` | `31533947898` | **85541064513** |
| `Base-Trusted Rollout Authorization` run, event `pull_request_target` | `31533952257` | 85541077280 |
| check-run `Base-trusted rollout authorization` | `93920483971` | **85541064513** |

The anchor's check-run lands in the **push run's** suite, because GitHub attaches
an API-created check-run to the existing suite for `(app=github-actions,
head_sha)` rather than to the suite of the run that posted it. The consequences
are not obvious:

- `GET /actions/runs/31533947898/jobs` returns **10 jobs** against the pull
  twin's 9, and the tenth is `Base-trusted rollout authorization`. **There is no
  such job.** `adapter-tests.yml` contains no anchor step and is byte-identical
  on `main` and at `36cdc765` — verified, not assumed.
- A reader therefore concludes the twins are structurally asymmetric and that a
  push-only job could manufacture a false divergence. **The evidence at hand
  falsifies that**: the anchor check-run is `failure` on both attempts while run
  `31533947898` attempt 2 concludes **`success`**. An API-created check-run does
  not enter the run conclusion, so it cannot move a conclusion-level comparison.
- `job.id == check_run.id` for real jobs, so id-matching does not separate them
  either. The separator is `external_id`: GitHub's is a UUID, this one is
  `adapteng-rollout-trust-v1:<repo>:128`.

**Independently reproduced, twice, and both times returned here as news.** Two
separate platform sessions later measured this and reported it as a new finding —
one from suite membership and the `external_id`, one from a suite census showing
**10 runs in `85541064513` against 9 in the `pull_request` twin's
`85541076809`, differing by exactly this check-run.** Both are correct and the
second is the cleanest statement of it. Recorded for two reasons. First, the
finding is now confirmed by measurements that *could* have disagreed and did not,
which is the standard F-16 sets. Second, and less comfortably: on the first
return this register accepted it as the correspondent's and replied that it was
*not verifying it*, having filed it here already. See F-16, where that failure is
recorded — **the corpus not searched was this document.**

**2. `run_started_at` is rewritten by a re-run; `created_at` is not.** The same
push run now reports `created_at = 2026-08-11T20:38:22Z` but `run_started_at =
2026-08-12T06:58:13Z`, because it is on `run_attempt = 2`. Any run-level census
keyed on `run_started_at` silently moves when someone re-runs a job, and a census
taken before a re-run cannot be reconciled with one taken after unless the
attempt is recorded. **Use `created_at`, and record `run_attempt`.**

**Shape.** Both are the same failure as F-16 seen from a different side: a
quantity was bound to the nearest *stable-looking* field rather than to the one
that means what the finding claims. Here the misleading fields are stable
*in appearance* — a job list and a start time are exactly what one would reach
for — which is why three separate parties reached for them.

### F-18 — the readiness gate names disk faults like contract findings — P3, backlog

**Not blocking, and recorded only because of *when* it would bite.** Reported by
a platform session, verified here against
`scripts/validation/validate_approved_assets_rollout.py` on `main` rather than
adopted: the module has exactly **one error class** and 85 raise sites.

```
368       class ContractError(ValueError)          ← the only error class
444-445   except OSError                   → ContractError(f"{label}.unreadable")
894-895   except (OSError, SubprocessError,
                  UnicodeError)            → ContractError("repository.git_inspection_failed")
904-905   except OSError                   → ContractError("repository.required_file_unavailable")
1815      except OSError                   → prints and returns 2, no verdict at all
```

Three sites give a pure infrastructure fault a name in the `repository.`
namespace — **contract-shaped, reachable only from a disk or subprocess
failure**. Line 1815 is the tell: the author felt the gap once and solved it
locally with an ad-hoc non-verdict exit instead of a second channel.

**Why it is worth a line despite being a diagnosability defect rather than a
safety one.** This is the validator behind the *offline rollout-readiness gate*,
which the owner must run before any approved-assets phase is dispatched, from a
POSIX host, in a lifecycle where a burned run is never retried. If a disk or
`git` fault surfaces as `repository.unreadable`, the owner's reasonable reading
is "my evidence packet is wrong" when the true cause is "the machine faulted."
That is a misdiagnosis at the one moment it is most expensive. It fails closed
either way, so nothing unsafe passes.

**Why it is not being fixed now.** The file is itself in
`PROTECTED_EXACT_PATHS` (line 92) *and* digest-pinned (line 405), so a fix is a
protected-path change requiring its own pin update — a second merge past an
advisory refusal, for a defect that blocks nothing. The remedy shape is already
shipped next door: the trust anchor's split between `undetermined.*` (broken
check, needs an operator) and `unauthorized.*` (refused change, needs an
approver). Same split, same file, whenever the freeze lifts.

**Shape.** Fourth sibling of F-16/F-17 and the same one as the anchor's own
former defect: the condition was bound to the nearest *available* verdict name
rather than to the one that means what happened.

### F-19 — asking the environment a question only the executable can answer — P2

Two instances, both in this control plane, both caught before they reached the
owner, both found the same way: by reading the script instead of describing it.

**Instance 1 — the interpreter that is present and still refused.** Owner lifted
the safety constraint, so I re-tested the four hard stops on the go-live path to
see which were policy and which were capability. `Get-Command bash` returned
`C:\WINDOWS\system32\bash.exe`, and Git Bash at
`C:\Program Files\Git\bin\bash.exe`. I drafted the conclusion that the POSIX stop
was **soft**. It is not. `authorize_approved_assets_phase.sh` lines 113–118:

```
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    printf '%s\n' "lifecycle.trusted_posix_required" >&2
    exit 1
```

The script detects and refuses **exactly** the workaround, before any work. I had
asked the *host* whether a POSIX shell existed. The only authority on whether the
script would accept one was the script.

**Instance 2 — the chain that was wrong by a factor of five.** §16.2 was written
from the runbook's prose. Read from the script's argument parsing and the
workflow's inputs, it is five governed phases rather than one step, each
consuming the previous phase's run ID; and the operator never dispatches the
workflow, because the script does it at line 237. The prose was not lying — it
was a summary, and I promoted a summary to an execution plan.

**Shape, and it is a new one for this register.** Every earlier F-16 sibling is a
question put to the *wrong corpus*, or to no corpus. This is a question put to
the wrong **authority**: the corpus consulted was real, populated and correctly
read, and still could not settle the question, because the deciding fact lived in
the artefact that would execute. **Capability is not permission, and description
is not contract.** When the question is "will this run", the only source that
answers it is the thing that runs.

Sharper than the general form: both instances would have produced a *confident*
wrong answer, not a missing one. Instance 1 had positive evidence — a real
interpreter at a real path. Instance 2 had an authoritative document. Neither
looked like a gap, which is why neither prompted a second look.

**Disposition.** Both fixed in `current-state.md` §16.2/§16.3/§16.6, with line
numbers so the next reader verifies rather than trusts. Standing rule added: for
any step on the go-live path, cite the executing source and its line, not the
runbook that describes it.

### F-20 — the artifact you did not mean to create — P2

A correspondent reported a tenth file in the shared `%TEMP%` directory and
attributed it to this control plane: a UTF-16LE copy of a platform module,
produced by a PowerShell redirect re-encoding a `git show`. Measured at
2026-08-12T20:0xZ: **the file is gone.** The directory now holds nine `.py`
files and `val.py` is present at 16120 bytes / `E489A24F…`, matching their
report exactly, so the census was sound when taken and has since moved.

**I cannot establish authorship after the fact, and that is the finding rather
than an evasion of it.** For an artifact you have forgotten writing, *"is it
mine"* is not answerable — which is precisely why the criterion has to be
positional. *"Is it under the container I created"* stays answerable about files
you never knew existed.

**First-party instance, measured in the same session, so this does not rest on
the correspondent's inference.** Fetching four platform blobs, my first attempt
used an `Accept: …raw` header that 404s on that endpoint. The failure still
**wrote three files** — under exactly the names the good copies would later take,
each containing the API's error text. The write succeeded; only the content was
wrong.

It was caught by a cheap invariant printed in the same breath as the write: a
line count. `auth.sh 9 lines` against a 410-line shell script is not a subtle
signal. Nothing about the exit status or the file's existence would have shown
it, and the second fetch overwrote the names, so by the time the work was done
the only evidence was a directory nobody was going to re-list.

**Shape, and it is not F-19's.** F-19 is asking the wrong authority. This is a
**failed operation that still produces an artifact**, so any cleanup keyed to the
things you *meant* to make will miss it — the intended set and the written set
differ, and only the intended set is memorable. "Delete my temp file" is true of
the object you are thinking about and false of the directory.

**Two rules, both stronger than the one they replace.** The prior rule was *never
write scratch to shared `%TEMP%`*, which is necessary and turned out not to be
sufficient. Add:

- **Scope cleanup to a container you created, never to a list of filenames you
  intended.** `Remove-Item <dir> -Recurse` is what actually cleared the three
  bad files in this session; a filename-keyed cleanup would have left them,
  because the names had been reused by the good copies.
- **Print a cheap invariant at the moment of any fetch or write** — size, line
  count, first bytes. A failed operation that produces a non-empty file is
  invisible to exit status and to `Test-Path`, and visible immediately to a
  count that contradicts the thing being fetched.

**Amendment 2026-08-12 — the name-family heuristic was not merely weak, it was
inverted, and I had recorded the near-miss backwards.** A running note in this
lane held that my scratch family `cmsg*` had "missed `cm*` by two characters,"
i.e. that `cmsg*` was mine and the `cm*` files in the shared directory belonged
to someone else. A correspondent, measuring the same directory, concluded a third
thing: that `cm*` belonged to *neither* of us.

Settled by reading the files rather than their names:

| file | evidence | owner |
|---|---|---|
| `cm3.txt`, `cm4.txt` | my commit messages — gateway network alias, `ops-runner` census | **mine** |
| `cm143.txt`, `cm143b.txt`, `cm144.txt` | my commit messages — F-8 per-job flake rate, `validate_sensitive_references.py` line numbering | **mine** |
| `cmsg.txt`, `cmsg2.txt` | *(redacted — see (a) below)*; written 21:04 and 21:09 that evening | **not this session's — see (b)** |

So the family I believed was mine is someone else's, the family I believed was
someone else's is mine, and the correspondent's reading was wrong in a third
direction. **Three parties, three different wrong answers, all derived from
names.**

The near-miss is therefore worse than recorded. It was never *"I nearly swept a
stranger's files"*; it was *"I had the two families the wrong way round."* Acting
on the belief as written — deleting `cmsg*` as my own — would have destroyed
another occupant's live work from forty minutes earlier.

**Two amendments to the row above, 2026-08-12, both against me.**

**(a) The evidence column is redacted, and the harm assessment is downgraded in
the same breath.** It quoted another occupant's working subject, read from a file
I had already decided was not mine, then republished into a second session on a
different repository and committed here. The redaction is the right default: the
row needs *"subject matter this session did not produce"*, never the subject
matter itself. But the disclosure turned out to be far less serious than it first
appeared, for the reason in (b), and overstating it would be its own error.

**(b) The conclusion is not merely unsupported — it is refuted, and by evidence I
had already read.** The file names **`adapteng.com`**: the public marketing site
of the principal this control plane acts for, whose Cloudways WordPress and
cache-purge operations this repository documents in at least six files, including
a named purge run in `ARCHITECTURE.md`. It is not a stranger's project. The
leading explanation is **another session of the same principal**, and
"third party's, live" should never have been written.

**The failure is worse than an instrument limitation.** I did not miss the
evidence — I opened the file, the company's own domain was in the text, and I
still filed it as foreign. The test I ran was *"do I recognise this work?"*; the
test I recorded was *"does this belong to my principal?"* **Session and principal
are different levels, and I compared across them without noticing** — F-24/F-25's
family in a third substrate, and the most expensive instrument returned the
answer I can least sustain.

**The rule this replaces the naming caution with.** A shared-namespace prefix is
not weak evidence of ownership; it is evidence **that can point the wrong way,
with nothing in the name to say which way it points.** Three instruments settle
it, and they are not interchangeable:

- **Container** — *is it under a path I created?* Survives amnesia, which is the
  case F-20 exists for, and discloses nothing.
- **Metadata** — mtime, size, extension. Cheap, non-disclosing, often sufficient
  to separate families.
- **Content** — *does it say something only I would have written?* Works
  retroactively, which container does not — and **it is the only one of the three
  that requires opening the file, which you must do before you learn whose it
  is.** The ordering cannot be fixed: *you cannot consult the content to decide
  whether you are permitted to consult the content.*

**Content's cost falls on someone who is not party to the question.** Container
and metadata cost nothing to anyone. Content spends another occupant's
confidentiality to settle a dispute between two other parties — and the one
paying never joined the conversation and cannot consent. Calling that a trade-off
misdescribes it, because the party trading is not the party paying. That the cost
happened to land on the same principal here is luck, not design: **it was
unknowable until after the file was open**, which is precisely the ordering
defect above.

**The dissolution, which is better than the trade-off.** Attribution is only ever
needed to *license a deletion*. If the default is **"do not delete what is not
under a container you created"**, the demand for retroactive attribution never
arises: the correct response to an unattributable file is to leave it, and
leaving it requires no attribution at all. Container does not merely beat content
going forward — **it removes the question content exists to answer.** Content
narrows to the one case where it is genuinely irreplaceable: files you scattered
yourself and are about to remove, read once, deliberately, immediately before
removal. Reported by platform session `2cab0595`; adopted in full.

**Applied immediately, and announced.** The five content-proven files above were
removed and the other two were left in place, with the list published to the
other occupant. An unannounced deletion is what produced this entire
investigation — a directory in which files vanish and no one will claim them — so
the deletion was named rather than merely performed.

**And the deletion inference is withdrawn, both halves.** The correspondent
concluded a third occupant was deleting, by the chain *neither of us owns `cm*` →
neither of us deleted it → someone else did*. Correcting the ownership — `cm*` is
mine — breaks the first link, and every later link was carried by it. My own
surviving half (*`cm.txt`/`cm2.txt` were probably mine and someone deleted them*)
inherited the same support and falls with it. Once the files are mine, the
ordinary explanation is available for the first time: **an owner deleted their own
files and does not recall it** — which the five announced removals above
demonstrate, since they would have been invisible had they not been published.
**Third-party deletion is unsupported, not disproven**; the distinction matters
because *someone else works here* is a fact to route around, while *someone else
deletes here* is a reason to distrust every census either party takes.

### F-21 — the predicate that cannot match what it is searching for — P2

Two agents, working the same question independently, each wrote a query that was
well-formed, returned a clean answer, and **structurally could not have found the
subject it was looking for.** Both caught it; neither was saved by the result
looking wrong, because it didn't.

| | the query | why it could not match |
|---|---|---|
| mine | anchor runs *updated at* `06:59:03Z` | a run that started earlier and finished later **contains** the instant while stamping a different `updated_at`. The containment case is the only interesting one, and it is exactly the one excluded. |
| theirs | runs with `created=2026-08-12` | returns runs *created* that day, excluding the one run created Aug 11 and **re-run** Aug 12 — the single run the question was about. |

Both answers survived re-measurement on the corrected predicate, which is the
trap: **a defective query that happens to return the true answer leaves no
evidence it was defective.** Mine reported `0` and the corrected containment test
also reports `0`. Nothing in the output distinguished them.

**Shape, and it is not F-16's.** F-16 is an API *default* silently narrowing a
result set — the fix is to pass `filter=all`. Here the default is irrelevant and
the API behaves correctly; the defect is in the **choice of predicate**, and no
flag repairs it. It is also not F-19: the authority answered was the right one,
it was simply asked a question whose shape excluded the answer.

**The rule.** Before trusting a negative, state the case that would produce a
positive and check that the predicate can express it. For an instant against an
interval that is `start <= t <= end`, never `end == t`. For an object that may
predate the window, anchor on identity (`head_sha`, id) rather than on a creation
date.

**And bound the margin instead of asserting it — but bound the half that needs
it.** The repaired read did not stop at `0`; it went on to interval arithmetic a
reader can check without re-running anything, and that is the durable part. **The
exemplar originally recorded here was the wrong comparison,** and it is now F-25:
it bounded the runs beginning *after* the instant — 328 s after, against a 318 s
longest-ever — where ordering alone already excludes them and the duration does no
work at all. The comparison that actually carries the conclusion is on the
before-side: nearest prior anchor run `31533952257` at `2026-08-11T20:38:25Z`, a
gap of **37,238 s** against a longest-ever **318 s** and a declared
`timeout-minutes: 5` — a margin of **117×**. Same conclusion, no longer fragile.
See F-25 for the general form.

### F-22 — spending an irreversible resource on a check that never executes — P1

**P1 because the resource is one-shot and the loss is unrecoverable.** Everything
else in this register costs time.

The rollout verifier audits base-tree closure at
`verify_rollout_trust_anchor.py:2669` and does not open the approval receipt
until `:2705`. With a stale base the closure audit raises and **the receipt is
never read at all**. The natural operator response to a check named
*Base-trusted rollout authorization* reading `unauthorized` is to go and obtain
the authorization — an out-of-band ceremony that can be performed once. Against a
stale base that ceremony is spent on a code path the verifier does not reach.

**The verdict names the guard that fired, not the condition you must change.**
The string was `rollout_trust_anchor.unauthorized.closure.dynamic_import`. It
names the closure mechanism. The actual defect was that the PR's base predated
another merge. Both agents working this initially read it as *the trust root is
broken*, and one of them checked and refuted that on `main` — all 15 pinned
digests matched there. The mismatch existed **only** against the stale base, and
nothing in the verdict said so.

**The compounding half: once reachable, the receipt is bound to a moving
target.** `:2394`–`:2409` binds `base_sha`, `base_tree_sha`, `subject_sha`,
`subject_tree_sha` and the exact protected-change set. Any commit landing on the
base branch between issuance and merge advances `live.base_sha` and voids the
receipt on line 2404. So the resource is not only spendable on an unreachable
check — it also **decays while held**.

**Two rules.**

- **Before spending an irreversible resource to satisfy a failing check, find the
  line that raises the *current* verdict and confirm the resource is consumed
  *after* it.** A red check tells you which guard fired, not which of the guards
  downstream of it would have. Reading 36 lines of source is cheap; the ceremony
  is not.
- **Freeze the base between issuance and merge, and re-confirm immediately
  before issuing.** "The base was current when I started" is not the property the
  verifier checks.

**Amendment, 2026-08-12 — the gap between the two lines is not empty, and what is
in it changes the operator's instructions.** This control plane strengthened the
ordering claim to *"`:2705` is the first point the file touches approval material
at all"* and published it as a two-line excerpt showing `2669` and `2705` alone.
`_approval_material_introduced` runs at `:2677`; `:2705` is the first read of the
receipt **entries**. Corrected by platform session `c96f72e3` against a claim
made in their favour. The ordering conclusion is unaffected, so the P1 stands.

Two things follow, and the second is worth more than the correction:

- **A two-point ordering proof presented as a two-point excerpt invites the
  reader to assume the gap is empty.** It is the same defect as F-25 one level
  down: the excerpt is accurate and the elision does the misleading. If an
  argument depends on `A` preceding `B`, quote what is *between* them or say
  explicitly that you have not.
- **The gap holds four raise sites** — `approval.unexpected` (`:2683`),
  `approval.commit_parent_invalid` (`:2690`), `approval.circular_or_stale`
  (`:2702`), `approval.commit_delta_invalid` (`:2704`) — so the ceremony can
  still fail after the base is correct and before the signature is opened. The
  worst of them is `:2702`: **the repair fails because performing it makes you
  the thing being checked.** The subject commit must not introduce approval
  material, so committing a corrected receipt on top of a failed one turns the
  failed receipt into the subject and raises. The remedy an operator reaches for
  precisely *because* the check went red is the one guaranteed to fail, and a
  fresh nonce means it cannot be made to pass. Only resetting to the subject
  works. **When a guard inspects the parent of the thing you are fixing, a fix
  that adds a commit changes what is inspected.**

**And the clause was already in this record — which is worse, not better.**
`current-state.md` has listed all four constraints since the §15 analysis,
`approval.circular_or_stale` among them. What was missing is the *consequence*:
the clause was recorded as a condition to satisfy, never as a repair that is
foreclosed. Twenty lines below it sat the sentence *"the remedy is a new commit
on top, not an amend"* — true of the code PR it was written about, and precisely
the wrong move for a failed receipt. **Two correct sentences, adjacent, whose
conjunction is a trap.** Both have now been cross-annotated.

- **Recording a guard's condition is not recording its consequences.** A
  constraint list answers "what must be true"; an operator under a red check is
  asking "what do I do now", and the same clause read that way often forecloses
  the obvious answer. Where a document gives remedies, check each one against
  every guard, not just the guard it was written for.

**Shape.** This is not F-19 — the right authority was asked. It is closer to
F-21's family: the *observation* is accurate and the *inference drawn from it* is
about a mechanism the observation cannot see. `unauthorized` is true; "therefore
supply authorization" does not follow, and the code is the only place that shows
why.

### F-23 — the remedy aimed at a path the fault did not take — P2

**First instance, and it was mine, merged, and in the owner's execution path.**
Having measured two anchor check-runs against one verification, I filed the
remedy *"make the POST idempotent on `external_id`"* and wrote that idempotency
"closes the defect under every hypothesis." Platform session `2cab0595` read the
function I was proposing to change:

```
ensure_check_run  :2829  lists by check_name + filter=all
                  :2855  if check_runs:  -> PATCH existing
                  :2880  else:           -> POST new
                  :2856  external_id already validated before the PATCH
```

**The remedy was already implemented, on a broader key** — `CHECK_NAME` is a
module constant matching every anchor mark on the head, where `external_id` is
only per-pull-request. They put a positive control under it on two heads neither
party had measured, each with two anchor runs that both executed the verifier
step: one check-run each, no duplicate. It could have come out the other way.

**The part that generalises is not the redundancy.** Redundancy alone would make
it merely wasted work. The duplicate on `36cdc765` **exists in the presence of
that guard**, so whatever created it never entered the POST branch — that branch
is reached only on an empty list, and it would have PATCHed. A stronger key
inside a function the fault provably did not pass through cannot prevent it.

- **Before proposing a fix to a code path, confirm the fault went through that
  path.** "This would have prevented it" is a claim about the route, and a route
  is checkable — usually by reading the branch condition. I checked neither.
- **A fix that is already present is worse than no fix filed**, because it
  consumes the reader's belief that the defect is addressed while the real one
  stays armed. The owner would have found it implemented and moved on.
- **Prefer the remedy that survives not knowing the cause.** I had already
  written that the cause was unmeasured; the correct response to an unmeasured
  cause is a remedy that does not name a mechanism, and mine named one.

**And what the real defect turned out to be — the category, not the count.**
`total_count > 1` raises a plain `TrustError` at `:2854`, which `_verify_command`
classifies at `:3167` as **`unauthorized`, exit 1** rather than `undetermined`,
exit 75. So a platform duplication is announced as an attempted forgery. It fires
at `:3141`, before any verification, and leaves `handle` at `None`, so
`_report_failure` at `:3118` publishes nothing at all.

**Why it survived a suite that tests exactly this case.**
`tests/test_rollout_trust_anchor.py:3305`–`3333` builds the two-check-run fixture
and asserts:

```python
with self.assertRaises(anchor.TrustError) as raised:   # 3327
self.assertEqual(raised.exception.code, code)          # 3333
```

`UndeterminedError` **subclasses** `TrustError` (`:485`). The assertion therefore
passes identically whichever category is raised — **converting it or leaving it
makes zero test difference.** That is F-10 exactly: the assertion, not the
fixture, is what classifies. *Assert the category, not only the code.*

**Not mine to fix.** The verifier is in the platform repository, which this
control plane holds read-only. What is mine is step 0 of §16.5, which now
requires confirming the head carries exactly one anchor check-run before the
ceremony — measured 2026-08-12 as 1 on #121, so the owner is not blocked today.

### F-24 — the proof whose premise is its own query — P2

**First instance, and it was a proof I merged in #191.** Having argued the anchor
doubling four ways, I published the one that "does not depend on a clock": a run
reads `conclusion=success` while its own job listing contains a `failure`; a
failing job fails its run; therefore the entry is not a job. I cited
`GET /actions/runs/<id>/jobs` and stopped there.

**The claim was true. The premise was unstated, and the premise is the query.**
That endpoint defaults to the *latest attempt only*. Measured across all four
scopings on run `31533947898`:

| query | entries | failures | proof holds? |
|---|---|---|---|
| `/jobs` (default) | 10 | 1 — the anchor | yes, by luck of the default |
| `/jobs?filter=all` | 20 | 3 — anchor ×2 + `drive-service-supply-chain` | **no** |
| `/attempts/1/jobs` | 10 | 2 | no — that attempt concluded `failure` |
| `/attempts/2/jobs` | 10 | 1 — the anchor | **yes, and immune** |

Under `filter=all` the listing carries an earlier attempt's genuine failure,
which coexists with a green run legitimately. Same surface reading, innocent
explanation available, proof gone.

**The sharp part: it is destroyed by a habit this register recommends.** Two
entries earlier the correct advice is to widen a listing with `filter=all` so a
reader's defaults cannot hide rows. Apply that good habit to this proof and the
proof dies. **A remedy for one failure mode was the attack on another**, and
nothing in either entry warned of the interaction.

- **A number's query is provenance; a proof's query is a premise.** Provenance
  tells the reader where a figure came from. A premise is load-bearing: change it
  and the conclusion is no longer supported. For anything *argued* rather than
  merely counted, state the scope as a condition of the argument.
- **Prefer the scoping that is immune, not the one that happens to be right.**
  `/attempts/2/jobs` and `/jobs` agree today; only the first still agrees after a
  re-run or a widening.
- **Both sides of a comparison must be read at the same level.** Third appearance
  in three substrates — a denominator, a set of identifiers, and now a proof.
  Here the two sides are a *conclusion* and a *listing*; reading the conclusion at
  run level and the listing at all-attempts level is the entire defect.

**Reported against their own filing** by platform session `b14546cd`, who wrote
that they *"gave a proof and withheld its premise, in the same exchange where I
criticised stopping at the first refutable mechanism."* I merged it without
asking for the query, which is the same omission from the receiving side.

### F-25 — the bound placed on the half that did not need it — P2

**First instance, mine, in owner-facing evidence.** To show no anchor run was
executing at `06:59:03Z` I published: *"the nearest candidate begins 328 s after
the instant, and the longest anchor run ever observed lasted 318 s."* The
arithmetic is right and the comparison is inapposite. **A run that starts after
an instant cannot contain it, however long it lasts** — that half is excluded by
ordering alone, and attaching a duration bound to it does nothing except present
a 10-second margin as though something depended on it.

The population splits by *reason for exclusion*, and only one half needs a
duration argument:

| half | excluded by | needs a duration bound? |
|---|---|---|
| starts after the instant | ordering | no |
| **starts before the instant** | **duration** | **yes — and I never gave one** |

Re-measured with every timestamp normalised to UTC: nearest before-side run
`31533952257` starts `2026-08-11T20:38:25Z`, a gap of **37,238 s**, against a
longest-ever window of **318 s** — a **117×** margin, plus a declared
`timeout-minutes: 5` ceiling on the job.

**The published exclusion was correct and its published argument did not cover
the case that could have broken it.** Nothing before the instant was bounded at
all; the conclusion survived because the true margin is enormous, which I had not
checked. F-24's "true by luck" in a second substrate — there an unstated premise,
here an unexamined half.

- **When excluding a population, split it by *why* each member is excluded and
  confirm every subset is covered by an argument that applies to it.** One
  comparison spread over a heterogeneous population will bound the easy half
  twice and the hard half never.
- **Prefer the margin that is structural over the one that is incidental.** 328
  versus 318 invites a reader to recheck it and would flip on any future run;
  37,238 versus 318 cannot be disturbed.
- **A sound calculation is not a sound argument.** A reader who verifies the
  arithmetic and then notices the comparison is beside the point trusts the
  document less than one who finds a number wrong.

Raised by platform session `2cab0595` in the same message in which they retracted
an overstatement of their own.

---

The condition described no longer exists. Leaving these in place actively
misleads the next agent.

| Item | Status | Action |
|---|---|---|
| "Migrations 002/003/005/006/007/008 unapplied" | Contradicted by the owner's production check: all nine logical units exact | Correct the note (drift D-1). Highest priority — it invites re-applying migrations that are already correct. |
| "Rollout authorization blocked pending lifecycle PR" | That chain merged | Re-verify and close (D-2). |
| Backup restore rehearsal outstanding | A real isolated restore was performed and confirmed | Do not request another. Must not become a rollout blocker again. |
| Migration 001 allocator drift | Fixed and merged | Closed. |
| "No Coolify deployment automation is possible" | The credential exists | Closed by WS-B. |

---

## What the audit did not find

No control was found that protects secrets, money or irreversible state and is
also unnecessary. The security design is sound. The cost is concentrated in
**scope and coupling** — checks that are individually correct but wired so that
an unrelated failure stops everything.

One correction to this section's own reasoning, made 2026-08-10. It originally
ended "…and gates that report infrastructure faults as authorization faults,"
which restated the trust-anchor diagnosis that F-3 has since disproved. That
gate was not misreporting a fault class; it could not reach a verdict at all,
in either direction. The sharper statement is that **a control which cannot
fail correctly is indistinguishable from one that is working**, and neither
this audit nor four days of red check-runs surfaced that on their own. It took
an agent refusing to implement against a brief it could not reproduce.

That is a much better problem to have than missing controls, and it is fixable
without weakening a single P0.
