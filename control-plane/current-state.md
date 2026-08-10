# Company OS — verified current state

**Reconciled:** 2026-08-10. **Method:** GitHub API reads against every
repository owned by the account, plus the failing CI logs behind each red
check. Production runtime was not reachable from the reconciling workstation;
every claim that would require it is marked `UNVERIFIED` and names what would
settle it.

## 1. Repositories

Discovered by enumeration, not from the handoff list. Six exist; five are in
Company OS scope.

| Repository | Role | `main` state | Open PRs |
|---|---|---|---|
| `adapteng-company-os` | Control plane, registry, governance | active | 1 (#35) |
| `adapteng-automation-platform` | Implementation: AI Gateway, adapters, migrations | `23a23f0` | 1 (#109) |
| `ai-dev-loop-control-plane` | Agent execution patterns, skills, admission | active | 0 |
| `adapteng-website` | Public website | active | 0 |
| `adapteng-marketing` | Marketing assets | active | 0 |
| `Kraken` | **Out of scope.** Personal trading project. | active | 0 |

`Kraken` is not Company OS. Its exclusion follows the boundary already recorded
in [`decisions/0002-…`](../decisions/0002-personal-projects-remain-outside-company-os.md).
It is the only repository with no `main-protected` ruleset, which is consistent
with it being outside the governed set. No action.

Two repositories the handoff did not mention were found by enumeration
(`Kraken`, `adapteng-marketing`). Neither changes the plan.

## 2. The single blocking fact

**`adapteng-automation-platform` cannot merge anything.** Not one pull request,
regardless of content.

The `main-protected` ruleset requires four status checks. Three pass. The
fourth, `Validate repository structure and content`, fails on a condition that
has nothing to do with any code under review:

```
n8n/isolation-waivers.json:waiver[0]: waiver expired isolation_ref=ISO-1
```

The repository holds one time-boxed waiver permitting a company-to-personal
resource crossing. Its `expires_on` is **2026-08-08**. Today is 2026-08-10.
The waiver lapsed two days ago and the check that reads it runs on every push
and every pull request to every branch.

Evidence that this is a date lapse and not a regression: the same workflow
succeeded on `main` at `23a23f0` on 2026-08-08, and fails at 2026-08-10 against
a pull request that touches only `services/ai-gateway/` and `docs/`.

The waiver is deliberately double-locked. The validator pins the approved
tuple in code, so the date in the JSON must equal the date in
`scripts/validation/validate_n8n_isolation.py` **and** be unexpired. Editing
one without the other fails with `does not match the approved ISO-1 tuple`.
That design is sound: it makes extending a data-boundary waiver a reviewable
code change rather than a quiet JSON edit. It is kept.

What is wrong is the blast radius, not the check. A lapsed waiver on an n8n
resource crossing currently halts unrelated engineering across the whole
repository, and it did so silently, on a date nobody was warned about.

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

- **No Coolify deployment automation exists anywhere.** Searched both
  repositories. The platform repository contains Coolify *specifications*
  (`deploy/coolify/`, compose files for Postgres and n8n), a spec validator
  (`scripts/validation/validate_deploy.py`) and five runbooks — all describing
  manual console work. No workflow or script calls the Coolify API. There is no
  AI Gateway resource definition.
- **The AI Gateway is not deployed.** Its own README says repository-ready, not
  deployed. `UNVERIFIED` that no Coolify application exists, because the API was
  not reachable from this workstation; the credential to check it is held as a
  repository secret in *this* repository (see §6).
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

| # | Claim | Where | Contradicted by | Verdict |
|---|---|---|---|---|
| D-1 | Migrations 002, 003, 005, 006, 007 and both 008 units "remain repo-only and unapplied" | [`owner/action-items.md`](../owner/action-items.md) | Owner's post-rollout manual production check: all nine logical units exact | **Stale.** Production outranks the note. Correct it. |
| D-2 | Rollout authorization blocked pending an automation-evidence lifecycle PR | [`owner/action-items.md`](../owner/action-items.md) | The referenced chain merged through platform PRs #93, #94, #98 | **Stale.** Re-verify and close. |
| D-3 | AI Gateway readiness reads as cost-and-runtime blocked | `ai/` notes | Gateway tests and supply-chain gates green on `main`; only deployment is missing | **Partly stale.** Narrow to "not deployed". |
| D-4 | Coolify deployment assumed to be manual console work | platform runbooks | Credential for API automation exists in this repository | **Obsolete once WS-B lands.** |
| D-5 | Migration 001 allocator schema incident open | prior narrative | Fixed and merged in platform PR #108 | **Closed.** |

D-1 is the most damaging: it invites an agent to re-apply migrations that are
already exact, which is the one class of mistake this system is built to
prevent.

`UNVERIFIED` — D-1 and D-2 rest on the owner's production check, which this
reconciliation could not repeat. A read-only schema verification run through
the existing migration runner would settle both permanently, and is exactly the
kind of check that should be automated rather than remembered.

## 10. Pull requests

| PR | Repository | State | Verdict |
|---|---|---|---|
| #35 | `adapteng-company-os` | **CLEAN**, all checks green | Ready. Documentation refresh; nothing is waiting on it but it is waiting on nobody. Merge. |
| #109 | `adapteng-automation-platform` | `MERGEABLE` but `BLOCKED` | Content is sound and its own tests are green. Blocked solely by §2. |

PR #109 adds credential-file validation that checks existence, readability and
non-emptiness without ever reading contents, its tests, and a least-privilege
runtime-role runbook. The role grants execute on the required definer functions
and no direct table access. Nothing in it warrants the delay it has had.

Two red checks on #109 are **not** required by the ruleset and do not block
merge: `Verify exact current head from merged base` and `Base-trusted rollout
authorization`. They fail for an infrastructural reason, not a safety one —
the runner performs a partial clone and then re-invokes git with a scrubbed
environment holding no credential, so the lazy object fetch fails:

```
fatal: could not read Username for 'https://github.com'
fatal: could not fetch <object> from promisor remote
rollout_trust_anchor.approval.unexpected
```

A gate that cannot complete its own verification is not providing safety; it is
producing a permanent red mark that trains everyone to ignore red marks. See
the friction audit, F-3.
