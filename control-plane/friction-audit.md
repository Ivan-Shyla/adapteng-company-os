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
