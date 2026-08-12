#!/usr/bin/env python3
"""Regression controls for the Coolify deployment driver.

The driver's whole value is that it refuses to do the wrong thing quietly, so
most of what follows hands it an input that must make it stop: a spec with an
unknown key, a resource that matches twice, an API that accepts a write and then
stores something else, a deployment that never reaches a terminal state. A check
that cannot fail is not a check.

The rest pins two promises that are easy to break by accident later: reconcile
makes no writes on a second run, and no operation can reach a removal endpoint.
"""

from __future__ import annotations

import copy
import datetime
import io
import json
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from scripts import coolify_deploy as driver
except ImportError:  # pragma: no cover - direct execution from scripts/
    import coolify_deploy as driver  # type: ignore[no-redef]


ACCESS_VALUE = "example-access-value-0123456789"
PROJECT = "adapteng-ops"
ENVIRONMENT = "production"
RESOURCE = "ai-gateway"
# Read from the committed spec rather than repeated here, so a change to the
# declared peer moves the fixture with it instead of silently making every peer
# test probe an application that no longer exists.
PEER_NAME = driver.load_spec(driver.spec_path(RESOURCE))["network"][
    "peer_probe_application"
]


def minimal_spec() -> dict:
    """Return a spec that loads cleanly, for tests to damage one field at a time."""

    return {
        "schema_version": 1,
        "service": "widget",
        "summary": "A service used only by these tests.",
        "source_of_declared_values": {"repository": "owner/name", "files": [], "verified_on": "2026-01-01"},
        "target": {
            "project": PROJECT,
            "environment": ENVIRONMENT,
            "resource_name": "widget",
            "server": None,
            "destination": None,
        },
        "source": {
            "kind": "private_github_app",
            "git_repository": "owner/name",
            "git_branch": "main",
            "github_app": None,
        },
        "build": {
            "build_pack": "dockerfile",
            "base_directory": "/services/widget",
            "dockerfile_location": "/Dockerfile",
        },
        "network": {
            "internal_port": 8081,
            "public_fqdn": None,
            "connect_to_docker_network": True,
            "peer_probe_application": "ops-runner",
        },
        "health_check": {
            "enabled": True,
            "container_gate": "coolify_http",
            "container_gate_note": "generated probe",
            "path": "/health",
            "method": "GET",
            "scheme": "http",
            "return_code": 200,
            "interval_seconds": 15,
            "timeout_seconds": 10,
            "retries": 5,
            "start_period_seconds": 30,
        },
        "delivery": {"auto_deploy_on_push": False, "preview_deployments": False, "force_https": False},
        "configuration": [{"key": "WIDGET_HTTP_HOST", "value": "0.0.0.0"}],
        "externally_provided_configuration": [{"key": "WIDGET_DATABASE_URL", "reason": "owner held"}],
    }


def write_spec(directory: Path, spec: dict, name: str = "widget") -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


class FakeInstance:
    """A Coolify instance that answers only the calls this driver makes.

    It stores what it is told to store, which is what lets the read-back tests be
    meaningful: a fake that always echoed the request would prove nothing.
    """

    def __init__(self, *, with_application: bool = False, deployment_states=None) -> None:
        self.projects = [{"id": 1, "uuid": "prj-1", "name": PROJECT}]
        self.environments = {"prj-1": [{"id": 7, "uuid": "env-1", "name": ENVIRONMENT}]}
        self.servers = [{"id": 1, "uuid": "srv-1", "name": "hetzner"}]
        self.destinations = {"srv-1": [{"id": 1, "uuid": "dst-1", "name": "coolify"}]}
        # The production instance answers 404 for this endpoint. The default here
        # stays 200 so the existing suites keep exercising the endpoint path, and
        # the tests that reproduce production set it to False explicitly.
        self.destinations_endpoint_present = True
        # How this instance reports the delivery flags: "relation" nests them,
        # "fields" carries them at top level, "none" reports them nowhere.
        # Production is "none"; all three are exercised.
        self.settings_response_shape = "relation"
        self.call_bodies: list[tuple[str, str, dict]] = []
        # Mirrors production: two sources are offered, and only the installed App
        # can read a private repository. A fixture with one app would let a spec
        # that names none pass here and abort against the real instance.
        self.github_apps = [
            {"id": 1, "uuid": "gha-1", "name": "adapteng-coolify"},
            {"id": 2, "uuid": "gha-2", "name": "Public GitHub"},
        ]
        self.applications: list[dict] = []
        # A database object carries the credential its engine was created with.
        # The fixture holds one so a test can assert the report never prints it;
        # a fixture without a credential-bearing field could not tell the
        # difference between a report that redacts and one that has nothing to
        # redact.
        self.databases: list[dict] = [
            {
                "uuid": "db-1",
                "name": "adapteng-postgres",
                "status": "running:healthy",
                "database_type": "standalone-postgresql",
                "image": "postgres:16-alpine",
                "internal_db_url": "postgresql://postgres:s3cr3t-not-for-a-log@adapteng-postgres:5432/postgres",
                "postgres_password": "s3cr3t-not-for-a-log",
            }
        ]
        self.environment_entries: dict[str, list[dict]] = {}
        # Same reasoning as the database fixture above: the file body is the
        # part that must never be reported, so it has to be present here or the
        # assertion proves nothing.
        self.storages: dict = {
            "file_storages": [
                {
                    "uuid": "storage-1",
                    "name": "adc",
                    "mount_path": "/var/run/adapteng/adc.json",
                    "is_directory": False,
                    "resource_type": "App\\Models\\Application",
                    "content": "not-for-a-log-a-stand-in-for-a-real-service-account-key",
                }
            ],
            "persistent_storages": [],
        }
        self.deployment_states = list(deployment_states or ["queued", "in_progress", "finished"])
        # What this instance puts in a deployment's ``logs`` field. None means the
        # field is absent, which is one of the shapes the reader must survive.
        self.deployment_logs = None
        self.deployments: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.reject_writes_silently = False
        if with_application:
            self.add_application()

    # -- state helpers ----------------------------------------------------- #

    def add_application(self, **overrides) -> dict:
        spec = driver.load_spec(driver.spec_path(RESOURCE))
        record = dict(driver.desired_application_fields(spec))
        uuid = "app-1" if not self.applications else f"app-{len(self.applications) + 1}"
        record.update(
            {
                "id": 11 + len(self.applications),
                "uuid": uuid,
                "environment_id": 7,
                "fqdn": None,
                "status": "running:healthy",
                "settings": dict(driver.desired_settings(spec)),
            }
        )
        record.update(overrides)
        self.applications.append(record)
        self.environment_entries[uuid] = [
            {"key": item["key"], "value": item["value"], "is_preview": False}
            for item in spec["configuration"]
        ] + [
            {"key": item["key"], "value": "owner-set", "is_preview": False}
            for item in spec["externally_provided_configuration"]
        ]
        return record

    def application(self, uuid: str) -> dict | None:
        for item in self.applications:
            if item["uuid"] == uuid:
                return item
        return None

    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] in {"POST", "PATCH", "PUT", "DELETE"}]

    # -- transport --------------------------------------------------------- #

    def request(self, method, path, body=None, query=None):
        self.calls.append((method.upper(), path))
        self.call_bodies.append((method.upper(), path, copy.deepcopy(body or {})))
        handler = getattr(self, f"_{method.lower()}", None)
        if handler is None:
            return 405, {"message": "method not allowed"}
        return handler(path, body or {}, query or {})

    def _get(self, path, body, query):
        if path == "/projects":
            return 200, copy.deepcopy(self.projects)
        if path == "/servers":
            return 200, copy.deepcopy(self.servers)
        if path == "/github-apps":
            return 200, copy.deepcopy(self.github_apps)
        if path == "/databases":
            return 200, copy.deepcopy(self.databases)
        if path == "/applications":
            return 200, copy.deepcopy(self.applications)
        match = re.fullmatch(r"/projects/([^/]+)/environments", path)
        if match:
            return 200, copy.deepcopy(self.environments.get(match.group(1), []))
        match = re.fullmatch(r"/servers/([^/]+)/destinations", path)
        if match:
            if not self.destinations_endpoint_present:
                return 404, {"message": "Not found."}
            return 200, copy.deepcopy(self.destinations.get(match.group(1), []))
        match = re.fullmatch(r"/applications/([^/]+)/storages", path)
        if match:
            if self.storages is None:
                return 404, {"message": "Not found."}
            return 200, copy.deepcopy(self.storages)
        match = re.fullmatch(r"/applications/([^/]+)/envs", path)
        if match:
            return 200, copy.deepcopy(self.environment_entries.get(match.group(1), []))
        match = re.fullmatch(r"/applications/([^/]+)", path)
        if match:
            found = self.application(match.group(1))
            if found is None:
                return 404, {"message": "not found"}
            found = copy.deepcopy(found)
            relation = found.pop("settings", {})
            if self.settings_response_shape == "fields":
                found.update(relation)
            elif self.settings_response_shape == "relation":
                found["settings"] = relation
            # "none" is the production shape: the flags are reported nowhere.
            return 200, found
        match = re.fullmatch(r"/deployments/applications/([^/]+)", path)
        if match:
            return 200, [copy.deepcopy(item) for item in self.deployments.values()]
        match = re.fullmatch(r"/deployments/([^/]+)", path)
        if match:
            record = self.deployments.get(match.group(1))
            if record is None:
                return 404, {"message": "not found"}
            if self.deployment_states:
                record["status"] = self.deployment_states.pop(0)
            if self.deployment_logs is not None:
                record["logs"] = self.deployment_logs
            return 200, copy.deepcopy(record)
        return 404, {"message": f"no route for {path}"}

    def _post(self, path, body, query):
        if path == "/projects":
            record = {"id": len(self.projects) + 1, "uuid": "prj-new", "name": body.get("name")}
            self.projects.append(record)
            self.environments["prj-new"] = []
            return 201, {"uuid": record["uuid"]}
        match = re.fullmatch(r"/projects/([^/]+)/environments", path)
        if match:
            bucket = self.environments.setdefault(match.group(1), [])
            record = {"id": 90 + len(bucket), "uuid": "env-new", "name": body.get("name")}
            bucket.append(record)
            return 201, {"uuid": record["uuid"]}
        if path in {"/applications/private-github-app", "/applications/public"}:
            return self._create_application(body)
        match = re.fullmatch(r"/applications/([^/]+)/envs", path)
        if match:
            self.environment_entries.setdefault(match.group(1), []).append(
                {"key": body["key"], "value": body["value"], "is_preview": False}
            )
            return 201, {"uuid": "env-var"}
        if path == "/deploy":
            uuid = f"dep-{len(self.deployments) + 1}"
            self.deployments[uuid] = {
                "deployment_uuid": uuid,
                "status": "queued",
                "created_at": "2026-01-01T00:00:00Z",
                "id": len(self.deployments) + 1,
            }
            return 200, {"deployments": [{"deployment_uuid": uuid, "resource_uuid": query.get("uuid")}]}
        return 404, {"message": f"no route for {path}"}

    def _create_application(self, body):
        settings_keys = set(driver.desired_settings(driver.load_spec(driver.spec_path(RESOURCE))))
        # The real endpoint answers 422 for is_preview_deployments_enabled with
        # "This field is not allowed." Reproducing that here is the point: with a
        # fixture that accepted settings on creation, the payload that production
        # rejects passed every test in this file.
        rejected = sorted(settings_keys & set(body))
        if rejected:
            return 422, {
                "message": "Validation failed.",
                "errors": {name: ["This field is not allowed."] for name in rejected},
            }
        record = {
            "id": 12,
            "uuid": "app-created",
            "environment_id": 7,
            "fqdn": None,
            "status": "exited",
            # A newly created application carries the platform's own defaults, not
            # an empty relation. The exact values are a stand-in; what matters is
            # that they differ from the declared ones, so convergence has real
            # drift to repair rather than an empty dict to fill in.
            "settings": {
                "is_auto_deploy_enabled": True,
                "is_preview_deployments_enabled": False,
                "is_force_https_enabled": True,
                "connect_to_docker_network": False,
            },
        }
        for key, value in body.items():
            if key in settings_keys:
                record["settings"][key] = value
            elif not key.endswith("_uuid") and key not in {
                "environment_name",
                "instant_deploy",
                "autogenerate_domain",
            }:
                record[key] = value
        self.applications.append(record)
        self.environment_entries[record["uuid"]] = [
            {"key": "AI_GATEWAY_BEARER_TOKENS", "value": "owner-set", "is_preview": False},
            {"key": "AI_GATEWAY_DATABASE_URL", "value": "owner-set", "is_preview": False},
            {"key": "AI_GATEWAY_PROVIDER_PROJECT", "value": "owner-set", "is_preview": False},
            {"key": "AI_GATEWAY_FX_USD_EUR", "value": "owner-set", "is_preview": False},
            {"key": "AI_GATEWAY_FX_AS_OF", "value": "owner-set", "is_preview": False},
            {"key": "AI_GATEWAY_FX_SOURCE", "value": "owner-set", "is_preview": False},
        ]
        return 201, {"uuid": record["uuid"]}

    def _patch(self, path, body, query):
        match = re.fullmatch(r"/applications/([^/]+)/envs", path)
        if match:
            for entry in self.environment_entries.get(match.group(1), []):
                if entry["key"] == body["key"] and not entry.get("is_preview"):
                    if not self.reject_writes_silently:
                        entry["value"] = body["value"]
                    return 201, {"uuid": "env-var"}
            return 404, {"message": "no such key"}
        match = re.fullmatch(r"/applications/([^/]+)", path)
        if match:
            record = self.application(match.group(1))
            if record is None:
                return 404, {"message": "not found"}
            if not self.reject_writes_silently:
                settings_keys = set(record.get("settings") or {}) | {
                    "is_auto_deploy_enabled",
                    "is_preview_deployments_enabled",
                    "is_force_https_enabled",
                    "connect_to_docker_network",
                }
                for key, value in body.items():
                    if key in settings_keys:
                        record.setdefault("settings", {})[key] = value
                    else:
                        record[key] = value
            return 200, copy.deepcopy(record)
        return 404, {"message": f"no route for {path}"}


def run_operation(operation, instance, spec=None, **kwargs) -> tuple[int, str]:
    """Run one operation against a fake instance and capture its report."""

    spec = spec or driver.load_spec(driver.spec_path(RESOURCE))
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = operation(instance, spec, **kwargs)
    return code, buffer.getvalue()


def load_committed_spec() -> dict:
    return driver.load_spec(driver.spec_path(RESOURCE))


def application_patches(instance) -> list[dict]:
    """Bodies of the PATCHes made to the application resource itself."""

    return [
        body
        for method, path, body in instance.call_bodies
        if method == "PATCH" and re.fullmatch(r"/applications/[^/]+", path)
    ]


def environment_writes(instance) -> list[tuple[str, str]]:
    """Writes that change stored state, which a repeat run must not produce.

    A blind setting has to be re-sent on every run because nothing can be
    compared against it, so counting every write would make idempotency
    untestable. Environment writes are the ones that carry values, so they are
    the ones a second run must not make.
    """

    return [
        (method, path)
        for method, path in instance.writes()
        if not re.fullmatch(r"/applications/[^/]+", path)
    ]


class CommittedSpecTests(unittest.TestCase):
    """The AI Gateway spec is the artefact a deployment depends on, so it is pinned."""

    def setUp(self) -> None:
        self.spec = driver.load_spec(driver.spec_path(RESOURCE))

    def test_the_committed_spec_loads(self) -> None:
        self.assertEqual(self.spec["service"], RESOURCE)
        self.assertEqual(self.spec["target"]["project"], PROJECT)
        self.assertEqual(self.spec["target"]["environment"], ENVIRONMENT)

    def test_the_private_source_names_its_github_app(self) -> None:
        """Two sources exist on this instance, so the choice cannot be inferred.

        Leaving this null aborted a reconcile against production. Only the
        installed App can read a private repository; the built-in 'Public GitHub'
        source cannot. Reverting to null would abort again, one API call before
        the application is created.
        """

        source = self.spec["source"]
        self.assertEqual(source["kind"], "private_github_app")
        self.assertEqual(source["github_app"], "adapteng-coolify")

    def test_the_loopback_bind_is_overridden(self) -> None:
        """The reason this spec exists.

        The service image ends with a loopback bind. A process on loopback inside
        a container answers nobody on the container network, so the health check
        could never pass and every internal caller would be refused.
        """

        declared = {item["key"]: item["value"] for item in self.spec["configuration"]}
        self.assertEqual(declared["AI_GATEWAY_HTTP_HOST"], "0.0.0.0")
        self.assertNotEqual(declared["AI_GATEWAY_HTTP_HOST"], "127.0.0.1")

    def test_the_spec_declares_no_public_route(self) -> None:
        self.assertIsNone(self.spec["network"]["public_fqdn"])

    def test_the_port_is_declared_once_and_used_everywhere(self) -> None:
        fields = driver.desired_application_fields(self.spec)
        declared = {item["key"]: item["value"] for item in self.spec["configuration"]}
        self.assertEqual(fields["ports_exposes"], "8081")
        self.assertEqual(fields["health_check_port"], "8081")
        self.assertEqual(declared["AI_GATEWAY_HTTP_PORT"], "8081")

    def test_the_build_matches_the_service_layout(self) -> None:
        fields = driver.desired_application_fields(self.spec)
        self.assertEqual(fields["build_pack"], "dockerfile")
        self.assertEqual(fields["base_directory"], "/services/ai-gateway")
        self.assertEqual(fields["dockerfile_location"], "/Dockerfile")
        self.assertEqual(fields["git_branch"], "main")

    def test_owner_held_values_are_named_and_never_valued(self) -> None:
        """The section may say what a key is and whether it is secret, never what it is."""

        for entry in self.spec["externally_provided_configuration"]:
            self.assertEqual(set(entry) - {"note", "sensitive"}, {"key", "reason"})
            self.assertNotIn("value", entry)

    def test_the_committed_spec_states_what_gates_a_rollout(self) -> None:
        """Coolify's enabled flag does not answer this, so the spec must.

        The generated probe runs only when enabled is true; the image's own
        HEALTHCHECK is honoured only when it is false. Reading enabled alone
        cannot distinguish a real gate from no gate at all.
        """

        health = self.spec["health_check"]
        self.assertIn(health["container_gate"], driver.CONTAINER_GATES)
        self.assertTrue(health["container_gate_note"].strip())

    def test_the_curl_free_image_is_why_the_generated_probe_is_off(self) -> None:
        """The image is python:3.11-slim with no curl and no wget.

        Coolify's generated probe is a curl/wget shell command run inside the
        container, so it exited 1 on every attempt and every deployment rolled
        back. Turning enabled back on restores that, so it is pinned off here
        together with the reason.
        """

        health = self.spec["health_check"]
        self.assertFalse(health["enabled"])
        self.assertNotEqual(health["container_gate"], "coolify_http")
        self.assertIn("curl", health["note"])

    def test_the_absent_gate_is_declared_rather_than_implied(self) -> None:
        """A missing check has to be stated, because this service has already
        been reported healthy while unreachable. The note must name what
        restores the gate, so the follow-up is specified rather than remembered.
        """

        health = self.spec["health_check"]
        if health["container_gate"] != "absent":
            self.skipTest("gate is present; nothing to disclose")
        note = health["container_gate_note"]
        self.assertIn("HEALTHCHECK", note)
        self.assertIn("services/ai-gateway/Dockerfile", note)

    def test_the_image_gate_numbers_would_match_the_declared_ones(self) -> None:
        """parseHealthcheckFromDockerfile overwrites the stored interval,
        timeout, retries and start period from the HEALTHCHECK directives. If
        the image and this spec disagreed, reconcile and deploy would overwrite
        each other on every run, so the directives quoted in the note carry
        exactly the numbers declared here.
        """

        health = self.spec["health_check"]
        note = health["container_gate_note"]
        if "HEALTHCHECK" not in note:
            if health["container_gate"] == "image":
                self.fail(
                    "container_gate is 'image', so the image's HEALTHCHECK is the "
                    "only thing gating a rolling update, and its directives "
                    "overwrite the four numbers declared here. The note has to "
                    "quote them or nothing checks that they still agree."
                )
            self.skipTest("no image directives quoted")
        for directive, field in (
            ("--interval=", "interval_seconds"),
            ("--timeout=", "timeout_seconds"),
            ("--start-period=", "start_period_seconds"),
            ("--retries=", "retries"),
        ):
            quoted = re.search(rf"{re.escape(directive)}(\d+)", note)
            self.assertIsNotNone(quoted, f"{directive} is not quoted in the note")
            self.assertEqual(
                int(quoted.group(1)),
                health[field],
                f"{directive} in the image would overwrite {field}",
            )

    def test_the_provider_project_is_no_longer_an_owner_decision(self) -> None:
        """It is a published identifier, and app/config.py refuses to start without it.

        Holding it back made a value that is already committed as a repository
        variable on the platform repository into a startup blocker, which is a
        ceremony that buys nothing.
        """

        declared = {item["key"]: item["value"] for item in self.spec["configuration"]}
        self.assertEqual(declared["AI_GATEWAY_PROVIDER_PROJECT"], "adapteng-workspace-automation")
        owner_held = {item["key"] for item in self.spec["externally_provided_configuration"]}
        self.assertNotIn("AI_GATEWAY_PROVIDER_PROJECT", owner_held)

    def test_authentication_material_is_sensitive_and_provenance_is_not(self) -> None:
        """Redaction follows the reviewed flag, not a guess about the value's shape."""

        flags = {
            item["key"]: driver.is_sensitive(item)
            for item in self.spec["externally_provided_configuration"]
        }
        self.assertTrue(flags["AI_GATEWAY_BEARER_TOKENS"])
        self.assertTrue(flags["AI_GATEWAY_DATABASE_URL"])
        for key in (
            "AI_GATEWAY_FX_USD_EUR",
            "AI_GATEWAY_FX_AS_OF",
            "AI_GATEWAY_FX_SOURCE",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ):
            self.assertFalse(flags[key], f"{key} is provenance or a path, not a credential")

    def test_the_reservation_lease_clears_the_floor_config_computes(self) -> None:
        """app/config.py refuses a lease at or below the provider attempt window.

        The floor is derived from three other declared values, so changing any of
        them can make a previously valid lease refuse to start. Recomputing it
        here means that arrives as a failing test rather than a boot loop.
        """

        declared = {item["key"]: item["value"] for item in self.spec["configuration"]}
        timeout = int(declared["AI_GATEWAY_PROVIDER_TIMEOUT_SECONDS"])
        attempts = int(declared["AI_GATEWAY_PROVIDER_MAX_ATTEMPTS"])
        backoff = float(declared["AI_GATEWAY_PROVIDER_RETRY_BACKOFF_SECONDS"])
        lease = int(declared["AI_GATEWAY_RESERVATION_LEASE_SECONDS"])
        floor = timeout * attempts + backoff * (2 ** (attempts - 1) - 1) + 15
        self.assertGreater(lease, floor)

    def test_the_adc_path_is_declared_but_not_asserted_inline(self) -> None:
        """Setting it before the file exists is a boot-time failure, not a warning."""

        owner_held = {item["key"] for item in self.spec["externally_provided_configuration"]}
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", owner_held)
        declared = {item["key"] for item in self.spec["configuration"]}
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", declared)

    def test_a_push_to_the_platform_repository_does_not_release(self) -> None:
        self.assertFalse(driver.desired_settings(self.spec)["is_auto_deploy_enabled"])


class SpecValidationTests(unittest.TestCase):
    """Every one of these inputs must be refused."""

    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def load(self, spec: dict, name: str = "widget"):
        return driver.load_spec(write_spec(self.root, spec, name))

    def test_a_well_formed_spec_loads(self) -> None:
        self.assertEqual(self.load(minimal_spec())["service"], "widget")

    def test_a_missing_section_is_refused(self) -> None:
        spec = minimal_spec()
        del spec["health_check"]
        with self.assertRaises(driver.Abort):
            self.load(spec)

    def test_an_unknown_key_is_refused(self) -> None:
        """A key nobody applies is a declared value that silently stops being real."""

        spec = minimal_spec()
        spec["build"]["dockerfle_location"] = "/Dockerfile"
        with self.assertRaises(driver.Abort) as raised:
            self.load(spec)
        self.assertIn("unknown keys", str(raised.exception))

    def test_an_unknown_container_gate_is_refused(self) -> None:
        """Isolated from the enabled/gate invariant on purpose.

        With enabled true, any unknown value also trips the invariant, so the
        test would pass even with the membership check deleted. Disabled plus an
        unknown name satisfies the invariant and leaves only this guard standing.
        """

        spec = minimal_spec()
        spec["health_check"]["enabled"] = False
        spec["health_check"]["container_gate"] = "probably_fine"
        with self.assertRaises(driver.Abort) as raised:
            self.load(spec)
        self.assertIn("unknown container_gate", str(raised.exception))

    def test_claiming_the_generated_probe_while_disabled_is_refused(self) -> None:
        """Coolify runs its generated probe only when health_check_enabled is
        true. A spec that claims coolify_http with the flag off would advertise
        a gate that never runs, which is exactly the confusion this field is for.
        """

        spec = minimal_spec()
        spec["health_check"]["enabled"] = False
        spec["health_check"]["container_gate"] = "coolify_http"
        with self.assertRaises(driver.Abort) as raised:
            self.load(spec)
        self.assertIn("must agree", str(raised.exception))

    def test_claiming_the_image_gate_while_enabled_is_refused(self) -> None:
        """The mirror case, and the one that silently loses the image's probe:
        parseHealthcheckFromDockerfile records a HEALTHCHECK only when the flag
        is off, so with it on Coolify ignores the image and generates curl.
        """

        spec = minimal_spec()
        spec["health_check"]["enabled"] = True
        spec["health_check"]["container_gate"] = "image"
        with self.assertRaises(driver.Abort) as raised:
            self.load(spec)
        self.assertIn("must agree", str(raised.exception))

    def test_a_blank_gate_note_is_refused(self) -> None:
        spec = minimal_spec()
        spec["health_check"]["container_gate_note"] = "   "
        with self.assertRaises(driver.Abort) as raised:
            self.load(spec)
        self.assertIn("container_gate_note", str(raised.exception))

    def test_a_public_route_is_refused(self) -> None:
        spec = minimal_spec()
        spec["network"]["public_fqdn"] = "widget.example.com"
        with self.assertRaises(driver.Abort) as raised:
            self.load(spec)
        self.assertIn("public FQDN", str(raised.exception))

    def test_an_unknown_schema_version_is_refused(self) -> None:
        spec = minimal_spec()
        spec["schema_version"] = 2
        with self.assertRaises(driver.Abort):
            self.load(spec)

    def test_an_unsupported_source_kind_is_refused(self) -> None:
        spec = minimal_spec()
        spec["source"]["kind"] = "carrier-pigeon"
        with self.assertRaises(driver.Abort):
            self.load(spec)

    def test_a_key_declared_both_inline_and_externally_is_refused(self) -> None:
        spec = minimal_spec()
        spec["externally_provided_configuration"].append(
            {"key": "WIDGET_HTTP_HOST", "reason": "contradiction"}
        )
        with self.assertRaises(driver.Abort):
            self.load(spec)

    def test_a_file_naming_a_different_service_is_refused(self) -> None:
        spec = minimal_spec()
        spec["service"] = "something-else"
        with self.assertRaises(driver.Abort):
            self.load(spec)

    def test_malformed_json_is_refused(self) -> None:
        path = self.root / "widget.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(driver.Abort):
            driver.load_spec(path)

    def test_a_service_name_cannot_escape_the_spec_directory(self) -> None:
        for name in ("../secrets", "a/b", "Widget", "", "..", "with space"):
            with self.subTest(name=name), self.assertRaises(driver.Abort):
                driver.spec_path(name)


class ComparisonTests(unittest.TestCase):
    def test_a_clone_url_and_a_short_name_are_the_same_repository(self) -> None:
        """Otherwise every run reports drift and rewrites a field that already matches."""

        for value in (
            "Ivan-Shyla/adapteng-automation-platform",
            "https://github.com/Ivan-Shyla/adapteng-automation-platform",
            "https://github.com/Ivan-Shyla/adapteng-automation-platform.git",
            "git@github.com:Ivan-Shyla/adapteng-automation-platform.git",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    driver.normalize_repository(value),
                    "ivan-shyla/adapteng-automation-platform",
                )

    def test_a_different_repository_is_not_absorbed(self) -> None:
        self.assertNotEqual(
            driver.normalize_repository("Ivan-Shyla/adapteng-automation-platform"),
            driver.normalize_repository("Ivan-Shyla/adapteng-website"),
        )

    def test_numbers_and_their_string_forms_compare_equal(self) -> None:
        self.assertEqual(driver.difference({"health_check_return_code": 200}, {"health_check_return_code": "200"}), [])

    def test_drift_is_reported_with_both_sides(self) -> None:
        changes = driver.difference({"git_branch": "main"}, {"git_branch": "develop"})
        self.assertEqual(changes, [("git_branch", "develop", "main")])

    def test_an_absent_field_counts_as_drift(self) -> None:
        self.assertEqual(driver.difference({"base_directory": "/x"}, {}), [("base_directory", "", "/x")])

    def test_an_unreadable_settings_block_stops_the_run(self) -> None:
        """An unverifiable write must never be reported as a converged one.

        The guard moved from the reader to settings_delta, because only the spec
        knows whether an absence was foreseen. A spec that acknowledges nothing
        still aborts, which is the default this asserts.
        """

        with self.assertRaises(driver.Abort) as raised:
            driver.settings_delta(minimal_spec(), {"uuid": "app-1"})
        self.assertIn("refusing to report success", str(raised.exception))


class EnvironmentPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = minimal_spec()

    def test_an_absent_key_is_created_and_a_matching_one_is_left_alone(self) -> None:
        create, update, unchanged, absent = driver.environment_plan(self.spec, [])
        self.assertEqual(create, [("WIDGET_HTTP_HOST", "0.0.0.0")])
        self.assertEqual((update, unchanged), ([], []))
        self.assertEqual(absent, ["WIDGET_DATABASE_URL"])

        entries = [
            {"key": "WIDGET_HTTP_HOST", "value": "0.0.0.0"},
            {"key": "WIDGET_DATABASE_URL", "value": "owner-set"},
        ]
        create, update, unchanged, absent = driver.environment_plan(self.spec, entries)
        self.assertEqual((create, update, unchanged, absent), ([], [], ["WIDGET_HTTP_HOST"], []))

    def test_a_drifted_value_is_updated(self) -> None:
        entries = [{"key": "WIDGET_HTTP_HOST", "value": "127.0.0.1"}]
        create, update, _, _ = driver.environment_plan(self.spec, entries)
        self.assertEqual((create, update), ([], [("WIDGET_HTTP_HOST", "0.0.0.0")]))

    def test_a_preview_row_is_not_mistaken_for_the_runtime_value(self) -> None:
        entries = [{"key": "WIDGET_HTTP_HOST", "value": "127.0.0.1", "is_preview": True}]
        create, update, _, _ = driver.environment_plan(self.spec, entries)
        self.assertEqual((create, update), ([("WIDGET_HTTP_HOST", "0.0.0.0")], []))

    def test_a_duplicated_key_stops_the_run(self) -> None:
        """Two rows for one key means the applied value is a guess."""

        entries = [
            {"key": "WIDGET_HTTP_HOST", "value": "0.0.0.0"},
            {"key": "WIDGET_HTTP_HOST", "value": "127.0.0.1"},
        ]
        with self.assertRaises(driver.Abort):
            driver.environment_plan(self.spec, entries)

    def test_a_supplied_owner_held_value_is_created_and_stops_being_pending(self) -> None:
        create, update, _, absent = driver.environment_plan(
            self.spec, [], {"WIDGET_DATABASE_URL": "postgresql://example"}
        )
        self.assertIn(("WIDGET_DATABASE_URL", "postgresql://example"), create)
        self.assertEqual(update, [])
        self.assertEqual(absent, [])

    def test_a_supplied_owner_held_value_that_differs_is_updated(self) -> None:
        entries = [
            {"key": "WIDGET_HTTP_HOST", "value": "0.0.0.0"},
            {"key": "WIDGET_DATABASE_URL", "value": "postgresql://stale"},
        ]
        create, update, _, absent = driver.environment_plan(
            self.spec, entries, {"WIDGET_DATABASE_URL": "postgresql://fresh"}
        )
        self.assertEqual(create, [])
        self.assertEqual(update, [("WIDGET_DATABASE_URL", "postgresql://fresh")])
        self.assertEqual(absent, [])

    def test_an_unsupplied_owner_held_value_is_never_written_over(self) -> None:
        """The property that makes a partially configured resource safe to reconcile.

        Nothing in this repository holds the value, so there is nothing to push,
        so a value set by someone else cannot be replaced by a stale one.
        """

        entries = [
            {"key": "WIDGET_HTTP_HOST", "value": "0.0.0.0"},
            {"key": "WIDGET_DATABASE_URL", "value": "postgresql://set-elsewhere"},
        ]
        create, update, unchanged, absent = driver.environment_plan(self.spec, entries)
        self.assertEqual((create, update, absent), ([], [], []))
        self.assertNotIn("WIDGET_DATABASE_URL", unchanged)


class SuppliedValueTests(unittest.TestCase):
    """An owner-held value may be handed over by reference, and only by reference."""

    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)
        self.spec = minimal_spec()

    def test_a_declared_key_is_accepted(self) -> None:
        supplied = driver.supplied_values(
            self.spec, {"COOLIFY_SECRET_WIDGET_DATABASE_URL": "postgresql://example"}
        )
        self.assertEqual(supplied, {"WIDGET_DATABASE_URL": "postgresql://example"})

    def test_an_undeclared_key_stops_the_run(self) -> None:
        """A dispatch must not be able to introduce configuration nobody reviewed."""

        with self.assertRaises(driver.Abort) as raised:
            driver.supplied_values(self.spec, {"COOLIFY_SECRET_WIDGET_SMUGGLED": "value"})
        self.assertEqual(raised.exception.code, driver.EXIT_MISCONFIGURED)
        self.assertIn("WIDGET_SMUGGLED", str(raised.exception))

    def test_an_inline_key_cannot_be_overridden_from_the_environment(self) -> None:
        """The reviewed spec stays the only source for a declared value."""

        with self.assertRaises(driver.Abort):
            driver.supplied_values(self.spec, {"COOLIFY_SECRET_WIDGET_HTTP_HOST": "127.0.0.1"})

    def test_an_empty_variable_is_not_a_supplied_value(self) -> None:
        """An unset repository variable expands to an empty string, not to nothing."""

        for blank in ("", "   "):
            self.assertEqual(
                driver.supplied_values(self.spec, {"COOLIFY_SECRET_WIDGET_DATABASE_URL": blank}),
                {},
            )

    def test_unrelated_variables_are_ignored(self) -> None:
        self.assertEqual(
            driver.supplied_values(self.spec, {"PATH": "/usr/bin", "HOME": "/root"}), {}
        )

    def test_a_sensitive_value_is_registered_for_redaction(self) -> None:
        driver.supplied_values(
            self.spec, {"COOLIFY_SECRET_WIDGET_DATABASE_URL": "postgresql://secret-value"}
        )
        self.assertNotIn("postgresql://secret-value", driver.redact("postgresql://secret-value"))

    def test_a_value_marked_not_sensitive_stays_readable(self) -> None:
        """Provenance is evidence. Masking it would defeat the reason it is recorded."""

        spec = minimal_spec()
        spec["externally_provided_configuration"] = [
            {"key": "WIDGET_FX_SOURCE", "reason": "published reference", "sensitive": False}
        ]
        driver.supplied_values(spec, {"COOLIFY_SECRET_WIDGET_FX_SOURCE": "ECB daily 2026-08-10"})
        self.assertEqual(driver.redact("ECB daily 2026-08-10"), "ECB daily 2026-08-10")

    def test_the_default_is_sensitive(self) -> None:
        """Forgetting the flag must hide a value, not expose one."""

        self.assertTrue(driver.is_sensitive({"key": "K", "reason": "r"}))
        self.assertTrue(driver.is_sensitive({"key": "K", "reason": "r", "sensitive": True}))
        self.assertFalse(driver.is_sensitive({"key": "K", "reason": "r", "sensitive": False}))

    def test_a_non_boolean_sensitive_flag_is_refused(self) -> None:
        spec = minimal_spec()
        spec["externally_provided_configuration"] = [
            {"key": "WIDGET_DATABASE_URL", "reason": "owner held", "sensitive": "no"}
        ]
        with TemporaryDirectory() as directory:
            path = write_spec(Path(directory), spec)
            with self.assertRaises(driver.Abort):
                driver.load_spec(path)


class UniqueMatchTests(unittest.TestCase):
    def test_exactly_one_candidate_is_returned(self) -> None:
        self.assertEqual(driver.unique_match([{"uuid": "a"}], "server", "x"), {"uuid": "a"})

    def test_nothing_and_several_are_both_refused(self) -> None:
        for candidates in ([], [{"uuid": "a"}, {"uuid": "b"}]):
            with self.subTest(count=len(candidates)), self.assertRaises(driver.Abort):
                driver.unique_match(candidates, "server", "x")


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def test_removal_is_not_reachable(self) -> None:
        """The guarantee that a later edit cannot quietly reach a delete endpoint."""

        client = driver.Client("https://coolify.example.com", ACCESS_VALUE)
        with self.assertRaises(driver.Abort) as raised:
            client.request("DELETE", "/applications/app-1")
        self.assertEqual(raised.exception.code, driver.EXIT_MISCONFIGURED)

    def test_an_unexpected_status_stops_the_run(self) -> None:
        class Rejecting:
            def request(self, method, path, body=None, query=None):
                return 500, {"message": "upstream exploded"}

        with self.assertRaises(driver.Abort) as raised:
            driver.call(Rejecting(), "GET", "/projects")
        self.assertIn("HTTP 500", str(raised.exception))

    def test_a_body_of_the_wrong_shape_stops_the_run(self) -> None:
        with self.assertRaises(driver.Abort):
            driver.expect_list({"message": "not a list"}, "projects")
        with self.assertRaises(driver.Abort):
            driver.expect_object([1, 2], "application")


class RedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def test_a_registered_value_never_reaches_the_log(self) -> None:
        driver.register_redaction(ACCESS_VALUE)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            driver.emit(f"the API rejected {ACCESS_VALUE} outright")
        self.assertNotIn(ACCESS_VALUE, buffer.getvalue())
        self.assertIn("[redacted]", buffer.getvalue())

    def test_an_echoed_authorization_header_is_masked(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            driver.emit("upstream said: Bearer abcdef0123456789")
        self.assertNotIn("abcdef0123456789", buffer.getvalue())

    def test_a_short_value_is_not_registered(self) -> None:
        """Masking a two-character value would blank out unrelated words."""

        driver.register_redaction("ab")
        self.assertEqual(driver.redact("about"), "about")


class InspectTests(unittest.TestCase):
    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def test_inspect_writes_nothing(self) -> None:
        instance = FakeInstance(with_application=True)
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(instance.writes(), [])
        self.assertIn("matches_spec=yes", report)

    def test_an_absent_application_is_a_normal_answer(self) -> None:
        instance = FakeInstance()
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("application=absent", report)
        self.assertEqual(instance.writes(), [])

    def test_an_absent_project_is_reported_rather_than_invented(self) -> None:
        instance = FakeInstance()
        instance.projects = []
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn(f"project {PROJECT}: ABSENT", report)
        self.assertEqual(instance.writes(), [])

    def test_drift_is_reported_without_writing(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.applications[0]["git_branch"] = "some-feature-branch"
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("field git_branch", report)
        self.assertIn("matches_spec=no", report)
        self.assertEqual(instance.writes(), [])

    def test_two_applications_with_the_declared_name_stop_the_run(self) -> None:
        instance = FakeInstance(with_application=True)
        twin = copy.deepcopy(instance.applications[0])
        twin["uuid"] = "app-2"
        instance.applications.append(twin)
        with self.assertRaises(driver.Abort):
            run_operation(driver.operate_inspect, instance)

    def test_the_database_report_names_the_host_without_printing_a_credential(self) -> None:
        """The DSN host has to come from somewhere; a log is not where a password goes.

        The fixture's database carries a credential in two fields, one of them a
        URL. Both must be absent from the report while the identity that makes
        the report worth running - the address a container would resolve - is
        present. The URL is the interesting one: its address half is exactly what
        is needed and its userinfo half is exactly what must not appear, so
        printing it whole and printing nothing are both wrong.
        """

        instance = FakeInstance(with_application=True)
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("adapteng-postgres", report)
        self.assertIn("standalone-postgresql", report)
        self.assertIn("internal address: adapteng-postgres:5432/postgres", report)
        self.assertIn("internal_db_url", report)
        self.assertNotIn("s3cr3t-not-for-a-log", report)
        self.assertEqual(instance.writes(), [])

    def test_the_address_of_a_url_drops_its_credential(self) -> None:
        self.assertEqual(
            driver.address_of("postgresql://someone:a-credential@db.internal:5432/ops"),
            "db.internal:5432/ops",
        )
        self.assertEqual(driver.address_of(None), "not reported")
        self.assertEqual(driver.address_of(""), "not reported")
        self.assertNotIn(
            "a-credential",
            driver.address_of("postgresql://someone:a-credential@db.internal:5432/ops"),
        )

    def test_an_instance_reporting_no_databases_is_not_an_error(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.databases = []
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("databases: 0", report)

    def test_the_storage_report_names_the_mount_without_printing_the_file(self) -> None:
        """Where the file lands is the fact needed; what is in it is the fact withheld.

        app/config.py fails to boot on a path it cannot open, so the mount path
        has to be known before the environment key naming it is ever set. That
        makes the location worth printing. The body of a file storage would be
        the service account key, so it is the one field a report must drop even
        though the API hands it over in the same object.
        """

        instance = FakeInstance(with_application=True)
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("file_storages: 1", report)
        self.assertIn("mount_path=/var/run/adapteng/adc.json", report)
        self.assertIn("content", report)
        self.assertNotIn("not-for-a-log-a-stand-in-for-a-real-service-account-key", report)
        self.assertEqual(instance.writes(), [])

    def test_an_instance_without_the_storages_endpoint_is_not_an_error(self) -> None:
        """An absent endpoint is a reading, not a failure, exactly as for destinations.

        This driver has already met one Coolify instance that did not serve an
        endpoint the upstream specification lists. Inspect exists to find that
        out, so a 404 here has to leave a line in the report and a zero exit.
        """

        instance = FakeInstance(with_application=True)
        instance.storages = None
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("storages: not reported by this instance", report)
        self.assertEqual(instance.writes(), [])

    def test_the_storage_report_survives_an_instance_answering_with_a_list(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.storages = [
            {"uuid": "storage-9", "mount_path": "/mnt/one", "content": "withheld-body-value"}
        ]
        code, report = run_operation(driver.operate_inspect, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("storages: 1", report)
        self.assertIn("mount_path=/mnt/one", report)
        self.assertNotIn("withheld-body-value", report)


class ContainerGateReportTests(unittest.TestCase):
    """What gates a rollout has to appear in the report, not just in the spec.

    A deploy that prints ok has proved Coolify finished. With no container probe
    it has not proved the container works, and Coolify does not say so: it marks
    the new version healthy on the way past. These tests exist because this
    service was reported running:healthy for hours while detached, and a gap
    nobody prints is a gap nobody sees.
    """

    def gate_report(self, gate: str) -> str:
        spec = driver.load_spec(driver.spec_path(RESOURCE))
        spec["health_check"]["container_gate"] = gate
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            driver.report_container_gate(spec)
        return buffer.getvalue()

    def test_an_absent_gate_says_a_started_container_is_promoted_untested(self) -> None:
        report = self.gate_report("absent")
        self.assertIn("GATE ABSENT", report)
        self.assertIn("without testing", report)

    def test_an_absent_gate_names_the_only_thing_that_does_prove_reachability(self) -> None:
        """Naming the after-the-fact check is the difference between a known gap
        and an unexamined one: the reader is told where the evidence comes from
        and that it arrives after promotion rather than before it."""

        report = self.gate_report("absent")
        self.assertIn("gateway_readiness.py", report)

    def test_a_real_gate_is_not_reported_as_a_warning(self) -> None:
        for gate in ("image", "coolify_http"):
            with self.subTest(gate=gate):
                report = self.gate_report(gate)
                self.assertNotIn("GATE ABSENT", report)
                self.assertIn(gate, report)

    def test_reconcile_states_the_gate_on_its_result_line(self) -> None:
        instance = FakeInstance()
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        gate = driver.load_spec(driver.spec_path(RESOURCE))["health_check"]["container_gate"]
        self.assertIn(f"health_gate={gate}", report)
        self.assertIn("RESULT reconcile ok", report)


class ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def test_an_absent_application_is_created_and_then_converged(self) -> None:
        instance = FakeInstance()
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("changed=yes", report)
        self.assertIn("VERIFY OK", report)
        self.assertEqual(len(instance.applications), 1)

    def test_the_second_run_changes_nothing(self) -> None:
        """Idempotency is the property that makes this safe to run from a chat prompt.

        A setting the API will not report has to be written on every run, because
        there is nothing to compare against - so "no calls at all" is no longer
        the right shape of this guard. What still has to hold is that a second
        run changes no state: the only write is the declared blind value, and it
        carries nothing else.
        """

        instance = FakeInstance()
        first, _ = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(first, driver.EXIT_OK)
        instance.calls.clear()
        instance.call_bodies.clear()
        second, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(second, driver.EXIT_OK)
        self.assertIn("changed=no", report)
        blind = set(driver.settings_written_blind(load_committed_spec()))
        for body in application_patches(instance):
            self.assertEqual(set(body), blind)
        self.assertEqual(environment_writes(instance), [])

    def test_a_created_application_is_never_released_by_the_creation_call(self) -> None:
        instance = FakeInstance()
        captured: list[dict] = []
        original = instance.request

        def recording(method, path, body=None, query=None):
            if method.upper() == "POST" and path in {
                "/applications/private-github-app",
                "/applications/public",
            }:
                captured.append(dict(body or {}))
            return original(method, path, body, query)

        instance.request = recording
        run_operation(driver.operate_reconcile, instance)
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0]["instant_deploy"])
        self.assertFalse(captured[0]["autogenerate_domain"])

    def test_the_creation_call_carries_no_settings(self) -> None:
        """The creation endpoint refuses them; convergence applies them instead."""

        instance = FakeInstance()
        captured: list[dict] = []
        original = instance.request

        def recording(method, path, body=None, query=None):
            if method.upper() == "POST" and path in {
                "/applications/private-github-app",
                "/applications/public",
            }:
                captured.append(dict(body or {}))
            return original(method, path, body, query)

        instance.request = recording
        code, _ = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(len(captured), 1)
        spec = driver.load_spec(driver.spec_path(RESOURCE))
        for name in driver.desired_settings(spec):
            self.assertNotIn(name, captured[0])

    def test_settings_are_converged_after_a_creation(self) -> None:
        """Omitting them from creation must not mean they go unset."""

        instance = FakeInstance()
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        spec = driver.load_spec(driver.spec_path(RESOURCE))
        stored = instance.applications[0]["settings"]
        self.assertEqual(stored, driver.desired_settings(spec))
        self.assertIn("change connect_to_docker_network", report)

    def test_drift_is_written_and_confirmed(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.applications[0]["git_branch"] = "some-feature-branch"
        instance.applications[0]["settings"]["is_auto_deploy_enabled"] = True
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("change git_branch", report)
        self.assertIn("change is_auto_deploy_enabled", report)
        self.assertEqual(instance.applications[0]["git_branch"], "main")
        self.assertFalse(instance.applications[0]["settings"]["is_auto_deploy_enabled"])

    def test_a_drifted_environment_value_is_written_and_confirmed(self) -> None:
        instance = FakeInstance(with_application=True)
        for entry in instance.environment_entries["app-1"]:
            if entry["key"] == "AI_GATEWAY_HTTP_HOST":
                entry["value"] = "127.0.0.1"
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("change env AI_GATEWAY_HTTP_HOST", report)
        stored = {item["key"]: item["value"] for item in instance.environment_entries["app-1"]}
        self.assertEqual(stored["AI_GATEWAY_HTTP_HOST"], "0.0.0.0")

    def test_a_supplied_owner_held_value_is_stored_and_clears_the_pending_line(self) -> None:
        instance = FakeInstance()
        secret = "postgresql://ai_gateway_runtime:example-password@db:5432/adapteng_ops"
        driver.register_redaction(secret)
        code, report = run_operation(
            driver.operate_reconcile, instance, supplied={"AI_GATEWAY_DATABASE_URL": secret}
        )
        self.assertEqual(code, driver.EXIT_OK)
        stored = {
            item["key"]: item["value"]
            for item in instance.environment_entries[instance.applications[0]["uuid"]]
        }
        self.assertEqual(stored["AI_GATEWAY_DATABASE_URL"], secret)
        self.assertNotIn("PENDING-OWNER env AI_GATEWAY_DATABASE_URL", report)

    def test_a_supplied_secret_never_reaches_the_report(self) -> None:
        """The property the whole binding-by-reference design exists to hold."""

        instance = FakeInstance()
        # "example" inside the literal is what marks this a placeholder to
        # validate_sensitive_references.py. It must appear in the source text, not
        # only in the interpolated result: the checker reads the line, not the run.
        # A test fixture is not an exception to that rule ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â the checker cannot tell
        # a fake credential from a real one, and it is right not to try.
        marker = "example-password-must-not-appear"
        secret = "postgresql://ai_gateway_runtime:example-password-must-not-appear@db/ops"
        driver.register_redaction(secret)
        _, report = run_operation(
            driver.operate_reconcile, instance, supplied={"AI_GATEWAY_DATABASE_URL": secret}
        )
        self.assertNotIn(secret, report)
        self.assertNotIn(marker, report)
        self.assertIn("AI_GATEWAY_DATABASE_URL", report)

    def test_supplying_a_value_twice_writes_once(self) -> None:
        """Binding by reference must not make every run an environment write."""

        instance = FakeInstance()
        supplied = {"AI_GATEWAY_FX_USD_EUR": "0.865426"}
        first, _ = run_operation(driver.operate_reconcile, instance, supplied=supplied)
        self.assertEqual(first, driver.EXIT_OK)
        instance.calls.clear()
        second, report = run_operation(driver.operate_reconcile, instance, supplied=supplied)
        self.assertEqual(second, driver.EXIT_OK)
        self.assertIn("changed=no", report)
        self.assertEqual(environment_writes(instance), [])

    def test_an_unsupplied_owner_held_key_is_still_reported_as_pending(self) -> None:
        """Supplying one key must not silence the pending line for the others."""

        instance = FakeInstance(with_application=True)
        instance.environment_entries["app-1"] = [
            entry
            for entry in instance.environment_entries["app-1"]
            if entry["key"] not in {"AI_GATEWAY_DATABASE_URL", "AI_GATEWAY_FX_USD_EUR"}
        ]
        code, report = run_operation(
            driver.operate_reconcile, instance, supplied={"AI_GATEWAY_FX_USD_EUR": "0.865426"}
        )
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("PENDING-OWNER env AI_GATEWAY_DATABASE_URL", report)
        self.assertNotIn("PENDING-OWNER env AI_GATEWAY_FX_USD_EUR", report)
        stored = {item["key"]: item["value"] for item in instance.environment_entries["app-1"]}
        self.assertEqual(stored["AI_GATEWAY_FX_USD_EUR"], "0.865426")
        self.assertNotIn("AI_GATEWAY_DATABASE_URL", stored)

    def test_an_accepted_write_that_stored_nothing_fails(self) -> None:
        """The reason the run re-reads instead of trusting the write response."""

        instance = FakeInstance(with_application=True)
        instance.applications[0]["git_branch"] = "some-feature-branch"
        instance.reject_writes_silently = True
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("VERIFY FAILED", report)
        self.assertIn("RESULT reconcile failed", report)

    def test_an_environment_write_that_stored_nothing_fails(self) -> None:
        instance = FakeInstance(with_application=True)
        for entry in instance.environment_entries["app-1"]:
            if entry["key"] == "AI_GATEWAY_HTTP_HOST":
                entry["value"] = "127.0.0.1"
        instance.reject_writes_silently = True
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("AI_GATEWAY_HTTP_HOST", report)

    def test_a_public_route_fails_the_run_and_is_not_removed(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.applications[0]["fqdn"] = "https://gateway.example.com"
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("public FQDN", report)
        self.assertEqual(instance.applications[0]["fqdn"], "https://gateway.example.com")

    def test_owner_held_values_are_reported_but_never_written(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.environment_entries["app-1"] = [
            entry
            for entry in instance.environment_entries["app-1"]
            if entry["key"] != "AI_GATEWAY_DATABASE_URL"
        ]
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("PENDING-OWNER env AI_GATEWAY_DATABASE_URL", report)
        stored = {item["key"] for item in instance.environment_entries["app-1"]}
        self.assertNotIn("AI_GATEWAY_DATABASE_URL", stored)

    def test_reconcile_never_removes_anything(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.applications[0]["git_branch"] = "some-feature-branch"
        run_operation(driver.operate_reconcile, instance)
        self.assertEqual([call for call in instance.calls if call[0] == "DELETE"], [])

    def test_an_absent_project_is_created_rather_than_written_into_the_wrong_one(self) -> None:
        instance = FakeInstance()
        instance.projects = []
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn(f"project {PROJECT}: created", report)
        self.assertEqual([item["name"] for item in instance.projects], [PROJECT])

    def test_two_servers_stop_a_creation_that_would_have_to_guess(self) -> None:
        instance = FakeInstance()
        instance.servers.append({"id": 2, "uuid": "srv-2", "name": "other"})
        with self.assertRaises(driver.Abort) as raised:
            run_operation(driver.operate_reconcile, instance)
        self.assertIn("server", str(raised.exception))


class SettingsShapeTests(unittest.TestCase):
    """Reconcile must complete against an instance that sends no settings relation.

    This is the production shape. The unit-level reader is covered above; this
    covers the whole operation, because the abort it replaced fired after the
    application had already been created.
    """

    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def test_a_creation_completes_with_flags_as_top_level_fields(self) -> None:
        instance = FakeInstance()
        instance.settings_response_shape = "fields"
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("VERIFY OK", report)
        spec = driver.load_spec(driver.spec_path(RESOURCE))
        self.assertEqual(instance.applications[0]["settings"], driver.desired_settings(spec))

    def test_drift_is_repaired_with_flags_as_top_level_fields(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.settings_response_shape = "fields"
        instance.applications[0]["settings"]["is_auto_deploy_enabled"] = True
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("change is_auto_deploy_enabled", report)
        self.assertFalse(instance.applications[0]["settings"]["is_auto_deploy_enabled"])

    def test_inspect_reports_which_shape_it_read(self) -> None:
        instance = FakeInstance(with_application=True)
        instance.settings_response_shape = "fields"
        _, report = run_operation(driver.operate_inspect, instance)
        self.assertIn("top-level fields", report)

    def test_inspect_compares_against_the_object_reconcile_verifies(self) -> None:
        """Inspect must not report agreement for drift reconcile would repair.

        The list entry and the detail read are different responses. Here the
        detail read carries drift the list entry does not.
        """

        instance = FakeInstance(with_application=True)
        instance.settings_response_shape = "fields"
        instance.applications[0]["settings"]["is_force_https_enabled"] = True
        _, report = run_operation(driver.operate_inspect, instance)
        self.assertIn("setting is_force_https_enabled", report)
        self.assertIn("matches_spec=no", report)

    def test_inspect_names_the_keys_without_printing_any_value(self) -> None:
        """The shape dump must stay a shape dump: names only, never values."""

        instance = FakeInstance(with_application=True)
        instance.applications[0]["private_key_here"] = "supersecretvalue"
        _, report = run_operation(driver.operate_inspect, instance)
        self.assertIn("private_key_here", report)
        self.assertNotIn("supersecretvalue", report)


class StoredSettingsTests(unittest.TestCase):
    """The delivery flags must be readable in whichever shape the API sends them.

    Production returns no settings relation at all. Demanding one aborted a run
    immediately after it had created the application, leaving a resource behind
    with a failed report.
    """

    def setUp(self) -> None:
        self.spec = driver.load_spec(driver.spec_path(RESOURCE))
        self.desired = driver.desired_settings(self.spec)

    def test_the_key_constant_matches_what_the_spec_produces(self) -> None:
        """One list of owned names, so the two readers cannot drift apart."""

        self.assertEqual(set(driver.SETTING_KEYS), set(self.desired))

    def test_a_nested_block_is_read(self) -> None:
        self.assertEqual(
            driver.stored_settings({"settings": dict(self.desired)}), self.desired
        )

    def test_top_level_fields_are_read_when_no_block_is_sent(self) -> None:
        self.assertEqual(driver.stored_settings(dict(self.desired)), self.desired)

    def test_the_nested_block_wins_when_both_are_present(self) -> None:
        """The relation is the authoritative copy where one exists."""

        application = dict(self.desired)
        application["is_force_https_enabled"] = True
        application["settings"] = dict(self.desired)
        self.assertEqual(driver.stored_settings(application), self.desired)

    def test_an_empty_block_falls_through_to_the_fields(self) -> None:
        """An eager-loaded but empty relation is absence, not an answer."""

        application = dict(self.desired)
        application["settings"] = {}
        self.assertEqual(driver.stored_settings(application), self.desired)

    def test_neither_shape_reports_nothing_rather_than_aborting(self) -> None:
        """Reading is not judging. The absence is settings_delta's to rule on."""

        self.assertEqual(driver.stored_settings({"uuid": "app-1", "name": "ai-gateway"}), {})

    def test_a_partial_field_set_is_reported_as_partial(self) -> None:
        shape = driver.settings_shape({"is_force_https_enabled": False})
        self.assertIn("top-level fields", shape)
        self.assertIn(f"1 of {len(driver.SETTING_KEYS)}", shape)


class UnverifiableSettingsTests(unittest.TestCase):
    """A setting the API will not report may be tolerated only if the spec says so.

    Production reports none of the four. The guard that refused to proceed was
    right in principle and unconditional in practice: it aborted a run that had
    already created the application. Tolerance now has to be declared, with a
    reason, in the committed spec.
    """

    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)
        self.spec = driver.load_spec(driver.spec_path(RESOURCE))
        self.desired = driver.desired_settings(self.spec)

    def test_the_committed_spec_acknowledges_every_flag_with_a_justification(self) -> None:
        acknowledged = driver.unreportable_settings(self.spec)
        self.assertEqual(set(acknowledged), set(driver.SETTING_KEYS))
        for name, entry in acknowledged.items():
            self.assertTrue(entry["reason"].strip(), name)
            self.assertTrue(entry["compensating_control"].strip(), name)

    def test_an_unacknowledged_absence_still_aborts(self) -> None:
        """The fail-closed half of the guard, kept and pinned."""

        spec = copy.deepcopy(self.spec)
        del spec["settings_not_reported_by_api"]["keys"]["is_force_https_enabled"]
        with self.assertRaises(driver.Abort) as raised:
            driver.settings_delta(spec, {"uuid": "app-1"})
        self.assertIn("is_force_https_enabled", str(raised.exception))
        self.assertIn("refusing to report success", str(raised.exception))

    def test_a_misspelt_acknowledgement_aborts(self) -> None:
        """Otherwise a typo silently widens what the tool will ignore."""

        spec = copy.deepcopy(self.spec)
        spec["settings_not_reported_by_api"]["keys"]["is_force_https_enable"] = {
            "reason": "x",
            "compensating_control": "y",
        }
        with self.assertRaises(driver.Abort) as raised:
            driver.unreportable_settings(spec)
        self.assertIn("is_force_https_enable", str(raised.exception))

    def test_an_acknowledgement_without_a_reason_aborts(self) -> None:
        spec = copy.deepcopy(self.spec)
        spec["settings_not_reported_by_api"]["keys"]["is_force_https_enabled"]["reason"] = "  "
        with self.assertRaises(driver.Abort) as raised:
            driver.unreportable_settings(spec)
        self.assertIn("justification", str(raised.exception))

    def test_a_reported_flag_is_still_compared(self) -> None:
        """Acknowledgement tolerates absence, not disagreement."""

        stored = {"is_force_https_enabled": not self.desired["is_force_https_enabled"]}
        changes, unverifiable = driver.settings_delta(self.spec, stored)
        self.assertEqual([name for name, _, _ in changes], ["is_force_https_enabled"])
        self.assertNotIn("is_force_https_enabled", unverifiable)

    def test_reconcile_completes_when_the_api_reports_none_of_them(self) -> None:
        """The production shape, end to end, including the creation path."""

        instance = FakeInstance()
        instance.settings_response_shape = "none"
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("VERIFY OK", report)
        self.assertIn("unreported_settings=3", report)
        self.assertIn("written_blind=1", report)
        self.assertIn("PENDING-OWNER-UI", report)

    def test_an_unreportable_setting_without_a_verifier_is_never_written(self) -> None:
        """A write that can be confirmed by nothing at all must not be attempted.

        The rule narrowed rather than relaxed: it is about whether anything
        observes the setting, not about whether this endpoint reads it back. A
        setting that names no check is still unverifiable and is still withheld.
        """

        instance = FakeInstance()
        instance.settings_response_shape = "none"
        run_operation(driver.operate_reconcile, instance)
        blind = set(driver.settings_written_blind(load_committed_spec()))
        self.assertTrue(blind, "the spec should exercise this path")
        withheld = set(driver.SETTING_KEYS) - blind
        for body in application_patches(instance):
            self.assertEqual(set(body) & withheld, set())

    def test_a_blind_write_is_never_folded_into_the_verified_result(self) -> None:
        """This run sent it and cannot read it back. Saying otherwise invents a check."""

        instance = FakeInstance()
        instance.settings_response_shape = "none"
        _, report = run_operation(driver.operate_reconcile, instance)
        self.assertIn("WRITTEN NOT VERIFIED connect_to_docker_network", report)
        self.assertIn("gateway_readiness.py probe", report)

    def test_a_setting_becomes_writable_only_by_naming_its_check(self) -> None:
        """The named check is the whole permission, so an empty name grants nothing."""

        spec = load_committed_spec()
        keys = spec["settings_not_reported_by_api"]["keys"]
        self.assertIn("verified_by", keys["connect_to_docker_network"])
        for name in ("is_auto_deploy_enabled", "is_force_https_enabled"):
            with self.subTest(name=name):
                self.assertNotIn("verified_by", keys[name])
        stripped = copy.deepcopy(spec)
        stripped["settings_not_reported_by_api"]["keys"]["connect_to_docker_network"].pop(
            "verified_by"
        )
        self.assertEqual(driver.settings_written_blind(stripped), {})
        blank = copy.deepcopy(spec)
        blank["settings_not_reported_by_api"]["keys"]["connect_to_docker_network"][
            "verified_by"
        ] = "   "
        self.assertEqual(driver.settings_written_blind(blank), {})

    def test_the_blind_value_written_is_the_declared_one(self) -> None:
        """A blind write that sent something other than the spec would be undetectable."""

        instance = FakeInstance()
        instance.settings_response_shape = "none"
        run_operation(driver.operate_reconcile, instance)
        declared = driver.desired_settings(load_committed_spec())
        seen = {}
        for body in application_patches(instance):
            seen.update({k: v for k, v in body.items() if k in driver.SETTING_KEYS})
        self.assertEqual(seen.get("connect_to_docker_network"), declared["connect_to_docker_network"])


class ApiMessageTests(unittest.TestCase):
    """An error body must report why, not merely that."""

    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def test_field_errors_are_not_shadowed_by_the_generic_message(self) -> None:
        """The defect this replaced: a 422 read "Validation failed." and no more.

        Coolify sends the generic message and the field-level errors together.
        Returning the first key found discarded the only actionable half.
        """

        rendered = driver.api_message(
            {
                "message": "Validation failed.",
                "errors": {"ports_exposes": ["The ports exposes field is required."]},
            }
        )
        self.assertIn("Validation failed.", rendered)
        self.assertIn("ports_exposes", rendered)

    def test_a_lone_message_still_renders(self) -> None:
        self.assertIn("Not found.", driver.api_message({"message": "Not found."}))

    def test_a_body_with_no_known_key_is_still_shown(self) -> None:
        self.assertIn("unexpected", driver.api_message({"unexpected": "shape"}))

    def test_an_empty_body_is_named(self) -> None:
        self.assertEqual(driver.api_message(None), "no body")

    def test_a_registered_secret_is_redacted_from_an_error_body(self) -> None:
        driver.register_redaction("example-password-in-an-error")
        self.assertNotIn(
            "example-password-in-an-error",
            driver.api_message({"message": "rejected example-password-in-an-error"}),
        )


class DestinationResolutionTests(unittest.TestCase):
    """Placement must survive an instance that does not expose a destination list.

    The production instance answers 404 for /servers/{uuid}/destinations. A
    creation still needs somewhere to go, and the applications already running
    in the target environment are the strongest evidence of where that is.
    """

    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def absent_endpoint_instance(self) -> "FakeInstance":
        instance = FakeInstance()
        instance.destinations_endpoint_present = False
        return instance

    def test_the_destination_is_inherited_from_a_neighbour(self) -> None:
        instance = self.absent_endpoint_instance()
        instance.add_application(name="adapteng-baserow-adapter", destination_uuid="dst-live")
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("inherited dst-live", report)
        created = [item for item in instance.applications if item["uuid"] == "app-created"]
        self.assertEqual(len(created), 1)

    def test_a_nested_destination_object_is_read(self) -> None:
        """Some versions report the destination as an object, not a flat uuid."""

        instance = self.absent_endpoint_instance()
        instance.add_application(
            name="adapteng-baserow-adapter", destination={"uuid": "dst-nested", "name": "coolify"}
        )
        _, report = run_operation(driver.operate_reconcile, instance)
        self.assertIn("inherited dst-nested", report)

    def test_neighbours_on_different_destinations_stop_the_run(self) -> None:
        """Inheriting from a split environment would be a coin toss."""

        instance = self.absent_endpoint_instance()
        instance.add_application(name="one", destination_uuid="dst-a")
        instance.add_application(name="two", destination_uuid="dst-b")
        with self.assertRaises(driver.Abort) as raised:
            run_operation(driver.operate_reconcile, instance)
        self.assertIn("destinations", str(raised.exception))

    def test_a_destination_list_on_the_server_object_is_used(self) -> None:
        instance = self.absent_endpoint_instance()
        instance.servers[0]["destinations"] = [{"uuid": "dst-inline", "name": "coolify"}]
        code, _ = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)

    def test_an_empty_environment_still_creates_without_a_destination(self) -> None:
        """Coolify assigns the default destination when none is given."""

        instance = self.absent_endpoint_instance()
        code, _ = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(len(instance.applications), 1)

    def test_a_declared_destination_with_no_list_to_match_stops_the_run(self) -> None:
        """Silently ignoring a declared name would place the resource by guess."""

        instance = self.absent_endpoint_instance()
        instance.add_application(name="neighbour", destination_uuid="dst-live")
        spec = driver.load_spec(driver.spec_path(RESOURCE))
        spec["target"]["destination"] = "coolify"
        with self.assertRaises(driver.Abort) as raised:
            run_operation(driver.operate_reconcile, instance, spec=spec)
        self.assertIn("coolify", str(raised.exception))

    def test_the_endpoint_is_still_preferred_when_it_answers(self) -> None:
        instance = FakeInstance()
        instance.add_application(name="neighbour", destination_uuid="dst-neighbour")
        code, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertNotIn("inherited", report)

    def test_a_non_404_failure_is_not_routed_around(self) -> None:
        """allow_absent excuses absence, never a broken instance."""

        instance = FakeInstance()
        original = instance.request

        def failing(method, path, body=None, query=None):
            if path.endswith("/destinations"):
                return 500, {"message": "boom"}
            return original(method, path, body, query)

        instance.request = failing
        with self.assertRaises(driver.Abort) as raised:
            run_operation(driver.operate_reconcile, instance)
        self.assertIn("500", str(raised.exception))


class DeployTests(unittest.TestCase):
    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)
        self.ticks = iter(range(0, 100000, 5))

    def clock(self) -> float:
        return next(self.ticks)

    def deploy(self, instance, **kwargs):
        settings = {
            "poll_seconds": 1,
            "timeout_seconds": 600,
            "sleep": lambda _seconds: None,
            "clock": self.clock,
        }
        settings.update(kwargs)
        return run_operation(driver.operate_deploy, instance, **settings)

    def test_a_finished_deployment_succeeds(self) -> None:
        instance = FakeInstance(with_application=True)
        code, report = self.deploy(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT deploy ok", report)
        self.assertIn("dep-1", report)

    def test_a_successful_deploy_still_discloses_that_nothing_gated_it(self) -> None:
        """The most dangerous line this tool prints is a green deploy.

        Coolify finishing is not the container working, and with no probe it is
        not even the process answering itself. A reader who sees only 'ok' will
        assume a check passed, so the gate travels on the same line.
        """

        instance = FakeInstance(with_application=True)
        code, report = self.deploy(instance)
        self.assertEqual(code, driver.EXIT_OK)
        gate = driver.load_spec(driver.spec_path(RESOURCE))["health_check"]["container_gate"]
        self.assertIn(f"health_gate={gate}", report)
        if gate == "absent":
            self.assertIn("GATE ABSENT", report)

    def test_a_failed_deployment_fails_the_run(self) -> None:
        instance = FakeInstance(
            with_application=True, deployment_states=["queued", "in_progress", "failed"]
        )
        code, report = self.deploy(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT deploy failed", report)
        self.assertIn("dep-1", report)

    def test_a_failure_reports_the_reason_and_not_only_the_verdict(self) -> None:
        """state=failed alone is a verdict with the cause removed.

        The build log is the only place the cause exists, so a run that reports
        the state and stops has told the reader that something is wrong and
        withheld the one fact they need.
        """

        instance = FakeInstance(
            with_application=True, deployment_states=["queued", "failed"]
        )
        instance.deployment_logs = json.dumps(
            [
                {"output": "Step 4/9 : COPY requirements.lock ."},
                {"output": "ERROR: failed to solve: lstat requirements.lock: no such file"},
            ]
        )
        code, report = self.deploy(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("failed to solve", report)
        self.assertIn("Step 4/9", report)

    def test_a_log_that_quotes_an_owner_held_value_does_not_disclose_it(self) -> None:
        """A build log is untrusted text and may echo the environment it was given."""

        instance = FakeInstance(
            with_application=True, deployment_states=["queued", "failed"]
        )
        # Deliberately not written in the shape of a connection string. The
        # property under test is that a stored owner-held value is masked when
        # a log quotes it, and that holds for any opaque value - so there is no
        # need for a credential-shaped literal in this repository, and the
        # sensitive-reference gate is right to refuse one.
        stored = "value-that-must-not-appear-in-any-run-log"
        for entry in instance.environment_entries["app-1"]:
            if entry["key"] == "AI_GATEWAY_DATABASE_URL":
                entry["value"] = stored
        instance.deployment_logs = json.dumps(
            [{"output": f"connecting with {stored} failed"}]
        )
        _, report = self.deploy(instance)
        self.assertNotIn(stored, report)
        # Without this the assertion above would also pass if the log were
        # never printed at all, which is the failure mode being guarded against.
        self.assertIn("connecting with [redacted] failed", report)

    def test_a_committed_value_stays_legible_because_hiding_it_protects_nothing(self) -> None:
        """Masking everything would rebuild the opaque verdict this replaces.

        Values under ``configuration`` are committed in this repository in clear
        text. Redacting them costs the reader the cause and buys no secrecy.
        """

        instance = FakeInstance(
            with_application=True, deployment_states=["queued", "failed"]
        )
        instance.deployment_logs = json.dumps(
            [{"output": "AI_GATEWAY_MODEL=gemini-3.1-flash-lite was rejected"}]
        )
        _, report = self.deploy(instance)
        self.assertIn("gemini-3.1-flash-lite", report)

    def test_every_shape_this_instance_has_used_for_logs_is_read(self) -> None:
        """A log reader that raises fires exactly when something is already wrong."""

        cases = {
            "json string of entries": (json.dumps([{"output": "alpha"}]), ["alpha"]),
            "real list of entries": ([{"output": "alpha"}], ["alpha"]),
            "single entry object": ({"output": "alpha"}, ["alpha"]),
            "plain text": ("alpha\nbeta", ["alpha", "beta"]),
            "list of strings": (["alpha", "beta"], ["alpha", "beta"]),
            "absent": (None, []),
            "empty": ("", []),
        }
        for label, (raw, expected) in cases.items():
            with self.subTest(shape=label):
                self.assertEqual(driver.deployment_log_lines({"logs": raw}), expected)

    def test_an_unreadable_entry_is_shown_rather_than_dropped(self) -> None:
        """Dropping it would hide the one line that does not fit the expected shape."""

        lines = driver.deployment_log_lines({"logs": [{"message": "no output key"}]})
        self.assertEqual(len(lines), 1)
        self.assertIn("no output key", lines[0])

    def test_a_long_log_is_tailed_and_says_so(self) -> None:
        instance = FakeInstance(
            with_application=True, deployment_states=["queued", "failed"]
        )
        instance.deployment_logs = json.dumps(
            [{"output": f"line-{index}"} for index in range(200)]
        )
        _, report = self.deploy(instance)
        self.assertIn("last 60 of 200 lines", report)
        self.assertIn("line-199", report)
        self.assertNotIn("line-0\n", report)

    def test_a_deployment_that_never_settles_stops_the_run(self) -> None:
        instance = FakeInstance(with_application=True, deployment_states=["in_progress"])
        with self.assertRaises(driver.Abort) as raised:
            self.deploy(instance, timeout_seconds=20)
        self.assertIn("terminal state", str(raised.exception))

    def test_deploy_refuses_while_owner_held_values_are_missing(self) -> None:
        """Releasing a service that cannot start is a worse outcome than not releasing."""

        instance = FakeInstance(with_application=True)
        instance.environment_entries["app-1"] = [
            entry
            for entry in instance.environment_entries["app-1"]
            if entry["key"] != "AI_GATEWAY_BEARER_TOKENS"
        ]
        with self.assertRaises(driver.Abort) as raised:
            self.deploy(instance)
        self.assertIn("AI_GATEWAY_BEARER_TOKENS", str(raised.exception))
        self.assertEqual([call for call in instance.calls if call[1] == "/deploy"], [])

    def test_deploy_refuses_when_the_application_does_not_exist(self) -> None:
        instance = FakeInstance()
        with self.assertRaises(driver.Abort) as raised:
            self.deploy(instance)
        self.assertIn("run reconcile first", str(raised.exception))

    def test_an_unknown_state_is_polled_rather_than_assumed_good(self) -> None:
        self.assertEqual(driver.deployment_outcome("in_progress"), "pending")
        self.assertEqual(driver.deployment_outcome("something-new"), "pending")
        self.assertEqual(driver.deployment_outcome(None), "pending")
        self.assertEqual(driver.deployment_outcome("finished"), "succeeded")
        for state in ("failed", "cancelled-by-user", "error", "cancelled"):
            with self.subTest(state=state):
                self.assertEqual(driver.deployment_outcome(state), "failed")


class PollingTests(unittest.TestCase):
    """A deployment runs on the server whether or not we can ask about it.

    This distinction was learned the expensive way: a single transport blip
    mid-poll aborted a run whose deployment then completed successfully, and the
    report said the deployment failed. The observer's problem was published as
    the subject's.
    """

    class Answers:
        """Stands in for the client, returning one scripted answer per call.

        The call ceiling is deliberate. A poller that never gives up would
        otherwise hang the suite rather than fail it, and a hang is a much worse
        signal than a failure: it reports as a timed-out job with no named
        assertion, in a suite where every other verdict is precise.
        """

        CEILING = 50

        def __init__(self, script: list) -> None:
            self.script = list(script)
            self.calls = 0

        def request(self, method: str, path: str, **_kwargs):
            self.calls += 1
            if self.calls > self.CEILING:
                raise AssertionError(
                    f"polling did not stop after {self.CEILING} calls; the budget "
                    "is not bounding it"
                )
            answer = self.script[min(self.calls - 1, len(self.script) - 1)]
            if isinstance(answer, Exception):
                raise answer
            return 200, {"status": answer}

    def poll(self, script: list, timeout_seconds: int = 30):
        """Poll with time advanced by the sleeps, so no test waits on a clock."""

        elapsed = {"value": 0}

        def sleep(seconds):
            elapsed["value"] += seconds

        return driver.poll_deployment(
            self.Answers(script),
            "deployment-uuid",
            poll_seconds=10,
            timeout_seconds=timeout_seconds,
            sleep=sleep,
            clock=lambda: elapsed["value"],
        )

    def test_a_transport_failure_mid_poll_does_not_fail_the_deployment(self) -> None:
        unreachable = driver.Unreachable("the API at https://example is unreachable: URLError")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            outcome, state = self.poll(["in_progress", unreachable, "finished"])
        self.assertEqual(outcome, "succeeded")
        self.assertEqual(state, "finished")
        self.assertIn("not answered", buffer.getvalue())

    def test_every_blind_tick_is_reported_rather_than_only_the_first(self) -> None:
        """One line for a long blind stretch would read as a single blip."""

        unreachable = driver.Unreachable("unreachable: URLError")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.poll([unreachable, unreachable, unreachable, "finished"])
        self.assertEqual(buffer.getvalue().count("not answered"), 3)

    def test_patience_is_bounded_by_the_same_budget_and_not_extended(self) -> None:
        unreachable = driver.Unreachable("unreachable: URLError")
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(driver.Abort) as raised:
                self.poll([unreachable], timeout_seconds=30)
        self.assertIn("could not be read", str(raised.exception))

    def test_an_unreadable_deployment_is_reported_unknown_rather_than_failed(self) -> None:
        """The truthful verdict. 'Failed' would assert something never observed."""

        unreachable = driver.Unreachable("unreachable: URLError")
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(driver.Abort) as raised:
                self.poll([unreachable], timeout_seconds=10)
        message = str(raised.exception)
        self.assertIn("unknown rather than failed", message)
        self.assertIn("check the application state", message)

    def test_only_transport_failures_are_tolerated_not_every_abort(self) -> None:
        """Catching Abort here would also swallow a refused write or a bad body."""

        with redirect_stdout(io.StringIO()):
            with self.assertRaises(driver.Abort) as raised:
                self.poll([driver.Abort("the API returned a body that is not JSON")])
        self.assertIn("not JSON", str(raised.exception))

    def test_a_transport_failure_outside_polling_still_stops_the_run(self) -> None:
        """Unreachable stays an Abort, so nothing else changed behaviour."""

        self.assertTrue(issubclass(driver.Unreachable, driver.Abort))
        self.assertEqual(driver.Unreachable("x").code, driver.EXIT_FAILED)

    def test_a_deployment_that_never_finishes_still_times_out(self) -> None:
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(driver.Abort) as raised:
                self.poll(["in_progress"], timeout_seconds=30)
        self.assertIn("still in_progress", str(raised.exception))


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def test_status_writes_nothing_and_reports_the_state(self) -> None:
        instance = FakeInstance(with_application=True)
        code, report = run_operation(driver.operate_status, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(instance.writes(), [])
        self.assertIn("running:healthy", report)
        self.assertIn("deployment=none", report)

    def test_status_on_an_absent_application_is_a_normal_answer(self) -> None:
        instance = FakeInstance()
        code, report = run_operation(driver.operate_status, instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("application=absent", report)


class EntryPointTests(unittest.TestCase):
    def setUp(self) -> None:
        driver.reset_redactions()
        self.addCleanup(driver.reset_redactions)

    def environment(self, **overrides) -> dict:
        base = {
            driver.OPERATION_VARIABLE: "inspect",
            driver.SERVICE_VARIABLE: RESOURCE,
            driver.BASE_URL_VARIABLE: "https://coolify.example.com",
            driver.CREDENTIAL_VARIABLE: ACCESS_VALUE,
        }
        base.update(overrides)
        return {key: value for key, value in base.items() if value is not None}

    def assert_refused(self, environ) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer), self.assertRaises(driver.Abort) as raised:
            driver.run(environ)
        self.assertEqual(raised.exception.code, driver.EXIT_MISCONFIGURED)

    def test_an_empty_credential_stops_before_any_call(self) -> None:
        self.assert_refused(self.environment(**{driver.CREDENTIAL_VARIABLE: None}))

    def test_an_empty_base_address_stops_before_any_call(self) -> None:
        self.assert_refused(self.environment(**{driver.BASE_URL_VARIABLE: None}))

    def test_a_cleartext_base_address_is_refused(self) -> None:
        """Sending the access value over http would expose it on the wire."""

        self.assert_refused(self.environment(**{driver.BASE_URL_VARIABLE: "http://coolify.example.com"}))

    def test_an_unknown_operation_is_refused(self) -> None:
        self.assert_refused(self.environment(**{driver.OPERATION_VARIABLE: "destroy"}))

    def test_every_offered_operation_is_implemented(self) -> None:
        """The dispatch menu and the implemented set must not drift apart.

        A menu entry with no implementation is a dead button; an implementation
        with no menu entry is unreachable. Either way the list below is also what
        stops a destructive operation being offered by a later edit.
        """

        self.assertEqual(
            set(driver.OPERATIONS),
            {
                "inspect",
                "reconcile",
                "deploy",
                "status",
                "verify",
                "peer-verify",
                "peer-tools",
                "peer-diagnose",
                "diagnose",
            },
        )
        workflow = (
            Path(driver.ROOT) / ".github" / "workflows" / "coolify-deploy.yml"
        ).read_text(encoding="utf-8")
        block = re.search(r"\n\s+options:\n((?:\s+- \S+\n)+)", workflow)
        self.assertIsNotNone(block)
        self.assertEqual(re.findall(r"- (\S+)", block.group(1)), list(driver.OPERATIONS))

    def test_a_bad_poll_setting_is_refused(self) -> None:
        for value in ("0", "-5", "soon"):
            with self.subTest(value=value), self.assertRaises(driver.Abort) as raised:
                driver.positive_integer({driver.POLL_VARIABLE: value}, driver.POLL_VARIABLE, 10)
            self.assertEqual(raised.exception.code, driver.EXIT_MISCONFIGURED)

    def test_an_absent_poll_setting_uses_the_default(self) -> None:
        self.assertEqual(driver.positive_integer({}, driver.POLL_VARIABLE, 10), 10)


class ReadinessInstance(FakeInstance):
    """A Coolify instance that also answers the scheduled-task endpoints.

    It models the two facts that shape the driver. There is no execute route,
    matching the deployed instance, so a POST to it is refused; and the
    scheduler only runs enabled tasks, so an execution appears only while the
    task is armed. A fake that produced an execution regardless would let a
    driver that never armed anything pass.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(with_application=True, **kwargs)
        self.tasks: list[dict] = []
        self.executions: list[dict] = []
        self.next_execution: dict | None = {
            "status": "success",
            "message": "ADAPTENG_READY 200\n",
        }
        # A real execution does not appear the instant the task is armed, and
        # its first observable state is not its last. Both delays are modelled
        # so a driver that reads once and believes the answer is caught.
        self.reveal_after_polls = 0
        self.running_for_polls = 0
        # -1 refuses every disarm; a positive count refuses that many and then
        # lets one through, which is the transient failure the retry exists for.
        self.refuse_disarm_times = 0
        self.refuse_arm = False
        self.execution_polls = 0
        # The live instance returns every execution with id null and a distinct
        # created_at. Minting rising ids here is exactly what let a driver that
        # keyed novelty on id pass this suite and then fail to see four real
        # executions against the real thing. The id-bearing variant stays
        # available so both shapes are covered.
        self.mint_execution_ids = False
        self.execution_clock = 0
        # Which task the modelled scheduler will run. The peer probe uses a
        # different name in the same machinery, and a fake hard-wired to the
        # readiness name would report "no execution" for a correct peer run.
        self.task_name = driver.READINESS_TASK_NAME
        # Extra rows revealed in the same poll as next_execution.
        self.also_reveal: list[dict] = []
        # None models the 400 "Application is not running." the endpoint returns
        # when no container is up -- the same condition the scheduler skips on.
        self.logs: str | None = "listening on 8081\n"

    def armed_task(self) -> dict | None:
        """The task the scheduler would actually run.

        Coolify dispatches a task only when it is enabled *and* its cron
        matches the current minute. A fake that checked only the flag would let
        a driver that armed the flag but left the leap-day schedule pass, and
        that driver would hang against the real instance.
        """

        for task in self.tasks:
            if task.get("name") != self.task_name:
                continue
            if task.get("enabled") and task.get("frequency") == driver.READINESS_ARMED_FREQUENCY:
                return task
        return None

    def _get(self, path, body, query):
        if re.fullmatch(r"/applications/([^/]+)/logs", path):
            if self.logs is None:
                return 400, {"message": "Application is not running."}
            return 200, {"logs": self.logs}
        match = re.fullmatch(r"/applications/([^/]+)/scheduled-tasks", path)
        if match:
            return 200, copy.deepcopy(self.tasks)
        match = re.fullmatch(
            r"/applications/([^/]+)/scheduled-tasks/([^/]+)/executions", path
        )
        if match:
            self.execution_polls += 1
            if (
                self.armed_task() is not None
                and self.next_execution is not None
                and self.execution_polls > self.reveal_after_polls
            ):
                # A slow poll can reveal more than one run at once: the
                # scheduler fires once a minute and this polls faster.
                for pending in [self.next_execution, *self.also_reveal]:
                    entry = dict(pending)
                    self.execution_clock += 1
                    entry.setdefault(
                        "created_at",
                        f"2026-08-11T21:{self.execution_clock:02d}:00.000000Z",
                    )
                    if self.mint_execution_ids:
                        numbers = [
                            int(item["id"])
                            for item in self.executions
                            if item.get("id")
                        ]
                        entry["id"] = (max(numbers) if numbers else 0) + 1
                    else:
                        entry["id"] = None
                    self.executions.append(entry)
                self.also_reveal = []
                self.next_execution = None
            rows = copy.deepcopy(self.executions)
            if rows and self.running_for_polls > 0:
                self.running_for_polls -= 1
                rows[-1] = dict(rows[-1], status="running", message="")
            # The live instance returns executions newest first. A fake that
            # returned them oldest first would let a driver that reads rows[-1]
            # pass here and read the oldest row against the real thing.
            rows.reverse()
            return 200, rows
        return super()._get(path, body, query)

    def _post(self, path, body, query):
        match = re.fullmatch(r"/applications/([^/]+)/scheduled-tasks", path)
        if match:
            record = dict(body)
            record["uuid"] = f"task-{len(self.tasks) + 1}"
            self.tasks.append(record)
            return 201, copy.deepcopy(record)
        # The deployed instance has no execute route and answers the generic
        # 404 there. Modelling that is what stops the driver quietly relying
        # on an endpoint the real instance does not have.
        if re.fullmatch(r"/applications/([^/]+)/scheduled-tasks/([^/]+)/execute", path):
            return 404, {"message": "Not found.", "docs": "https://coolify.io/docs"}
        return super()._post(path, body, query)

    def _patch(self, path, body, query):
        match = re.fullmatch(r"/applications/([^/]+)/scheduled-tasks/([^/]+)", path)
        if match:
            for task in self.tasks:
                if task.get("uuid") != match.group(2):
                    continue
                arming = bool(body.get("enabled"))
                # A write that returns 200 and changes nothing is the failure
                # shape worth modelling: it is indistinguishable from success
                # to anything that does not read back.
                if arming and self.refuse_arm:
                    return 200, copy.deepcopy(task)
                if not arming and self.refuse_disarm_times != 0:
                    if self.refuse_disarm_times > 0:
                        self.refuse_disarm_times -= 1
                    return 200, copy.deepcopy(task)
                task.update(body)
                return 200, copy.deepcopy(task)
            return 404, {"message": "not found"}
        return super()._patch(path, body, query)


class VerifyTests(unittest.TestCase):
    """The in-container readiness probe.

    Every network vantage point was ruled out by measurement: the readiness
    runner sits on a Docker network holding the managed database but none of
    the applications. Coolify's scheduled task reaches inside the container
    without SSH, a Docker socket, or a network change, so these tests cover the
    one instrument that can answer the question.
    """

    def real_spec(self):
        return driver.load_spec(driver.spec_path(RESOURCE))

    def run_verify(self, instance, spec=None):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_verify(
                instance, spec or self.real_spec(), sleep=lambda _seconds: None
            )
        return code, buffer.getvalue()

    def test_a_two_hundred_from_inside_the_container_is_the_database_proof(self) -> None:
        instance = ReadinessInstance()
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT verify ok ready=yes answer=200", output)

    def test_the_task_is_returned_to_rest_after_a_successful_probe(self) -> None:
        """Arming is temporary, and the resting state is both guards restored."""

        instance = ReadinessInstance()
        code, _ = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(len(instance.tasks), 1)
        at_rest = instance.tasks[0]
        self.assertIs(at_rest["enabled"], False)
        self.assertEqual(at_rest["frequency"], driver.READINESS_TASK_FREQUENCY)

    def test_the_task_is_returned_to_rest_even_when_the_probe_fails(self) -> None:
        """A failed probe must not leave a job firing every minute."""

        instance = ReadinessInstance()
        instance.next_execution = {"status": "success", "message": "ADAPTENG_READY 503"}
        code, _ = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIs(instance.tasks[0]["enabled"], False)
        self.assertEqual(
            instance.tasks[0]["frequency"], driver.READINESS_TASK_FREQUENCY
        )

    def test_a_task_that_will_not_disarm_is_reported_loudly_and_fails(self) -> None:
        """The one outcome that leaves something running must be unmissable.

        Reporting ready=yes here would be the worst case: a true readiness
        answer paid for with a job nobody knows is running.
        """

        instance = ReadinessInstance()
        instance.refuse_disarm_times = -1
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("COULD NOT DISARM", output)
        self.assertIn("ready=undetermined reason=task_left_armed", output)
        self.assertNotIn("RESULT verify ok", output)

    def test_a_disarm_that_fails_once_is_retried(self) -> None:
        """Leaving a job armed because one write was dropped is not acceptable.

        The retry is the difference between a transient API failure costing
        nothing and it costing a probe that fires every minute until someone
        notices.
        """

        instance = ReadinessInstance()
        instance.refuse_disarm_times = 2
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("did not take effect", output)
        self.assertIn("returned to rest", output)
        self.assertIs(instance.tasks[0]["enabled"], False)

    def test_an_arming_write_that_does_not_take_effect_stops_the_run(self) -> None:
        """Waiting for an execution that cannot happen wastes the budget and
        then reports undetermined, which reads like a gateway problem. It is
        not one, so it is refused up front instead."""

        instance = ReadinessInstance()
        instance.refuse_arm = True
        with self.assertRaises(driver.Abort) as raised:
            self.run_verify(instance)
        self.assertIn("did not arm", str(raised.exception))

    def test_a_disabled_task_on_the_armed_schedule_is_not_at_rest(self) -> None:
        """Half-disarmed is the state a partial failure leaves behind.

        Rest is both guards restored. Treating the flag alone as rest would
        let the leap-day schedule quietly stay off, removing the second line of
        defence for every later run without any visible symptom.
        """

        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": driver.readiness_command(self.real_spec()),
                "enabled": False,
                "frequency": driver.READINESS_ARMED_FREQUENCY,
            }
        )
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("disarmed and corrected", output)
        self.assertEqual(
            instance.tasks[0]["frequency"], driver.READINESS_TASK_FREQUENCY
        )

    def test_a_task_left_armed_by_an_earlier_run_is_disarmed_first(self) -> None:
        """The next run heals what an interrupted one left behind."""

        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": driver.readiness_command(self.real_spec()),
                "enabled": True,
                "frequency": driver.READINESS_ARMED_FREQUENCY,
            }
        )
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("disarmed and corrected", output)
        self.assertIs(instance.tasks[0]["enabled"], False)

    def test_the_execute_endpoint_is_never_called(self) -> None:
        """It does not exist on the deployed instance.

        The route answers the generic 404 while its sibling /executions answers
        401, which is how the absence was established without presenting a
        credential. A driver that called it would fail against production while
        passing against a permissive fake.
        """

        instance = ReadinessInstance()
        self.run_verify(instance)
        self.assertEqual([p for _, p in instance.calls if p.endswith("/execute")], [])

    def test_a_five_oh_three_is_reported_as_the_gateways_own_verdict(self) -> None:
        instance = ReadinessInstance()
        instance.next_execution = {"status": "success", "message": "ADAPTENG_READY 503"}
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT verify failed ready=no answer=503", output)
        self.assertIn("gateway's own verdict", output)

    def test_a_transport_failure_inside_the_container_is_reported_by_name(self) -> None:
        instance = ReadinessInstance()
        instance.next_execution = {
            "status": "success",
            "message": "ADAPTENG_READY URLError",
        }
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT verify failed ready=no answer=URLError", output)

    def test_output_without_the_marker_is_undetermined_not_unready(self) -> None:
        """A missing answer is not a negative answer.

        This is the distinction the whole workstream exists to keep: an absent
        verdict must not be reported as a refusal.
        """

        instance = ReadinessInstance()
        instance.next_execution = {
            "status": "failed",
            "message": "sh: 1: python: not found",
        }
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("ready=undetermined reason=no_marker", output)
        self.assertNotIn("ready=no ", output)

    def test_an_execution_that_never_appears_is_undetermined(self) -> None:
        instance = ReadinessInstance()
        instance.next_execution = None
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("ready=undetermined reason=no_execution", output)

    def test_a_stale_execution_is_not_mistaken_for_this_run(self) -> None:
        """The previous run's answer must not be read as this run's answer."""

        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": driver.readiness_command(self.real_spec()),
                "enabled": False,
            }
        )
        instance.executions.append(
            {
                "id": None,
                "status": "success",
                "created_at": "2026-08-11T20:00:00.000000Z",
                "message": "ADAPTENG_READY 200",
            }
        )
        instance.next_execution = None
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("ready=undetermined reason=no_execution", output)

    def test_a_stale_execution_does_not_end_the_wait_for_a_slow_one(self) -> None:
        """The previous run's row must not be read as this run's answer.

        This is the sharper form of the stale-row case: a driver that stops at
        the newest row it can see will stop at the old one, before the new one
        has appeared, and report an absent answer for a probe that did in fact
        answer 200.
        """

        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": driver.readiness_command(self.real_spec()),
                "enabled": False,
            }
        )
        instance.executions.append(
            {
                "id": None,
                "status": "success",
                "created_at": "2026-08-11T20:00:00.000000Z",
                "message": "ADAPTENG_READY 503",
            }
        )
        instance.reveal_after_polls = 4
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT verify ok ready=yes answer=200", output)

    def test_a_running_execution_is_waited_out_rather_than_read(self) -> None:
        """An execution that has started has no verdict yet.

        Reading its empty output would report a missing marker, which is the
        undetermined verdict, for a probe that simply had not finished.
        """

        instance = ReadinessInstance()
        instance.running_for_polls = 3
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT verify ok ready=yes answer=200", output)

    def test_an_execution_without_an_id_is_still_seen(self) -> None:
        """The shape the live instance actually returns.

        Every execution came back with id null. Keying novelty on a rising id
        made all of them compare equal to each other and to the baseline, so
        four probe runs that each answered 200 were reported as no execution at
        all -- this operation committing the exact conflation of "not ready"
        with "could not tell" that it exists to prevent.
        """

        instance = ReadinessInstance()
        instance.mint_execution_ids = False
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT verify ok ready=yes answer=200", output)
        self.assertTrue(
            all(row["id"] is None for row in instance.executions),
            "the fake stopped modelling the live shape",
        )

    def test_an_instance_that_does_number_its_executions_also_works(self) -> None:
        """Fixing the null-id case must not break the case that has ids."""

        instance = ReadinessInstance()
        instance.mint_execution_ids = True
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT verify ok ready=yes answer=200", output)

    def test_a_repeated_answer_is_told_apart_from_the_one_before_it(self) -> None:
        """Consecutive runs produce identical messages, so content cannot identify.

        The live probe answers ADAPTENG_READY 200 every single time. With no id
        and an identical message, created_at is the only thing separating this
        run's answer from the last one's, and the count is the only thing left
        if created_at ties.
        """

        instance = ReadinessInstance()
        instance.executions.append(
            {
                "id": None,
                "status": "success",
                "created_at": "2026-08-11T20:00:00.000000Z",
                "message": "ADAPTENG_READY 200",
            }
        )
        instance.reveal_after_polls = 3
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT verify ok ready=yes answer=200", output)
        self.assertEqual(len(instance.executions), 2)

    def test_an_execution_that_nothing_identifies_is_not_guessed_at(self) -> None:
        """A row that cannot be told apart is not an answer.

        With neither id nor created_at, the count still proves something ran,
        but nothing says which row it is. Reporting the newest-looking row
        would present a guess as a measurement -- and it would report the
        stale row, since order is all that is left to sort by.
        """

        instance = ReadinessInstance()
        instance.executions.append(
            {"id": None, "created_at": None, "status": "success", "message": "old"}
        )
        instance.next_execution = {
            "id": None,
            "created_at": None,
            "status": "success",
            "message": "ADAPTENG_READY 200",
        }
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn(
            "ready=undetermined reason=unidentifiable_execution", output
        )
        self.assertNotIn("ready=no ", output)

    def test_the_newest_of_two_simultaneous_executions_is_the_answer(self) -> None:
        """Two runs can appear between polls, and only the later one is current.

        The scheduler fires once a minute while this polls every ten seconds,
        so one slow poll can reveal two new rows at once. Both are new, so both
        pass the novelty filter and only created_at separates them.

        Both orderings are exercised because position must not be standing in
        for recency. The live instance happens to return rows newest first, but
        nothing in the contract says so, and a driver that reads the first or
        the last row would pass under one ordering and read the wrong answer
        under the other.
        """

        older = {
            "status": "success",
            "created_at": "2026-08-11T21:29:00.000000Z",
            "message": "ADAPTENG_READY 503",
        }
        newer = {
            "status": "success",
            "created_at": "2026-08-11T21:30:00.000000Z",
            "message": "ADAPTENG_READY 200",
        }
        for label, batch in (
            ("older first", [older, newer]),
            ("newer first", [newer, older]),
        ):
            with self.subTest(order=label):
                instance = ReadinessInstance()
                instance.next_execution = dict(batch[0])
                instance.also_reveal = [dict(batch[1])]
                code, output = self.run_verify(instance)
                self.assertEqual(code, driver.EXIT_OK)
                self.assertIn("RESULT verify ok ready=yes answer=200", output)

    def test_the_wait_is_bounded_and_ends_in_undetermined(self) -> None:
        """A probe that never finishes must not poll forever, or lie."""

        instance = ReadinessInstance()
        instance.running_for_polls = 10_000
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("ready=undetermined", output)
        self.assertLessEqual(
            instance.execution_polls, driver.READINESS_EXECUTION_ATTEMPTS + 1
        )

    def test_the_task_is_created_disabled_and_can_never_fire_on_its_own(self) -> None:
        """Disabled is the guard; the rare date is only a second line.

        Coolify's scheduler selects tasks with where('enabled', true), so a
        disabled task is never dispatched whatever its frequency says. The
        frequency still has to be a valid expression, because the API builds a
        next run date to validate it and refuses one that has none -- which is
        how an unmatchable date like February 31st is rejected, as the live
        instance demonstrated.
        """

        instance = ReadinessInstance()
        self.run_verify(instance)
        self.assertEqual(len(instance.tasks), 1)
        created = instance.tasks[0]
        self.assertIs(created["enabled"], False)
        self.assertEqual(created["frequency"], driver.READINESS_TASK_FREQUENCY)
        # February 29th: valid, and only in leap years.
        self.assertEqual(driver.READINESS_TASK_FREQUENCY.split()[2:4], ["29", "2"])

    def test_the_frequency_is_a_date_that_can_actually_occur(self) -> None:
        """The live instance rejected February 31st, and this is why.

        Coolify validates a cron expression by building its next run date, so
        an expression naming a date that never occurs is refused outright and
        the probe cannot be created at all. Field-range checking would not have
        caught it: 31 is a legal day and 2 is a legal month. Only asking
        whether the pair can ever coincide catches it.
        """

        fields = driver.READINESS_TASK_FREQUENCY.split()
        self.assertEqual(len(fields), 5)
        day, month = int(fields[2]), int(fields[3])
        start = datetime.date.today()
        horizon = start.replace(year=start.year + 8)
        cursor, found = start, False
        while cursor < horizon:
            if cursor.day == day and cursor.month == month:
                found = True
                break
            cursor += datetime.timedelta(days=1)
        self.assertTrue(
            found,
            f"{driver.READINESS_TASK_FREQUENCY} names a date that never occurs; "
            "Coolify will refuse it and the probe will never be created",
        )

    def test_the_command_comes_from_the_spec_and_carries_no_single_quote(self) -> None:
        """Coolify wraps the command in sh -c '...' and escapes single quotes.

        A command containing none cannot be broken by that wrapping, so the
        absence is a property worth asserting rather than a coincidence. The
        same goes for $ and backtick, which sh would expand inside the double
        quotes the command does use, and for the newline: the first version of
        this command spanned several lines and the API answered HTTP 500 when
        asked to store it.
        """

        spec = self.real_spec()
        spec["network"]["internal_port"] = 9099
        command = driver.readiness_command(spec)
        self.assertIn("http://127.0.0.1:9099/ready", command)
        self.assertNotIn("'", command)
        self.assertNotIn("$", command)
        self.assertNotIn("`", command)
        self.assertNotIn("\n", command)
        self.assertEqual(command.count(chr(34)), 2)
        self.assertIn(driver.READINESS_MARKER, command)

    def test_the_probe_reports_a_status_rather_than_raising_on_it(self) -> None:
        """503 is the interesting answer, and urlopen raises on it by default.

        /ready returns 503 when it cannot reach the database, which is the
        condition this operation exists to detect. A probe that raised there
        would report undetermined for the one case it was built to see. This
        runs the exact command against a real server to check it does not.
        """

        import http.server
        import subprocess
        import sys as sys_module
        import threading

        answers = {}
        for status in (200, 503, 404):
            class Handler(http.server.BaseHTTPRequestHandler):
                code = status

                def do_GET(self):
                    self.send_response(self.code)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                def log_message(self, *_args):
                    pass

            server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                spec = self.real_spec()
                spec["network"]["internal_port"] = server.server_port
                command = driver.readiness_command(spec)
                # Recover the argument vector the container would receive.
                code_start = command.index('"') + 1
                code_end = command.index('"', code_start)
                argv = [
                    sys_module.executable,
                    "-c",
                    command[code_start:code_end],
                    *command[code_end + 1 :].split(),
                ]
                result = subprocess.run(
                    argv, capture_output=True, text=True, timeout=30
                )
            finally:
                server.shutdown()
            answers[status] = result.stdout.strip()

        for status, line in answers.items():
            self.assertEqual(line, f"{driver.READINESS_MARKER} {status}")
            self.assertEqual(driver.read_marker(line), str(status))

    def test_the_probe_targets_ready_even_when_the_gate_polls_health(self) -> None:
        """The probe must not inherit the container gate's path.

        health_check.path is /health today, and /health touches nothing. Only
        /ready opens a database connection, which is the whole point of this
        operation, so a spec change must not be able to silently retarget it
        at an endpoint that proves nothing.
        """

        spec = self.real_spec()
        spec["health_check"]["path"] = "/health"
        self.assertIn("/ready", driver.readiness_command(spec))
        self.assertNotIn("/health", driver.readiness_command(spec))

    def test_a_drifted_command_is_rewritten_and_read_back(self) -> None:
        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": "echo something else",
                "enabled": True,
            }
        )
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("disarmed and corrected", output)
        self.assertEqual(len(instance.tasks), 1)
        self.assertEqual(
            instance.tasks[0]["command"], driver.readiness_command(self.real_spec())
        )

    def test_a_write_that_does_not_hold_stops_the_run(self) -> None:
        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": "echo something else",
                "enabled": True,
                "frequency": driver.READINESS_ARMED_FREQUENCY,
            }
        )

        def refuse(path, body, query):
            return 200, {}

        instance._patch = refuse
        with self.assertRaises(driver.Abort) as raised:
            self.run_verify(instance)
        self.assertIn("did not come to rest", str(raised.exception))

    def test_a_correct_task_is_reused_rather_than_duplicated(self) -> None:
        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": driver.readiness_command(self.real_spec()),
                "enabled": False,
                "frequency": driver.READINESS_TASK_FREQUENCY,
            }
        )
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("already at rest", output)
        self.assertEqual(len(instance.tasks), 1)

    def test_two_tasks_of_the_same_name_are_not_resolved_by_guessing(self) -> None:
        instance = ReadinessInstance()
        for index in (1, 2):
            instance.tasks.append(
                {
                    "uuid": f"task-{index}",
                    "name": driver.READINESS_TASK_NAME,
                    "command": "echo",
                    "enabled": False,
                }
            )
        with self.assertRaises(driver.Abort) as raised:
            self.run_verify(instance)
        self.assertIn("ambiguous", str(raised.exception))

    def test_an_absent_application_is_a_failure_not_a_readiness_verdict(self) -> None:
        instance = ReadinessInstance()
        instance.applications = []
        code, output = self.run_verify(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT verify failed application=absent", output)

    def test_no_removal_is_ever_attempted(self) -> None:
        instance = ReadinessInstance()
        self.run_verify(instance)
        self.assertEqual([m for m, _ in instance.calls if m == "DELETE"], [])

    def test_the_marker_reader_refuses_to_invent_a_verdict(self) -> None:
        self.assertEqual(driver.read_marker("ADAPTENG_READY 200"), "200")
        self.assertEqual(driver.read_marker("noise\nADAPTENG_READY 503\nmore"), "503")
        self.assertIsNone(driver.read_marker("ADAPTENG_READY"))
        self.assertIsNone(driver.read_marker(""))
        self.assertIsNone(driver.read_marker(None))
        self.assertIsNone(driver.read_marker({"status": 200}))


class PeerInstance(ReadinessInstance):
    """A Coolify instance holding the service *and* a peer to probe from.

    The peer is what makes this fixture different from ReadinessInstance in the
    way that matters: the probe must be written to the peer's application, not
    the service's. A single-application fake could not tell a driver that
    probes the right container from one that probes itself, which is precisely
    the confusion this operation exists to remove.
    """

    def __init__(self, *, with_peer: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.task_name = driver.PEER_TASK_NAME
        self.next_execution = {
            "status": "success",
            "message": "ADAPTENG_PEER 200 200\n",
        }
        if with_peer:
            self.add_application(name=PEER_NAME, status="running:unknown")


class PeerVerifyTests(unittest.TestCase):
    """Reachability asked from a different container.

    verify answers "is this process up", from inside the gateway against
    127.0.0.1. That answer is identical whether or not the container was ever
    attached to the shared Docker network, so it cannot close the gap between
    "the container is healthy" and "a caller can reach it". Only a probe run
    from another container can, and these tests cover that instrument.
    """

    def real_spec(self):
        return driver.load_spec(driver.spec_path(RESOURCE))

    def run_peer(self, instance, spec=None):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_peer_verify(
                instance, spec or self.real_spec(), sleep=lambda _seconds: None
            )
        return code, buffer.getvalue()

    def test_dns_tcp_and_both_endpoints_together_are_the_reachability_proof(self) -> None:
        instance = PeerInstance()
        code, output = self.run_peer(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn(
            "RESULT peer-verify ok reachable=yes health=200 ready=200",
            output,
        )

    def test_the_probe_is_written_to_the_peer_not_to_the_service(self) -> None:
        """The whole point is the vantage point.

        A probe written to the gateway's own container would pass this suite on
        every other assertion and answer the loopback question again.
        """

        instance = PeerInstance()
        code, _ = self.run_peer(instance)
        self.assertEqual(code, driver.EXIT_OK)
        peer = next(item for item in instance.applications if item["name"] == PEER_NAME)
        service = next(
            item for item in instance.applications if item["name"] == RESOURCE
        )
        written = {path for _method, path in instance.writes() if "scheduled-task" in path}
        self.assertTrue(written)
        self.assertTrue(all(peer["uuid"] in path for path in written))
        self.assertFalse(any(service["uuid"] in path for path in written))

    def test_the_probe_runs_an_interpreter_the_peer_was_measured_to_have(self) -> None:
        """The first armed run died on `sh: 1: python: not found`.

        peer-tools then reported python3, curl and perl present on the peer and
        bare python absent. This pins the probe to that reading: the
        interpreter must be one the census actually asks about, and it must not
        be the name that was measured missing. Deriving the guard from
        PEER_TOOLS_CANDIDATES rather than restating "python3" keeps the two
        halves of the fix attached -- a probe fitted to a tool the census never
        looks for would be a guess again, and nothing else here would say so.
        """

        command = driver.peer_command(self.real_spec())
        self.assertIn(driver.PEER_INTERPRETER, driver.PEER_TOOLS_CANDIDATES)
        self.assertTrue(command.startswith(f"{driver.PEER_INTERPRETER} -c "))
        self.assertNotRegex(command, r"(?<![a-z0-9])python(?![0-9])")

    def test_the_probe_stays_under_the_measured_acceptance_limit(self) -> None:
        """The first live run was refused with HTTP 500 for being too long.

        peer-diagnose located the boundary by measurement: 245 characters
        accepted, 300 refused, with the refused rung differing from an
        accepted one by padding alone. That number is not derivable from
        anything in the code, and nothing else in this suite would notice the
        command crossing it again -- the failure appears only against the live
        API, several minutes into a run, as a 500 with no field-level reason.
        """

        command = driver.peer_command(self.real_spec())
        self.assertLessEqual(len(command), driver.PEER_COMMAND_LIMIT)

    def test_the_probe_reports_a_non_two_hundred_instead_of_crashing(self) -> None:
        """"reachable but not ready" has to survive as an answer.

        urllib raises on a non-2xx, which would turn a 503 into a missing
        marker and therefore into "undetermined" -- losing the distinction
        between a service that cannot be reached and one that answers badly.
        http.client returns the status, so the distinction survives.
        """

        command = driver.peer_command(self.real_spec())
        self.assertIn("http.client", command)
        self.assertNotIn("HTTPErrorProcessor", command)

    def test_the_probe_addresses_the_service_by_name_on_its_declared_port(self) -> None:
        """The command must come from the spec, so it cannot drift from it."""

        command = driver.peer_command(self.real_spec())
        self.assertIn(f" {RESOURCE} 8081 ", command)
        self.assertIn("/health", command)
        self.assertIn("/ready", command)
        self.assertNotIn("127.0.0.1", command)
        self.assertNotIn("localhost", command)

    def test_the_command_survives_coolifys_shell_wrapper(self) -> None:
        """Coolify runs this as docker exec <c> sh -c '<command>'.

        A single quote would terminate the wrapper's own quoting, and a dollar
        or a backtick would be expanded by that shell before the probe ever
        ran. A multi-line command made the API answer 500. Each of those was
        found the expensive way; this keeps them found.
        """

        command = driver.peer_command(self.real_spec())
        self.assertNotIn("'", command)
        self.assertNotIn("$", command)
        self.assertNotIn("`", command)
        self.assertNotIn("\n", command)
        self.assertEqual(command.count(chr(34)), 2)

    def test_the_probe_presents_no_credential_and_calls_no_model(self) -> None:
        """A reachability check that spent a model call would be a bug.

        /health and /ready both answer before the Authorization header is read,
        so requesting them cannot reach inference. Naming an inference path
        here would silently make a probe billable.
        """

        command = driver.peer_command(self.real_spec())
        self.assertNotIn("Authorization", command)
        self.assertNotIn("/v1", command)
        self.assertNotIn("generate", command)

    def test_an_absent_peer_is_undetermined_not_unreachable(self) -> None:
        """No vantage point is not the same as no route.

        Reporting unreachable here would be a false negative about the
        gateway, produced entirely by a missing probe container.
        """

        instance = PeerInstance(with_peer=False)
        code, output = self.run_peer(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("reachable=undetermined reason=peer_absent", output)
        self.assertNotIn("reachable=no", output)

    def test_a_missing_marker_is_undetermined_and_prints_what_came_back(self) -> None:
        """A peer image without python looks exactly like this.

        That is an unusable probe, not an unreachable service, so the captured
        output is printed: it is the only thing that tells the two apart.
        """

        instance = PeerInstance()
        instance.next_execution = {
            "status": "failed",
            "message": "sh: python: not found\n",
        }
        code, output = self.run_peer(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("reachable=undetermined reason=no_marker", output)
        self.assertIn("python: not found", output)
        self.assertNotIn("reachable=no", output)

    def test_a_resolvable_name_that_answers_five_hundred_is_reachable_no(self) -> None:
        """Here the probe did run and did get an answer, so it is a real no."""

        instance = PeerInstance()
        instance.next_execution = {
            "status": "success",
            "message": "ADAPTENG_PEER 200 503\n",
        }
        code, output = self.run_peer(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("reachable=no", output)
        self.assertNotIn("undetermined", output)

    def test_the_peer_task_is_returned_to_rest_on_success_and_on_failure(self) -> None:
        for message, expected in (
            ("ADAPTENG_PEER 200 200\n", driver.EXIT_OK),
            ("ADAPTENG_PEER 200 503\n", driver.EXIT_FAILED),
        ):
            with self.subTest(message=message):
                instance = PeerInstance()
                instance.next_execution = {"status": "success", "message": message}
                code, _ = self.run_peer(instance)
                self.assertEqual(code, expected)
                task = next(
                    item
                    for item in instance.tasks
                    if item["name"] == driver.PEER_TASK_NAME
                )
                self.assertIs(task["enabled"], False)
                self.assertEqual(task["frequency"], driver.READINESS_TASK_FREQUENCY)

    def test_a_peer_task_that_will_not_disarm_fails_and_says_where(self) -> None:
        instance = PeerInstance()
        instance.refuse_disarm_times = -1
        code, output = self.run_peer(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("COULD NOT DISARM", output)
        self.assertIn("reachable=undetermined reason=task_left_armed", output)
        self.assertNotIn("RESULT peer-verify ok", output)

    def test_the_peer_probe_never_touches_the_readiness_task(self) -> None:
        """Two probes, two tasks. Sharing one would make each disarm the other.

        They differ in vantage point and in command, so a shared name would
        also mean whichever ran last silently redefined what the other
        measured.
        """

        self.assertNotEqual(driver.PEER_TASK_NAME, driver.READINESS_TASK_NAME)
        self.assertNotEqual(driver.PEER_MARKER, driver.READINESS_MARKER)
        instance = PeerInstance()
        code, _ = self.run_peer(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(
            [item["name"] for item in instance.tasks], [driver.PEER_TASK_NAME]
        )

    def test_no_delete_is_issued_anywhere_in_a_peer_run(self) -> None:
        """The probe is disclosed and left in place, never removed."""

        instance = PeerInstance()
        self.run_peer(instance)
        self.assertEqual(
            [method for method, _path in instance.writes() if method == "DELETE"], []
        )

    def test_the_committed_spec_names_a_peer_that_is_not_the_service(self) -> None:
        """Probing the service from itself is the loopback question again."""

        spec = self.real_spec()
        peer = spec["network"]["peer_probe_application"]
        self.assertTrue(peer)
        self.assertNotEqual(peer, spec["target"]["resource_name"])

    def test_the_ladder_rises_in_length_so_a_refusal_brackets_a_boundary(self) -> None:
        """Out of order, the rungs cannot bracket anything.

        The whole inference is "everything accepted is shorter than everything
        refused", which is only available if the rungs are monotone.
        """

        lengths = [len(command) for _label, command in driver.peer_ladder(self.real_spec())]
        self.assertEqual(lengths, sorted(lengths))
        self.assertLess(lengths[0], 40)

    def test_two_ladder_rungs_differ_only_in_length(self) -> None:
        """Length has to be separable from content or the answer is ambiguous.

        The filler rungs share a prefix and add nothing but repeated padding,
        so a refusal of the longer one cannot be blamed on a new character.
        """

        rungs = dict(driver.peer_ladder(self.real_spec()))
        short, long = rungs["filler-300"], rungs["filler-500"]
        self.assertTrue(long.startswith(short))
        self.assertEqual(set(long[len(short):]), {"x"})
        self.assertLess(len(short), len(long))

    def test_the_diagnostic_never_arms_anything(self) -> None:
        """It asks what the API accepts, not what the container does.

        Arming would run a command inside a production peer to answer a
        question about request validation, which is a far larger action than
        the question needs.
        """

        instance = PeerInstance()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_peer_diagnose(instance, self.real_spec())
        self.assertEqual(code, driver.EXIT_OK)
        self.assertTrue(instance.tasks)
        for task in instance.tasks:
            self.assertIs(task["enabled"], False)
            self.assertEqual(task["frequency"], driver.READINESS_TASK_FREQUENCY)
        self.assertEqual(instance.executions, [])

    def test_the_diagnostic_creates_exactly_one_task(self) -> None:
        """One task, rewritten per rung, so the command is the only variable."""

        instance = PeerInstance()
        with redirect_stdout(io.StringIO()):
            driver.operate_peer_diagnose(instance, self.real_spec())
        self.assertEqual(
            [item["name"] for item in instance.tasks],
            [driver.PEER_LADDER_TASK_NAME],
        )
        self.assertEqual(
            [m for m, _p in instance.writes() if m == "DELETE"], []
        )


class PeerToolsInstance(PeerInstance):
    """A peer whose shell answers the census.

    The default output is deliberately *not* the full candidate list: a fake
    that reported every tool present would let a driver that never read the
    output pass, and the whole value of this operation is in which lines are
    missing.
    """

    def __init__(self, *, census: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.task_name = driver.PEER_TOOLS_TASK_NAME
        self.next_execution = {
            "status": "success",
            "message": census
            if census is not None
            else "/usr/bin/curl\n/bin/busybox\nADAPTENG_TOOLS end\n",
        }


class PeerToolsTests(unittest.TestCase):
    """Measuring the peer's capabilities instead of guessing at them.

    The first armed peer probe came back reachable=undetermined
    reason=no_marker with 'sh: 1: python: not found'. Writing python3 in its
    place would be a guess that looks like a fix either way, and if the peer
    has no python at all the next run would establish nothing a second time.
    peer-diagnose set the precedent: measure the constraint first.
    """

    def real_spec(self):
        return driver.load_spec(driver.spec_path(RESOURCE))

    def run_tools(self, instance, spec=None):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_peer_tools(
                instance, spec or self.real_spec(), sleep=lambda _seconds: None
            )
        return code, buffer.getvalue()

    def test_a_completed_census_separates_present_from_absent(self) -> None:
        instance = PeerToolsInstance()
        code, output = self.run_tools(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT peer-tools ok tools=curl,busybox", output)
        self.assertIn("present: curl busybox", output)
        self.assertIn("python3", output.split("absent:")[1])

    def test_the_census_is_written_to_the_peer_not_to_the_service(self) -> None:
        """Same vantage-point requirement as the probe it prepares for."""

        instance = PeerToolsInstance()
        code, _ = self.run_tools(instance)
        self.assertEqual(code, driver.EXIT_OK)
        peer = next(item for item in instance.applications if item["name"] == PEER_NAME)
        service = next(
            item for item in instance.applications if item["name"] == RESOURCE
        )
        written = {
            path for _method, path in instance.writes() if "scheduled-task" in path
        }
        self.assertTrue(written)
        self.assertTrue(all(peer["uuid"] in path for path in written))
        self.assertFalse(any(service["uuid"] in path for path in written))

    def test_the_census_contacts_nothing(self) -> None:
        """It asks what is on PATH; it must not double as a reachability probe.

        An instrument that answered both questions at once could not say which
        one failed, which is the exact conflation the peer probe already had to
        be rescued from.
        """

        command = driver.peer_tools_command()
        for forbidden in (RESOURCE, "8081", "http", "/health", "/ready"):
            self.assertNotIn(forbidden, command)

    def test_the_census_obeys_the_in_container_construction_rules(self) -> None:
        """Coolify runs this as docker exec <c> sh -c '<command>'."""

        command = driver.peer_tools_command()
        for forbidden in ("'", '"', "$", "`", "\n"):
            self.assertNotIn(forbidden, command)
        self.assertLessEqual(len(command), driver.PEER_COMMAND_LIMIT)

    def test_every_candidate_is_actually_asked_for(self) -> None:
        """A candidate absent from the command reads as absent from the peer."""

        command = driver.peer_tools_command()
        for tool in driver.PEER_TOOLS_CANDIDATES:
            self.assertIn(f"command -v {tool}", command)

    def test_the_marker_is_last_so_silence_can_be_read_as_absence(self) -> None:
        """`command -v` prints nothing for a missing tool.

        Absence is therefore indistinguishable from truncation unless the list
        is known to have reached its end, which is the only thing the trailing
        marker is doing. If it moved to the front it would still be found, and
        every short answer would be silently reported as a set of absences.
        """

        command = driver.peer_tools_command()
        self.assertTrue(command.rstrip().endswith(f"echo {driver.PEER_TOOLS_MARKER} end"))

    def test_an_unfinished_census_concludes_nothing_about_any_tool(self) -> None:
        instance = PeerToolsInstance(census="/usr/bin/curl\n")
        code, output = self.run_tools(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT peer-tools failed reason=no_marker", output)
        self.assertNotIn("present:", output)
        self.assertIn("/usr/bin/curl", output)

    def test_a_completed_census_with_no_tools_is_an_answer_not_a_gap(self) -> None:
        """The two failures must not look alike.

        tools=none means the peer was asked and has nothing; no_marker means it
        was never successfully asked. Collapsing them would repeat the
        unreachable/undetermined conflation one level down.
        """

        instance = PeerToolsInstance(census="ADAPTENG_TOOLS end\n")
        code, output = self.run_tools(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT peer-tools failed tools=none", output)
        self.assertNotIn("no_marker", output)

    def test_a_tool_name_inside_an_unrelated_line_is_not_counted(self) -> None:
        """Substring matching would manufacture tools out of error text.

        'sh: 1: python: not found' is the literal output that prompted this
        operation, and it contains the name of a tool that is definitively
        absent.
        """

        instance = PeerToolsInstance(
            census="sh: 1: python: not found\n/usr/lib/python3/x\nADAPTENG_TOOLS end\n"
        )
        code, output = self.run_tools(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT peer-tools failed tools=none", output)

    def test_the_report_is_ordered_by_candidate_not_by_output(self) -> None:
        """The order is the recommendation, so it must not follow the shell."""

        self.assertEqual(
            driver.read_tool_census("/usr/bin/wget\n/usr/bin/python3\nADAPTENG_TOOLS end"),
            ["python3", "wget"],
        )

    def test_the_census_task_is_returned_to_rest_and_never_deleted(self) -> None:
        instance = PeerToolsInstance()
        self.run_tools(instance)
        self.assertEqual(
            [item["name"] for item in instance.tasks],
            [driver.PEER_TOOLS_TASK_NAME],
        )
        for task in instance.tasks:
            self.assertIs(task["enabled"], False)
            self.assertEqual(task["frequency"], driver.READINESS_TASK_FREQUENCY)
        self.assertEqual([m for m, _p in instance.writes() if m == "DELETE"], [])

    def test_an_absent_peer_is_reported_rather_than_worked_around(self) -> None:
        instance = PeerToolsInstance(with_peer=False)
        code, output = self.run_tools(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT peer-tools failed application=absent", output)

    def test_the_census_uses_its_own_task_name(self) -> None:
        """Sharing a task with the probe would overwrite the probe's command."""

        self.assertNotIn(
            driver.PEER_TOOLS_TASK_NAME,
            {driver.PEER_TASK_NAME, driver.PEER_LADDER_TASK_NAME, driver.READINESS_TASK_NAME},
        )


class ForeignTextRedactionTests(unittest.TestCase):
    """Container output is the one thing this tool prints that it did not build.

    The registered-value list cannot protect it, because a traceback can carry
    a credential this process never saw. These cases are the shapes that
    actually occur in a database failure, plus the ones a shape-based masker is
    most likely to get wrong.
    """

    def setUp(self) -> None:
        driver.reset_redactions()

    def tearDown(self) -> None:
        driver.reset_redactions()

    def test_a_password_inside_a_connection_string_is_masked(self) -> None:
        text, masked = driver.redact_foreign_text(
            "could not connect: postgresql://ai_gateway:hunter2swordfish@db:5432/x"
        )
        self.assertNotIn("hunter2swordfish", text)
        self.assertIn("postgresql://ai_gateway:[redacted]@db:5432/x", text)
        self.assertGreaterEqual(masked, 1)

    def test_the_user_and_host_survive_so_the_error_stays_readable(self) -> None:
        """Masking that removes the diagnosis defeats its own purpose."""

        text, _ = driver.redact_foreign_text(
            "FATAL: postgresql://ai_gateway:hunter2swordfish@db:5432/adapteng_ops"
        )
        self.assertIn("ai_gateway", text)
        self.assertIn("db:5432", text)
        self.assertIn("FATAL", text)

    def test_labelled_credentials_are_masked_with_the_label_kept(self) -> None:
        # Each case carries its own value and its own label. Sharing one value
        # across the cases made three of the four assertions vacuous: the value
        # was absent because it had never been in that line, not because it was
        # masked.
        for line, value, label in (
            ("PGPASSWORD=examplealpha", "examplealpha", "PGPASSWORD"),
            ("password: examplebravo", "examplebravo", "password"),
            ('api_key="examplecharlie"', "examplecharlie", "api_key"),
            ("Authorization: Basic exampledelta", "exampledelta", "Authorization"),
        ):
            with self.subTest(line=line):
                text, masked = driver.redact_foreign_text(line)
                self.assertNotIn(value, text)
                self.assertIn(label, text)
                self.assertIn("[redacted]", text)
                self.assertGreaterEqual(masked, 1)

    def test_a_long_dense_run_is_masked_even_unlabelled(self) -> None:
        """The case that matters: a secret whose shape is all there is to go on.

        A masker that only knows labels fails exactly when the log line was
        written by something that did not label it.
        """

        text, masked = driver.redact_foreign_text(
            "unexpected token tokn.a0AfB_byDx7KqR3nVpZ2mLwT8sQ4hJc6XbN1 rejected"
        )
        self.assertNotIn("tokn.a0AfB_byDx7KqR3nVpZ2mLwT8sQ4hJc6XbN1", text)
        self.assertIn("unexpected token", text)
        self.assertIn("rejected", text)
        self.assertGreaterEqual(masked, 1)

    def test_the_long_names_worth_reading_a_log_for_survive(self) -> None:
        """The discriminator's whole job, stated as the cases that decide it.

        Length is not the signal. An exception class and a dotted module path
        are long and dense-looking, and they are the two things a failure log
        is actually read for. Masking them leaves a redactor that is safe and
        useless.

        psycopg2.OperationalError is here because it broke the first
        discriminator: scoring distinct characters over length put it at 0.68,
        above a threshold set at 0.65, so the redactor masked the exception
        class and left the diagnosis unreadable.
        """

        for candidate in (
            "sqlalchemy.exc.OperationalError",
            "psycopg2.OperationalError",
            "psycopg2.errors.InsufficientPrivilege",
            "asyncpg.exceptions.InvalidPasswordError",
            "django.db.utils.OperationalError",
            "urllib3.exceptions.NewConnectionError",
            "adapteng.ai_gateway.http.ai_gateway.http_access",
            "adapteng-readiness-probe-application",
            "AI_GATEWAY_PG_DSN_HOST_VARIABLE_NAME",
            "ConnectionRefusedError.errno.ECONNREFUSED",
        ):
            with self.subTest(candidate=candidate):
                self.assertFalse(driver.looks_like_a_key(candidate))

        # The prefixes below are deliberately synthetic. Earlier revisions
        # used real ones and GitHub push protection correctly refused the
        # push, because a fixture credential is shape-identical to a real one.
        # That is this redactor's own argument turned back on its own tests,
        # so the fixtures conform rather than the scanner being told to ignore
        # them. Nothing is lost: the rule never reads a prefix, only the
        # density of the body.
        for candidate in (
            "tokn.a0AfB_byDx7KqR3nVpZ2mLwT8sQ4hJc6XbN1",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "gAAAAABm3xQ7pLkR2vN8sT4wY6zC1bD5eF9hJ0mK",
            "opaque-9Xk2Lm4Np7Qr1Tv5Wy8Zb3Cd6Fg0Hj",
            "opaque_16C7e42F292c6912E7710c838347Ae178B4a",
            "svc-2401278980-2402343Hqr8Xk2Lm4Np7Qr1",
            "a3f5e9c1b2d4a6f8e0c2b4d6a8f0e2c4b6d8a0f2",
            # A digest with a vowel every second character. Its longest
            # consonant run is 2, so the run rule does not see it at all and
            # only the all-hex rule catches it. The previous digest here has a
            # run of 7, which meant both rules fired and deleting the all-hex
            # rule broke nothing -- the rule was load-bearing and untested.
            "deadbeefcafedeadbeefcafedeadbeefcafedead",
        ):
            with self.subTest(candidate=candidate):
                self.assertTrue(driver.looks_like_a_key(candidate))

    def test_a_short_dense_token_is_left_alone_on_purpose(self) -> None:
        """The length floor is a policy, not an accident, so it is stated here.

        Nothing in the measured corpus of real names needs the floor to
        survive, so no other test exercises it and removing it broke nothing.
        It is kept because the trade is asymmetric below 24 characters: short
        dense strings are overwhelmingly identifiers, abbreviations and hashes
        of nothing, while a credential short enough to qualify would be a weak
        one. Masking them would cost readability everywhere to protect almost
        nothing.
        """

        candidate = "x1B4gT7qZ9"
        self.assertGreaterEqual(
            driver.longest_nonvowel_run(candidate), driver.KEYLIKE_NONVOWEL_RUN
        )
        self.assertFalse(driver.looks_like_a_key(candidate))

    def test_an_identifier_this_tool_resolved_survives_the_shape_rule(self) -> None:
        """Coolify mints uuids the way it mints secrets, so shape cannot judge.

        The application uuid scores a consonant run of 11 and was masked inside
        the container log -- while the line above printed it in the clear. That
        protected nothing and destroyed the only thing that says which
        application a log line came from.
        """

        uuid = "e13v7c6zjof7dmcpywqbyas3"
        self.assertTrue(driver.looks_like_a_key(uuid))

        masked_text, masked = driver.redact_foreign_text(f"container {uuid} started")
        self.assertNotIn(uuid, masked_text)
        self.assertEqual(masked, 1)

        kept_text, kept = driver.redact_foreign_text(
            f"container {uuid} started", known=(uuid,)
        )
        self.assertIn(uuid, kept_text)
        self.assertEqual(kept, 0)

    def test_an_exemption_cannot_unmask_a_labelled_credential(self) -> None:
        """The allowlist is one-directional or it is a hole.

        Exempting a span suppresses the shape rule only. If a caller passed a
        real secret as a known identifier -- by mistake or otherwise -- the
        labelled patterns still mask it, so the exemption can never be used to
        widen what gets printed.
        """

        example_value = "examplekeymaterialexamplekeymaterial"
        text, masked = driver.redact_foreign_text(
            f"api_key={example_value}", known=(example_value,)
        )
        self.assertNotIn(example_value, text)
        self.assertIn("[redacted]", text)
        self.assertGreaterEqual(masked, 1)

    def test_ordinary_prose_is_left_alone(self) -> None:
        """Over-masking is a real cost, not a free safety margin.

        A redactor that eats the message is one that gets switched off.
        """

        message = (
            "2026-08-11T21:30:00Z readiness check failed: connection refused "
            "after 5 seconds, retrying"
        )
        text, masked = driver.redact_foreign_text(message)
        self.assertEqual(text, message)
        self.assertEqual(masked, 0)

    def test_a_registered_value_is_masked_even_without_a_credential_shape(self) -> None:
        driver.register_redaction("plainword")
        text, _ = driver.redact_foreign_text("the value plainword appeared")
        self.assertNotIn("plainword", text)

    def test_the_count_reports_masking_so_an_eaten_message_is_visible(self) -> None:
        """The count is what distinguishes "nothing sensitive" from "all of it"."""

        _, none_masked = driver.redact_foreign_text("all clear")
        _, some_masked = driver.redact_foreign_text(
            "token=exampleecho password=examplefoxtrot"
        )
        self.assertEqual(none_masked, 0)
        self.assertGreaterEqual(some_masked, 2)


class DiagnoseTests(unittest.TestCase):
    """diagnose reads and reports; it must never write.

    It exists because "no execution was recorded" has several causes that look
    identical from outside, and Coolify skips a task silently in two of them.
    """

    def setUp(self) -> None:
        driver.reset_redactions()

    def tearDown(self) -> None:
        driver.reset_redactions()

    def real_spec(self) -> dict:
        return driver.load_spec(driver.spec_path(RESOURCE))

    def run_diagnose(self, instance) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_diagnose(instance, self.real_spec())
        return code, buffer.getvalue()

    def test_it_writes_nothing(self) -> None:
        instance = ReadinessInstance()
        self.run_diagnose(instance)
        self.assertEqual(
            [(method, path) for method, path in instance.calls if method != "GET"], []
        )

    def test_a_stopped_container_is_named_as_the_reason_a_probe_never_ran(self) -> None:
        """The finding, not an error: it is the same predicate the scheduler uses."""

        instance = ReadinessInstance()
        instance.logs = None
        code, output = self.run_diagnose(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("Application is not running", output)
        self.assertIn("Coolify skips a scheduled task", output)

    def test_the_dispatch_predicate_is_reported_from_the_status(self) -> None:
        instance = ReadinessInstance()
        instance.applications[0]["status"] = "exited:unhealthy"
        code, output = self.run_diagnose(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("scheduler would dispatch a task for it: False", output)

    def test_container_logs_are_masked_before_they_are_printed(self) -> None:
        instance = ReadinessInstance()
        instance.logs = (
            "starting\nOperationalError: postgresql://ai_gateway:hunter2swordfish@db:5432/x\n"
        )
        code, output = self.run_diagnose(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertNotIn("hunter2swordfish", output)
        self.assertIn("OperationalError", output)
        self.assertIn("spans masked", output)

    def test_the_application_uuid_survives_inside_its_own_log(self) -> None:
        """The wiring, not the rule: diagnose must say what it resolved.

        A Coolify uuid is shape-identical to a credential -- this one scores a
        consonant run of 11 -- so the redactor masks it unless the caller says
        it already knows it. diagnose prints that uuid in the clear two lines
        earlier, so masking it inside the log protected nothing and removed the
        only marker tying a log line to an application.
        """

        uuid = "e13v7c6zjof7dmcpywqbyas3"
        instance = ReadinessInstance()
        instance.applications[0]["uuid"] = uuid
        instance.logs = (
            f"container {uuid} bound 0.0.0.0:8081\n"
            "unexpected tokn.a0AfB_byDx7KqR3nVpZ2mLwT8sQ4hJc6XbN1 rejected\n"
        )
        code, output = self.run_diagnose(instance)

        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn(f"container {uuid} bound", output)
        # The exemption is for this identifier only; an unknown dense run in
        # the same text is still masked.
        self.assertNotIn("tokn.a0AfB_byDx7KqR3nVpZ2mLwT8sQ4hJc6XbN1", output)
        self.assertIn("1 credential-shaped spans masked", output)

    def test_an_execution_message_is_masked_before_it_is_printed(self) -> None:
        """An execution message is container output too.

        It is the captured stdout and stderr of docker exec, so it can carry a
        credential this process never registered, exactly as the container log
        can.
        """

        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": driver.readiness_command(self.real_spec()),
                "enabled": False,
            }
        )
        instance.executions.append(
            {
                "id": None,
                "status": "failed",
                "created_at": "2026-08-11T21:29:00.000000Z",
                "message": "psycopg2.OperationalError PGPASSWORD=examplegolf denied",
            }
        )
        code, output = self.run_diagnose(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertNotIn("examplegolf", output)
        self.assertIn("OperationalError", output)

    def test_an_absent_application_is_reported_rather_than_crashed_on(self) -> None:
        instance = FakeInstance(with_application=False)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_diagnose(instance, self.real_spec())
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT diagnose failed application=absent", buffer.getvalue())

    def test_execution_history_is_reported_including_none(self) -> None:
        instance = ReadinessInstance()
        instance.tasks.append(
            {
                "uuid": "task-old",
                "name": driver.READINESS_TASK_NAME,
                "command": driver.readiness_command(self.real_spec()),
                "enabled": False,
                "frequency": driver.READINESS_TASK_FREQUENCY,
            }
        )
        code, output = self.run_diagnose(instance)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("executions ever recorded: 0", output)


if __name__ == "__main__":
    unittest.main()
