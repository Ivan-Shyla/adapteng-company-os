# Platform v1 — release command center

## Operational checkpoint — 2026-09-05

This checkpoint controls the next execution. The longer 2026-08-13 GitHub-only
review below is retained as historical evidence, not as the current task queue.
When facts conflict, use this order: freshly observed provider runtime, current
repository `main`, this checkpoint, then the older review.

### Evidence boundary

- **GITHUB-VERIFIED:** public `adapteng-company-os` `main` has moved from
  `7805545dbb3f509bafafc341400c8169698bf1f4` to
  `5774be96d81560cdff540f939dcabf14795f17cd` by a clean fast-forward of one
  commit (`docs: record L1 checkpoint and execution authority (#226)`). Draft
  PR #222 remains open and is unrelated to the L1 runtime path.
- **OWNER-SUPPLIED EXECUTION EVIDENCE:** the latest Coolify/n8n/GitHub results
  supplied by Ivan are recorded below. This recording session could not access
  the private implementation repositories or provider consoles, so the next
  connected agent must re-read them before changing the state.
- **HISTORICAL BASELINE ONLY:** the supplied n8n health check and migration
  audit are dated 2026-06-22; the architecture documents are dated
  2026-06-25/26. The inventory contains 83 original workflow rows plus 9
  restored inactive helpers in the later archive. None of those counts may be
  treated as current without a live n8n read.
- **UNKNOWN:** `n8n-selfhosted`'s own Docker network membership remains the one
  unresolved runtime fact, because the Coolify API could not be queried. Private
  repository heads, credential presence and the adapter's alias/port were
  re-read on 2026-09-05 and are recorded in the verification table below.

### Current verdict

| Layer | Verdict | Latest evidence |
|---|---|---|
| Repository development | **GO** | Company OS `main` is reachable and current public history is intact. |
| L1 authorization | **GO** | Owner-authoritative runtime policy permits existing access, reversible provider configuration, green ordinary PR merges and one bounded internal model proof. |
| L1 end-to-end runtime | **PARTIAL** | Adapter deploy is healthy, but the n8n proof cannot reach it: the proof workflow runs on n8n Cloud, which is off-host and cannot resolve the private Coolify alias. |
| L2 controlled business writes | **NOT PROVEN** | Do not infer L2 from a healthy adapter or repository CI. |
| L3 autonomous external action | **NOT AUTHORIZED** | External send/publish, DNS, destructive production action and unbounded spend remain explicit owner gates. |

Authorization and operation are deliberately separate: L1 is allowed now, but
it is not operational until the live read path and one useful internal result
complete successfully.

### Latest L1 handoff

| Component | State |
|---|---|
| `adapteng-automation-platform` PR #130 | Merged at `f9daf1b50c490e4fdaa4a36cc38beddf18c022ac`; post-merge CI was reported green. Re-verify in the private repository. |
| `adapteng-baserow-adapter` | Deployed from the PR #130 merge and reported `running:healthy`; deployment reference `qr2zdnpthsnewaz6i06ftpsn`. |
| Read contract | `/healthz`, authenticated `/v1/schema/system`, authenticated `/v1/sample/system?limit=3`, expected unauthenticated `401`, expected disallowed-kind `403`. |
| n8n proof | `L1 - Baserow Systems Read Proof`, id `h4P3lQIIUmlmhJAD` (corrected 2026-09-05; the previously recorded `h4P31QIIUm1mhJAD` used digit `1` where the live id uses lowercase `l`), inactive after failed attempts. Latest supplied execution `22677`; earlier attempts `22675` and `22676`. |
| Failure | `ENOTFOUND` before any request reached the adapter. Short display name and UUID-derived guesses failed. |
| Network setting | `n8n-selfhosted` showed `Connect to predefined network`; a real redeploy was completed, but DNS still failed. Actual shared network membership and runtime alias remain unverified. |
| Existing n8n credential reference | `X-Worker-Token`; reuse by reference and never expose its stored value. |
| Evidence PR #129 | Draft, supplied head `6952f2e335f49ef192950a48a4b173e648237574`, reported 12/12 CI green. Keep draft until live proof succeeds. |
| Baserow mutation | None. The failed request did not reach the adapter and no write was authorized. |

**Single immediate blocker (SUPERSEDED 2026-09-05, second pass):** this entry
read "attach `n8n-selfhosted` to the Docker network that already carries the
`adapteng-baserow-adapter` alias". That attachment was then verified to already
exist, so it is **not** the fault. See "Root cause — 2026-09-05 (second pass)".
Do not try further guessed hostnames.

### Connected-session verification — 2026-09-05

A session with GitHub and authenticated n8n access, but **no** Coolify, Docker,
Postgres, Baserow or B2 client, token or network route, re-read the items above.
It changed no provider state.

| Claim under test | Result | Evidence |
|---|---|---|
| Platform PR #130 merge SHA | **CONFIRMED** | `f9daf1b5…` is the current `adapteng-automation-platform` `main`; its 30 latest main check runs all concluded success. Upgraded from owner-supplied to GITHUB-VERIFIED. |
| Adapter alias is wrong or unresolvable | **REFUTED** | Company-os run `31590576870` measured `adapteng-baserow-adapter` → `10.0.1.11` from inside the shared `adapteng-ops` network. The adapter sets `custom_network_aliases=adapteng-baserow-adapter`. |
| Adapter port / read contract | **CONFIRMED from source** | Binds `0.0.0.0:8080`; `/healthz` unauthenticated; `/v1/schema/system` and `/v1/sample/system` authenticated; `system` is the only readable kind, other kinds return `403`. |
| Proof workflow id | **CORRECTED** | Live id is `h4P3lQIIUmlmhJAD`, not `h4P31QIIUm1mhJAD`. |
| Proof workflow is executable by an agent | **REFUTED** | It is one of only 2 workflows out of 91 with `availableInMCP: false`, so a connected agent cannot read, update or run it. |
| n8n instance census | **REFRESHED** | 91 workflows, 32 active, 89 MCP-exposed. The 2026-07-27 census of 2 + 89 is superseded; the self-hosted/cloud split is **UNKNOWN** from this read. |
| Coolify control plane is usable | **REFUTED** | Two authorized read-only probes (runs `33962710912`, `33962771132`, ~90 s apart) both returned `HTTP 502` on `GET /projects`. The same workflow and token reference succeeded on 2026-08-13 (run `31678106671`). |
| Deployed applications are down | **NOT SUPPORTED** | In the same session the n8n management API answered normally and n8n runs on this host, so containers and ingress were serving while the control plane returned 502. |
| Baserow was written to | **NO** | No request reached the adapter; no write was attempted or authorized. |

### Root cause — 2026-09-05 (second pass, Coolify restored)

The Coolify control plane recovered, which closed the 502 condition above and
allowed the network question to be settled. Settling it **refuted** the network
theory.

| Claim under test | Result | Evidence |
|---|---|---|
| Coolify control plane is usable | **RESTORED** | Read-only `networks` probe run `33986216491` returned `RESULT networks ok shared_network=YES alias=YES`. |
| `n8n-selfhosted` is off the adapter's network | **REFUTED** | All four applications report `destination.network='coolify'`, `destination_id='0'`. `n8n-selfhosted` (`z3alm18h2giehus9ztzzk9gq`) already shares the network with `adapteng-baserow-adapter` (`rrzq6gk3qpjfwuphvj1vsfzq`). |
| The proof workflow runs on self-hosted n8n | **REFUTED** | The instance holding `h4P3lQIIUmlmhJAD` reports its own base URL as `ivanshyla.app.n8n.cloud`, which resolves to Cloudflare addresses. `n8n.adapteng.com` resolves to `37.27.213.220` and serves with no CDN. Two different instances. |
| The failed proof attempts ran on that same instance | **CONFIRMED** | Execution `22674` (2026-08-23T07:16:46Z) is present on it, immediately preceding the documented attempts `22675` and `22676`. |
| `ENOTFOUND` is a Docker-DNS misconfiguration | **REFUTED** | Reproduced live at 2026-09-05T19:16:17Z (execution `25497`): `getaddrinfo ENOTFOUND adapteng-baserow-adapter`. The same instance resolved public hosts normally in the same session, so egress DNS works; only the private alias is unreachable. |
| The stored n8n API reference still authenticates | **REFUTED** | Both n8n hosts answered HTTP 401 unauthorized to the existing stored reference. Its value was never displayed or copied. |

**Corrected root cause.** `h4P3lQIIUmlmhJAD` executes on **n8n Cloud**, which is
not on the Hetzner host. A managed multi-tenant runner cannot resolve a private
Coolify Docker alias, so no Coolify network change can make this workflow reach
`adapteng-baserow-adapter:8080`. The previously recorded remediation would not
have worked. No Coolify network change was made, and the adapter was not
redeployed.

**Verdict: `PLATFORM V1 L1 PARTIAL`.** The blocker is workflow placement, not
networking. Two owner actions remain, in order:

1. Decide the execution home for the L1 proof. Either rebuild it on the
   self-hosted `n8n.adapteng.com` instance, which already sits on the adapter's
   network, or give the adapter a governed route the cloud instance may use.
   Rebuilding on self-hosted is the smaller path and needs no new exposure.
2. Supply a valid self-hosted n8n API key so the proof can run without console
   work. Requested per the credential-request rule, value never displayed:
   - required secret name: `N8N_SELFHOSTED_API_KEY`
   - provider: self-hosted n8n at `n8n.adapteng.com`
   - exact destination field: the `X-N8N-API-KEY` header of an n8n
     `httpHeaderAuth` credential
   - minimum required scope: workflow read and execute only

`adapteng-automation-platform` PR #129 stays **draft**: the prompt-level rule is
that it merges only after a successful live proof, and no proof occurred.

### Durability follow-up — Coolify auto-update

This item does not block L1. It is recorded because an unattended upgrade on
2026-08-27 removed the four control-plane containers while the upgrade script
recorded success.

Instance auto-update is an instance-level Coolify setting (Settings → Instance →
**Auto Update**; API field `instance_auto_update` on `PATCH /api/v1/settings`).
It could not be changed from this session: the authorized company-os route,
`scripts/coolify_deploy.py`, exposes only `inspect`, `reconcile`, `deploy`,
`status`, `verify`, the `peer-*` and `service-resolve` probes, `diagnose` and
`networks`. None of them reach instance settings, and `DELETE` is globally
forbidden. No direct provider client is configured here.

**Precise follow-up (owner, reversible):** turn **Auto Update** off in the
Coolify instance settings, and re-enable it only for a supervised upgrade
window. No upgrade was executed during this mission.

### Access and credential posture

No new token is requested by this checkpoint. Existing configured access must
be tested first, by name/presence only. Likely relevant references include the
GitHub App/connector for private repositories, the existing Coolify API
connection, the n8n management API connection, `X-Worker-Token`, the adapter's
runtime Baserow credential, the AI Gateway caller credential and its existing
Google/Vertex identity. Their presence is **not** assumed and their values must
never be printed.

If the next agent lacks access, it must return one consolidated setup table:
provider, exact UI/store, credential/variable name, minimum scope, purpose and
verification test. Ivan adds values directly in the named provider store; no
value is pasted into chat or committed.

### Completion target

Platform v1 becomes **L1 OPERATIONAL** when all of the following are evidenced:

1. n8n reaches the adapter on a verified internal address;
2. the five HTTP contract checks return `200/200/200/401/403` as applicable;
3. the sample contains no more than three records and only the approved system
   fields;
4. the existing n8n proof execution succeeds and the workflow returns inactive;
5. one internal, schema-valid recommendation is produced from sanitized system
   metadata, optionally through one already-budgeted AI Gateway call;
6. no Baserow business write, external send or publication occurs;
7. PR #129 is updated with sanitized evidence, passes CI and is merged at its
   exact verified head;
8. Company OS registries and this command center are refreshed from the final
   evidence.

Use the credential-free execution package in
[`polina-ai-platform-completion-prompt.md`](polina-ai-platform-completion-prompt.md).
It grants broad practical authority under
[`owner-ai-runtime-policy.md`](owner-ai-runtime-policy.md) and prevents another
open-ended security audit from replacing delivery.

---

**Owner decision — 2026-08-21.**
[Owner-authoritative AI runtime policy](owner-ai-runtime-policy.md) authorizes
L1 internal AI operation now. Its approval of existing Baserow access and the
current instance-level n8n MCP state controls over older L1 prerequisites; B-2
and B-7 remain historical evidence and later operational considerations, not L1
blockers.

**observed_at:** 2026-08-13T11:40Z (UTC). **Corrected 2026-08-13T19:20Z** after an
independent review; §7 records the withdrawn finding.

**Evidence scope.** GitHub only, read via the REST API and `gh` as the
authenticated account `PalinaRuban`: repository metadata, branch heads, complete
Actions run history, check-run outputs, and Git ancestry computed from fresh clean
clones. Six repositories were inspected; all six were accessible.

**Provider runtime was NOT inspected.** No Coolify, Hetzner, n8n, Backblaze B2,
Google, Cloudways or WordPress console, API or host was contacted. Nothing below
is a statement about what is actually running.

**Evidence-state tags.** Every blocker carries one: `GITHUB-VERIFIED`
(reproducible from the API or Git history now) · `RECORDED` (asserted by a tracked
file, not independently confirmed) · `STALE` (from a dated audit not since
repeated) · `OWNER-ATTESTED` (owner's manual check, not reproducible from GitHub) ·
`UNKNOWN` (not settleable from GitHub).

**Freeze respected.** `adapteng-automation-platform` was read only: no branch,
push, rebase, rerun, comment or edit of PR #121 occurred.

---

## 1. Release verdict and operating levels

**G1–G5 (§4) gate the full Platform v1 production/autonomous release. They do not
uniformly block narrower operating levels.** A gate unmet for autonomous external
action does not automatically forbid an internal read-only pilot.

| Level | Verdict | Scope | Controlling reason |
|---|---|---|---|
| **L0 — Repository development** | **GO** | Code, tests, CI, draft PRs. No provider or production access. | All five active repositories green on `main`; `main-protected` rulesets active. |
| **L1 — Internal read-only AI pilot** | **GO** | Owner-approved internal documents, AI model calls, internal drafts and recommendations. **Forbidden:** production writes, deployment, email sending, public publishing, deletion, autonomous external actions. | Authorized by the 2026-08-21 owner runtime policy. B-2 and B-7 do not block L1; neither do PR #121, production backup, a second administrator, a separate POSIX host, or full G1–G5 closure. |
| **L2 — Controlled production actions** | **NO-GO** | Human-approved writes and deployments with rollback. | B-3 (deploy source not `main`), B-4 (no evidenced backup), B-5 (restore fails closed). |
| **L3 — Autonomous external actions** | **NO-GO** | Requires L2 controls, credential closure, proven end-to-end control and an owner-approved external-action policy. | B-1, B-2, B-7, plus all of L2. |

**L1 operating boundary.** L1 is internal, draft-only, and human-reviewed. The
owner runtime policy authorizes continued use of approved existing access; B-2
and B-7 are not L1 prerequisites. PR #121 authorization remains a separate,
later owner decision.

---

## 2. Repository matrix

Heads read 2026-08-13T11:40Z.

| Repository | Vis. | Authoritative role | Default | Current `main` SHA | Open PRs | Open issues | Latest CI on main | Relation to Platform v1 | Current blocker | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| `Ivan-Shyla/adapteng-company-os` | public | Index + control plane; not implementation | `main` | `bc70a896357147fd278f999646a086a6dc3d17ca` | 1 (this draft) | 2 (#32, #18) | `CI` **success** 2026-08-13T10:28Z | Governs the release; owns backup/restore + deploy drivers | B-5 | [repo](https://github.com/Ivan-Shyla/adapteng-company-os) |
| `Ivan-Shyla/adapteng-automation-platform` | private | Platform implementation authority (**FROZEN, read-only**) | `main` | `6ecdd5fb224eae878ed49a522857bc5a21c32b9f` | 1 (#121) | 0 | `Validate Repo` / `Adapter Tests` / `Rollout Policy` / `Secret Scan` all **success** on `6ecdd5f` | Rollout ceremony, AI gateway, adapters, n8n deploy spec | B-1 | [PR #121](https://github.com/Ivan-Shyla/adapteng-automation-platform/pull/121) |
| `Ivan-Shyla/adapteng-website` | private | Public site + Fluent Forms lead capture | `main` | `ae8073085964aa761252138ba739bc5efa24d49f` | 0 | 0 | `Validate Website` **success** 2026-08-12T19:24Z | Lead intake producer (WEB-001) | **none** — former B-6 withdrawn, §7 | [repo](https://github.com/Ivan-Shyla/adapteng-website) |
| `Ivan-Shyla/adapteng-marketing` | private | Marketing media/content worker | `main` | `9afaf96db1024685652383bbf825fc2994da13bc` | 0 | 0 | `validate` **success** 2026-07-31T09:59Z | Media intake; live worker on a legacy credential binding | L-2 | [repo](https://github.com/Ivan-Shyla/adapteng-marketing) |
| `Ivan-Shyla/ai-dev-loop-control-plane` | private | Generic agent lifecycle contract | `main` | `327fc4b63ec60afc8a8a6c3169d062a58d9eb4da` | 0 | 0 | `CI` + `Gitleaks` **success** 2026-08-03T15:11Z | Repository contract only; no business runtime | not on the v1 path | [repo](https://github.com/Ivan-Shyla/ai-dev-loop-control-plane) |
| `PalinaRuban/adapteng` | private | **Legacy, non-authoritative, read-only** | `main` | `9c8acd166bf57dc416ed6de86ced8f0b26ac3eb5` | 0 | 0 | `Legacy containment` **success** 2026-07-28T10:43Z | June-2026 WordPress/Azure snapshot; excluded from Company OS authority | B-2 | [PR #3](https://github.com/PalinaRuban/adapteng/pull/3) |
| `Ivan-Shyla/Kraken` | private | **OUT OF SCOPE** — personal trading project | `main` | not inspected beyond classification | — | — | — | Excluded by `decisions/0002` | n/a | `registry/environments.yaml` |

No additional `Ivan-Shyla` repositories are visible to this account. No repository
was inaccessible.

---

## 3. Deployment relationship matrix

Derived only from repository evidence. A recorded status is what a tracked file or
an Actions run asserts; it is not a runtime observation.

| Service | Owning repository | Declared deployment source branch | Recorded status | Live verification state |
|---|---|---|---|---|
| `adapteng-website` (theme) | `adapteng-website` | `main`, auto-deploy under `wp-content` | `live` | **UNVERIFIED** — last accepted post-deploy evidence is run `31329017343` |
| `adapteng-core` (lead producer) | `adapteng-website` | `main`, manual `Deploy to Cloudways`, confirmation-gated | **deployed carrying WEB-001** | **GITHUB-VERIFIED (deployment) / UNVERIFIED (runtime)** — run [`31622808618`](https://github.com/Ivan-Shyla/adapteng-website/actions/runs/31622808618) succeeded 2026-08-12T17:28Z at head `ce1a200b`, and WEB-001 `6770e749` is a Git ancestor of that head. The workflow rsyncs `wp-content/plugins/adapteng-core/` to the host. GitHub Actions therefore proves the plugin deployment workflow **successfully deployed a tree containing WEB-001**. It does not prove lead-flow business health. |
| `baserow-self-hosted` | `adapteng-automation-platform` (`deploy/coolify`) | not recorded in company-os | `live` | **UNVERIFIED** |
| `n8n-self-hosted` | `adapteng-automation-platform` (`deploy/coolify`) | **`palinaruban-repo-status-review`** (exists at `4b67fa47`, ≠ `main` `6ecdd5fb`) | `live-partial-authority` | **UNVERIFIED** — and the declared source is provably not `main` |
| `n8n-cloud` | n/a (SaaS) | n/a | `live` (current MM/LM authority) | **UNVERIFIED** |
| `postgres-adapteng-ops` | `adapteng-automation-platform` | Coolify, internal-only | `live` | **UNVERIFIED** |
| `ai-gateway` | `adapteng-automation-platform` | Coolify (target), driven by company-os `Coolify deploy` | **CONFLICT** — see C-1 | **UNVERIFIED** — `Coolify deploy` run `31542579590` succeeded 2026-08-11T22:28Z; a workflow success is not a service observation |
| `adapteng-media-worker` | `adapteng-marketing` | Coolify | `live-legacy-binding` | **UNVERIFIED** |
| `adapteng-drive-adapter`, `adapteng-run-ledger`, `integrity-reconciler` | `adapteng-automation-platform` | none | `repo-merged-not-live` | n/a — no deployment claimed |
| `ai-dev-loop-control-plane` | `ai-dev-loop-control-plane` | none | `repo-merged-not-live` | n/a |
| legacy WordPress/Azure | `PalinaRuban/adapteng` | `Deploy WordPress to Azure App Service` | retired | **UNVERIFIED, negative** — last three recorded runs failed; not run since 2026-06-30 |

---

## 4. The five Platform v1 release gates

These gate the **full** production/autonomous release (L2/L3). See §1 for the
narrower levels they do not uniformly block.

| # | Gate | State | What decides it |
|---|---|---|---|
| G1 | **Owner/access continuity** | **NOT MET** | No second administrator or tested break-glass operator is evidenced; the `ISO-1` waiver expired 2026-08-08; the shared deploy key is still shared. Blocker: B-2. A standing second administrator may be deferred to L3 in favour of a documented, tested break-glass procedure now. |
| G2 | **Production source from authoritative `main`** | **NOT MET** | `n8n-self-hosted` deploys from `palinaruban-repo-status-review`, not `main`. Blocker: **B-3 only** — the website producer path is now GitHub-verified as deployed from `main` carrying WEB-001 (§3, §7). |
| G3 | **Security P0** | **NOT MET** | The Baserow primary API token was committed to Git history and is not evidenced rotated; n8n MCP exposure is unverified against current provider state; legacy credential rotations are `false`. Blockers: B-2, B-7. |
| G4 | **Production backup + isolated restore** | **NOT MET** | No production backup is evidenced or automation-confirmed by this GitHub-only review: restore manifests are `NOT_CONFIGURED` and automation reports `BLOCKED_ON_UNCONFIGURED_PRODUCTION_BACKUP`. B2 connectivity (`30752237109`) and the nightly isolated rehearsal (`31668917675`) are not proof of a production backup. Blockers: B-4, B-5. |
| G5 | **One controlled end-to-end business flow** | **NOT MET** | The first model proof has never run and `Migrate Approved Assets` has zero runs; its four-phase chain is gated behind PR #121. Blocker: B-1. |

---

## 5. Release blocker queue

Six blockers. Each names the operating level it actually blocks.

| ID | Sev | Evidence state | Blocks | Exact evidence | Owner repo/service | Executable by | Acceptance criterion | Rollback | Recommended next action |
|---|---|---|---|---|---|---|---|---|---|
| **B-1** | P0 | `GITHUB-VERIFIED` | **Not L0/L1.** Full-rollout / L3 authorization; G5 | PR #121 head `4c4a2f00`: `Base-trusted rollout authorization` → **FAILURE**, "The exact current head is not externally authorized"; `Verify exact current head from merged base` → **FAILURE** (2×). 29 other checks green. `migrate-approved-assets.yml` has zero runs. | `adapteng-automation-platform` | **owner-only** (repo admin + phase-env authorization secret) | The exact head carries valid external authorization, both trust-anchor checks pass, then `db_status → preflight → import → replay_verify` each record a successful run | Do not merge; PR #121 stays open at its current head | Owner authorizes exact head `4c4a2f00` **as a separate later decision**. Do not rebase, retitle, rerun or weaken the mechanism. |
| **B-2** | P0 | `RECORDED`; provider-side rotation state `UNKNOWN` from GitHub | **Not L1 under the 2026-08-21 owner policy.** L3 credential closure; G1, G3 | `owner/action-items.md` 🔴: Baserow primary API token literal committed to history, "rotation is not complete or evidenced"; legacy `credential_rotations_complete: false`; shared deploy key not isolated; `ISO-1` waiver expired 2026-08-08 | provider consoles / `PalinaRuban/adapteng` | **owner-only / provider verification** | Token revoked at the provider, replacement installed, and the **old value verified to fail** | n/a — rotation is forward-only | Later operational consideration: owner may rotate or confirm rotation and record old-value failure. **Do not rewrite Git history solely to remove an already-rotated historical token.** |
| **B-3** | P0 | `RECORDED`; branch divergence `GITHUB-VERIFIED` | **L2** deploy-source integrity; G2 | `registry/services.yaml`: "Coolify still deploys from branch `palinaruban-repo-status-review`". That branch exists at `4b67fa4704ea05e6e63de3e22d69f66779f84499`; `main` is `6ecdd5fb`. Provably different commits. | `n8n-self-hosted` / Coolify | **owner-only** (Coolify console) | Coolify source is `main`; an auto-deploy from `main` is observed and recorded | Repoint to the prior branch; it is retained | Owner verifies and repoints the Coolify source to `main`. |
| **B-4** | P0 | `RECORDED`; provider-side absence `UNKNOWN` | **L2/L3** production writes; G4 | `owner/action-items.md` ⚪: "the backup itself is still not configured"; automation literal `BLOCKED_ON_UNCONFIGURED_PRODUCTION_BACKUP`; restore manifests `NOT_CONFIGURED`, which stops execution by design | `adapteng-company-os` + provider | **owner-only** for provider steps; manifest population is agent-executable | One production pgBackRest backup of `adapteng_ops` exists, post-backup `check` passes, parsed selected-set `verify` passes, and one isolated restore completes on a disposable host | Delete the disposable restore host/volumes; revoke the read-only key | Owner takes one production backup and completes one isolated restore proof before L2 writes. No provider-side absence is claimed without provider inspection. |
| **B-5** | P1 | `GITHUB-VERIFIED` | A real restore attempt; **L2**; G4 | `validate_repository` in `scripts/postgres_restore_guard.py` (currently near line 472 on this branch) compares `config["repo_path"]` against the hardcoded literal `"/adapteng-ops"` and raises `GuardError("repository stanza/repo is not exact")`. The configured `PGBACKREST_REPO1_PATH` does not match, so a wired restore fails closed before it starts. Tracked as issue [#32](https://github.com/Ivan-Shyla/adapteng-company-os/issues/32). | `adapteng-company-os` | **agent-executable, in a separate bounded PR** | Guard and configured variable agree; a test asserts they cannot diverge; issue #32 closable by evidence | Revert the single commit; the guard is fail-closed either way | Fix in the separate PR sketched in §10. **Not fixed here**; B-5 stays open until that code mismatch is fixed. |
| **B-7** | P1 | `RECORDED BUT NOT LIVE-VERIFIED; STALE — owner audit dated 2026-07-27` | **Not L1 under the 2026-08-21 owner policy.** G3 | `owner/action-items.md` 🔴: audited n8n workflows still have **Available in MCP** enabled because the update API rejected that unsupported field. The 89 non-archived / 31 active / 58 inactive counts come from an owner audit dated **2026-07-27** and are **not current live GitHub-verifiable state**. Merged containment (platform PR #85, `99f4d88e`) changed no live availability. | `n8n-cloud` + `n8n-self-hosted` | **owner-only / provider verification** | Instance-level MCP disabled, or effective exposure proven to be an explicit strict allowlist, verified against an authenticated session | Re-enable per-workflow availability; normal triggers are unaffected either way | Later operational consideration: current n8n MCP configuration may be verified for G3, but it does not block L1. Do not treat the 2026-07-27 counts as current. |

**B-6 was withdrawn** on independent re-verification — see §7. No new blockers
were created.

---

## 6. Later backlog (non-blocking)

| ID | Item | Source |
|---|---|---|
| L-1 | Issue #18 — `scheduler_records()` fails closed on symlinked systemd units; downstream of B-4. | [#18](https://github.com/Ivan-Shyla/adapteng-company-os/issues/18) |
| L-2 | `adapteng-media-worker` still runs on the legacy `GDRIVE_SA_JSON` service account. | `registry/services.yaml` |
| L-3 | Baserow off-host export/restore completion. | `owner/action-items.md` |
| L-4 | Rotate the Coolify API token post-launch. | `owner/action-items.md` |
| L-5 | n8n Cloud inventory drift: 14 live-only / 7 repo-only vs 82 repository exports (`STALE`, same 2026-07-27 audit as B-7). | `registry/services.yaml` |
| L-6 | Legacy `Approval_Log` / `Publish_Plan` reconciliation; 19 pending `Content_Drafts`. | `owner/action-items.md` |
| L-7 | INT-001 live wiring, deferred by ADR-0011; writes forbidden permanently. | `registry/services.yaml` |
| L-8 | `ai-dev-loop-control-plane` is `repo-merged-not-live`, `REJECT_LIVE`; not on the v1 path. | `registry/services.yaml` |
| L-9 | Record vendor commercial baselines; GitHub Actions has a $10/month hard stop. | `owner/action-items.md` |
| L-10 | 45 stale branches on `adapteng-automation-platform`, 19 on `adapteng-company-os`, await deliberate classification. | branch listing, 2026-08-13 |

---

## 7. B-6 — withdrawn on independent re-verification

The earlier B-6 claim ("no plugin deploy carries the lead-intake code", resting on
run `30720691975`) was **materially false**. It came from reading one cached run
instead of the complete workflow history.

Complete history queried for `Ivan-Shyla/adapteng-website`, workflow **305353567**
(`Deploy to Cloudways`, `.github/workflows/deploy-cloudways.yml`): **58 successful
runs**. Four post-date the WEB-001 merge `6770e749` (2026-08-01T22:54:57Z).
Ancestry was computed with `git merge-base --is-ancestor` on a fresh clean clone —
**not inferred from dates**:

| Run | Head SHA | Created | `6770e749` is ancestor |
|---|---|---|---|
| [`31622808618`](https://github.com/Ivan-Shyla/adapteng-website/actions/runs/31622808618) | `ce1a200b94f1b8d0a391ade14bc13ed3b3384e39` | 2026-08-12T17:28:36Z | **yes** |
| [`31609664551`](https://github.com/Ivan-Shyla/adapteng-website/actions/runs/31609664551) | `b35624f4fbc0be971e227ad1677d18a0b7d70f2d` | 2026-08-12T14:57:38Z | **yes** |
| [`31584895075`](https://github.com/Ivan-Shyla/adapteng-website/actions/runs/31584895075) | `fac754239ef04cf599cf8e0dbef788d06bbc54d8` | 2026-08-12T09:52:35Z | **yes** |
| [`31581843295`](https://github.com/Ivan-Shyla/adapteng-website/actions/runs/31581843295) | `c9d1fdabba7c1d5a210c7e9f98d72ed815ab26a7` | 2026-08-12T09:12:14Z | **yes** |
| `30720691975` (previously cited) | `50e7ecc4101c9e0f7d7e2d706cadc97829d92d38` | 2026-08-01T22:10:23Z | no — and `50e7ecc4` is itself an *ancestor* of `6770e749`, confirming it was a genuinely pre-WEB-001 deploy |

The workflow's `Deploy adapteng-core` step rsyncs `wp-content/plugins/adapteng-core/`
to the Cloudways host, so a successful run of this workflow is a plugin
deployment, not a theme-only one. The `lead-intake.php` blob differs across the
boundary (`d1deb4d7` at `50e7ecc4` → `b834339d` at `ce1a200b`), so the deployed
tree carries the WEB-001 contract and its later hardening.

**Newest independently verified successful run carrying WEB-001: `31622808618`.**

**Conclusion.** GitHub Actions proves the plugin deployment workflow successfully
deployed a tree containing WEB-001. B-6 is **removed** from the blocker queue, not
downgraded, and G2 no longer cites it. This does **not** claim runtime
business-flow health: producer T1–T4, atomic no-dual-write cutover, reconciliation
and MM-18 retirement remain separate questions, not evidenced here.

One reviewer-cited run ID, `31518432905`, returns **HTTP 404** and does not exist
in this repository; it is excluded. Run `31584895075`, not cited by the reviewer,
was found by the complete-history query and is included.

---

## 8. Evidence conflicts

| # | Contradiction | Which evidence is stronger | Disposition |
|---|---|---|---|
| C-1 | `registry/services.yaml` (updated 2026-08-10) says `ai-gateway` is `implemented-tested-not-deployed`, `deployed: false`. `control-plane/current-state.md` §9 D-3 says it **is** deployed and `/ready` answers 200, citing run `31542579590`. | **current-state.md is newer.** Run `31542579590` is real: company-os `Coolify deploy`, `workflow_dispatch`, `success`, head `f5726cb2`, 2026-08-11T22:28Z — a day after the registry's `updated:` date. | Registry entry is stale. A successful deploy workflow is still not a runtime observation, so live state stays **UNVERIFIED**. Registry not edited here (outside allowed paths). |
| C-2 | Whether repository documentation contradicts the restore guard about the pinned prefix literal. | **Settled: the documentation error was already corrected.** Commit `b06a4055` (*"Consume configured pgBackRest repo settings instead of hardcoding them"*, PR #28, 2026-08-03), an ancestor of `main`, rewrote both `runbooks/backup-and-restore.md` and `owner/action-items.md`. Current `main` does **not** assert that nothing reads a literal — it quotes that sentence only to mark it as a past error, and explicitly names `validate_repository` as comparing against a hardcoded literal and failing closed. | Two distinct things, kept apart: (a) the **historical documentation error** — corrected 2026-08-03, closed; (b) the **underlying guard/config mismatch** — still open, tracked as **B-5** and issue [#32](https://github.com/Ivan-Shyla/adapteng-company-os/issues/32). Current documentation is **not** presented as contradicting the code. |
| C-3 | D-1/D-2 rest on the owner's manual production check that all nine migration units are exact. | `OWNER-ATTESTED`; **UNKNOWN from GitHub**. | Leave as owner-attested. Zero runs of `Migrate Approved Assets` is **not** evidence of an unapplied database. |
| C-4 | `registry/services.yaml` `updated: 2026-08-10` predates several 2026-08-11..13 merges. | **Live GitHub state is stronger** for every repository fact. | Registry lag is structural; §2 supersedes it for heads, PRs and CI. |
| C-5 | `PalinaRuban/adapteng` `main`-protection state cannot be read (rulesets API 403, below GitHub Pro). | **UNKNOWN.** Not settleable on this plan. | Legacy repository is non-authoritative; classification unchanged. |

---

## 9. Security proportionality

Recorded so right-sizing is a decision, not drift.

**Keep now.** Exact-head authorization for production-affecting changes · branch
protection and passing CI · credential rotation after known exposure · no new
secrets in Git · per-service deploy-key isolation · production source from
reviewed `main` · provider backup plus one isolated restore proof before
destructive production work · human approval for deployments · human approval for
email, publication and destructive actions · n8n MCP disabled or strictly
allowlisted before AI access · a hard AI-provider spend cap.

**Simplify or defer, in a future separate PR.** The Ed25519 ceremony may be
replaced by GitHub Environment required-reviewer approval, an owner-typed
exact-SHA confirmation, the existing branch protection and CI, and the auditable
GitHub Actions record. A separate non-Windows POSIX machine is **not** required —
the runbook already supports native Windows PowerShell and bundled OpenSSH; only
that false requirement is withdrawn, and the genuine repo-admin scoping stays. A
standing second administrator may be deferred to L3 in favour of a documented,
tested break-glass procedure now. Bespoke audit-log infrastructure may be
deferred; GitHub Actions records suffice initially. Do not rewrite Git history
solely to remove an already-rotated historical token.

**Unchanged.** Keep the current PR #121 trust-anchor mechanism until a separately
reviewed replacement exists and both old and new checks pass during transition. Do
not weaken, bypass, rerun, rebase, retitle, authorize or merge PR #121.

---

## 10. Next actions

Exactly five, in order.

1. **AGENT-EXECUTABLE** — re-verify B-6 against the current complete GitHub Actions history and correct PR #221. ✅ **Done in this revision** (§7).
2. **LATER OWNER OPERATIONAL CONSIDERATION** — rotate or confirm rotation of the Baserow primary token and verify the old value fails (B-2); this does not block L1.
3. **LATER OWNER OPERATIONAL CONSIDERATION** — verify current n8n MCP state for G3 (B-7); this does not block L1.
4. **OWNER ACTION** — set a hard monthly AI-provider spend cap.
5. **OWNER ACTION / PROVIDER VERIFICATION, with later agent support** — verify the Coolify source, take one production pgBackRest backup and complete one isolated restore proof before L2 production writes (B-3, B-4, B-5).

PR #121 authorization remains a **separate later owner decision** and is not placed
ahead of the L1 pilot.

### Next implementation batch — proposed, not started

Three bounded agent-executable tasks. **No branch below was created during this
run.** Each is a separate PR in `adapteng-company-os` only.

| # | Task | Proposed branch | Allowed paths | Acceptance tests | Forbidden actions |
|---|---|---|---|---|---|
| N-1 | Fix the B-5 guard/config mismatch so a wired restore no longer fails closed on an exact-literal comparison (issue [#32](https://github.com/Ivan-Shyla/adapteng-company-os/issues/32)). | `fix/restore-guard-repo-path-32` | `scripts/postgres_restore_guard.py`, `scripts/test_postgres_restore_*.py`, `runbooks/backup-and-restore.md` | `python scripts/validate_sensitive_references.py`; the full `README.md` unit-test list; a **new** test asserting the guard's expected repo path and the configured `PGBACKREST_REPO1_PATH` cannot diverge | Do not weaken, bypass or delete the guard to make tests pass; do not touch provider config; do not close #32 without evidence; do not alter `control-plane/*` or `owner/action-items.md` |
| N-2 | Refresh the stale `ai-gateway` registry entry so C-1 stops contradicting `current-state.md`, recording deployment evidence while keeping live state `UNVERIFIED`. | `chore/registry-ai-gateway-c1` | `registry/services.yaml` | `python scripts/validate_sensitive_references.py`; any registry schema/lint test already in CI | Do not assert the service is live or healthy; do not contact Coolify; do not edit any other registry entry; do not change `control-plane/current-state.md` |
| N-3 | Make `scheduler_records()` tolerate symlinked systemd units instead of failing closed (issue [#18](https://github.com/Ivan-Shyla/adapteng-company-os/issues/18), L-1). | `fix/scheduler-records-symlink-18` | `scripts/postgres_restore_scheduler*.py`, `scripts/test_postgres_restore_scheduler*.py` | The POSIX-only `scripts.test_postgres_restore_scheduler_surface` module **on Linux CI**, plus the full `README.md` unit-test list | Do not follow symlinks outside the unit directory; do not relax `O_NOFOLLOW` protections generally; do not run the POSIX-only surface test on Windows; do not touch backup or guard code |

Each task must be validated on Linux CI where the test is POSIX-only. None of them
unblocks L2 on its own: B-2, B-3, B-4 and B-7 remain owner/provider work.

---

## 11. What this run did not do

No issue, comment, release or deployment run was created. No repository other than
`adapteng-company-os` was modified, and within it only this file and the original
`README.md` link. `adapteng-automation-platform` was read only: PR #121 was not
rebased, retitled, rerun, approved, merged or closed. No provider was contacted.
B-5 was not fixed. No new blockers were created.
