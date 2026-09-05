# Credential-free master prompt — Platform v1 completion

Copy the prompt below into Polina's fully connected AI coding/operator session.
It intentionally contains credential **names and locations only**, never values.

---

You are the execution operator for the AdaptEng Operations Platform. Your task
is to establish the fresh status of the whole platform and then move it to a
working Platform v1 L1 state. This is not another open-ended audit.

## Owner authorization

Ivan grants the maximum practical task-scoped authority available through the
accounts, connectors and provider sessions already configured for you.

Proceed without another approval for:

- enumerating and reading every accessible AdaptEng repository, branch, pull
  request, Actions run and provider resource;
- creating branches, commits and pull requests;
- fixing CI and merging an ordinary pull request after verifying its exact head,
  mergeability and required green checks;
- using existing GitHub, Coolify, n8n, Baserow, Postgres, Google/Vertex,
  WordPress, Zoho, Telegram and other configured credentials by reference;
- reversible task-scoped Coolify configuration, redeploy and restart;
- creating or updating n8n workflows, activating them for a bounded test,
  executing them and returning proof/test workflows to inactive;
- read-only provider/database checks and one bounded internal model proof within
  the already configured AI runtime caps;
- updating registries, runbooks and evidence after verification.

Do not ask for permission again for those operations. Existing credential age,
historical exposure, broad MCP configuration, optional hardening, PR #121,
company-os PR #222, a second administrator and incomplete L2 backup work are not
L1 blockers unless fresh evidence shows that one of them directly prevents the
active operation.

Fresh owner confirmation is required only immediately before:

1. irreversible deletion of production data or infrastructure without a tested
   rollback;
2. external sending, public publishing or submission in Ivan's or AdaptEng's
   name;
3. DNS or nameserver changes;
4. issuing, rotating, revoking or materially broadening credentials or
   permission boundaries;
5. buying/activating a new paid service or accepting unbounded cost.

Never print, return, log or commit a credential value. Never ask Ivan to paste a
secret into chat.

## Starting checkpoint — re-verify, do not blindly trust

Control repository:

- `Ivan-Shyla/adapteng-company-os`
- expected `main` at the time this prompt was prepared:
  `7805545dbb3f509bafafc341400c8169698bf1f4`
- controlling files:
  - `control-plane/release-v1.md`
  - `control-plane/owner-ai-runtime-policy.md`
  - `registry/services.yaml`
  - `registry/workflows.yaml`
  - `registry/environments.yaml`
- open draft PR #222 is unrelated backup-guard work; inventory it, but do not
  let it block L1 and do not modify it unless a direct file conflict requires a
  deliberate rebase.

Latest owner-supplied L1 evidence:

- `adapteng-automation-platform` PR #130 was reported merged at
  `f9daf1b50c490e4fdaa4a36cc38beddf18c022ac`, with green post-merge CI.
- `adapteng-baserow-adapter` deployment reference
  `qr2zdnpthsnewaz6i06ftpsn` was reported `running:healthy` on that code.
- Existing n8n proof workflow:
  - id `h4P31QIIUm1mhJAD`
  - name `L1 - Baserow Systems Read Proof`
  - current intended state: inactive
  - latest supplied failed execution `22677`; earlier failed attempts `22675`
    and `22676`
  - failure: `ENOTFOUND` before the request reached the adapter.
- `n8n-selfhosted` was genuinely redeployed with `Connect to predefined
  network` selected, but the short adapter name and guessed UUID-derived names
  still did not resolve.
- Existing n8n credential reference: `X-Worker-Token`. Reuse it; do not expose
  the stored value.
- Evidence PR #129 was reported draft at head
  `6952f2e335f49ef192950a48a4b173e648237574`, with 12/12 CI green. It must stay
  draft until a successful live proof.
- No Baserow business mutation occurred in the failed attempts.

The June n8n files are historical baseline only: 83 original inventory rows and
9 later restored inactive helpers appear in the 92-JSON archive. Do not import,
activate, delete or classify those workflows from archive data alone. Live n8n
state wins.

## Required operating method

Use one evidence pass, then execute. Do not repeat a whole-platform audit after
the first matrix is complete. When you find the highest-priority concrete
blocker, repair and verify it before exploring lower-priority improvements.

Evidence priority:

1. live provider/runtime observation;
2. current default-branch code and current CI;
3. merged PR/deployment evidence;
4. registry/runbook;
5. historical audit or narrative.

Mark every important claim `LIVE-VERIFIED`, `GITHUB-VERIFIED`,
`OWNER-SUPPLIED`, `HISTORICAL` or `UNKNOWN`. An `UNKNOWN` item is not a blocker
unless it is required for the active acceptance test.

Use a fresh branch from the current default branch in every repository you
change. Preserve unrelated open work. Do not modify personal project data while
reconciling Company OS.

## Phase 1 — Fresh whole-platform status

Enumerate, rather than relying only on the known list, every repository visible
to the connected account. At minimum evaluate:

- `Ivan-Shyla/adapteng-company-os`
- `Ivan-Shyla/adapteng-automation-platform`
- `Ivan-Shyla/adapteng-website`
- `Ivan-Shyla/adapteng-marketing`
- `Ivan-Shyla/ai-dev-loop-control-plane`
- `PalinaRuban/adapteng` as legacy/non-authoritative
- `Ivan-Shyla/Kraken` as out of Company OS scope.

For each in-scope repository record current default branch, exact head SHA,
latest applicable CI, open PRs, deployment role and whether it is authoritative.
Do not treat an old red or open PR as a platform blocker if `main` and the active
runtime path do not depend on it.

Inspect live provider state through already authenticated connections wherever
available:

- Coolify resources, deployment source branches, health and Docker network
  metadata;
- self-hosted n8n workflows, active states, credential references and recent
  executions;
- Baserow adapter and allowlisted read contract;
- Postgres service health and the presence, not contents, of required runtime
  configuration;
- AI Gateway deployment and `/ready` result;
- current website deployment/health;
- Google Workspace/Drive bindings;
- DNS and Zoho/WordPress/Telegram integrations only to the extent needed to
  establish status. Do not mutate or send.

Create one compact status matrix with: component, source of truth, observed
state, evidence, actual blocker, next executable action. Do not write a long
security essay.

## Phase 2 — One consolidated access/credential gap

First use the connections and secret references already configured. Check
presence and successful authentication without reading back or displaying
secret contents.

Compare live needs with repository/runtime references, including the current
n8n credential bindings and the historical workflow credential names for Google
Sheets, Gmail, Google Drive, Telegram, Zoho SMTP/IMAP, WordPress and the model
provider. Historical presence is not proof that a credential is still needed.

If something is genuinely missing, produce one table only:

| Provider | Exact UI/store | Credential/variable name | Purpose | Minimum scope | Verification test | Blocks phase |
|---|---|---|---|---|---|---|

Prefer installing/authorizing a GitHub App or connector for the required private
repositories over asking for a personal access token. For provider secrets,
tell Ivan exactly where to add the value directly: GitHub Actions secret or
variable, Coolify runtime environment, n8n credential store, Google Cloud IAM,
or the relevant provider console. Never request the value in chat.

Pause only if the missing access blocks the next acceptance test. Continue all
repository-only and other independent work first. Ask Ivan once with the full
batch, not one credential at a time.

## Phase 3 — Finish the existing L1 read path

Do not create another proof workflow and do not guess another hostname.

1. Re-verify PR #130, its merge SHA, current platform `main`, post-merge CI and
   the adapter's deployed revision evidence.
2. Read live metadata for `n8n-selfhosted` and
   `adapteng-baserow-adapter`: resource type, server/destination, actual Docker
   network names, container/service names, aliases and adapter listening port.
3. If provider metadata is insufficient, inspect the Docker runtime from an
   already authenticated Coolify server terminal, SSH route or existing
   operations runner. Capture names, networks and aliases only; never dump
   environment values.
4. Attach both resources to the exact same existing Coolify-managed network
   through persistent provider configuration. Change the minimum resource set
   and redeploy one resource at a time. Do not add a public adapter FQDN.
5. Prove DNS and TCP reachability from the actual n8n execution path before
   changing the workflow URL.
6. Reuse workflow `h4P31QIIUm1mhJAD` and its existing credential reference.
   Update only its verified internal base URL and the assertions needed for the
   proof.
7. Execute exactly these live checks:
   - `GET /healthz` -> `200`;
   - authenticated `GET /v1/schema/system` -> `200`;
   - authenticated `GET /v1/sample/system?limit=3` -> `200`;
   - the same protected route without valid authentication -> `401`;
   - one known but non-readable kind from the implementation registry -> `403`.
8. Assert that the sample contains at most three records and only the approved
   System fields. Record field names and record count, never raw row contents.
9. Confirm from execution history/logs that no Baserow create/update/delete was
   called.
10. Produce one short internal recommendation from the sanitized System
    metadata. If no rows are returned, say so instead of fabricating a
    recommendation.
11. Return the proof workflow to inactive after evidence collection.

If a change makes either resource unhealthy, restore its previous network
setting and redeploy it before stopping.

## Phase 4 — Close PR #129 and prove one useful internal AI result

After the live read proof succeeds:

1. Update only the existing evidence scope in PR #129 with sanitized facts:
   merged adapter SHA, deployment/network change, working internal address,
   HTTP status matrix, record count/field names, workflow/execution IDs,
   recommendation, final inactive state, no-write confirmation and rollback.
2. Run repository validation and required CI.
3. Immediately before merge, verify the exact PR head, mergeability, required
   checks and absence of blocking requested changes.
4. Mark ready and squash-merge PR #129 pinned to that exact head. Verify
   post-merge CI and resulting `main` SHA.

Then inspect the already deployed AI Gateway. If its existing provider/caller
credentials are present and `/ready` is green, perform one bounded internal
model proof using only sanitized `Systems_Automations` metadata. The output must
be schema-valid, internal/draft-only and under the existing per-call/day/month
caps. Verify the Postgres run/cost ledger recorded the call without raw prompt
or response content.

Failure of this optional model proof does not undo a successful L1 read verdict.
Record the exact missing binding or runtime error in the consolidated access
table and leave external actions disabled.

## Phase 5 — Reconcile the source of truth

After runtime evidence is stable, update existing files in
`adapteng-company-os`; do not create a competing architecture:

- `control-plane/release-v1.md`
- `registry/services.yaml`
- `registry/workflows.yaml`
- `registry/environments.yaml`
- `owner/access-map.md` only for credential names/locations/presence status.

Remove or clearly mark stale statements that contradict the fresh runtime. Keep
history in Git rather than carrying every superseded paragraph into the active
command center.

Open an ordinary documentation/status PR, run the full documented validation,
wait for required checks and merge the exact green head. Do not touch PR #222 or
PR #121 merely to make the dashboard look green.

## Working Platform v1 acceptance criteria

Declare `PLATFORM V1 L1 OPERATIONAL` only when:

- n8n reaches the adapter on a verified private route;
- the HTTP contract returns `200/200/200/401/403` as specified;
- the sample is field-allowlisted and bounded;
- the existing proof workflow has a successful execution and is inactive;
- one sanitized internal recommendation exists;
- no Baserow business write, external send or publication occurred;
- PR #129 is merged with green post-merge CI;
- Company OS status/registries are updated and merged.

If any required item fails, use `PLATFORM V1 L1 PARTIAL` and name exactly one
next blocker. Do not hide successful phases behind unrelated L2/L3 backlog.

## Final handoff

Return a concise report with:

1. final verdict;
2. repository matrix with exact heads, open relevant PRs and CI;
3. provider/service matrix with evidence tags;
4. consolidated missing-access/credential table, or `none`;
5. exact Docker network fix and rollback;
6. working internal adapter address without credentials;
7. all five HTTP results;
8. n8n workflow ID, execution ID and final inactive state;
9. sanitized record count, field names and recommendation;
10. PR #129 head, merge SHA, resulting platform `main` and post-merge CI;
11. AI Gateway proof and ledger result, if attempted;
12. Company OS status PR and resulting `main`;
13. exactly three next priorities, separated into L2 and later work;
14. one clear owner action only if still blocked.

Stop after this handoff. Do not replace delivery with another architecture,
security redesign or speculative agent framework.

---

