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
and stands at one. Deliberately not restated here — §12 is the single copy.

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

---

### F-8 — A required check that is nondeterministic — P1

**`root-rollout-tests` produced two different verdicts for the same commit,
twice.** It is one of the five checks required by the platform ruleset, so
every occurrence randomly blocks a merge that has nothing wrong with it.

Evidence, from the API rather than from a report:

| Commit | `push` run | `pull_request` run |
|---|---|---|
| `2c9824ba` (PR #112) | attempt 1 **success** | attempt 1 **failure** |
| `084c4d17` (PR #114) | attempt 1 **success** | needed **attempt 2** |

Identical trees, opposite verdicts. Both were re-run to green, which is why
the current check-run conclusions all read `success` and the failures are
invisible unless you look at run attempts.

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

The failure is always the same case of
`test_production_lifecycle_cleanup_status_is_fail_closed`: the `ok-fail-0-90`
case exits 1 with `lifecycle.run_selection_failed` instead of reaching the
cleanup path and exiting 90.

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
degradation path #121 deliberately built. The predicate that separates them is
**is the failure of this command observable anywhere downstream?** At 264 it is:
an unreadable or absent error file yields empty output, and line 268
(`[ -n "$selection_error" ] || selection_error="(none)"`) gives that state a
printed name. The suppression is there so a missing diagnostic degrades to
`(none)` instead of tripping `pipefail` and manufacturing a second failure inside
the reporting path. At `main` 254/379 and `f0a2d17` 388 there is no downstream
name — the status is checked, the cause is gone.

That is the same distinction the whole finding rests on, applied one level down:
what matters is not whether a stream is discarded but whether the failure keeps a
name. Stated as "count the redirects" the rule is cheap and wrong; stated as
"find the failures nothing downstream can name" it is the rule #121 implements.

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

---

## P3 — obsolete, delete

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
