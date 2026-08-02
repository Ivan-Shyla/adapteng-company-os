#!/usr/bin/env python3
"""Create, update and verify the ``main`` branch ruleset in each target repository.

The credential is supplied by the workflow environment and is never logged. Only
repository names, rule kinds and check names are printed.

Two modes are supported. ``plan`` reads the current state and reports the delta
without writing. ``apply`` performs the write and then re-reads every repository
to confirm the stored ruleset matches what was requested.

Required status checks are deliberately restricted to checks that are known to
run on every pull request. A required check that never starts blocks a pull
request permanently, so path-filtered checks must not be listed here.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field


API_ROOT = "https://api.github.com"
OWNER = "Ivan-Shyla"
RULESET_NAME = "main-protected"
CREDENTIAL_VARIABLE = "ADMIN_CREDENTIAL"


@dataclass(frozen=True)
class Target:
    """A repository and the checks that must pass before ``main`` accepts a merge."""

    repo: str
    checks: tuple[str, ...] = field(default=())


TARGETS: tuple[Target, ...] = (
    Target(
        "adapteng-company-os",
        ("Python unit tests", "Sensitive-reference validation"),
    ),
    Target(
        "adapteng-automation-platform",
        (
            "Fail on unencrypted secret-like content",
            "independent-rollout-policy",
            "root-rollout-tests",
            "Validate repository structure and content",
        ),
    ),
    Target("adapteng-website", ("validate",)),
    Target(
        "ai-dev-loop-control-plane",
        (
            "business-eval-harness",
            "container-acceptance",
            "gitleaks",
            "python-tests (ubuntu)",
            "python-tests (windows)",
            "repository-gates",
        ),
    ),
    Target("adapteng-marketing", ("validate",)),
)


def build_payload(target: Target) -> dict:
    """Return the ruleset body for ``target``.

    ``strict_required_status_checks_policy`` stays off on purpose: requiring a
    branch to be current before merging forces a rebase every time ``main``
    moves, and the protection value here is that checks passed, not that the
    branch was freshly rebased.
    """

    rules: list[dict] = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
            },
        },
    ]
    if target.checks:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {"context": name} for name in target.checks
                    ],
                },
            }
        )
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
        },
        "rules": rules,
    }


def request(method: str, path: str, credential: str, body: dict | None = None):
    """Perform one API call and return ``(status, parsed_body)``.

    Transport and HTTP errors are returned rather than raised so that one
    unreachable repository cannot hide the outcome for the others.
    """

    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{API_ROOT}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {credential}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "adapteng-ruleset-bootstrap")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = {"message": raw[:200]}
        return error.code, parsed
    except OSError as error:
        return 0, {"message": f"transport failure: {error.__class__.__name__}"}


def find_existing(repo: str, credential: str):
    """Return ``(status, ruleset_id_or_None, error_message_or_None)``."""

    status, body = request("GET", f"/repos/{OWNER}/{repo}/rulesets", credential)
    if status != 200 or not isinstance(body, list):
        message = body.get("message") if isinstance(body, dict) else "unexpected body"
        return status, None, message
    for entry in body:
        if entry.get("name") == RULESET_NAME:
            return status, entry.get("id"), None
    return status, None, None


def describe(ruleset: dict) -> str:
    """Summarise a stored ruleset for the run log."""

    kinds = sorted({rule.get("type", "?") for rule in ruleset.get("rules", [])})
    contexts: list[str] = []
    for rule in ruleset.get("rules", []):
        if rule.get("type") == "required_status_checks":
            parameters = rule.get("parameters") or {}
            contexts = [
                check.get("context", "?")
                for check in parameters.get("required_status_checks", [])
            ]
    return (
        f"enforcement={ruleset.get('enforcement')} "
        f"rules={','.join(kinds)} "
        f"checks={len(contexts)}[{'; '.join(sorted(contexts))}]"
    )


def verify(target: Target, stored: dict) -> list[str]:
    """Return the list of expectations ``stored`` fails to meet."""

    problems: list[str] = []
    if stored.get("enforcement") != "active":
        problems.append(f"enforcement is {stored.get('enforcement')!r}, expected 'active'")

    conditions = (stored.get("conditions") or {}).get("ref_name") or {}
    if "~DEFAULT_BRANCH" not in (conditions.get("include") or []):
        problems.append("condition does not target the default branch")

    kinds = {rule.get("type") for rule in stored.get("rules", [])}
    for required in ("deletion", "non_fast_forward", "pull_request"):
        if required not in kinds:
            problems.append(f"missing rule {required}")

    stored_checks: set[str] = set()
    for rule in stored.get("rules", []):
        if rule.get("type") == "required_status_checks":
            parameters = rule.get("parameters") or {}
            stored_checks = {
                check.get("context")
                for check in parameters.get("required_status_checks", [])
            }
    expected_checks = set(target.checks)
    if expected_checks and "required_status_checks" not in kinds:
        problems.append("missing rule required_status_checks")
    missing = expected_checks - stored_checks
    if missing:
        problems.append(f"required checks absent: {sorted(missing)}")
    unexpected = stored_checks - expected_checks
    if unexpected:
        problems.append(f"unexpected required checks: {sorted(unexpected)}")
    return problems


def process(target: Target, credential: str, apply: bool) -> bool:
    """Handle one repository. Returns True when the repository ends in the wanted state."""

    print(f"--- {target.repo}")
    status, existing_id, error = find_existing(target.repo, credential)
    if error is not None:
        print(f"    LIST FAILED http={status} message={error}")
        return False
    print(f"    list http={status} existing={'yes' if existing_id else 'no'}")

    payload = build_payload(target)
    if not apply:
        print(f"    PLAN would {'update' if existing_id else 'create'} "
              f"{len(payload['rules'])} rules, {len(target.checks)} required checks")
        return True

    if existing_id:
        write_status, body = request(
            "PUT", f"/repos/{OWNER}/{target.repo}/rulesets/{existing_id}", credential, payload
        )
        expected_status = 200
    else:
        write_status, body = request(
            "POST", f"/repos/{OWNER}/{target.repo}/rulesets", credential, payload
        )
        expected_status = 201

    print(f"    write http={write_status} expected={expected_status}")
    if write_status != expected_status:
        message = body.get("message") if isinstance(body, dict) else "unexpected body"
        print(f"    WRITE FAILED message={message}")
        if isinstance(body, dict) and body.get("errors"):
            print(f"    errors={json.dumps(body['errors'])[:400]}")
        return False

    ruleset_id = body.get("id") if isinstance(body, dict) else None
    if ruleset_id is None:
        print("    WRITE FAILED no ruleset id in response")
        return False

    read_status, stored = request(
        "GET", f"/repos/{OWNER}/{target.repo}/rulesets/{ruleset_id}", credential
    )
    if read_status != 200 or not isinstance(stored, dict):
        print(f"    VERIFY READ FAILED http={read_status}")
        return False

    print(f"    verify http={read_status} {describe(stored)}")
    problems = verify(target, stored)
    if problems:
        for problem in problems:
            print(f"    VERIFY FAILED {problem}")
        return False
    print("    VERIFY OK")
    return True


def main() -> int:
    credential = os.environ.get(CREDENTIAL_VARIABLE, "")
    if not credential:
        print(f"{CREDENTIAL_VARIABLE} is empty; nothing can be read or written.")
        return 2

    mode = os.environ.get("MODE", "plan").strip().lower()
    if mode not in {"plan", "apply"}:
        print(f"MODE must be 'plan' or 'apply', received {mode!r}.")
        return 2

    if mode == "apply" and os.environ.get("CONFIRM", "").strip() != "APPLY RULESETS":
        print("apply mode requires the confirmation phrase; refusing to write.")
        return 2

    print(f"mode={mode} repositories={len(TARGETS)}")
    results = {target.repo: process(target, credential, mode == "apply") for target in TARGETS}

    succeeded = sorted(repo for repo, ok in results.items() if ok)
    failed = sorted(repo for repo, ok in results.items() if not ok)
    print("")
    print(f"SUMMARY mode={mode} ok={len(succeeded)} failed={len(failed)}")
    for repo in succeeded:
        print(f"  OK      {repo}")
    for repo in failed:
        print(f"  FAILED  {repo}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
