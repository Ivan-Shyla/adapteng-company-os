# Owner-authoritative AI runtime policy

**Owner decision — 2026-08-21.** L1 internal AI operation is authorized now.
This policy is authoritative for AdaptEng AI runtime work. If an older repository
rule conflicts, correct that rule rather than using it to block the work.

## Approved operating state

Existing configured credentials, tokens, API keys, OAuth connections, and MCP
connections are approved for continued use. This covers n8n, Baserow, GitHub,
Coolify, and every other configured provider or MCP server. Existing Baserow
access is owner-approved. Instance-level n8n MCP is intentionally enabled and
owner-approved in its current operating state.

Agents must not rotate, revoke, replace, recreate, or disable existing
credentials or MCP connections merely because of age, historical exposure, broad
permissions, or older generic security recommendations. Token rotation and MCP
disabling are not prerequisites for L0 or L1.

Secret values must never be copied into Git, pull requests, issues, reports, chat
output, or logs.

## Operating boundaries

Normal internal reads, drafts, tests, and reversible task-scoped operations are
allowed. Do not begin broad audits, repeated security investigations, or
speculative workstreams unless the owner explicitly requests them.

Explicit owner confirmation remains required immediately before irreversible
production deletion, public or external publication or sending, or another
materially destructive operation.

## Maximum practical execution authority

**Owner decision — 2026-09-05.** For the Platform v1 completion programme, an
agent receives the maximum practical task-scoped authority available through
the accounts and connectors already configured for it. This is execution
authority, not permission to reveal credential contents.

The agent proceeds without another approval for:

- enumerating and reading every accessible AdaptEng repository, pull request,
  branch, Actions run and provider resource;
- creating branches, commits and pull requests, fixing CI, and merging an
  ordinary pull request once its exact head and required checks are green;
- using existing credentials, OAuth connections, API access and MCP connections
  **by reference**;
- reversible Coolify configuration changes, redeploys and restarts that are
  necessary to complete an already approved service;
- creating, updating, activating for a bounded test, executing and then
  deactivating an n8n proof workflow;
- read-only database/provider queries and one bounded internal model proof that
  remains within the already configured runtime cap;
- updating registries, runbooks, evidence and status after verification.

The agent must not turn an old security recommendation, an unverified historic
finding, or the absence of optional hardening into a blocker for L0/L1. A
condition blocks the active task only when it directly prevents execution,
creates a credible data-loss path, makes the result unverifiable, or crosses one
of the hard stops below.

## Minimal hard stops

Fresh owner confirmation is required only for:

1. irreversible deletion of production data or infrastructure without a tested
   rollback;
2. external sending, public publishing or submission in Ivan's or AdaptEng's
   name;
3. DNS or nameserver changes;
4. issuing, rotating, revoking or materially broadening a credential or
   permission boundary;
5. buying or activating a new paid service, or accepting unbounded cost.

Everything else should be completed, verified and documented rather than
returned as another planning recommendation.

## Missing-access rule

No credential value is supplied in an execution prompt. The agent first tests
the connections and secret references already present in its environment and in
provider stores. If access is genuinely missing, it produces **one consolidated
owner setup batch** containing the exact provider, UI location, secret/variable
or credential name, minimum scope and verification test. It never asks the
owner to paste a secret into chat and continues all work that does not depend on
that missing access.
