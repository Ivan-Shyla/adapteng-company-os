# Runbook — governed agent pilot: disable and rollback

How to stop or roll back any part of the governed agent pilot (A00/A10/A20
runtime → internal `ai-gateway` → Postgres run ledger → Baserow/Drive draft),
from "pause it" to "undo the deployment entirely." Every action here is
additive/reversible by construction — nothing in the pilot drops schema,
deletes data, or requires an irreversible step.

Components involved: `adapteng-automation-platform/services/adapteng-agent-runtime`
(deployable service, PR #133), `n8n/workflows/experimental/MM-32-governed-agent-pilot-canary.json`
(manual-trigger canary, PR #134), `adapteng-company-os/deploy/agent-runtime.json`
(Coolify spec, PR #247), `.github/workflows/ai-gateway-credentials.yml` and
`.github/workflows/coolify-deploy.yml` (both in `adapteng-company-os`).

## Fastest disable: the feature flag (no deploy needed)

`GOVERNED_AGENT_RUNTIME_ACTIVE` gates every real action `POST /v1/run` can
take (`services/adapteng-agent-runtime/app/config.py`; defaults to inactive
and fails closed on any value other than the exact lowercase string `"true"`).
While it is `false`, the deployed service still answers `/health`/`/ready`
but every `POST /v1/run` returns `503 {"error":"inactive"}` — no ledger write,
no gateway call, ever.

1. In Coolify, set the `agent-runtime` application's `GOVERNED_AGENT_RUNTIME_ACTIVE`
   environment value to `false` (or remove `true` if it was set for a canary).
2. Redeploy so the new value takes effect:
   ```bash
   gh workflow run coolify-deploy.yml --repo Ivan-Shyla/adapteng-company-os \
     -f operation=deploy -f service=agent-runtime
   ```
3. Confirm with `-f operation=status` and `-f operation=verify`.

This is the correct response to "stop the pilot right now" — it requires no
code change, no PR, and cannot be undone accidentally by a later merge
(the repository's own default, in `deploy/agent-runtime.json`'s
`configuration` block, is already `"GOVERNED_AGENT_RUNTIME_ACTIVE": "false"`).

## Stop the n8n canary from ever running again

The canary (`MM-32 Governed Agent Pilot Canary`) is manual-trigger-only
(`n8n-nodes-base.manualTrigger`) and was never imported into live n8n with an
active state — nothing to disable there. If it has since been imported and
run:

1. In n8n, open the workflow and confirm it is `active: false` (manual
   trigger workflows do not need to be "active" to be run by hand, but
   confirm no other trigger was added).
2. If it needs to be fully retired, move its file from
   `n8n/workflows/experimental/` to `n8n/workflows/archived/` in a small PR
   and update its `lifecycle_state` to `archived` in both
   `n8n/workflow-classification.json` and `n8n/workflow-index.json` (the
   `scripts/validation/validate_n8n_isolation.py` check enforces these three
   stay in sync — see PR #134 for the registration pattern to reverse).

## Roll back the deployed `agent-runtime` service

If a new deploy introduced a regression (distinct from just wanting it
inactive):

1. Identify the last known-good image/commit (`coolify-deploy.yml -f
   operation=inspect -f service=agent-runtime` shows the currently deployed
   git reference).
2. Deploy the prior `adapteng-automation-platform` commit explicitly:
   ```bash
   gh workflow run coolify-deploy.yml --repo Ivan-Shyla/adapteng-company-os \
     -f operation=deploy -f service=agent-runtime
   ```
   (the spec's `source.git_branch` is `main`; to pin an exact prior commit,
   temporarily set `git_branch` to that commit SHA in `deploy/agent-runtime.json`,
   deploy, then revert the spec once `main` is good again — never edit the
   spec's `target`/`network` sections to work around a bad deploy, only
   `source`).
3. Confirm health: `-f operation=status` then `-f operation=verify`.

This never touches the run ledger or `ai-gateway` — `agent-runtime` is a
separate Coolify application (`deploy/agent-runtime.json`'s
`network.network_aliases: ["agent-runtime"]`), so rolling it back or removing
it entirely cannot affect `ai-gateway`'s own deployment or credential state.

## Undo the credential bind (Phase 3)

If binding the dedicated `VERTEX_SERVICE_ACCOUNT_JSON` credential to
`ai-gateway` causes a regression (the gateway stops booting, `/ready` starts
failing):

1. `gh workflow run ai-gateway-credentials.yml --repo Ivan-Shyla/adapteng-company-os -f operation=status`
   to see the currently mounted identity.
2. If the new mount is broken, the fastest recovery is redeploying
   `ai-gateway` from its last-known-good commit (same pattern as the
   `agent-runtime` rollback above, targeting `service=ai-gateway`), which
   restarts the container and re-reads whatever credential is currently
   bound.
3. To fully revert to the previous general-Workspace-credential binding,
   `git revert` the squash commit of `adapteng-company-os` PR #246 on
   `main`, then re-run `ai-gateway-credentials.yml -f operation=bind-adc`
   and redeploy `ai-gateway`. This is a real behavior change (back to a
   credential without the dedicated Vertex identity) — treat it as a last
   resort, not a routine rollback.

## Revoke IAM (only if Phase 2 granted `roles/aiplatform.user`)

Phase 2 (`verify-vertex-runtime.yml`) already confirmed the dedicated
`company-os-vertex-runtime` service account has `aiplatform.endpoints.predict`
without any grant being necessary. If a later run of that workflow did need
to grant `roles/aiplatform.user` to reach that state, and the pilot is being
aborted, revoke exactly that one binding at project `adapteng-workspace-automation`
for that one service account — never a broader role, never touch the
Workspace/DWD service account.

## What never needs rollback

- **Database schema**: every migration behind this pilot (`002_run_ledger.sql`,
  `008_ai_gateway_runtime_hardening.sql`) is additive-only with a fail-closed
  `status`/`apply` drift contract — there is nothing to "undo" in Postgres;
  disabling the pilot just means no new rows are written.
- **The one bounded model-smoke call (Phase 4)**: `operate_model_smoke` in
  `adapteng-company-os/scripts/coolify_deploy.py` uses a fixed `call_id`, so
  it can only ever bill once regardless of how many times the operation is
  dispatched — there is no "undo a charge" step because there is structurally
  never a second one.
- **The canary's synthetic Baserow draft row**: labeled `synthetic: true`
  with `source_workflow` set, tagged with `task_id`/`run_id` — safe to leave
  in place as evidence, or delete directly in Baserow if a clean slate is
  wanted; deleting it has no effect on the run ledger or the gateway.

## Full teardown (remove the pilot entirely)

1. Set `GOVERNED_AGENT_RUNTIME_ACTIVE=false` and redeploy (see above) — do
   this first, before anything else, so the service stops accepting real
   work immediately.
2. Delete the `agent-runtime` Coolify application (`coolify-deploy.yml` has
   no destructive operation for this by design — remove it through the
   Coolify UI directly, the one step in this runbook that is not automatable
   through the existing workflows, consistent with this repository's
   pattern of never letting an automated script delete a live resource).
3. Move `deploy/agent-runtime.json` out of `deploy/` (or delete it) in a
   small reviewed PR once the application is confirmed gone, so the spec
   stops declaring a target that no longer exists.
4. Archive the n8n canary workflow (see above).
5. Leave the Postgres schema and the `adapteng-agent-runtime` service code in
   `adapteng-automation-platform` in place — they are inert without a
   deployed, active service to call them, and removing working, tested code
   is not itself a safety requirement.
