# `deploy/` — declared deployment state

One file per deployable service. The file says what the service should look like
in Coolify; [`scripts/coolify_deploy.py`](../scripts/coolify_deploy.py) makes the
instance match it and [`.github/workflows/coolify-deploy.yml`](../.github/workflows/coolify-deploy.yml)
is the thin wrapper that runs it.

The point of the split is that the target state is reviewable in a pull request.
The workflow inputs choose only *which* service and *which* operation. They can
never carry a value to apply, so nothing reaches production without first
appearing in a diff here.

| Service | Spec |
|---|---|
| AI Gateway | [`ai-gateway.json`](ai-gateway.json) |

## Operations

Run them from **Actions → Coolify deploy → Run workflow**, one at a time.

| Operation | Writes? | What it does |
|---|---|---|
| `inspect` | no | Lists the project, the environment and the applications in it, says whether the declared application exists, and prints the delta between this file and the live resource. This is the plan step. |
| `reconcile` | yes | Creates what is missing, updates what has drifted, then re-reads everything it wrote and fails if the stored state still differs. Running it twice makes no change the second time. |
| `deploy` | yes | Triggers one deployment and polls it to a terminal state, then reports success or failure with the deployment identifier. |
| `status` | no | Reports the resource state and the newest deployment. |

## Rules the tooling enforces

- **Nothing is ever removed.** The HTTP client refuses the `DELETE` method, so no
  later edit can reach a destructive endpoint by accident. Taking a resource away
  stays an owner action performed in the console.
- **Fail closed.** A missing environment value, an unreachable API, a match that
  is not unique and any unexpected HTTP status all stop the run with a non-zero
  exit. A partial apply is never reported as converged.
- **Read back, do not trust the write.** `reconcile` re-reads the application, its
  settings block and its environment after writing, and fails when the stored
  state still disagrees. This is the same pattern as
  [`scripts/bootstrap_rulesets.py`](../scripts/bootstrap_rulesets.py).
- **Private network only.** A spec that declares a public FQDN is rejected, new
  applications are created with domain autogeneration off, and `reconcile` fails
  if it finds a public route on the resource — it will not remove the route
  itself, because that is an owner decision.
- **Values are never echoed.** Environment values are not printed, the access
  value is masked everywhere, and owner-held names are checked for presence only.

## File format

```jsonc
{
  "schema_version": 1,
  "service": "<must equal the file name>",
  "summary": "<one line for a human>",
  "source_of_declared_values": { "repository": "...", "files": [...], "verified_on": "YYYY-MM-DD" },

  "target": {
    "project": "<Coolify project name>",
    "environment": "<Coolify environment name>",
    "resource_name": "<application name>",
    "server": null,        // null = resolve the only server, abort if several
    "destination": null    // null = resolve the only destination, abort if several
  },

  "source": {
    "kind": "private_github_app",   // or "public"
    "git_repository": "<owner/name>",
    "git_branch": "<branch>",
    "github_app": null              // null = resolve the only app, abort if several
  },

  "build":   { "build_pack": "dockerfile", "base_directory": "/...", "dockerfile_location": "/Dockerfile" },
  "network": { "internal_port": 0, "public_fqdn": null, "connect_to_docker_network": true },

  "health_check": {
    "enabled": true, "path": "/health", "method": "GET", "scheme": "http",
    "return_code": 200, "interval_seconds": 15, "timeout_seconds": 10,
    "retries": 5, "start_period_seconds": 30
  },

  "delivery": { "auto_deploy_on_push": false, "preview_deployments": false, "force_https": false },

  // Non-sensitive configuration. These values ARE applied to the resource.
  "configuration": [
    { "key": "NAME", "value": "value", "note": "optional explanation" }
  ],

  // Owner-held configuration, referenced by name only. Never read, written or
  // printed. `reconcile` reports any that is absent; `deploy` refuses to run
  // while one is missing, because the service could not start without it.
  "externally_provided_configuration": [
    { "key": "NAME", "reason": "why it is not in this file" }
  ]
}
```

Validation is strict in both directions: a missing section and an unknown key are
equally fatal. A key that is silently ignored is how a declared value quietly
stops being applied. Every section also accepts an optional `note`, which is prose
for the reader and carries no behaviour.

## Instance prerequisites

Held in this repository, already configured, and referenced by name only:

- repository variable `COOLIFY_URL` — the base address of the Coolify instance.
- repository secret `COOLIFY_API_TOKEN` — the API access value, injected into the
  job environment as `COOLIFY_API_CREDENTIAL` and never printed.

## Adding a service

1. Add `deploy/<service>.json` and get it reviewed.
2. Run `inspect` for that service and read the reported delta.
3. Run `reconcile`, then run it again — the second run must report `changed=no`.
4. Run `deploy` once the owner-held configuration is in place.
