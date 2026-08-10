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

### F-1 — A lapsed n8n waiver merge-locks the entire repository

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

### F-3 — A trust-anchor gate that cannot complete its own check

`Verify exact current head from merged base` and `Base-trusted rollout
authorization` fail on PR #109 because the runner does a partial clone and then
re-runs git with a scrubbed environment containing no credential, so the lazy
object fetch fails outright:

```
fatal: could not read Username for 'https://github.com'
fatal: could not fetch <object> from promisor remote
rollout_trust_anchor.approval.unexpected
```

That is an infrastructure failure being reported as an authorization failure.
The two are indistinguishable from the outside, which is the actual danger: a
gate that is always red teaches everyone to ignore red.

Neither check is in the ruleset's required list, so they block nothing — they
only add noise.

**Change:** fix the fetch so the check can actually run and distinguish "not
authorized" from "could not determine". If it is repaired, promote it; if it is
not going to be repaired, remove it. Leaving a permanently-red non-blocking
gate is the worst of the three options.

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
an unrelated failure stops everything, and gates that report infrastructure
faults as authorization faults.

That is a much better problem to have than missing controls, and it is fixable
without weakening a single P0.
