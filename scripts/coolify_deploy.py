#!/usr/bin/env python3
"""Inspect, reconcile, deploy and report the services declared under ``deploy/``.

Every value this applies comes from a committed spec file, never from a workflow
input, so what production is supposed to look like stays reviewable in git. The
access credential arrives through the environment and is never printed.

Four operations exist and each one is invoked on its own.

``inspect``    Read-only. Lists the project, the environment and the applications
               in it, says whether the declared application exists, and prints the
               delta between the declared spec and the live resource.
``reconcile``  Creates what is missing and updates what has drifted, then re-reads
               everything it wrote and fails when the stored state still differs.
``deploy``     Triggers one deployment and polls it to a terminal state.
``status``     Read-only. Reports the resource state and the newest deployment.

Nothing here removes anything. The HTTP client refuses the DELETE method outright,
so no later edit can reach a destructive endpoint by accident; taking a resource
away stays an owner action performed in the console.

Every failure is loud. A missing environment value, an unreachable API, a match
that is not unique and any non-2xx response all stop the run with a message and a
non-zero exit, so a partial apply can never be mistaken for a converged one.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_DIRECTORY = ROOT / "deploy"
API_PREFIX = "/api/v1"

CREDENTIAL_VARIABLE = "COOLIFY_API_CREDENTIAL"
BASE_URL_VARIABLE = "COOLIFY_BASE_URL"
OPERATION_VARIABLE = "OPERATION"
SERVICE_VARIABLE = "SERVICE"
POLL_VARIABLE = "DEPLOY_POLL_SECONDS"
TIMEOUT_VARIABLE = "DEPLOY_TIMEOUT_SECONDS"

# An owner-held value may be handed to this run through the environment under
# this prefix, so a credential can be moved from one store to another without
# ever being written to a file in this repository. The value is bound by
# reference: the spec still names the key and nothing else, and the value exists
# in this process only for the length of one request.
SUPPLY_PREFIX = "COOLIFY_SECRET_"

OPERATIONS = (
    "inspect",
    "reconcile",
    "deploy",
    "status",
    "verify",
    "peer-verify",
    "peer-diagnose",
    "diagnose",
)
FORBIDDEN_METHODS = frozenset({"DELETE"})

# Coolify reports a deployment through these states. Anything outside the two
# sets below is unknown, and an unknown state is polled rather than guessed.
SUCCEEDED_STATES = frozenset({"finished"})
FAILED_STATES = frozenset({"failed", "error", "cancelled", "cancelled-by-user"})

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_MISCONFIGURED = 2

DEFAULT_POLL_SECONDS = 10
DEFAULT_TIMEOUT_SECONDS = 1800
MINIMUM_REDACTABLE_LENGTH = 8

BEARER_HEADER = re.compile(r"(?i)\bbearer\s+\S+")

SPEC_SECTIONS = {
    "schema_version": (int,),
    "service": (str,),
    "summary": (str,),
    "source_of_declared_values": (dict,),
    "target": (dict,),
    "source": (dict,),
    "build": (dict,),
    "network": (dict,),
    "health_check": (dict,),
    "delivery": (dict,),
    "configuration": (list,),
    "externally_provided_configuration": (list,),
}
SECTION_KEYS = {
    "target": {"project", "environment", "resource_name", "server", "destination"},
    "source": {"kind", "git_repository", "git_branch", "github_app"},
    "build": {"build_pack", "base_directory", "dockerfile_location"},
    "network": {
        "internal_port",
        "public_fqdn",
        "connect_to_docker_network",
        "peer_probe_application",
    },
    "health_check": {
        "enabled",
        "container_gate",
        "container_gate_note",
        "path",
        "method",
        "scheme",
        "return_code",
        "interval_seconds",
        "timeout_seconds",
        "retries",
        "start_period_seconds",
    },
    "delivery": {"auto_deploy_on_push", "preview_deployments", "force_https"},
}
SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_SOURCE_KINDS = frozenset({"private_github_app", "public"})

# What actually gates a rolling update. Coolify's health_check_enabled flag does
# not answer this on its own, so the spec has to say which of the three it is.
#   coolify_http  the generated curl/wget probe; requires health_check_enabled
#   image         a HEALTHCHECK instruction in the image; requires it disabled,
#                 because parseHealthcheckFromDockerfile only records one then
#   absent        nothing runs; health_check() sets newVersionIsHealthy and
#                 returns, so any container that starts is promoted
CONTAINER_GATES = frozenset({"coolify_http", "image", "absent"})
GATE_REQUIRING_ENABLED = "coolify_http"

_REDACTIONS: list[str] = []


class Abort(Exception):
    """A stop condition that must end the run with a non-zero exit."""

    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.code = code


class Unreachable(Abort):
    """The API could not be spoken to at all, as distinct from refusing.

    This is a subclass rather than a flag so that every existing caller keeps
    behaving exactly as it did - it is still an Abort and still stops the run.
    It exists so the one place that legitimately continues past a transport
    failure can name precisely that condition, instead of catching Abort and
    thereby also swallowing a refused write or a malformed body.
    """


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def register_redaction(value: str | None) -> None:
    """Remember a value that must never reach the run log."""

    text = (value or "").strip()
    if len(text) >= MINIMUM_REDACTABLE_LENGTH and text not in _REDACTIONS:
        _REDACTIONS.append(text)
        quoted = urllib.parse.quote(text, safe="")
        if quoted != text and quoted not in _REDACTIONS:
            _REDACTIONS.append(quoted)


def reset_redactions() -> None:
    """Drop the remembered values. Exists so tests start from a known state."""

    _REDACTIONS.clear()


def redact(text: str) -> str:
    """Return ``text`` with every remembered value and any bearer header masked."""

    result = text
    for value in _REDACTIONS:
        result = result.replace(value, "[redacted]")
    return BEARER_HEADER.sub("bearer [redacted]", result)


# Container output is the one input this tool prints that it did not construct,
# so the registered-value list cannot cover it: a traceback can carry a
# credential this process never saw and therefore never registered. These
# patterns mask by shape instead of by value, which is what makes them work on
# an unknown secret.
CREDENTIAL_SHAPES = (
    # A DSN's password, between the first colon after the scheme and the @.
    (re.compile(r"(?i)(://[^\s:/@]+:)[^\s@]+(@)"), r"\1[redacted]\2"),
    # An auth header's value is a scheme followed by the credential, so masking
    # one token would leave the credential in place. Everything after the label
    # goes.
    (
        re.compile(r"(?im)^(.*?\b(?:authorization|auth)\s*[=:]\s*).+$"),
        r"\1[redacted]",
    ),
    # key=value and key: value for the words that introduce a credential.
    (
        re.compile(
            r"(?i)\b(pass|passwd|password|pgpassword|secret|token|api[_-]?key|"
            r"access[_-]?key|private[_-]?key|credential)"
            r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1\2[redacted]",
    ),
)

# A run long and dense enough to be a key. Dots are inside the run because the
# tokens that matter most here are dotted -- a JWT and a Google access token
# both are -- and excluding them let exactly those through.
DENSE_RUN = re.compile(r"[A-Za-z0-9+/=_.-]{24,}")
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
VOWELS = frozenset("aeiouAEIOU")
# Measured over a corpus of real exception classes, dotted module paths and
# real credential formats. The first attempt scored the ratio of distinct
# characters to length and put the threshold at 0.65, on the claim that the
# densest real name was psycopg2.errors.InsufficientPrivilege at 0.51. That was
# an artifact of the corpus: psycopg2.OperationalError scores 0.68 and so was
# masked, while a hex digest scores 0.375 and so was not. The two classes are
# interleaved under that measure, so no threshold separates them.
#
# Consonant structure does separate them, because these names are built from
# English words and credentials are not. Worst real name is 6
# (asyncpg.exceptions.InvalidPasswordError); sparsest real credential is 7.
KEYLIKE_NONVOWEL_RUN = 7


def longest_nonvowel_run(text: str) -> int:
    """The longest stretch with no vowel in it.

    Words put a vowel in every few characters; random tokens do not. Anything
    that is not a letter or a digit ends a run, so a separator does not join
    two words into one apparent stretch.
    """

    best = 0
    current = 0
    for character in text:
        if character in VOWELS or not character.isalnum():
            current = 0
            continue
        current += 1
        best = max(best, current)
    return best


def looks_like_a_key(candidate: str) -> bool:
    """Decide whether a long run is a credential or just a long name.

    Length is not the signal. ``psycopg2.OperationalError`` and
    ``sqlalchemy.exc.OperationalError`` are long, dotted and dense-looking, and
    they are exactly what a failure log is read for. Masking them leaves a
    redactor that is safe and useless, which is a redactor that gets switched
    off.
    """

    core = candidate.strip("._-")
    if len(core) < 24:
        return False
    if all(character in HEX_DIGITS for character in core):
        return True
    return longest_nonvowel_run(core) >= KEYLIKE_NONVOWEL_RUN


def redact_foreign_text(text: str, known: Iterable[str] = ()) -> tuple[str, int]:
    """Mask credential-shaped spans in output this tool did not produce.

    Returns the masked text and how many spans were masked, because a redactor
    that quietly eats the error message is indistinguishable from one that
    found nothing -- and the count is what tells the two apart without
    disclosing what was removed.

    Masking is by shape, so an unregistered secret is still caught. It is
    deliberately eager: a hex digest is masked too. That is the correct trade
    here. Losing an identifier costs a second query; printing a credential
    cannot be undone.

    ``known`` names strings the caller resolved itself and has already emitted
    in the clear -- the application and task uuids. Coolify mints those the way
    it mints secrets, so shape cannot tell them apart, and measuring said so:
    the application uuid scores a consonant run of 11. Masking them hid the one
    identifier that says which application a log line came from, while the line
    above printed it unmasked, so nothing was protected and the correlation was
    lost. An exemption only ever suppresses the shape rule; a span that a
    labelled pattern already matched stays masked even if it is listed here, so
    this cannot be used to unmask a real secret.
    """

    result = redact(text)
    masked = 0
    for pattern, replacement in CREDENTIAL_SHAPES:
        result, count = pattern.subn(replacement, result)
        masked += count

    exempt = {item for item in known if item}

    def mask_dense(match: re.Match) -> str:
        nonlocal masked
        if match.group(0) in exempt or not looks_like_a_key(match.group(0)):
            return match.group(0)
        masked += 1
        return "[redacted]"

    return DENSE_RUN.sub(mask_dense, result), masked


def emit(text: str = "") -> None:
    print(redact(text))


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #


def spec_path(service: str) -> Path:
    """Return the spec file for ``service``, refusing anything but a plain name."""

    if not service or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", service):
        raise Abort(
            f"service name {service!r} is not a plain lowercase name",
            EXIT_MISCONFIGURED,
        )
    return SPEC_DIRECTORY / f"{service}.json"


def load_spec(path: Path) -> dict:
    """Read and fully validate one spec file.

    Validation is strict in both directions. A missing section and an unexpected
    key are equally fatal, because a silently ignored key is how a declared value
    stops being applied without anyone noticing.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise Abort(
            f"cannot read spec {path.name}: {error.__class__.__name__}",
            EXIT_MISCONFIGURED,
        ) from error

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as error:
        raise Abort(f"spec {path.name} is not valid JSON: {error}", EXIT_MISCONFIGURED) from error

    if not isinstance(spec, dict):
        raise Abort(f"spec {path.name} must be an object", EXIT_MISCONFIGURED)

    for name, types in SPEC_SECTIONS.items():
        if name not in spec:
            raise Abort(f"spec {path.name} is missing {name}", EXIT_MISCONFIGURED)
        if not isinstance(spec[name], types):
            raise Abort(f"spec {path.name} has a wrong type for {name}", EXIT_MISCONFIGURED)

    if spec["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        raise Abort(
            f"spec {path.name} declares schema_version "
            f"{spec['schema_version']}, this tool understands "
            f"{SUPPORTED_SCHEMA_VERSION}",
            EXIT_MISCONFIGURED,
        )

    for section, required in SECTION_KEYS.items():
        present = set(spec[section])
        missing = required - present
        if missing:
            raise Abort(
                f"spec {path.name} section {section} is missing {sorted(missing)}",
                EXIT_MISCONFIGURED,
            )
        # "note" is free prose for the reader and carries no behaviour.
        unexpected = present - required - {"note"}
        if unexpected:
            raise Abort(
                f"spec {path.name} section {section} has unknown keys {sorted(unexpected)}",
                EXIT_MISCONFIGURED,
            )

    if spec["source"]["kind"] not in SUPPORTED_SOURCE_KINDS:
        raise Abort(
            f"spec {path.name} declares an unsupported source kind "
            f"{spec['source']['kind']!r}",
            EXIT_MISCONFIGURED,
        )

    if spec["network"]["public_fqdn"] is not None:
        raise Abort(
            f"spec {path.name} declares a public FQDN. This tool only manages "
            "private-network services; publishing a route is an owner action.",
            EXIT_MISCONFIGURED,
        )

    gate = spec["health_check"]["container_gate"]
    if gate not in CONTAINER_GATES:
        raise Abort(
            f"spec {path.name} declares an unknown container_gate {gate!r}; "
            f"expected one of {sorted(CONTAINER_GATES)}",
            EXIT_MISCONFIGURED,
        )
    # Coolify ties the generated probe to health_check_enabled and ties the
    # image's own HEALTHCHECK to that same flag being off, so exactly one of
    # the two can be in force. Letting the spec claim otherwise would let a
    # deployment report a gate it does not have, which is the failure this
    # field exists to make impossible.
    enabled = spec["health_check"]["enabled"]
    if (gate == GATE_REQUIRING_ENABLED) != bool(enabled):
        raise Abort(
            f"spec {path.name} declares container_gate {gate!r} with "
            f"health_check.enabled {enabled!r}. Coolify runs its generated probe "
            f"only when enabled is true, and honours the image's own HEALTHCHECK "
            f"only when it is false, so {GATE_REQUIRING_ENABLED!r} and enabled "
            "must agree.",
            EXIT_MISCONFIGURED,
        )
    if not str(spec["health_check"]["container_gate_note"]).strip():
        raise Abort(
            f"spec {path.name} leaves container_gate_note blank. A gate weaker "
            "than a real probe has to say so in words.",
            EXIT_MISCONFIGURED,
        )

    seen: set[str] = set()
    for entry in spec["configuration"]:
        check_configuration_entry(path, entry, "configuration", "value")
        seen.add(entry["key"])
    for entry in spec["externally_provided_configuration"]:
        check_configuration_entry(path, entry, "externally_provided_configuration", "reason")
        if entry["key"] in seen:
            raise Abort(
                f"spec {path.name} declares {entry['key']} both inline and externally",
                EXIT_MISCONFIGURED,
            )
        seen.add(entry["key"])

    if spec["service"] != path.stem:
        raise Abort(
            f"spec {path.name} names the service {spec['service']!r}",
            EXIT_MISCONFIGURED,
        )
    return spec


def check_configuration_entry(path: Path, entry: object, section: str, field: str) -> None:
    if not isinstance(entry, dict):
        raise Abort(f"spec {path.name} section {section} has a non-object entry", EXIT_MISCONFIGURED)
    for name in ("key", field):
        if not isinstance(entry.get(name), str) or not entry[name]:
            raise Abort(
                f"spec {path.name} section {section} has an entry without a usable {name}",
                EXIT_MISCONFIGURED,
            )
    allowed = {"key", field, "note"}
    if section == "externally_provided_configuration":
        allowed.add("sensitive")
        if "sensitive" in entry and not isinstance(entry["sensitive"], bool):
            raise Abort(
                f"spec {path.name} section {section} entry {entry['key']} "
                "declares a non-boolean sensitive flag",
                EXIT_MISCONFIGURED,
            )
    unexpected = set(entry) - allowed
    if unexpected:
        raise Abort(
            f"spec {path.name} section {section} entry {entry['key']} "
            f"has unknown keys {sorted(unexpected)}",
            EXIT_MISCONFIGURED,
        )


def is_sensitive(entry: dict) -> bool:
    """Return whether an owner-held value must be kept out of the run log.

    The default is ``True``. An owner-held value is assumed to carry
    authentication material unless the spec says otherwise in reviewed text, so
    forgetting the flag hides a value rather than exposing one.
    """

    return bool(entry.get("sensitive", True))


def supplied_values(spec: dict, environ: dict) -> dict[str, str]:
    """Return the owner-held values handed to this run through the environment.

    Only a key the spec already declares under
    ``externally_provided_configuration`` can be supplied. A variable naming any
    other key is refused rather than ignored: the spec is the reviewed list of
    what this resource may be given, and silently accepting an extra key would
    let a dispatch introduce configuration that no one reviewed.
    """

    declared = {entry["key"]: entry for entry in spec["externally_provided_configuration"]}
    supplied: dict[str, str] = {}
    for name, raw in sorted(environ.items()):
        if not name.startswith(SUPPLY_PREFIX):
            continue
        key = name[len(SUPPLY_PREFIX) :]
        value = (raw or "").strip()
        if not value:
            continue
        entry = declared.get(key)
        if entry is None:
            raise Abort(
                f"{name} supplies {key}, which spec {spec['service']}.json does not "
                "declare under externally_provided_configuration",
                EXIT_MISCONFIGURED,
            )
        if is_sensitive(entry):
            register_redaction(value)
        supplied[key] = value
    return supplied


# --------------------------------------------------------------------------- #
# Comparison helpers
# --------------------------------------------------------------------------- #


def normalize_repository(value: object) -> str:
    """Reduce a repository reference to ``owner/name``.

    Coolify stores a git source either as ``owner/name`` or as a clone URL
    depending on how the resource was created. Comparing the raw strings would
    report drift on every run and make the reconciler write forever.
    """

    text = "" if value is None else str(value).strip()
    lowered = text.lower()
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:", "github.com/"):
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    if text.lower().endswith(".git"):
        text = text[:-4]
    return text.strip("/").lower()


def normalize(field: str, value: object) -> str:
    """Return the comparable form of one field value."""

    if field == "git_repository":
        return normalize_repository(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def difference(desired: dict, current: dict) -> list[tuple[str, str, str]]:
    """Return ``(field, stored, declared)`` for every field that does not match."""

    changes: list[tuple[str, str, str]] = []
    for field in sorted(desired):
        want = normalize(field, desired[field])
        have = normalize(field, current.get(field))
        if want != have:
            changes.append((field, have, want))
    return changes


def desired_application_fields(spec: dict) -> dict:
    """Return the application fields this tool owns, from the declared spec."""

    build = spec["build"]
    health = spec["health_check"]
    source = spec["source"]
    port = str(spec["network"]["internal_port"])
    return {
        "name": spec["target"]["resource_name"],
        "git_repository": source["git_repository"],
        "git_branch": source["git_branch"],
        "build_pack": build["build_pack"],
        "base_directory": build["base_directory"],
        "dockerfile_location": build["dockerfile_location"],
        "ports_exposes": port,
        "health_check_enabled": health["enabled"],
        "health_check_path": health["path"],
        "health_check_port": port,
        "health_check_method": health["method"],
        "health_check_scheme": health["scheme"],
        "health_check_return_code": health["return_code"],
        "health_check_interval": health["interval_seconds"],
        "health_check_timeout": health["timeout_seconds"],
        "health_check_retries": health["retries"],
        "health_check_start_period": health["start_period_seconds"],
    }


def desired_settings(spec: dict) -> dict:
    """Return the resource settings this tool owns, from the declared spec."""

    delivery = spec["delivery"]
    return {
        "is_auto_deploy_enabled": delivery["auto_deploy_on_push"],
        "is_preview_deployments_enabled": delivery["preview_deployments"],
        "is_force_https_enabled": delivery["force_https"],
        "connect_to_docker_network": spec["network"]["connect_to_docker_network"],
    }


# The names desired_settings owns, as a constant, so that reading stored state
# does not have to build a spec in order to know which keys belong to this tool.
SETTING_KEYS = (
    "is_auto_deploy_enabled",
    "is_preview_deployments_enabled",
    "is_force_https_enabled",
    "connect_to_docker_network",
)


def stored_settings(application: dict) -> dict:
    """Return the delivery flags the API reports, in whichever shape it uses.

    Coolify does not report these consistently. Some versions nest them under a
    settings relation; this instance omits that relation and reports none of them
    at all. This function only reads. Deciding whether an absence is tolerable is
    settings_delta's job, because only the spec knows what was declared.
    """

    settings = application.get("settings")
    if isinstance(settings, dict) and settings:
        return settings
    owned = set(SETTING_KEYS)
    return {key: value for key, value in application.items() if key in owned}


def unreportable_settings(spec: dict) -> dict:
    """Return the declared settings this API is acknowledged not to report.

    Acknowledgement lives in the committed spec, so tolerating an unverifiable
    setting is a reviewable decision with a recorded reason, not a silent skip.
    A name that is not an owned setting is a typo and aborts, since a misspelt
    acknowledgement would silently widen what the tool is willing to ignore.
    """

    declared = spec.get("settings_not_reported_by_api", {}).get("keys", {})
    unknown = sorted(set(declared) - set(SETTING_KEYS))
    if unknown:
        raise Abort(
            "the spec acknowledges settings this tool does not own: "
            f"{unknown}; owned settings are {sorted(SETTING_KEYS)}"
        )
    for name, entry in declared.items():
        for field in ("reason", "compensating_control"):
            if not str(entry.get(field) or "").strip():
                raise Abort(
                    f"the spec acknowledges {name} as unreportable without a {field}; "
                    "an unverifiable setting has to carry its justification"
                )
    return declared


def settings_written_blind(spec: dict) -> dict:
    """Return acknowledged settings that are written anyway, and how each is checked.

    The rule everywhere else here is that a write which cannot be re-read must
    not be reported as success. That rule is about verification, not about
    reading: a setting whose effect is observable by some other check is
    verifiable, just not by this endpoint.

    ``connect_to_docker_network`` is the case that forced the distinction. It is
    not reported, so it was not written, so the container was created detached
    and nothing noticed until a probe asked the network directly. Refusing to
    write it did not keep the deployment honest; it kept it broken while
    reporting PENDING-OWNER-UI, which reads like a formality.

    So a setting may name the check that observes it. Naming one is what makes
    it eligible to be written, and the check has to be a real thing a person can
    run - it is quoted in the report so that a claim nobody verifies is visible
    as such.
    """

    eligible = {}
    for name, entry in unreportable_settings(spec).items():
        verified_by = str(entry.get("verified_by") or "").strip()
        if verified_by:
            eligible[name] = verified_by
    return eligible


def settings_delta(spec: dict, application: dict) -> tuple[list, list[str]]:
    """Return the settings that differ, and those the API will not report.

    A declared setting the API does not report cannot be verified after a write,
    and an unverifiable write must not be reported as success. So it is not
    written at all: it is surfaced for the owner, exactly as an owner-held
    environment key is. An unacknowledged absence still aborts.
    """

    desired = desired_settings(spec)
    acknowledged = unreportable_settings(spec)
    stored = stored_settings(application)
    missing = set(desired) - set(stored)
    unacknowledged = sorted(missing - set(acknowledged))
    if unacknowledged:
        raise Abort(
            f"the API reports no value for {unacknowledged}, so a write to them "
            "could not be verified. Either the response shape changed or these "
            "have to be acknowledged in settings_not_reported_by_api with a "
            "reason; refusing to report success"
        )
    reportable = {name: value for name, value in desired.items() if name in stored}
    return difference(reportable, stored), sorted(missing)


def settings_shape(application: dict) -> str:
    """Name the shape the flags arrived in, for a report that must be diagnosable."""

    settings = application.get("settings")
    if isinstance(settings, dict) and settings:
        return "settings block"
    present = sorted(key for key in application if key in set(SETTING_KEYS))
    if present:
        return f"top-level fields ({len(present)} of {len(SETTING_KEYS)})"
    return "not reported by this API"


def runtime_environment(entries: list) -> dict:
    """Index the non-preview environment entries by key.

    A key that appears twice is an ambiguous match, and guessing which row the
    container will actually see is how a wrong value gets applied quietly.
    """

    indexed: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("is_preview"):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            continue
        if key in indexed:
            raise Abort(
                f"environment key {key} is defined more than once on this "
                "resource; refusing to guess which definition applies"
            )
        indexed[key] = entry
    return indexed


def environment_plan(
    spec: dict, entries: list, supplied: dict[str, str] | None = None
) -> tuple[list, list, list, list]:
    """Return ``(create, update, unchanged, absent_external)`` for the environment.

    Keys declared inline are always written. An owner-held key is written only
    when this run was handed its value; otherwise it is checked for presence by
    name and never read, written or printed. That asymmetry is deliberate: a
    value this repository does not hold cannot be pushed over a fresher one set
    elsewhere, because nothing here knows what to push.
    """

    supplied = supplied or {}
    indexed = runtime_environment(entries)
    create: list[tuple[str, str]] = []
    update: list[tuple[str, str]] = []
    unchanged: list[str] = []
    for item in spec["configuration"]:
        key, value = item["key"], item["value"]
        if key not in indexed:
            create.append((key, value))
        elif normalize(key, indexed[key].get("value")) != normalize(key, value):
            update.append((key, value))
        else:
            unchanged.append(key)
    absent: list[str] = []
    for item in spec["externally_provided_configuration"]:
        key = item["key"]
        value = supplied.get(key)
        if value is None:
            if key not in indexed:
                absent.append(key)
            continue
        if key not in indexed:
            create.append((key, value))
        elif normalize(key, indexed[key].get("value")) != normalize(key, value):
            update.append((key, value))
        else:
            unchanged.append(key)
    return create, update, unchanged, absent


def creation_payload(
    spec: dict,
    *,
    project_uuid: str,
    environment_name: str,
    environment_uuid: str | None,
    server_uuid: str,
    destination_uuid: str | None,
    github_app_uuid: str | None,
) -> dict:
    """Return the body that creates the application in its declared placement.

    Settings are deliberately not sent here. The creation endpoint rejects
    is_preview_deployments_enabled outright ("This field is not allowed."), and
    which of the others it tolerates is undocumented and would be discovered one
    422 at a time. Reconcile re-reads the application immediately after creating
    it and converges every setting through the same path that repairs drift on
    an existing resource, so nothing is lost by leaving them out -- and that path
    verifies what was stored, which the creation call does not.

    Nothing is exposed in the interval: instant_deploy is false, so a created
    application is not running, and autogenerate_domain is false with no fqdn
    declared, so no public route exists at any point.
    """

    payload = dict(desired_application_fields(spec))
    payload.update(
        {
            "project_uuid": project_uuid,
            "environment_name": environment_name,
            "server_uuid": server_uuid,
            # Creation must never release anything. Deployment is a separate,
            # explicitly requested operation.
            "instant_deploy": False,
            # The declared spec has no public FQDN, so Coolify must not invent one.
            "autogenerate_domain": False,
        }
    )
    if environment_uuid:
        payload["environment_uuid"] = environment_uuid
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    if github_app_uuid:
        payload["github_app_uuid"] = github_app_uuid
    return payload


def creation_path(spec: dict) -> str:
    return (
        "/applications/private-github-app"
        if spec["source"]["kind"] == "private_github_app"
        else "/applications/public"
    )


def deployment_outcome(status: str | None) -> str:
    """Classify a deployment state as ``succeeded``, ``failed`` or ``pending``."""

    state = (status or "").strip().lower()
    if state in SUCCEEDED_STATES:
        return "succeeded"
    if state in FAILED_STATES:
        return "failed"
    return "pending"


def unique_match(candidates: list, kind: str, label: str) -> dict:
    """Return the only candidate, or stop.

    Neither zero nor several is a usable answer, and picking the first of several
    is how automation writes to the wrong resource.
    """

    if not candidates:
        raise Abort(f"no {kind} named {label} exists")
    if len(candidates) > 1:
        raise Abort(
            f"{len(candidates)} {kind} entries match {label}; refusing to guess which one"
        )
    return candidates[0]


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #


class Client:
    """A small JSON client for the Coolify API.

    It refuses DELETE, so removal is not reachable from this tool at all, and it
    turns every non-2xx answer into a stop rather than a value the caller might
    treat as data.
    """

    def __init__(self, base_url: str, credential: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self._credential = credential
        self._timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
    ) -> tuple[int, object]:
        verb = method.upper()
        if verb in FORBIDDEN_METHODS:
            raise Abort(
                f"{verb} is not available in this tool; removing a resource is an owner action",
                EXIT_MISCONFIGURED,
            )
        url = f"{self.base_url}{API_PREFIX}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        message = urllib.request.Request(url, data=data, method=verb)
        message.add_header("Authorization", "Bearer " + self._credential)
        message.add_header("Accept", "application/json")
        message.add_header("User-Agent", "adapteng-coolify-deploy")
        if data is not None:
            message.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(message, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                parsed = {"message": raw[:300]}
            return error.code, parsed
        except json.JSONDecodeError as error:
            raise Abort(f"the API returned a body that is not JSON: {error}") from error
        except OSError as error:
            raise Unreachable(
                f"the API at {self.base_url} is unreachable: {error.__class__.__name__}"
            ) from error


def call(
    client: Client,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    query: dict | None = None,
    expect: tuple[int, ...] = (200,),
    allow_absent: bool = False,
) -> object:
    """Perform one call and stop unless the status is one that was expected.

    allow_absent turns a 404 into None instead of an abort, and only that. It is
    for endpoints whose presence varies across Coolify versions, where absence
    is a fact to route around rather than a failure. It is never used for a
    write, and never to excuse an unexpected status other than 404.
    """

    status, parsed = client.request(method, path, body=body, query=query)
    if allow_absent and status == 404:
        return None
    if status not in expect:
        raise Abort(
            f"{method.upper()} {path} returned HTTP {status} "
            f"(expected {list(expect)}): {api_message(parsed)}"
        )
    return parsed


def api_message(parsed: object) -> str:
    """Render an API error body, keeping the part that says what was wrong.

    A validation failure arrives as a generic message plus a field-level errors
    object. Returning the first key found meant the generic message shadowed the
    specific one, so a 422 reported "Validation failed." and nothing else --
    true, unactionable, and indistinguishable from every other 422. Both are
    kept here, because the reason a call was rejected is the whole diagnostic
    value of the response.
    """

    if isinstance(parsed, dict):
        parts = [
            redact(json.dumps(parsed[key])[:300])
            for key in ("message", "error", "errors")
            if key in parsed
        ]
        if parts:
            return " ".join(parts)
        return redact(json.dumps(parsed)[:300])
    if parsed is None:
        return "no body"
    return redact(str(parsed)[:300])


def expect_list(parsed: object, what: str) -> list:
    if not isinstance(parsed, list):
        raise Abort(f"the API returned {type(parsed).__name__} instead of a list of {what}")
    return parsed


def expect_object(parsed: object, what: str) -> dict:
    if not isinstance(parsed, dict):
        raise Abort(f"the API returned {type(parsed).__name__} instead of a {what}")
    return parsed


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def find_project(client: Client, name: str) -> dict | None:
    projects = expect_list(call(client, "GET", "/projects"), "projects")
    matches = [item for item in projects if isinstance(item, dict) and item.get("name") == name]
    if len(matches) > 1:
        raise Abort(f"{len(matches)} projects are named {name}; refusing to guess which one")
    return matches[0] if matches else None


def find_environment(client: Client, project_uuid: str, name: str) -> dict | None:
    parsed = call(client, "GET", f"/projects/{project_uuid}/environments")
    environments = expect_list(parsed, "environments")
    matches = [
        item for item in environments if isinstance(item, dict) and item.get("name") == name
    ]
    if len(matches) > 1:
        raise Abort(f"{len(matches)} environments are named {name}; refusing to guess which one")
    return matches[0] if matches else None


def applications_in(client: Client, environment: dict) -> list:
    applications = expect_list(call(client, "GET", "/applications"), "applications")
    environment_id = environment.get("id")
    return [
        item
        for item in applications
        if isinstance(item, dict) and item.get("environment_id") == environment_id
    ]


def find_application(applications: list, name: str) -> dict | None:
    matches = [item for item in applications if item.get("name") == name]
    if len(matches) > 1:
        raise Abort(
            f"{len(matches)} applications in this environment are named {name}; "
            "refusing to guess which one"
        )
    return matches[0] if matches else None


def destination_uuid_of(application: dict) -> str | None:
    """Read the destination an existing application is already placed on.

    Coolify exposes this under more than one name across versions, and on some
    it is a nested object rather than a flat uuid. Every spelling is read here
    so that a working neighbour, rather than a guess about the API surface, is
    what tells this script where a new application belongs.
    """

    for key in ("destination_uuid", "destinationUuid"):
        value = application.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = application.get("destination")
    if isinstance(nested, dict):
        value = nested.get("uuid")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def report_placement_sources(client: Client, applications: list) -> None:
    """Report where existing applications are placed, without changing anything.

    A create call needs a server and a destination. Asking the API for the list
    of destinations is not portable: this instance answers 404 for it. What is
    portable is that the neighbours already running here carry the answer.
    """

    for item in sorted(applications, key=lambda entry: entry.get("name") or ""):
        uuid = item.get("uuid")
        if not isinstance(uuid, str):
            continue
        detail = call(client, "GET", f"/applications/{uuid}")
        if not isinstance(detail, dict):
            continue
        server = detail.get("server_uuid")
        emit(
            f"      placement of {detail.get('name')}: "
            f"server={server if isinstance(server, str) else 'unreported'} "
            f"destination={destination_uuid_of(detail) or 'unreported'}"
        )


def report_github_apps(client: Client) -> None:
    """List the GitHub apps this instance can read private repositories through.

    The spec may leave source.github_app null when exactly one exists. When more
    than one does, the choice is a decision, not a lookup, so it belongs in the
    committed spec. Printing the candidates here is what makes that decision
    reviewable instead of a console discovery.
    """

    parsed = call(client, "GET", "/github-apps", allow_absent=True)
    if parsed is None:
        emit("    github apps: this instance does not report any")
        return
    apps = [item for item in expect_list(parsed, "GitHub apps") if isinstance(item, dict)]
    emit(f"    github apps available: {len(apps)}")
    for item in sorted(apps, key=lambda entry: entry.get("name") or ""):
        emit(
            f"      - {item.get('name')} uuid={item.get('uuid')} "
            f"organization={item.get('organization') or 'none'}"
        )


def resolve_server(client: Client, declared: str | None) -> dict:
    servers = expect_list(call(client, "GET", "/servers"), "servers")
    usable = [item for item in servers if isinstance(item, dict)]
    if declared:
        return unique_match(
            [item for item in usable if item.get("name") == declared], "server", declared
        )
    return unique_match(usable, "server", "the only server on this instance")


def resolve_destination(
    client: Client,
    server: dict,
    declared: str | None,
    neighbours: list | None = None,
) -> dict | None:
    """Find the destination to place the application on.

    Three sources are tried in order of authority, and the first that answers
    wins. The endpoint is tried first because it is the only one that can honour
    a destination declared by name. When it is absent, as it is on this
    instance, the server object often carries the same list inline. Failing
    both, an application already running in the target environment is the
    strongest evidence available: it is placed where a new sibling belongs, and
    it is placed there by the same instance that will read this value back.
    """

    server_uuid = server.get("uuid")
    destinations: list = []
    parsed = call(client, "GET", f"/servers/{server_uuid}/destinations", allow_absent=True)
    if parsed is not None:
        destinations = [item for item in expect_list(parsed, "destinations") if isinstance(item, dict)]
    if not destinations:
        inline = server.get("destinations")
        if isinstance(inline, list):
            destinations = [item for item in inline if isinstance(item, dict)]
    if declared:
        if not destinations:
            raise Abort(
                f"the spec names destination {declared!r}, but this instance does not report "
                "any destination list to match it against"
            )
        return unique_match(
            [item for item in destinations if item.get("name") == declared],
            "destination",
            declared,
        )
    if destinations:
        return unique_match(destinations, "destination", "the only destination on this server")

    inherited = {
        found
        for item in neighbours or []
        if isinstance(item, dict) and (found := destination_uuid_of(item))
    }
    if len(inherited) == 1:
        uuid = inherited.pop()
        emit(f"    destination: inherited {uuid} from an application already in this environment")
        return {"uuid": uuid, "name": None}
    if len(inherited) > 1:
        raise Abort(
            "the applications already in this environment are split across "
            f"{len(inherited)} destinations, so there is no single one to inherit; "
            "declare target.destination in the spec"
        )
    return None


def resolve_github_app(client: Client, declared: str | None) -> dict:
    parsed = call(client, "GET", "/github-apps")
    apps = [item for item in expect_list(parsed, "GitHub apps") if isinstance(item, dict)]
    if declared:
        return unique_match(
            [item for item in apps if item.get("name") == declared], "GitHub app", declared
        )
    return unique_match(apps, "GitHub app", "the only GitHub app on this instance")


# --------------------------------------------------------------------------- #
# Reporting helpers
# --------------------------------------------------------------------------- #


def report_settings_sources(client: Client, application_uuid: str) -> None:
    """Probe, read-only, for any route that reports the delivery flags.

    The application resource on this instance carries none of them. Before
    treating that as unverifiable, the other places they could plausibly live are
    checked, so the conclusion rests on an enumeration rather than on one read.

    Status codes and key names only. Every request is a GET.
    """

    owned = set(SETTING_KEYS)
    candidates = (
        f"/applications/{application_uuid}/settings",
        f"/applications/{application_uuid}?include=settings",
        f"/applications/{application_uuid}/advanced",
    )
    emit("      settings source probe (read-only):")
    for path in candidates:
        status, parsed = client.request("GET", path)
        found: list[str] = []
        if isinstance(parsed, dict):
            found = sorted(key for key in parsed if key in owned)
            nested = parsed.get("settings")
            if isinstance(nested, dict):
                found = sorted(set(found) | (owned & set(nested)))
        emit(f"        GET {path} -> HTTP {status}, owned keys reported: {found}")


SAFE_DATABASE_FIELDS = (
    "uuid",
    "name",
    "status",
    "database_type",
    "image",
    "postgres_db",
    "postgres_user",
    "is_public",
    "public_port",
    "enable_ssl",
    "ssl_mode",
    "server_status",
)


def address_of(url: object) -> str:
    """Return host:port/database from a DSN, dropping the credential.

    A connection URL is not a secret in the parts that name where to connect; it
    is a secret in the userinfo. Splitting it here means the address the gateway
    needs can be read from a log while the credential in the same string cannot.
    """

    if not isinstance(url, str) or not url:
        return "not reported"
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "unparseable"
    host = parsed.hostname or "?"
    port = parsed.port
    database = (parsed.path or "").lstrip("/") or "?"
    return f"{host}:{port if port is not None else '?'}/{database}"


def report_databases(client: Client) -> None:
    """Print what databases this instance manages, by name and shape only.

    A database object carries the credential its own engine was created with, so
    this prints key names, fields that are identities rather than secrets, and
    the address half of each connection URL. The reason to look at all is that
    the gateway's DSN has to name a host the gateway's container can resolve, and
    guessing that address is the one thing scripts/postgres_runtime_role.py
    refuses to do.
    """

    databases = call(client, "GET", "/databases", allow_absent=True)
    if not isinstance(databases, list):
        emit("    databases: not reported by this instance")
        return
    emit(f"    databases: {len(databases)}")
    for item in databases:
        if not isinstance(item, dict):
            continue
        shown = " ".join(
            f"{name}={item.get(name)}"
            for name in SAFE_DATABASE_FIELDS
            if item.get(name) is not None
        )
        emit(f"      - {shown}")
        emit(f"        internal address: {address_of(item.get('internal_db_url'))}")
        emit(f"        external address: {address_of(item.get('external_db_url'))}")
        emit(f"        keys: {sorted(item)}")


SAFE_STORAGE_FIELDS = (
    "uuid",
    "name",
    "mount_path",
    "host_path",
    "fs_path",
    "is_directory",
    "resource_type",
)


def report_storages(client: Client, application_uuid: str) -> None:
    """Print where this application's file mounts land, never what is in them.

    A file storage carries the body of the file in a `content` field, so for the
    binding this gateway still needs that field would hold the service account
    key itself. Location is reportable and body is not, so this prints mount
    paths and key names and stops there.

    The reason to look at all is that app/config.py checks the Vertex binding by
    opening the path it is given, and treats an unreadable path as a boot
    failure rather than an absence. So the binding has to be a real file inside
    the container, which is what a mount supplies, and the environment key has
    to be set only once that file is there. Reporting the mounts is how that
    ordering gets checked before anything is written.
    """

    storages = call(
        client, "GET", f"/applications/{application_uuid}/storages", allow_absent=True
    )
    if isinstance(storages, dict):
        groups = [(name, storages.get(name)) for name in ("file_storages", "persistent_storages")]
    elif isinstance(storages, list):
        groups = [("storages", storages)]
    else:
        emit("    storages: not reported by this instance")
        return
    for label, items in groups:
        if not isinstance(items, list):
            emit(f"      {label}: not reported by this instance")
            continue
        emit(f"      {label}: {len(items)}")
        for item in items:
            if not isinstance(item, dict):
                continue
            shown = " ".join(
                f"{name}={item.get(name)}"
                for name in SAFE_STORAGE_FIELDS
                if item.get(name) is not None
            )
            emit(f"        - {shown}")
            emit(f"          keys: {sorted(item)}")


def report_object_shape(client: Client, application: dict) -> dict:
    """Print the key names the API reports for this application, and nothing else.

    Only names. The object carries owner-held values, so printing it whole would
    put secrets in a log. Names are enough to tell which shape a response uses.

    Both the list entry and the detail read are reported, because they are
    different responses. The detail read is returned, so that a caller compares
    against the same object a write will be verified against.
    """

    owned = set(SETTING_KEYS)

    def describe(label: str, obj: dict) -> None:
        nested = obj.get("settings")
        emit(f"      {label}: {len(obj)} keys, settings relation={type(nested).__name__}")
        emit(f"        keys: {sorted(obj)}")
        if isinstance(nested, dict):
            emit(f"        settings keys: {sorted(nested)}")
        emit(f"        owned keys at top level: {sorted(key for key in obj if key in owned)}")

    describe("list entry", application)
    detail = call(client, "GET", f"/applications/{application['uuid']}", allow_absent=True)
    if not isinstance(detail, dict):
        emit("      detail read: not available")
        return application
    describe("detail read", detail)
    report_settings_sources(client, application["uuid"])
    return detail


def report_placement(spec: dict, project: dict | None, environment: dict | None) -> None:
    target = spec["target"]
    if project is None:
        emit(f"    project {target['project']}: ABSENT")
        return
    emit(f"    project {target['project']}: present uuid={project.get('uuid')}")
    if environment is None:
        emit(f"    environment {target['environment']}: ABSENT")
        return
    emit(f"    environment {target['environment']}: present id={environment.get('id')}")


def report_delta(spec: dict, application: dict, entries: list) -> bool:
    """Print the declared-versus-stored delta. Returns True when nothing differs."""

    fields = difference(desired_application_fields(spec), application)
    settings, unverifiable = settings_delta(spec, application)
    create, update, unchanged, absent = environment_plan(spec, entries)

    emit(f"    delivery flags read from: {settings_shape(application)}")
    for name, have, want in fields:
        emit(f"    field {name}: stored={have!r} declared={want!r}")
    for name, have, want in settings:
        emit(f"    setting {name}: stored={have!r} declared={want!r}")
    for name in unverifiable:
        emit(f"    setting {name}: PENDING-OWNER-UI, this API does not report it")
    # Environment values are never printed. A declared key can be looked up in the
    # spec file, and anything else on the resource is owner-held.
    for key, _ in create:
        emit(f"    env {key}: absent, would be created")
    for key, _ in update:
        emit(f"    env {key}: differs, would be updated")
    emit(f"    env unchanged: {len(unchanged)} of {len(spec['configuration'])} declared keys")
    for key in absent:
        emit(f"    env {key}: PENDING-OWNER, not set on the resource")

    fqdn = (application.get("fqdn") or "").strip()
    if fqdn:
        emit("    fqdn: PRESENT, but the spec declares private network only")
    else:
        emit("    fqdn: none, as declared")

    return not (fields or settings or create or update or fqdn)


def newest_deployment(client: Client, application_uuid: str) -> dict | None:
    """Return the most recent deployment, or None when the resource has none.

    A resource that has never been deployed is a normal answer, so a 404 here is
    read as "none recorded" rather than turned into a failure.
    """

    status, parsed = client.request("GET", f"/deployments/applications/{application_uuid}")
    if status == 404:
        return None
    if status != 200:
        raise Abort(
            f"listing deployments returned HTTP {status}: {api_message(parsed)}"
        )
    if isinstance(parsed, dict):
        parsed = parsed.get("deployments", [])
    entries = [item for item in expect_list(parsed, "deployments") if isinstance(item, dict)]
    if not entries:
        return None
    return sorted(entries, key=lambda item: (item.get("created_at") or "", item.get("id") or 0))[-1]


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def operate_inspect(client: Client, spec: dict) -> int:
    target = spec["target"]
    emit(f"--- inspect {spec['service']}")
    project = find_project(client, target["project"])
    environment = find_environment(client, project["uuid"], target["environment"]) if project else None
    report_placement(spec, project, environment)
    if project is None or environment is None:
        emit(f"    application {target['resource_name']}: ABSENT (no such placement yet)")
        emit("")
        emit("RESULT inspect ok application=absent")
        return EXIT_OK

    applications = applications_in(client, environment)
    emit(f"    applications in this environment: {len(applications)}")
    for item in sorted(applications, key=lambda entry: entry.get("name") or ""):
        emit(f"      - {item.get('name')} uuid={item.get('uuid')} status={item.get('status')}")
    report_placement_sources(client, applications)
    report_github_apps(client)
    report_databases(client)

    application = find_application(applications, target["resource_name"])
    if application is None:
        emit(f"    application {target['resource_name']}: ABSENT")
        emit("")
        emit("RESULT inspect ok application=absent")
        return EXIT_OK

    emit(
        f"    application {target['resource_name']}: present "
        f"uuid={application.get('uuid')} status={application.get('status')}"
    )
    report_storages(client, application["uuid"])
    # The delta is computed against the detail read, because that is the object
    # reconcile verifies a write against. Comparing the list entry here would let
    # inspect report agreement for a resource reconcile would still change.
    detail = report_object_shape(client, application)
    entries = expect_list(
        call(client, "GET", f"/applications/{application['uuid']}/envs"), "environment entries"
    )
    converged = report_delta(spec, detail, entries)
    emit("")
    emit(
        "RESULT inspect ok application=present "
        f"matches_spec={'yes' if converged else 'no'}"
    )
    return EXIT_OK


def ensure_placement(client: Client, spec: dict) -> tuple[dict, dict]:
    """Return the project and environment, creating either one when it is absent."""

    target = spec["target"]
    project = find_project(client, target["project"])
    if project is None:
        created = expect_object(
            call(
                client,
                "POST",
                "/projects",
                body={"name": target["project"]},
                expect=(200, 201),
            ),
            "created project",
        )
        emit(f"    project {target['project']}: created uuid={created.get('uuid')}")
        project = find_project(client, target["project"])
        if project is None:
            raise Abort("the project was reported created but cannot be read back")
    else:
        emit(f"    project {target['project']}: present uuid={project.get('uuid')}")

    environment = find_environment(client, project["uuid"], target["environment"])
    if environment is None:
        call(
            client,
            "POST",
            f"/projects/{project['uuid']}/environments",
            body={"name": target["environment"]},
            expect=(200, 201),
        )
        environment = find_environment(client, project["uuid"], target["environment"])
        if environment is None:
            raise Abort("the environment was reported created but cannot be read back")
        emit(f"    environment {target['environment']}: created id={environment.get('id')}")
    else:
        emit(f"    environment {target['environment']}: present id={environment.get('id')}")
    return project, environment


def create_application(client: Client, spec: dict, project: dict, environment: dict) -> str:
    target = spec["target"]
    server = resolve_server(client, target["server"])
    destination = resolve_destination(
        client, server, target["destination"], applications_in(client, environment)
    )
    github_app = (
        resolve_github_app(client, spec["source"]["github_app"])
        if spec["source"]["kind"] == "private_github_app"
        else None
    )
    emit(
        f"    placement: server={server.get('name')} "
        f"destination={(destination or {}).get('name') or (destination or {}).get('uuid')}"
    )
    if github_app is not None:
        emit(f"    source: GitHub app {github_app.get('name')}")

    payload = creation_payload(
        spec,
        project_uuid=project["uuid"],
        environment_name=environment["name"],
        environment_uuid=environment.get("uuid"),
        server_uuid=server["uuid"],
        destination_uuid=(destination or {}).get("uuid"),
        github_app_uuid=(github_app or {}).get("uuid"),
    )
    created = expect_object(
        call(client, "POST", creation_path(spec), body=payload, expect=(200, 201)),
        "created application",
    )
    uuid = created.get("uuid")
    if not uuid:
        raise Abort("the application was reported created but the response carries no uuid")
    emit(f"    application {target['resource_name']}: created uuid={uuid}")
    return str(uuid)


def read_application(client: Client, uuid: str) -> dict:
    return expect_object(call(client, "GET", f"/applications/{uuid}"), "application")


def read_environment_entries(client: Client, uuid: str) -> list:
    return expect_list(
        call(client, "GET", f"/applications/{uuid}/envs"), "environment entries"
    )


def report_container_gate(spec: dict) -> None:
    """Say plainly what gates a rolling update, especially when nothing does.

    A deployment that reports ok has proved that Coolify finished, not that the
    container works. When the gate is absent it has not even proved that the
    process answers itself, because Coolify skips the probe and marks the new
    version healthy on the way past. Printing that next to the result is the
    difference between a known gap and a silent one.
    """

    gate = spec["health_check"]["container_gate"]
    if gate == "absent":
        emit(
            "    GATE ABSENT no container probe runs: Coolify marks the new "
            "version healthy without testing it, so a container that starts and "
            "answers nobody is still promoted. Reachability is proved only by "
            "scripts/gateway_readiness.py, after the fact, not before promotion."
        )
    elif gate == "image":
        emit("    GATE image HEALTHCHECK; Coolify waits for the image's own probe")
    else:
        emit("    GATE coolify_http generated probe inside the container")


def operate_reconcile(client: Client, spec: dict, supplied: dict[str, str] | None = None) -> int:
    supplied = supplied or {}
    target = spec["target"]
    emit(f"--- reconcile {spec['service']}")
    if supplied:
        emit(f"    supplied owner-held keys: {sorted(supplied)}")
    project, environment = ensure_placement(client, spec)
    applications = applications_in(client, environment)
    application = find_application(applications, target["resource_name"])

    if application is None:
        uuid = create_application(client, spec, project, environment)
    else:
        uuid = str(application["uuid"])
        emit(f"    application {target['resource_name']}: present uuid={uuid}")

    stored = read_application(client, uuid)
    field_changes = difference(desired_application_fields(spec), stored)
    setting_changes, unverifiable = settings_delta(spec, stored)
    blind = settings_written_blind(spec)
    for name in unverifiable:
        if name in blind:
            continue
        emit(f"    setting {name}: PENDING-OWNER-UI, this API does not report it")
    if field_changes or setting_changes or blind:
        body = {name: desired_application_fields(spec)[name] for name, _, _ in field_changes}
        body.update({name: desired_settings(spec)[name] for name, _, _ in setting_changes})
        for name, have, want in field_changes + setting_changes:
            emit(f"    change {name}: {have!r} -> {want!r}")
        # Written on every reconcile rather than on a detected difference,
        # because there is nothing to detect a difference against. It is the
        # declared value either way, so a repeat write converges on the same
        # state; what it must never do is claim confirmation.
        for name, verified_by in sorted(blind.items()):
            body[name] = desired_settings(spec)[name]
            emit(
                f"    write-blind {name}: {desired_settings(spec)[name]!r} "
                f"(not reported back; observed by {verified_by})"
            )
        call(client, "PATCH", f"/applications/{uuid}", body=body)
    else:
        emit("    application fields and settings already match the spec")

    entries = read_environment_entries(client, uuid)
    create, update, unchanged, absent = environment_plan(spec, entries, supplied)
    for key, value in create:
        emit(f"    change env {key}: created")
        call(
            client,
            "POST",
            f"/applications/{uuid}/envs",
            body={"key": key, "value": value},
            expect=(200, 201),
        )
    for key, value in update:
        emit(f"    change env {key}: updated")
        call(
            client,
            "PATCH",
            f"/applications/{uuid}/envs",
            body={"key": key, "value": value},
            expect=(200, 201),
        )
    if not create and not update:
        emit(f"    environment already matches the spec ({len(unchanged)} declared keys)")

    # Nothing above is trusted. The stored state is read again and compared, so a
    # write the API accepted but did not store cannot pass as success.
    emit("    verifying by re-reading")
    verified = read_application(client, uuid)
    verified_entries = read_environment_entries(client, uuid)
    residual_fields = difference(desired_application_fields(spec), verified)
    residual_settings, still_unverifiable = settings_delta(spec, verified)
    residual_create, residual_update, _, absent = environment_plan(
        spec, verified_entries, supplied
    )
    fqdn = (verified.get("fqdn") or "").strip()

    problems: list[str] = []
    for name, have, want in residual_fields + residual_settings:
        problems.append(f"{name} is still {have!r} and not {want!r}")
    for key, _ in residual_create + residual_update:
        problems.append(f"environment key {key} still does not match the spec")
    if fqdn:
        problems.append(
            "a public FQDN is configured, but the spec declares private network only. "
            "Removing a route is an owner action and is not done here."
        )

    for key in absent:
        emit(f"    PENDING-OWNER env {key} is not set on the resource")

    if problems:
        for problem in problems:
            emit(f"    VERIFY FAILED {problem}")
        emit("")
        emit("RESULT reconcile failed")
        return EXIT_FAILED

    changed = bool(field_changes or setting_changes or create or update) or application is None
    withheld = [name for name in still_unverifiable if name not in blind]
    if withheld:
        emit(
            "    VERIFY OK for everything this API reports; "
            f"{len(withheld)} declared setting(s) are not reported and were not written: "
            f"{withheld}"
        )
    else:
        emit("    VERIFY OK stored state matches the declared spec")
    for name, verified_by in sorted(blind.items()):
        # Stated separately and never folded into VERIFY OK. This run wrote the
        # value and cannot read it back, so the honest report is that it was
        # sent and where the confirmation has to come from.
        emit(f"    WRITTEN NOT VERIFIED {name}; confirm with {verified_by}")
    report_container_gate(spec)
    emit("")
    emit(
        f"RESULT reconcile ok uuid={uuid} changed={'yes' if changed else 'no'} "
        f"pending_owner_env={len(absent)} unreported_settings={len(withheld)} "
        f"written_blind={len(blind)} "
        f"health_gate={spec['health_check']['container_gate']}"
    )
    return EXIT_OK


def operate_deploy(
    client: Client,
    spec: dict,
    poll_seconds: int,
    timeout_seconds: int,
    sleep=time.sleep,
    clock=time.monotonic,
) -> int:
    target = spec["target"]
    emit(f"--- deploy {spec['service']}")
    project = find_project(client, target["project"])
    if project is None:
        raise Abort(f"project {target['project']} does not exist; run reconcile first")
    environment = find_environment(client, project["uuid"], target["environment"])
    if environment is None:
        raise Abort(f"environment {target['environment']} does not exist; run reconcile first")
    application = find_application(
        applications_in(client, environment), target["resource_name"]
    )
    if application is None:
        raise Abort(
            f"application {target['resource_name']} does not exist; run reconcile first"
        )

    uuid = str(application["uuid"])
    entries = read_environment_entries(client, uuid)
    _, _, _, absent = environment_plan(spec, entries)
    if absent:
        raise Abort(
            "these owner-held environment keys are not set on the resource, so the "
            f"service cannot start: {sorted(absent)}"
        )

    parsed = expect_object(
        call(client, "POST", "/deploy", query={"uuid": uuid}), "deploy acknowledgement"
    )
    deployments = [item for item in (parsed.get("deployments") or []) if isinstance(item, dict)]
    if len(deployments) != 1:
        raise Abort(
            f"the deploy request returned {len(deployments)} deployments; expected exactly one"
        )
    deployment_uuid = deployments[0].get("deployment_uuid")
    if not deployment_uuid:
        raise Abort("the deploy request returned no deployment identifier")
    emit(f"    deployment {deployment_uuid} queued for application {uuid}")

    outcome, state = poll_deployment(
        client,
        str(deployment_uuid),
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        sleep=sleep,
        clock=clock,
    )
    emit("")
    if outcome != "succeeded":
        report_deployment_failure(client, spec, uuid, str(deployment_uuid))
        emit("")
        emit(f"RESULT deploy failed deployment={deployment_uuid} state={state}")
        return EXIT_FAILED
    application_state = read_application(client, uuid).get("status")
    report_container_gate(spec)
    emit(
        f"RESULT deploy ok deployment={deployment_uuid} state={state} "
        f"application_state={application_state} "
        f"health_gate={spec['health_check']['container_gate']}"
    )
    return EXIT_OK


def deployment_log_lines(record: dict) -> list[str]:
    """Flatten whatever this instance puts in a deployment's ``logs`` field.

    Coolify has shipped this as a JSON-encoded string of entries, as a real list
    of entries, and as plain text. Rather than pick one and be wrong on the next
    upgrade - this instance has already contradicted the published schema twice -
    every shape it might be is accepted and anything unrecognised degrades to its
    string form. A log reader that raises is worse than one that is untidy,
    because it fires exactly when something has already gone wrong.
    """

    raw = record.get("logs")
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return raw.splitlines()
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return str(raw).splitlines()
    lines: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            text = entry.get("output")
            text = str(text) if text is not None else json.dumps(entry, sort_keys=True)
        else:
            text = str(entry)
        lines.extend(text.splitlines() or [""])
    return lines


def report_deployment_failure(
    client: Client,
    spec: dict,
    application_uuid: str,
    deployment_uuid: str,
    tail: int = 60,
) -> None:
    """Print why a deployment failed, with the sensitive stored values masked first.

    The failure state alone is not a diagnosis. Reporting only ``state=failed``
    is the same defect this codebase keeps finding elsewhere: a specific cause
    collapsed into a generic verdict, leaving the reader to guess. The build log
    is the only place the cause exists, so it is fetched here rather than left in
    a console someone has to open by hand.

    A build log is untrusted text that may quote a connection string, so the
    values stored on the application are registered for redaction before a line
    is printed - otherwise the redaction table would hold only what this process
    happened to write itself, and a deploy writes almost nothing.

    Only the owner-held keys are masked, not every key. The values under
    ``configuration`` are committed in this repository in clear text, so hiding
    them protects nothing and costs the reader the one thing this function
    exists to give them. Masking a model name or a hostname would turn the log
    into the opaque verdict it is meant to replace.
    """

    sensitive_keys = {
        entry["key"]
        for entry in spec["externally_provided_configuration"]
        if is_sensitive(entry)
    }
    for entry in read_environment_entries(client, application_uuid):
        if isinstance(entry, dict) and entry.get("key") in sensitive_keys:
            register_redaction(entry.get("value"))

    try:
        record = expect_object(
            call(client, "GET", f"/deployments/{deployment_uuid}"), "deployment"
        )
    except Abort as exc:
        emit(f"    could not read the deployment log: {exc}")
        return

    lines = [line for line in deployment_log_lines(record) if line.strip()]
    if not lines:
        emit("    the deployment reported no log lines")
        return
    shown = lines[-tail:]
    if len(shown) != len(lines):
        emit(f"    deployment log, last {len(shown)} of {len(lines)} lines:")
    else:
        emit(f"    deployment log, {len(lines)} lines:")
    for line in shown:
        emit(f"    | {line}")


def poll_deployment(
    client: Client,
    deployment_uuid: str,
    *,
    poll_seconds: int,
    timeout_seconds: int,
    sleep=time.sleep,
    clock=time.monotonic,
) -> tuple[str, str]:
    """Poll one deployment until it is terminal, or stop when the budget runs out.

    A transport failure during polling does not end the run. The deployment is
    proceeding on the server whether or not this process can currently ask about
    it, so treating one unanswered question as a failed deployment reports the
    observer's problem as the subject's - and has already done so once here,
    aborting a deployment that then completed successfully.

    What it must not become is unbounded patience. The timeout budget is not
    extended, and an unreachable API at the end of it is reported as exactly
    that, rather than as a deployment that failed: the honest verdict is that
    the outcome is unknown, and the state to check is the application's.
    """

    started = clock()
    last_state = ""
    unanswered = 0
    while True:
        try:
            parsed = expect_object(
                call(client, "GET", f"/deployments/{deployment_uuid}"), "deployment"
            )
        except Unreachable as error:
            unanswered += 1
            # Reported every time rather than once. These are the ticks during
            # which the state is unknown, and a single earlier line would let a
            # long blind stretch read as one blip.
            emit(f"    deployment {deployment_uuid} not answered ({error}); still waiting")
            if clock() - started >= timeout_seconds:
                raise Unreachable(
                    f"deployment {deployment_uuid} could not be read for the last "
                    f"{unanswered} of {timeout_seconds}s; its outcome is unknown "
                    "rather than failed - check the application state before "
                    "deploying again"
                ) from error
            sleep(poll_seconds)
            continue
        state = str(parsed.get("status") or "unknown")
        if state != last_state:
            emit(f"    deployment {deployment_uuid} state={state}")
            last_state = state
        outcome = deployment_outcome(state)
        if outcome != "pending":
            return outcome, state
        if clock() - started >= timeout_seconds:
            raise Abort(
                f"deployment {deployment_uuid} was still {state} after "
                f"{timeout_seconds}s; giving up without a terminal state"
            )
        sleep(poll_seconds)


READINESS_TASK_NAME = "adapteng-readiness-probe"
# February 29th exists only in leap years, so this is the rarest schedule that
# is still a valid expression. It has to be valid: Coolify's validator builds a
# next run date and rejects an expression that has none, which is how the live
# instance refused an unmatchable February 31st with HTTP 422. Rarity is not
# what makes this safe, though. The task is created disabled, and Coolify's
# scheduler selects tasks with where('enabled', true), so a disabled task is
# never dispatched whatever its frequency says. Execution happens only through
# the explicit execute endpoint below, which ignores the enabled flag by design.
READINESS_TASK_FREQUENCY = "0 0 29 2 *"
# The armed frequency, held only for as long as it takes Coolify's scheduler to
# notice. This instance has no execute endpoint -- the route answers the generic
# 404 while its sibling /executions answers 401, which is how its absence was
# established without presenting a credential -- so the only way to run the
# probe is to let the scheduler run it.
READINESS_ARMED_FREQUENCY = "* * * * *"
READINESS_DISARM_ATTEMPTS = 4
READINESS_TASK_TIMEOUT_SECONDS = 60
READINESS_MARKER = "ADAPTENG_READY"
# Deliberately not read from health_check.path. That field is the container
# gate's target and is currently /health, which touches nothing. This probe
# exists to prove the database is reachable, and only /ready opens a
# connection, so the path is fixed here rather than inherited.
READINESS_PATH = "/ready"
# Coolify runs this as: docker exec <container> sh -c '<command>'. It escapes
# single quotes, so the command deliberately contains none; the outer single
# quotes make the double quotes below literal to sh, which then applies them.
# There is no $ or backtick either, so sh cannot expand anything.
#
# It is one line. The first version spanned several, and the API answered HTTP
# 500 when asked to store it; a single line removes that whole question.
#
# Patching HTTPErrorProcessor is what makes a 503 a reported number instead of
# a raised exception, which matters because 503 is the interesting answer here:
# it is what /ready returns when it cannot reach the database. Verified against
# a real server at 200, 401, 404 and 503.
#
# The URL and the marker arrive as argv rather than as literals inside the
# source, which is what keeps the source free of quotes.
READINESS_COMMAND = (
    'python -c "'
    "import urllib.request as R,sys;"
    "R.HTTPErrorProcessor.http_response=lambda s,q,r:r;"
    "R.HTTPErrorProcessor.https_response=lambda s,q,r:r;"
    "print(sys.argv[2],R.urlopen(sys.argv[1],timeout=5).status)"
    '" http://127.0.0.1:{port}{path} ' + READINESS_MARKER
)
READINESS_EXECUTION_ATTEMPTS = 24
READINESS_EXECUTION_INTERVAL_SECONDS = 10

# --- peer reachability -------------------------------------------------------
# verify asks "is the process up, and can it reach its database", from inside
# the gateway's own container against 127.0.0.1. That question cannot reach
# this one: whether a *different* container on the shared Docker network can
# resolve the service by name and open a connection to it. Loopback readiness
# is true whether or not the container was ever attached to the shared network,
# so the two are independent, and only this one closes the gap between "the
# container is healthy" and "a caller can reach it".
#
# It is asked from a peer application rather than from the Actions runner
# because the runner is not on the applications' network, which is why every
# address-level probe run from there has been unable to answer.
PEER_TASK_NAME = "adapteng-peer-reachability-probe"
PEER_MARKER = "ADAPTENG_PEER"
# Same construction rules as READINESS_COMMAND, for the same reason: Coolify
# runs it as docker exec <container> sh -c '<command>' and escapes single
# quotes, so there is no single quote, no dollar and no backtick anywhere here,
# and it is one line.
#
# Three independent facts, in the order that makes a partial answer readable.
# gethostbyname proves DNS and reports the address it resolved. create_connection
# proves TCP to the declared internal port. The two status codes prove HTTP.
# Both paths are requested because they differ in what they touch, and neither
# reads the Authorization header, so no credential is presented and no model
# call can occur.
PEER_COMMAND = (
    'python -c "'
    "import socket,urllib.request as R,sys;"
    "R.HTTPErrorProcessor.http_response=lambda s,q,r:r;"
    "R.HTTPErrorProcessor.https_response=lambda s,q,r:r;"
    "a=socket.gethostbyname(sys.argv[1]);"
    "c=socket.create_connection((sys.argv[1],int(sys.argv[2])),5);"
    "c.close();"
    "print(sys.argv[5],a,R.urlopen(sys.argv[3],timeout=5).status,"
    "R.urlopen(sys.argv[4],timeout=5).status)"
    '" {host} {port} http://{host}:{port}/health http://{host}:{port}/ready '
    + PEER_MARKER
)


def peer_command(spec: dict) -> str:
    """Build the peer probe from the committed spec, not from guesses."""

    return PEER_COMMAND.format(
        host=spec["target"]["resource_name"],
        port=spec["network"]["internal_port"],
    )


# The first live peer-verify was refused: POST .../scheduled-tasks answered 500
# on the peer while the same endpoint had always accepted the readiness task on
# the gateway. Three things differ at once -- the application, the task name and
# the command -- so the failure on its own does not say which was refused, and a
# 500 carries no field-level reason to read.
#
# This ladder separates them by measurement instead of by guessing. Each rung is
# written to the same task, so exactly one task is created and the only variable
# that moves between rungs is the command. A rung that is accepted and a rung
# that is refused bracket the boundary; the first refusal names it.
#
# Nothing is ever armed here, so no command runs inside any container. This
# probes only what the API will accept, which is the question that was raised.
PEER_LADDER_TASK_NAME = "adapteng-peer-probe-acceptance"
PEER_LADDER_TRIVIAL = "echo ADAPTENG_PEER trivial"


def peer_ladder(spec: dict) -> list:
    """Rungs from trivially short to longer than the full probe, ascending.

    The inference this supports is "everything accepted is shorter than
    everything refused", which is only available if the rungs are monotone in
    length, so they are built to exact lengths rather than to whatever a
    convenient string happened to measure.
    """

    def filler(size: int) -> str:
        head = "echo ADAPTENG_PEER "
        return head + "x" * (size - len(head))

    return [
        ("trivial", filler(26)),
        # Known accepted on the gateway, so it separates "this application
        # refuses tasks" from "this command is refused".
        ("readiness-shaped", readiness_command(spec)),
        # Padding only: same prefix, no new characters, longer. A refusal of a
        # filler rung cannot be blamed on anything but its length.
        ("filler-300", filler(300)),
        ("peer-full", peer_command(spec)),
        ("filler-500", filler(500)),
    ]


def operate_peer_diagnose(client: Client, spec: dict) -> int:
    """Find what the scheduled-task endpoint will accept on the peer.

    Read-mostly: it creates one task, rewrites it a few times, and leaves it
    disarmed. It never arms anything, so nothing executes in any container.
    """

    target = spec["target"]
    peer_name = spec["network"]["peer_probe_application"]
    emit(f"--- peer-diagnose {peer_name}")

    project = find_project(client, target["project"])
    environment = (
        find_environment(client, project["uuid"], target["environment"])
        if project
        else None
    )
    if project is None or environment is None:
        emit("    project or environment absent; nothing to diagnose")
        return EXIT_FAILED

    present = applications_in(client, environment)
    peer = find_application(present, peer_name)
    service = find_application(present, target["resource_name"])
    if peer is None:
        emit(f"    peer application {peer_name}: ABSENT")
        return EXIT_FAILED

    peer_uuid = str(peer["uuid"])
    emit(f"    peer {peer_name}: uuid={peer_uuid} state={peer.get('status')}")
    if service is not None:
        emit(
            f"    service {target['resource_name']}: uuid={service['uuid']} "
            f"state={service.get('status')}"
        )

    existing = find_readiness_task(client, peer_uuid, PEER_LADDER_TASK_NAME)
    task_uuid = str(existing["uuid"]) if existing else None
    results = []

    for label, command in peer_ladder(spec):
        body = readiness_task_body(command, armed=False, name=PEER_LADDER_TASK_NAME)
        if task_uuid is None:
            status, parsed = client.request(
                "POST", f"/applications/{peer_uuid}/scheduled-tasks", body=body
            )
            verb = "POST"
        else:
            status, parsed = client.request(
                "PATCH",
                f"/applications/{peer_uuid}/scheduled-tasks/{task_uuid}",
                body=body,
            )
            verb = "PATCH"

        accepted = status in (200, 201)
        results.append((label, len(command), status, accepted))
        emit(
            f"    {verb:5} {label:16} len={len(command):4} -> HTTP {status} "
            f"{'accepted' if accepted else 'REFUSED: ' + api_message(parsed)}"
        )

        if accepted and task_uuid is None:
            found = find_readiness_task(client, peer_uuid, PEER_LADDER_TASK_NAME)
            if found is None:
                emit("    the task was accepted but cannot be read back; stopping")
                return EXIT_FAILED
            task_uuid = str(found["uuid"])

    emit("")
    if not any(accepted for _l, _n, _s, accepted in results):
        emit(
            "    every rung was refused, including a 26-character echo. The "
            "command is not what is being rejected: this application does not "
            "accept scheduled tasks at all, and the probe needs a different "
            "peer or a different mechanism."
        )
        return EXIT_FAILED

    accepted_lengths = [n for _l, n, _s, ok in results if ok]
    refused_lengths = [n for _l, n, _s, ok in results if not ok]
    emit(f"    accepted lengths: {sorted(accepted_lengths)}")
    emit(f"    refused lengths:  {sorted(refused_lengths)}")
    if refused_lengths and max(accepted_lengths) < min(refused_lengths):
        emit(
            f"    every accepted command is shorter than every refused one, so "
            f"the boundary is length and it lies between "
            f"{max(accepted_lengths)} and {min(refused_lengths)} characters."
        )
    elif refused_lengths:
        emit(
            "    the refusals do not sort by length, so length is not the "
            "boundary; the refused rungs differ from the accepted ones in "
            "content."
        )
    else:
        emit(
            "    every rung was accepted, including the full probe. The "
            "original 500 was therefore not reproducible from the command, "
            "and the earlier failure needs a different explanation."
        )

    at_rest = readiness_task_body(
        PEER_LADDER_TRIVIAL, armed=False, name=PEER_LADDER_TASK_NAME
    )
    client.request(
        "PATCH",
        f"/applications/{peer_uuid}/scheduled-tasks/{task_uuid}",
        body=at_rest,
    )
    emit(
        f"    left one disabled task {PEER_LADDER_TASK_NAME} on {peer_name}, "
        "scheduled for a leap day and holding a harmless echo. It was never "
        "armed, so it has never run."
    )
    return EXIT_OK


def readiness_command(spec: dict) -> str:
    """Build the in-container probe from the committed spec, not from guesses."""

    return READINESS_COMMAND.format(
        port=spec["network"]["internal_port"],
        path=READINESS_PATH,
    )


def find_readiness_task(
    client: Client, uuid: str, task_name: str = READINESS_TASK_NAME
) -> dict | None:
    tasks = call(client, "GET", f"/applications/{uuid}/scheduled-tasks")
    if not isinstance(tasks, list):
        raise Abort("the scheduled-task listing was not a JSON array")
    matches = [
        task
        for task in tasks
        if isinstance(task, dict) and task.get("name") == task_name
    ]
    if len(matches) > 1:
        raise Abort(
            f"{len(matches)} scheduled tasks are named {task_name}; "
            "an ambiguous match is not resolved by guessing"
        )
    return matches[0] if matches else None


def readiness_task_body(command: str, *, armed: bool, name: str = READINESS_TASK_NAME) -> dict:
    """The stored shape of the probe task, in one of its two states.

    Disarmed is the resting state and is what the task is left in: disabled,
    and scheduled for a date that occurs only in leap years. Armed is held for
    as long as it takes Coolify's scheduler to notice, and no longer.
    """

    return {
        "name": name,
        "command": command,
        "frequency": READINESS_ARMED_FREQUENCY if armed else READINESS_TASK_FREQUENCY,
        "timeout": READINESS_TASK_TIMEOUT_SECONDS,
        "enabled": bool(armed),
    }


def task_is_armed(task: dict) -> bool:
    return bool(task.get("enabled")) or task.get("frequency") == READINESS_ARMED_FREQUENCY


def write_readiness_task(
    client: Client,
    uuid: str,
    task_uuid: str,
    body: dict,
    task_name: str = READINESS_TASK_NAME,
) -> dict:
    """Write the task and read it back, because a write that did not hold is
    the difference between a probe and a guess."""

    call(
        client,
        "PATCH",
        f"/applications/{uuid}/scheduled-tasks/{task_uuid}",
        body=body,
        expect=(200, 201),
    )
    after = find_readiness_task(client, uuid, task_name)
    if after is None:
        raise Abort("the readiness task disappeared while it was being written")
    return after


def converge_readiness_task(
    client: Client,
    uuid: str,
    command: str,
    task_name: str = READINESS_TASK_NAME,
) -> tuple[str, str]:
    """Create the probe task disarmed, or bring an existing one back to rest.

    Running this first is what makes an interrupted earlier run self-healing:
    a task left armed by a run that died is disarmed here, before anything
    else happens. Deletion is not attempted and is not reachable, because the
    client refuses DELETE outright; the residue is one disabled task per
    application, disclosed in the output rather than hidden.
    """

    existing = find_readiness_task(client, uuid, task_name)
    body = readiness_task_body(command, armed=False, name=task_name)
    if existing is None:
        created = call(
            client,
            "POST",
            f"/applications/{uuid}/scheduled-tasks",
            body=body,
            expect=(200, 201),
        )
        task_uuid = str((created or {}).get("uuid") or "")
        if not task_uuid:
            raise Abort("the API accepted the task but returned no uuid")
        return task_uuid, "created"

    task_uuid = str(existing.get("uuid") or "")
    if not task_uuid:
        raise Abort("the existing readiness task has no uuid")
    was_armed = task_is_armed(existing)
    if existing.get("command") == command and not was_armed:
        return task_uuid, "already at rest"

    after = write_readiness_task(client, uuid, task_uuid, body, task_name)
    if after.get("command") != command or task_is_armed(after):
        raise Abort(
            "the readiness task did not come to rest after it was written; "
            "refusing to arm a task whose stored state is unknown"
        )
    return task_uuid, "disarmed and corrected" if was_armed else "converged"


def disarm_readiness_task(
    client: Client,
    uuid: str,
    task_uuid: str,
    command: str,
    task_name: str = READINESS_TASK_NAME,
) -> bool:
    """Return the task to rest, and say plainly whether it got there.

    This runs even when the probe failed, because the alternative is a job
    that keeps firing every minute. It re-reads rather than trusting the
    write, and it reports its own failure rather than swallowing it.
    """

    body = readiness_task_body(command, armed=False, name=task_name)
    for attempt in range(1, READINESS_DISARM_ATTEMPTS + 1):
        try:
            after = write_readiness_task(client, uuid, task_uuid, body, task_name)
        except Abort as failure:
            emit(f"    disarm attempt {attempt} failed: {failure}")
            continue
        if not task_is_armed(after):
            return True
        emit(f"    disarm attempt {attempt} did not take effect")
    return False


def list_executions(client: Client, uuid: str, task_uuid: str) -> list[dict]:
    executions = call(
        client,
        "GET",
        f"/applications/{uuid}/scheduled-tasks/{task_uuid}/executions",
    )
    if not isinstance(executions, list):
        raise Abort("the execution listing was not a JSON array")
    return [item for item in executions if isinstance(item, dict)]


def execution_identity(row: dict) -> tuple[str, str]:
    """Name an execution by the fields the instance actually populates.

    Not by id alone. The live instance returns every execution with id null,
    so an id comparison made all of them equal to each other and to the
    baseline: four successful probe runs were reported as no execution at all.
    created_at is the field that is actually filled in, and the pair degrades
    safely if either is missing.
    """

    return (str(row.get("id") or ""), str(row.get("created_at") or ""))


def execution_sort_key(row: dict) -> tuple[str, int]:
    try:
        numeric = int(row.get("id") or 0)
    except (TypeError, ValueError):
        numeric = 0
    return (str(row.get("created_at") or ""), numeric)


def executions_snapshot(rows: list[dict]) -> tuple[int, set[tuple[str, str]]]:
    """What was already there, as both a count and a set of identities.

    Two measures rather than one because either can be defeated alone: an
    instance that caps the retained history keeps the count flat while the
    identities change, and an instance that populates neither id nor created_at
    keeps the identities equal while the count grows.
    """

    return len(rows), {execution_identity(row) for row in rows}


def newest_execution(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return max(rows, key=execution_sort_key)


def read_marker(message: object, marker: str = READINESS_MARKER) -> str | None:
    """Pull the probe's own word out of the captured container output.

    The marker exists so that an empty message, a shell error, or any other
    output cannot be mistaken for a verdict. Absence is reported as absence.
    """

    if not isinstance(message, str):
        return None
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith(marker):
            remainder = stripped[len(marker) :].strip()
            return remainder or None
    return None


def await_probe_answer(
    client: Client,
    uuid: str,
    task_uuid: str,
    before: tuple[int, set[tuple[str, str]]],
    sleep,
    marker: str = READINESS_MARKER,
) -> tuple[dict | None, str | None, str]:
    """Wait for the scheduler to run the probe once, and read what it said.

    Novelty is decided by identity and by count. Deciding it by a rising id is
    what made the first live run report ready=undetermined reason=no_execution
    while the probe had in fact run four times and answered 200 each time --
    this operation committing, against itself, the exact conflation of "not
    ready" with "could not tell" that it was built to prevent.
    """

    before_count, before_identities = before
    for attempt in range(1, READINESS_EXECUTION_ATTEMPTS + 1):
        sleep(READINESS_EXECUTION_INTERVAL_SECONDS)
        rows = list_executions(client, uuid, task_uuid)
        fresh = [
            row for row in rows if execution_identity(row) not in before_identities
        ]
        if not fresh:
            if len(rows) > before_count:
                # Something ran, but nothing in the reply says which row it is.
                # Picking one would be a guess presented as a measurement, so
                # this reports the ambiguity instead of resolving it.
                return None, None, "unidentifiable_execution"
            continue
        execution = max(fresh, key=execution_sort_key)
        status = str(execution.get("status") or "")
        if status in {"running", "queued", ""}:
            continue
        emit(
            f"    execution at {execution.get('created_at')!r} finished after "
            f"{attempt} polls: status={status}"
        )
        return execution, read_marker(execution.get("message"), marker), ""
    return None, None, "no_execution"


def operate_verify(client: Client, spec: dict, sleep=None) -> int:
    """Ask the container itself whether it is ready, from inside the container.

    Every other vantage point has been ruled out by measurement rather than by
    assumption: the readiness runner is on a Docker network that contains the
    managed database but none of the applications, so it is not running inside
    any of them. Coolify's scheduled task runs its command as
    `docker exec <container> sh -c ...` and captures the output, which reaches
    inside without SSH to the host, a Docker socket on the runner, or any
    change to the network.

    The task cannot be triggered directly. This instance has no
    POST .../scheduled-tasks/{uuid}/execute route -- it answers the generic 404
    while its sibling /executions answers 401, which is how the absence was
    established without presenting a credential. So the probe is armed, left
    for Coolify's own scheduler to notice, and disarmed again. It rests
    disabled and scheduled for a leap day; both are removed to arm it and both
    are restored to disarm it, so neither the flag nor the schedule alone is
    load-bearing.

    No credential is presented and no model is called: /ready answers before
    the Authorization header is read, so this cannot produce an inference call.
    """

    import time

    sleep = sleep or time.sleep
    target = spec["target"]
    emit(f"--- verify {spec['service']}")
    project = find_project(client, target["project"])
    environment = (
        find_environment(client, project["uuid"], target["environment"])
        if project
        else None
    )
    if project is None or environment is None:
        emit("    project or environment absent; nothing to verify")
        emit("RESULT verify failed application=absent")
        return EXIT_FAILED

    application = find_application(
        applications_in(client, environment), target["resource_name"]
    )
    if application is None:
        emit(f"    application {target['resource_name']}: ABSENT")
        emit("RESULT verify failed application=absent")
        return EXIT_FAILED

    uuid = str(application["uuid"])
    emit(
        f"    application {target['resource_name']}: uuid={uuid} "
        f"state={application.get('status')}"
    )

    command = readiness_command(spec)
    task_uuid, disposition = converge_readiness_task(client, uuid, command)
    emit(f"    readiness task {READINESS_TASK_NAME}: {disposition} uuid={task_uuid}")

    rows_before = list_executions(client, uuid, task_uuid)
    before = executions_snapshot(rows_before)
    latest = newest_execution(rows_before)
    emit(
        f"    executions already recorded: {before[0]}"
        + (f", newest at {latest.get('created_at')!r}" if latest else "")
    )

    armed = write_readiness_task(
        client, uuid, task_uuid, readiness_task_body(command, armed=True)
    )
    if not task_is_armed(armed):
        raise Abort(
            "the readiness task did not arm; refusing to wait for an execution "
            "that cannot happen"
        )
    emit(
        f"    armed at {READINESS_ARMED_FREQUENCY} and waiting for Coolify's "
        "scheduler; it will be returned to rest either way"
    )

    try:
        execution, verdict, reason = await_probe_answer(
            client, uuid, task_uuid, before, sleep
        )
    finally:
        at_rest = disarm_readiness_task(client, uuid, task_uuid, command)
        if at_rest:
            emit(
                "    returned to rest: disabled, and scheduled for a leap day. "
                "It is left in place rather than removed because this tool "
                "cannot issue DELETE."
            )
        else:
            emit("")
            emit(
                f"    COULD NOT DISARM the readiness task {task_uuid} on "
                f"application {uuid}. It is still enabled at "
                f"{READINESS_ARMED_FREQUENCY} and will keep running the probe "
                "every minute until it is disabled. The probe is a loopback "
                "HTTP request and calls nothing external, but this needs a "
                "hand: PATCH /applications/"
                f"{uuid}/scheduled-tasks/{task_uuid} with enabled=false, or "
                "run this operation again, which disarms before it does "
                "anything else."
            )

    if not at_rest:
        emit("RESULT verify failed ready=undetermined reason=task_left_armed")
        return EXIT_FAILED

    if execution is None and reason == "unidentifiable_execution":
        emit("")
        emit(
            "    an execution appeared while the task was armed, but the reply "
            "carries neither an id nor a created_at that tells it apart from "
            "the ones already there. The probe ran; which row is its answer "
            "cannot be established, so no answer is reported. Guessing the "
            "newest-looking row would present a guess as a measurement."
        )
        emit(
            "RESULT verify failed ready=undetermined "
            "reason=unidentifiable_execution"
        )
        return EXIT_FAILED

    if execution is None:
        emit("")
        emit(
            "    no new execution was recorded while the task was armed. The "
            "probe did not run, so this says nothing about the gateway's "
            "readiness either way."
        )
        emit("RESULT verify failed ready=undetermined reason=no_execution")
        return EXIT_FAILED

    if verdict is None:
        emit("")
        emit(
            "    the execution finished but its output does not carry the "
            f"{READINESS_MARKER} marker, so the probe did not run to completion "
            "inside the container. Reporting undetermined rather than not-ready: "
            "a missing answer is not a negative answer."
        )
        emit(f"    captured output: {str(execution.get('message'))[:400]!r}")
        emit("RESULT verify failed ready=undetermined reason=no_marker")
        return EXIT_FAILED

    emit(f"    the container answered {READINESS_MARKER} {verdict}")
    if verdict != "200":
        emit("")
        emit(
            "    /ready did not return 200, and it is the gateway's own verdict "
            "on its dependencies rather than a network or placement problem: "
            "the probe ran inside the container and reached the process. /ready "
            "opens a database connection and answers 503 when it cannot; the "
            "service logs the reason and deliberately keeps it out of the body."
        )
        emit(f"RESULT verify failed ready=no answer={verdict}")
        return EXIT_FAILED

    emit("")
    emit(
        "    Readiness confirmed from inside the container. This is the "
        "database proof: /ready opens a database connection and /health does "
        "not. No credential was presented and no model was called, because both "
        "endpoints answer before the Authorization header is read."
    )
    emit("RESULT verify ok ready=yes answer=200")
    return EXIT_OK


def operate_peer_verify(client: Client, spec: dict, sleep=None) -> int:
    """Ask a peer container whether it can reach this service by name.

    This is the question ``verify`` cannot ask. ``verify`` runs inside the
    gateway and talks to 127.0.0.1, which answers the same way whether or not
    the container is attached to the shared Docker network. A caller lives in a
    different container, so the only faithful vantage point is a different
    container, and the probe is run from the peer named in the spec.

    Nothing is created and nothing is exposed: the peer already exists, no FQDN
    is assigned, and the probe is one disabled scheduled task that is armed for
    as long as it takes the scheduler to run it once and is then returned to
    rest, exactly like the readiness probe.
    """

    sleep = sleep or time.sleep
    target = spec["target"]
    peer_name = spec["network"]["peer_probe_application"]
    service_name = target["resource_name"]

    emit(f"--- peer-verify {service_name} from {peer_name}")

    project = find_project(client, target["project"])
    environment = (
        find_environment(client, project["uuid"], target["environment"])
        if project
        else None
    )
    if project is None or environment is None:
        emit("    project or environment absent; nothing to verify")
        emit("RESULT peer-verify failed application=absent")
        return EXIT_FAILED

    present = applications_in(client, environment)
    service = find_application(present, service_name)
    peer = find_application(present, peer_name)
    if service is None:
        emit(f"    application {service_name}: ABSENT")
        emit("RESULT peer-verify failed application=absent")
        return EXIT_FAILED
    if peer is None:
        emit(
            f"    peer application {peer_name}: ABSENT. The probe has nowhere "
            "to run from, which says nothing about reachability either way."
        )
        emit("RESULT peer-verify failed reachable=undetermined reason=peer_absent")
        return EXIT_FAILED

    peer_uuid = str(peer["uuid"])
    emit(f"    service {service_name}: state={service.get('status')}")
    emit(f"    peer {peer_name}: uuid={peer_uuid} state={peer.get('status')}")
    emit(
        f"    placement: service destination={destination_uuid_of(service)} "
        f"peer destination={destination_uuid_of(peer)}"
    )

    command = peer_command(spec)
    task_uuid, disposition = converge_readiness_task(
        client, peer_uuid, command, PEER_TASK_NAME
    )
    emit(f"    peer probe task {PEER_TASK_NAME}: {disposition} uuid={task_uuid}")

    before = executions_snapshot(list_executions(client, peer_uuid, task_uuid))
    emit(f"    executions already recorded: {before[0]}")

    armed = write_readiness_task(
        client,
        peer_uuid,
        task_uuid,
        readiness_task_body(command, armed=True, name=PEER_TASK_NAME),
        PEER_TASK_NAME,
    )
    if not task_is_armed(armed):
        raise Abort(
            "the peer probe task did not arm; refusing to wait for an execution "
            "that cannot happen"
        )
    emit(
        f"    armed at {READINESS_ARMED_FREQUENCY} and waiting for Coolify's "
        "scheduler; it will be returned to rest either way"
    )

    try:
        execution, verdict, reason = await_probe_answer(
            client, peer_uuid, task_uuid, before, sleep, PEER_MARKER
        )
    finally:
        at_rest = disarm_readiness_task(
            client, peer_uuid, task_uuid, command, PEER_TASK_NAME
        )
        if at_rest:
            emit("    returned to rest: disabled, and scheduled for a leap day.")
        else:
            emit(
                f"    COULD NOT DISARM the peer probe task {task_uuid} on "
                f"application {peer_uuid}. It is still enabled and will keep "
                "running every minute until it is disabled: PATCH /applications/"
                f"{peer_uuid}/scheduled-tasks/{task_uuid} with enabled=false, or "
                "run this operation again, which disarms before anything else."
            )

    if not at_rest:
        emit("RESULT peer-verify failed reachable=undetermined reason=task_left_armed")
        return EXIT_FAILED

    if execution is None:
        emit("")
        emit(
            "    no identifiable new execution was recorded while the task was "
            f"armed ({reason or 'no_execution'}). The probe did not demonstrably "
            "run, so this says nothing about reachability either way."
        )
        emit(
            "RESULT peer-verify failed reachable=undetermined "
            f"reason={reason or 'no_execution'}"
        )
        return EXIT_FAILED

    if verdict is None:
        emit("")
        emit(
            f"    the execution finished but carries no {PEER_MARKER} marker, so "
            "the probe did not run to completion inside the peer. Reporting "
            "undetermined rather than unreachable: a missing answer is not a "
            "negative answer, and a peer image without python would look exactly "
            "like this."
        )
        emit(f"    captured output: {str(execution.get('message'))[:600]!r}")
        emit("RESULT peer-verify failed reachable=undetermined reason=no_marker")
        return EXIT_FAILED

    fields = verdict.split()
    if len(fields) != 3 or fields[1] != "200" or fields[2] != "200":
        emit("")
        emit(
            f"    the peer answered {PEER_MARKER} {verdict}, which is not the "
            "shape of a fully successful probe (address, /health 200, /ready 200)."
        )
        emit(f"RESULT peer-verify failed reachable=no answer={verdict!r}")
        return EXIT_FAILED

    address, health_status, ready_status = fields
    emit("")
    emit(f"    DNS: {service_name} resolved from inside {peer_name} to {address}")
    emit(f"    TCP: connected to {service_name}:{spec['network']['internal_port']}")
    emit(f"    HTTP: /health {health_status}, /ready {ready_status}")
    emit(
        "    Peer reachability confirmed. A different container on the shared "
        "network resolved this service by name, opened a TCP connection to its "
        "internal port, and got an answer from both endpoints. No public route "
        "was used or created, no credential was presented, and no model was "
        "called: both paths answer before the Authorization header is read."
    )
    emit(
        f"RESULT peer-verify ok reachable=yes address={address} "
        f"health={health_status} ready={ready_status}"
    )
    return EXIT_OK


DIAGNOSE_LOG_LINES = 200


def container_logs(client: Client, uuid: str) -> tuple[str | None, str]:
    """Read the container's own account of itself, or say why it cannot be read.

    The endpoint answers 400 "Application is not running." when no container is
    up, which is a finding rather than an error: it is the same condition
    Coolify's scheduler checks before it dispatches a task, so a 400 here
    explains a probe that never ran without needing a second query.
    """

    status, body = client.request(
        "GET",
        f"/applications/{uuid}/logs",
        query={"lines": str(DIAGNOSE_LOG_LINES), "show_timestamps": "true"},
    )
    if status == 400:
        message = (body or {}).get("message") if isinstance(body, dict) else None
        return None, str(message or "the API refused with 400")
    if status >= 400:
        return None, f"the API answered {status}"
    logs = body.get("logs") if isinstance(body, dict) else None
    if not isinstance(logs, str):
        return None, "the reply carried no logs field"
    return logs, ""


def operate_diagnose(client: Client, spec: dict) -> int:
    """Report what the instance says about this application, and change nothing.

    This exists because the readiness probe produced no execution, and "no
    execution" has several causes that are indistinguishable from outside: the
    container is not running, the server is not functional, or the scheduler
    did not dispatch. Coolify skips a task silently in the first two cases, so
    the absence of an execution is not evidence for any one of them.

    Every call here is a GET. Nothing is created, armed or written.
    """

    target = spec["target"]
    emit(f"--- diagnose {spec['service']}")
    project = find_project(client, target["project"])
    environment = (
        find_environment(client, project["uuid"], target["environment"])
        if project
        else None
    )
    if project is None or environment is None:
        emit("    project or environment absent; nothing to diagnose")
        emit("RESULT diagnose failed application=absent")
        return EXIT_FAILED

    application = find_application(
        applications_in(client, environment), target["resource_name"]
    )
    if application is None:
        emit(f"    application {target['resource_name']}: ABSENT")
        emit("RESULT diagnose failed application=absent")
        return EXIT_FAILED

    uuid = str(application["uuid"])
    state = str(application.get("status") or "")
    emit(f"    application {target['resource_name']}: uuid={uuid} state={state!r}")
    # This is the exact predicate Coolify applies before dispatching a task:
    # str($task->application->status)->contains('running'). Reproducing it here
    # turns a silent skip into a stated reason.
    dispatchable = "running" in state
    emit(
        f"    scheduler would dispatch a task for it: {dispatchable} "
        "(it requires the status to contain 'running')"
    )

    tasks = call(client, "GET", f"/applications/{uuid}/scheduled-tasks")
    entries = [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []
    emit(f"    scheduled tasks: {len(entries)}")
    for task in entries:
        task_uuid = str(task.get("uuid") or "")
        emit(
            f"      {task.get('name')!r} uuid={task_uuid} "
            f"enabled={task.get('enabled')} frequency={task.get('frequency')!r}"
        )
        executions = call(
            client,
            "GET",
            f"/applications/{uuid}/scheduled-tasks/{task_uuid}/executions",
        )
        rows = [item for item in executions if isinstance(item, dict)] if isinstance(executions, list) else []
        emit(f"        executions ever recorded: {len(rows)}")
        for row in rows[-5:]:
            body, masked = redact_foreign_text(
                str(row.get("message") or ""), known=(uuid, task_uuid)
            )
            emit(
                f"        id={row.get('id')} status={row.get('status')!r} "
                f"at={row.get('created_at')!r} message={body[:300]!r}"
                + (f" ({masked} spans masked)" if masked else "")
            )

    emit("    container logs:")
    logs, refusal = container_logs(client, uuid)
    if logs is None:
        emit(f"      unavailable: {refusal}")
        if "not running" in refusal.lower():
            emit(
                "      that is the whole answer to the missing execution: "
                "Coolify skips a scheduled task whose application is not "
                "running, and it skips it silently."
            )
    else:
        cleaned, masked = redact_foreign_text(logs, known=(uuid,))
        lines = [line for line in cleaned.splitlines() if line.strip()]
        emit(f"      {len(lines)} non-empty lines, {masked} credential-shaped spans masked")
        for line in lines[-60:]:
            emit(f"      | {line[:300]}")

    emit("RESULT diagnose ok")
    return EXIT_OK


def operate_status(client: Client, spec: dict) -> int:
    target = spec["target"]
    emit(f"--- status {spec['service']}")
    project = find_project(client, target["project"])
    environment = find_environment(client, project["uuid"], target["environment"]) if project else None
    report_placement(spec, project, environment)
    if project is None or environment is None:
        emit("")
        emit("RESULT status ok application=absent")
        return EXIT_OK

    application = find_application(
        applications_in(client, environment), target["resource_name"]
    )
    if application is None:
        emit(f"    application {target['resource_name']}: ABSENT")
        emit("")
        emit("RESULT status ok application=absent")
        return EXIT_OK

    uuid = str(application["uuid"])
    health = spec["health_check"]
    emit(f"    application {target['resource_name']}: uuid={uuid} state={application.get('status')}")
    emit(
        f"    declared health check: {health['method']} {health['scheme']}://"
        f"<container>:{spec['network']['internal_port']}{health['path']} "
        f"expecting {health['return_code']}"
    )
    emit(f"    stored health check enabled={application.get('health_check_enabled')}")
    emit(f"    fqdn: {'present' if (application.get('fqdn') or '').strip() else 'none, as declared'}")

    latest = newest_deployment(client, uuid)
    if latest is None:
        emit("    deployments: none recorded")
        emit("")
        emit(f"RESULT status ok application_state={application.get('status')} deployment=none")
        return EXIT_OK

    emit(
        f"    newest deployment {latest.get('deployment_uuid')} "
        f"state={latest.get('status')} created={latest.get('created_at')}"
    )
    emit("")
    emit(
        f"RESULT status ok application_state={application.get('status')} "
        f"deployment={latest.get('deployment_uuid')} deployment_state={latest.get('status')}"
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def positive_integer(environ: dict, name: str, default: int) -> int:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise Abort(f"{name} must be a whole number of seconds", EXIT_MISCONFIGURED) from error
    if value <= 0:
        raise Abort(f"{name} must be greater than zero", EXIT_MISCONFIGURED)
    return value


def run(environ: dict) -> int:
    operation = (environ.get(OPERATION_VARIABLE) or "").strip().lower()
    if operation not in OPERATIONS:
        raise Abort(
            f"{OPERATION_VARIABLE} must be one of {list(OPERATIONS)}, received {operation!r}",
            EXIT_MISCONFIGURED,
        )

    service = (environ.get(SERVICE_VARIABLE) or "").strip().lower()
    spec = load_spec(spec_path(service))

    base_url = (environ.get(BASE_URL_VARIABLE) or "").strip().rstrip("/")
    if not base_url:
        raise Abort(f"{BASE_URL_VARIABLE} is empty; there is nothing to call", EXIT_MISCONFIGURED)
    if not base_url.lower().startswith("https://"):
        raise Abort(
            f"{BASE_URL_VARIABLE} must be an https address so the access value is "
            "not sent in the clear",
            EXIT_MISCONFIGURED,
        )

    credential = (environ.get(CREDENTIAL_VARIABLE) or "").strip()
    if not credential:
        raise Abort(
            f"{CREDENTIAL_VARIABLE} is empty; nothing can be read or written",
            EXIT_MISCONFIGURED,
        )
    register_redaction(credential)

    # Gathered for every operation so that a supplied value is masked in the log
    # even when this run will not write it. Only reconcile applies them: deploy
    # deliberately re-reads the stored state instead, so its readiness check
    # answers whether the resource is configured, not whether this run was
    # handed the values.
    supplied = supplied_values(spec, environ)

    client = Client(base_url, credential)
    emit(f"operation={operation} service={spec['service']} api={base_url}")

    if operation == "inspect":
        return operate_inspect(client, spec)
    if operation == "reconcile":
        return operate_reconcile(client, spec, supplied)
    if operation == "status":
        return operate_status(client, spec)
    if operation == "verify":
        return operate_verify(client, spec)
    if operation == "peer-verify":
        return operate_peer_verify(client, spec)
    if operation == "peer-diagnose":
        return operate_peer_diagnose(client, spec)
    if operation == "diagnose":
        return operate_diagnose(client, spec)
    return operate_deploy(
        client,
        spec,
        poll_seconds=positive_integer(environ, POLL_VARIABLE, DEFAULT_POLL_SECONDS),
        timeout_seconds=positive_integer(environ, TIMEOUT_VARIABLE, DEFAULT_TIMEOUT_SECONDS),
    )


def main() -> int:
    try:
        return run(os.environ)
    except Abort as abort:
        emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main())
