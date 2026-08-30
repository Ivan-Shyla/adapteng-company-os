# Autonomy policy

Who may do what without asking. This replaces case-by-case permission with a
standing rule, so that authorising a piece of work once is enough.

The test applied throughout: **does a human decision here change the outcome?**
If a check is mechanical, a human in front of it adds delay and no safety. If a
decision is irreversible, costs money, or moves a security boundary, a human
belongs there and automation must not remove them.

## AUTO — agent proceeds, no approval

No owner interaction. These are either reversible, or read-only, or already
verified by machine.

- Reading any repository, and read-only inspection of production.
- Creating branches, committing, opening pull requests.
- Running and repairing CI, including rerunning failed jobs.
- Writing and changing tests.
- Documentation, registry and runbook updates.
- Ephemeral test infrastructure that is destroyed afterwards.
- Idempotent reconciliation of a deployment resource to its declared spec.
- Redeploying and restarting a service that is already approved to run.
- Deleting merged branches and other safe cleanup.

**Merging an ordinary pull request is AUTO** once its required checks pass. The
`main-protected` rulesets require zero approving reviews, so a human approval
step here is a habit rather than a control.

## AUTO + FAIL CLOSED — agent proceeds, machine must confirm

The agent acts without asking, but an automated check must pass first and the
operation must abort cleanly on failure. No human in the loop; no silent
continuation either.

- Database migrations through the approved runner.
- Deployment to production of an already-approved service.
- Schema changes.
- Approved-asset imports and bounded replay.
- Binding a secret **by reference** to an existing stored credential.
- Service configuration changes, including deployment environment values.

The obligation this creates is on the *check*, not the operator: if an
operation is in this tier, it must have a real automated verification. Moving
something here without building its verification is how fail-closed becomes
fail-open.

## OWNER APPROVAL — genuinely reserved

Short by design. Everything here is either irreversible, externally visible, or
moves a boundary.

- New architectural direction with material business consequence.
- Destructive production operations without a safe rollback.
- Deleting production data or infrastructure.
- Rotating, revoking or issuing credentials.
- Anything a customer or the public can see.
- First activation of a materially new paid provider.
- Unbounded cost exposure.
- Materially changing a security boundary or permission set — including
  extending a data-isolation waiver.
- Financial transactions.

### Approval does not expire on contact

Once the owner approves a bounded operation, the agent carries it through
without returning for confirmation at each step. Re-approval is required only
if the scope changes.

"Deploy the AI Gateway" therefore authorises creating the resource, binding
configuration, deploying, polling, reading logs, restarting on failure and
reporting — not nine separate confirmations.

## Standing rule for owner actions

Before any owner action is proposed, one question must be answered in writing:

> Can this be done safely with infrastructure that is already authenticated?

If yes, it is automated instead of asked. If no, the proposal must say what
specifically makes it owner-only.

An owner action that exists only because nobody wrote the automation is a
defect, and belongs in the friction audit rather than in a checklist.

## What this does not relax

Unchanged, and not negotiable:

- Secrets stay out of source control, logs and chat. Credential contents are
  never read, printed or echoed — validating that a credential file exists and
  is readable is not the same as reading it.
- Production writes stay auditable and attributable.
- Destructive operations fail closed.
- Model spend stays bounded, in EUR, against a pinned price version.
- Backups stay intact.
- Approvals for external and business-critical actions stay governed.

Removing ritual is not the same as removing control. Every gate this policy
relaxes is one where the machine, not the person, was already doing the
checking.
