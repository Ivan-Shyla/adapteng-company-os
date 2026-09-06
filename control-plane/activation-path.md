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
| Producer plugin on Cloudways | Deployed and current | `lead-intake.php` byte-identical at deployed `ce1a200b` and at `main` |
| Lead delivery configuration | Never switched on | `configure-lead-intake.yml` dispatched once ever, 2026-07-19 |
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
| `preflight_evidence_ref` | `WEB002-T1-T4:<sha>:<run_id>` | Must equal repository variable `AE_LEAD_WEB002_PREFLIGHT_EVIDENCE_REF` and bind the dispatch commit |

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

### Fresh consumer verification, 2026-09-06

The consumer contract was re-read live from the self-hosted n8n API rather than
inferred from the July artifact. `WEB-002 Lead Intake (governed)`
(`05ytz5If9kHUOYuA`, version `fa199c8e-ad42-4681-bd5b-123fcefeab65`, definition
last changed 2026-07-25) is active with 16 nodes, and every contract element is
present in the running definition:

| Element | Result |
|---|---|
| `POST` webhook, header authentication | present, bound to the existing webhook token credential |
| Webhook path matches `web002-lead-[0-9a-f]{8}` | yes |
| Reads `form.submission_id` | yes |
| Builds `sitelead_wp_<id>` | yes |
| Calls `reserve_lead_identity(...)` before any write | yes |
| Conflict path responds `409` | yes |
| `idempotency_key` occurrences | exactly 4, one per governed upsert |
| Governed upserts | `AE-ORG-`, `AE-PER-`, `AE-OPP-`, `AE-ACT-` |

The self-hosted instance holds four workflows: WEB-002 and AUT-001 (both
enabled, both still with no production traffic) and the two completed L1 proofs
(both inactive).

### What actually blocks the cutover

Neither remaining step is a code or design problem. Both are configuration
values that only the owner can place, and each one fails closed today.

| # | Blocker | Where | Why the agent cannot resolve it |
|---|---|---|---|
| 1 | `AE_N8N_LEAD_WEBHOOK_TOKEN` absent | website repo secret | Must equal the value of the existing self-hosted webhook token credential. n8n never returns credential values, and rotating the token is forbidden. |
| 2 | `AE_N8N_SELF_HOSTED_LEAD_WEBHOOK_URL` absent | website repo secret | Derivable, but useless without blocker 1. |
| 3 | `AE_N8N_WEB002_EVIDENCE_URL` / `AE_N8N_WEB002_EVIDENCE_TOKEN` absent | website repo secrets | Required by `web002-t1-t4-evidence.yml`. |
| 4 | Evidence lane not deployed | self-hosted n8n | The runner targets the isolated lane `/webhook/web002-lead-a11ce001`, whose artifact lives in the platform repository at `n8n/workflows/experimental/MM-WEB002-self-hosted-lead-evidence-lane.json`. No workflow on the instance serves that path. |
| 5 | Four attestation variables absent | website repo variables | `AE_LEAD_INTAKE_CUTOVER_APPROVED`, `AE_LEAD_WEB002_PREFLIGHT_EVIDENCE_REF`, `AE_LEAD_WEB002_PREFLIGHT_APPROVED_AT`, `AE_LEAD_WEB002_CONSUMER_IDEMPOTENCY_APPROVED`. They must not be set before a real T1–T4 run, because setting them is the attestation. |

Two further facts belong on the record. `configure-lead-intake.yml` has run
exactly once in its history, on 2026-07-19, so the producer still carries that
run's configuration and re-dispatching with the previous mode is the rollback.
And `deploy-cloudways.yml` last succeeded on 2026-08-12 against `ce1a200b`,
while website `main` is now `0214cfa1` — the deployed producer lags `main`.

### Backup before production writes

The cutover turns on four governed upserts against `adapteng_ops`, so a
recoverable backup should exist first. There is no agent-executable path to one.
`postgres-backup-rehearsal.yml` is real in every respect except the database:
it deliberately backs up and restores a disposable cluster, and a guard fails
the job if production connection configuration appears at all. Host-side
execution has no working route — the production host does not accept SSH from
GitHub-hosted runners, which is precisely the gap `ops-runner.yml` exists to
close. **A rehearsal is not a backup of production, and this document does not
treat it as one.**

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
declared first operation.

It is also, today, owner-only. `/health` and `/ready` are answered before the
`Authorization` header is read, which is why the reachability probe needed no
secret; `POST /v1/gateway` is the only authenticated route, and it requires a
bearer token from `AI_GATEWAY_BEARER_TOKENS`, whose value exists only in the
Coolify secret store. The service also ships its own one-shot proof
(`app/company_os_model_proof.py`), but it runs inside the container behind an
explicit owner acknowledgement, and no execution path into the container is
available from here.

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

1. Place the four website repository secrets. Two of them — the WEB-002 webhook
   token and the evidence-lane token — carry values that exist only in the n8n
   credential store, and no read path returns them.
2. Deploy the evidence-lane artifact to self-hosted n8n so the isolated lane
   `/webhook/web002-lead-a11ce001` answers, then run
   `web002-t1-t4-evidence.yml` and set the four attestation variables from its
   result.
3. Configure or evidence the production backup, then verify one isolated
   restore. This is the last substantive gate before live writes (B-4).
4. Repoint the Coolify application for `n8n-self-hosted` from
   `palinaruban-repo-status-review` to `main`. That branch is 82 commits behind
   with a head dated 2026-07-24 (B-3).
5. Redeploy the website so the running producer matches `main`, then dispatch
   `configure-lead-intake.yml` with the inputs in §2.

**Agent-executable once authorised:**

- After the first real leads land, verify the flow from the self-hosted
  execution log and report duplicates, 409s and 500s.
- Exercise the first bounded model call once a gateway bearer token is
  reachable by reference.
- Migrate a named Cloud workflow onto the governed adapter path, one at a time,
  each with its own proof.

---

## 6. What this document does not claim

- It does not claim any service is healthy right now beyond what §1 and §3
  record from live probes.
- It does not claim a production backup exists.
- It does not claim the T1–T4 preflight has been run. §2 records a live
  verification of the consumer contract, which is a weaker and different
  statement: it reads the running definition, it does not exercise it.
- It does not authorise the cutover. It supplies the answers the cutover form
  asks for so the owner can decide in one step instead of reopening the question.
