# Owner action items

Prioritized. Each item says **why**, **how**, and **who/where**. Check them off
in a status PR as you complete them, and mirror the change into
`ARCHITECTURE.md` §11.

Legend: 🔴 security / do first · 🟠 data hygiene · 🟡 unblock next steps · ⚪ standing / DoD

---

## 🔴 Security — do first

- [ ] **Revoke the leaked Baserow token `acJgo3…`.**
  - **Why:** it was exposed in an earlier Coolify build-arg log and independent
    testing on 2026-07-25 shows it **still authenticates** against `Company
    Operations`. Rotating the Coolify copy did **not** revoke it — the old
    value stays valid until deleted at the source.
  - **How:** Baserow → Settings → API tokens → delete that token. Then confirm a
    call using the old value is rejected (see `runbooks/secret-rotation.md`).

## 🟠 Data hygiene — synthetic test rows

- [ ] **Delete synthetic Baserow rows** created during live proofs (the data
  token has no delete permission, so this needs an admin JWT):
  - WEB-002: `AE-{ORG,PER,OPP,ACT}-100002`, `-100003`, `-100004`
    (tables Organizations 842 / People 843 / Opportunities 844 / Actions 846).
  - Earlier canary: `AE-ORG-0001`, `AE-ORG-0002`.
  - The reservation rows in Postgres (`900:1`–`903:1`) are PII-free append-only
    identity markers; leave them or clear deliberately.

## 🟡 Unblock the next build steps

- [ ] **Provide Google service-account credentials** (owner-held) for the
  `integrity-reconciler` Drive reader and the `drive-adapter`. Without Drive
  creds the INT-001 all-or-nothing live reconcile cannot run.
- [ ] **INT-001 (integrity) — approve the deferred wiring, one PR at a time.**
  ADR-0011 defers each of these to a *future approved PR*: live schedule, the
  Finding→Action adapter, the n8n workflow, live manifest wiring, and deployed
  credentials. Keep **migration 006 unapplied** until backup/restore planning.
  Nothing here should be forced by an agent.
- [ ] **AI-001 pilot inputs.** Ratify claims, style guide, 2–3 source documents,
  and pilot config; approve the `AG-007` quality set; verify ZDR privacy,
  cache-off and FX gates. Then a measured, inactive pilot can run on EU Vertex
  `gemini-3.1-flash-lite` (see `ai/model-choices.md`).
- [ ] **Website producer (automation-platform PR #78)** stays held until:
  migration plan, origin auth, retention proof, HTTP 409 mapping, durable
  reconciliation, inactive shadow, and synthetic E2E all pass. When unheld,
  point it at the **WEB-002** webhook with the header token — the governed
  intake is already live and proven.
- [ ] **self-hosted n8n cutover:** repoint the Coolify source from branch
  `palinaruban-repo-status-review` to `main`, verify auto-deploy, then complete
  the inactive shadow. n8n Cloud remains the authority until then.

## ⚪ Standing / Definition-of-Done

- [ ] **Restore drill:** restore an `adapteng_ops` backup into a scratch target
  and prove readability (§13 DoD). Backup last verified 2026-07-25 13:31.
- [ ] **Migrations not live:** 002 (run ledger), 003 (approval/outbox), 005 (AI
  gateway), 006 (integrity) — apply only with backup + a real consumer
  (`runbooks/apply-migration.md`).
- [ ] **Baserow off-host export/restore** completion; **Google Workspace**
  Manager/recovery acceptance.
- [ ] **Personal JM/EC isolation:** verify live credential/store identity;
  the `ISO-1` waiver expires **2026-08-08**.
- [ ] **Media intake:** approve snapshot/rollback, import the backward-compatible
  consumer first, canary one real case, then redeploy the marketing worker.
- [ ] **Rotate the Coolify API token** post-launch.
- [ ] **GitHub Actions** monthly budget is $10 hard-stop — mind it when queueing
  CI-heavy work.

---

## What's already done (live-proven, no action needed)

- Governed **baserow-adapter** live internal-only; migration 001 applied on first
  boot; adapter service token rotated post-canary.
- **AUT-001** Systems Registry governed workflow live (pure-internal, header-auth).
- **WEB-002** governed lead intake live and proven (T1–T4), migration 004 applied,
  fail-closed retry semantics.
- Baserow Company OS schema provisioned live (8 tables / 107 fields / 10 views);
  read-only table-id binding captured on `main`.
