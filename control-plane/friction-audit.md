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
closed. Detail and the one post-repair merge-race failure are in
[`current-state.md`](current-state.md) §12. Not promoted to required: the
precondition is a check that always starts and always reports, and that
deserves a run of clean pull requests first.

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
