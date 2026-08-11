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
        "network": {"internal_port": 8081, "public_fqdn": None, "connect_to_docker_network": True},
        "health_check": {
            "enabled": True,
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
        self.environment_entries: dict[str, list[dict]] = {}
        self.deployment_states = list(deployment_states or ["queued", "in_progress", "finished"])
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
        """Idempotency is the property that makes this safe to run from a chat prompt."""

        instance = FakeInstance()
        first, _ = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(first, driver.EXIT_OK)
        instance.calls.clear()
        second, report = run_operation(driver.operate_reconcile, instance)
        self.assertEqual(second, driver.EXIT_OK)
        self.assertIn("changed=no", report)
        self.assertEqual(instance.writes(), [])

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
        # A test fixture is not an exception to that rule — the checker cannot tell
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
        """Binding by reference must not make every run a write."""

        instance = FakeInstance()
        supplied = {"AI_GATEWAY_FX_USD_EUR": "0.865426"}
        first, _ = run_operation(driver.operate_reconcile, instance, supplied=supplied)
        self.assertEqual(first, driver.EXIT_OK)
        instance.calls.clear()
        second, report = run_operation(driver.operate_reconcile, instance, supplied=supplied)
        self.assertEqual(second, driver.EXIT_OK)
        self.assertIn("changed=no", report)
        self.assertEqual(instance.writes(), [])

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
        self.assertIn("unreported_settings=4", report)
        self.assertIn("PENDING-OWNER-UI", report)

    def test_an_unreportable_setting_is_never_written(self) -> None:
        """A write that cannot be re-read must not be attempted."""

        instance = FakeInstance()
        instance.settings_response_shape = "none"
        run_operation(driver.operate_reconcile, instance)
        written = [
            body
            for method, path, body in instance.call_bodies
            if method == "PATCH" and re.fullmatch(r"/applications/[^/]+", path)
        ]
        for body in written:
            self.assertEqual(set(body) & set(driver.SETTING_KEYS), set())


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

    def test_a_failed_deployment_fails_the_run(self) -> None:
        instance = FakeInstance(
            with_application=True, deployment_states=["queued", "in_progress", "failed"]
        )
        code, report = self.deploy(instance)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("RESULT deploy failed", report)
        self.assertIn("dep-1", report)

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

        self.assertEqual(set(driver.OPERATIONS), {"inspect", "reconcile", "deploy", "status"})
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


if __name__ == "__main__":
    unittest.main()
