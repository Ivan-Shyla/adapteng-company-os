# Platform v1 — activation path

- **observed_at:** 2026-09-06 (UTC)
- **Evidence:** live authenticated reads of the self-hosted n8n API, the GitHub
  API for five repositories, and deployed source in `adapteng-website`.
- **Purpose:** state what actually runs today and give the shortest honest route
  to a platform that processes real work. This is a build plan, not an audit.

---

## 1. What actually runs today

The governed pipeline is **built, deployed and correct**. It is simply not
receiving anything.

| Component | State | Evidence |
|---|---|---|
| Baserow adapter | Deployed and serving | Commit `f9daf1b5`; five live checks passed 2026-09-06 |
| Governed read path (L1) | **Operational** | Workflow `65ATNbi5sColtnp0`, execution `26`, `200/200/200/401/403` |
| AI Gateway | **Answering on the private network** | `health` 200, `ready` 200, alias resolves, 2026-09-06 |
| WEB-002 lead intake | Enabled, **zero traffic** | 16 nodes, 0 disabled, 0 executions in the retained self-hosted log |
| AUT-001 systems upsert | Enabled, **zero traffic** | 2 nodes, 0 executions in the retained self-hosted log |
| WordPress producer | Merged and deployed | Website PR #78 merged 2026-08-01; Cloudways deploy succeeded 2026-08-14 on `854f5971` |
| Lead delivery configuration | **Never switched on** | `configure-lead-intake.yml` last dispatched 2026-07-19 |

**The gap is one configuration switch, not missing code.** The consumer, the
producer and the transport all exist and have each been proven in isolation.
The producer was never pointed at the consumer.

### Correction to previous status

`registry/workflows.yaml` recorded website PR #78 as *draft, unmerged and
undeployed*. That has been false since 2026-08-01. The corrected entry is in the
same file. Anyone planning from the old line was planning around a blocker that
no longer existed.

---

## 2. The shortest route to a working business flow

One owner-dispatched GitHub Actions run activates the end-to-end lead flow:

> **Repository:** `Ivan-Shyla/adapteng-website`
> **Workflow:** `.github/workflows/configure-lead-intake.yml`

### Answers to the dispatch form

| Input | Value | Basis |
|---|---|---|
| `mode` | `self_hosted` | Routes the site to WEB-002 on `n8n.adapteng.com` |
| `confirm_cutover` | `true` | Owner decision |
| `consumer_idempotency_approved` | **`true`** | Verified 2026-09-06; see below |
| `preflight_evidence_ref` | *needs a fresh run* | The 2026-07-25 T1–T4 evidence is six weeks old |

### Why idempotency can be approved

WEB-002 does not rely on retry etiquette. It reserves identity in Postgres
before any write:

1. `Validate` reads `form.submission_id` from the body and enforces the `N:N` shape.
2. `Reserve` calls `public.reserve_lead_identity('lead.created','wordpress', …)`.
3. A duplicate returns outcome `conflict`, which routes straight to `Respond409`.
4. Only a fresh reservation reaches the four governed upserts, each carrying an
   idempotency key.

The WordPress producer emits the same value in both the `X-AE-Submission-ID`
header and `form.submission_id`. **One caveat worth recording:** WEB-002 reads
only the body field. Dedupe is consistent today, but a proxy that strips the body
field, or a future producer that sends only the header, would silently disable
deduplication. The body field is load-bearing.

### The one item still to produce

Fresh WEB-002 T1–T4 evidence. `tools/contracts/web002-evidence-runner.mjs`
exists in the website repository for exactly this. Running it against the live
webhook performs real writes, so it is an owner-authorised action, not an
autonomous one.

---

## 3. The AI layer

The same pattern holds on the AI side: built, deployed, and until today
unverified. A read-only probe issued from inside the shared network on
2026-09-06 returned `health` 200 and `ready` 200 from `http://ai-gateway:8081`,
and the `ai-gateway` alias resolved. Because `/ready` touches the database, that
single result proves the container is up, the name resolves and the gateway
reaches `postgres-adapteng-ops`. The alias gap measured on 2026-08-12 — when the
Baserow adapter resolved and this service did not — is closed.

The registry status moves from `deployed-live-unverified` to
`live-internal-verified`. Cost, model pricing and FX configuration were already
not blockers.

**What remains unproven** is the first real model call: the EU Vertex client,
Drive adapters, orchestration and canonical approval composition have never been
exercised against the running service. That is the next AI milestone, and it is
bounded — one schema-valid, side-effect-free draft, which is the service's
declared first operation. It costs a model call, so it is worth doing
deliberately rather than incidentally.

---

## 4. Proportionality: what is a real risk and what is not

The user-visible complaint is that the platform is over-guarded relative to what
it delivers. Separating the two honestly:

**Genuine, keep:**

- **No verified production backup (B-4).** Enabling live lead writes into
  Baserow with no restore-tested backup is a real business risk. This is the one
  control that should gate cutover, and it is owner-only.
- **WEB-002 fail-closed responses.** `Respond409`/`Respond500` are what make a
  producer retry instead of losing a lead. Removing them would lose business data.

**Over-weighted relative to delivered value:**

- **The 13 frozen n8n Cloud workflows.** They were frozen because they had
  ungoverned reach. The answer is not to unfreeze them in place, and not to keep
  auditing them: it is to migrate the ones that still matter onto the governed
  adapter path already proven at L1. Anything not worth migrating should be
  archived rather than carried as permanent open risk.
- **Repeat inventory audits of n8n Cloud.** The split question is now settled
  (see `registry/workflows.yaml`). Further census work adds no capability.

---

## 5. Ordered next actions

**Owner-only (cannot be automated from here):**

1. Configure or evidence the production backup, then verify one isolated
   restore. This is the last substantive gate before live writes (B-4).
2. Repoint the Coolify application for `n8n-self-hosted` from
   `palinaruban-repo-status-review` to `main`. That branch is 82 commits behind
   with a head dated 2026-07-24 (B-3).
3. Authorise a fresh WEB-002 T1–T4 evidence run.
4. Dispatch `configure-lead-intake.yml` with the inputs in §2.

**Agent-executable once authorised:**

- Produce and record the T1–T4 evidence artefact from the runner.
- After the first real leads land, verify the flow from the self-hosted
  execution log and report duplicates, 409s and 500s.
- Migrate a named Cloud workflow onto the governed adapter path, one at a time,
  each with its own proof.

---

## 6. What this document does not claim

- It does not claim any service is healthy right now. Runtime health was not
  probed; only the n8n API and GitHub were read.
- It does not claim a production backup exists.
- It does not authorise the cutover. It supplies the answers the cutover form
  asks for so the owner can decide in one step instead of reopening the question.
