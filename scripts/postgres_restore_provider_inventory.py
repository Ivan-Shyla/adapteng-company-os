#!/usr/bin/env python3
"""Read and validate current Hetzner restore-host firewall state."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROVIDER_MANIFEST = SCRIPT_DIR / "postgres_restore_provider_manifest.json"
API_ROOT = "https://api.hetzner.cloud/v1"
LOCKED_FIREWALL_NAME = "pg-restore-locked"
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
SHA256 = __import__("re").compile(r"^[0-9a-f]{64}$")
OPERATION_PHASES = {
    "TARGET_START_RECOVERY",
    "TARGET_START_FINAL",
    "PRE_SQL",
    "POST_SQL",
}
PUBLIC_NET_KEYS = {"ipv4", "ipv6", "floating_ips", "firewalls"}
FORBIDDEN_ATTACHMENT_KEYS = {
    "network",
    "networks",
    "private_network",
    "private_networks",
    "privateNet",
}


class ProviderInventoryError(RuntimeError):
    """Fail-closed provider inventory error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def expected_locked_rules(owner_ssh_cidr: str) -> list[dict[str, Any]]:
    return [
        {
            "description": "owner-ssh-only",
            "destination_ips": [],
            "direction": "in",
            "port": "22",
            "protocol": "tcp",
            "source_ips": [owner_ssh_cidr],
        },
        {
            "description": "deny-real-egress-test-net-only",
            "destination_ips": ["192.0.2.1/32"],
            "direction": "out",
            "port": "9",
            "protocol": "tcp",
            "source_ips": [],
        },
    ]


def validate_operation_request(
    operation: Any, generation: str
) -> dict[str, str]:
    required = {
        "schema_version",
        "operation_id",
        "challenge_sha256",
        "generation",
        "phase",
        "target_container_id_sha256",
        "target_image_identity_sha256",
        "requested_at",
    }
    if not isinstance(operation, dict) or set(operation) != required:
        raise ProviderInventoryError("provider operation request fields are not exact")
    if (
        operation["schema_version"] != 1
        or operation["generation"] != generation
        or operation["phase"] not in OPERATION_PHASES
        or not SHA256.fullmatch(str(operation["operation_id"]))
        or not SHA256.fullmatch(str(operation["challenge_sha256"]))
        or not SHA256.fullmatch(str(operation["target_container_id_sha256"]))
        or not SHA256.fullmatch(str(operation["target_image_identity_sha256"]))
    ):
        raise ProviderInventoryError("provider operation request binding is not exact")
    try:
        datetime.strptime(str(operation["requested_at"]), TIMESTAMP)
    except ValueError as exc:
        raise ProviderInventoryError(
            "provider operation request timestamp is invalid"
        ) from exc
    return {key: str(operation[key]) for key in required if key != "schema_version"}


def evaluate_provider_state(
    server: dict[str, Any],
    firewalls: list[dict[str, Any]],
    *,
    generation: str,
    observed_at: datetime,
    owner_ssh_cidr: str,
    operation: dict[str, Any],
    broker_response_sha256: str = "0" * 64,
    broker_id: str = "company-os-hetzner-inventory-broker",
    broker_version: int = 1,
) -> dict[str, Any]:
    if generation not in {"A", "B", "C"}:
        raise ProviderInventoryError("generation must be A, B or C")
    labels = server.get("labels")
    if not isinstance(labels, dict) or labels.get("purpose") != (
        "postgres-restore-rehearsal"
    ):
        raise ProviderInventoryError("server purpose label is not exact")
    if labels.get("generation") != generation:
        raise ProviderInventoryError("server generation label is not exact")
    if server.get("name") != f"pg-restore-{generation.lower()}":
        raise ProviderInventoryError("server name is not exact")
    server_id = server.get("id")
    if not isinstance(server_id, int) or server_id <= 0:
        raise ProviderInventoryError("server ID is invalid")

    if any(key in server for key in FORBIDDEN_ATTACHMENT_KEYS):
        raise ProviderInventoryError("server contains an ambiguous network attachment field")
    private_net = server.get("private_net")
    if not isinstance(private_net, list) or private_net:
        raise ProviderInventoryError("server private-network attachment state is not empty")
    public_net = server.get("public_net")
    if not isinstance(public_net, dict) or set(public_net) != PUBLIC_NET_KEYS:
        raise ProviderInventoryError("server public-network attachment shape is not exact")
    floating_ips = public_net.get("floating_ips")
    if not isinstance(floating_ips, list) or floating_ips:
        raise ProviderInventoryError("server has an unexpected floating IP attachment")
    operation_binding = validate_operation_request(operation, generation)
    if (
        not SHA256.fullmatch(broker_response_sha256)
        or broker_id != "company-os-hetzner-inventory-broker"
        or broker_version != 1
    ):
        raise ProviderInventoryError("provider broker identity is not exact")

    matching = [
        firewall
        for firewall in firewalls
        if firewall.get("name") == LOCKED_FIREWALL_NAME
    ]
    if len(matching) != 1:
        raise ProviderInventoryError("exactly one locked firewall is required")
    firewall = matching[0]
    firewall_id = firewall.get("id")
    if not isinstance(firewall_id, int) or firewall_id <= 0:
        raise ProviderInventoryError("locked firewall ID is invalid")
    applied_to = firewall.get("applied_to")
    if not isinstance(applied_to, list):
        raise ProviderInventoryError("locked firewall attachment inventory is missing")
    if (
        len(applied_to) != 1
        or not isinstance(applied_to[0], dict)
        or set(applied_to[0]) != {"type", "server"}
        or applied_to[0].get("type") != "server"
        or not isinstance(applied_to[0].get("server"), dict)
        or set(applied_to[0]["server"]) != {"id"}
        or applied_to[0]["server"].get("id") != server_id
    ):
        raise ProviderInventoryError("locked firewall is not attached only to this server")

    rules = firewall.get("rules")
    if rules != expected_locked_rules(owner_ssh_cidr):
        raise ProviderInventoryError("locked firewall policy is not exact")
    server_firewalls = public_net.get("firewalls")
    if not isinstance(server_firewalls, list):
        raise ProviderInventoryError("server firewall attachment state is missing")
    if (
        len(server_firewalls) != 1
        or not isinstance(server_firewalls[0], dict)
        or server_firewalls[0].get("id") != firewall_id
        or server_firewalls[0].get("status") != "applied"
    ):
        raise ProviderInventoryError("server has an unexpected or unapplied firewall")

    policy_sha256 = hashlib.sha256(canonical_json(rules)).hexdigest()
    return {
        "schema_version": 2,
        "collector_id": "company-os-hetzner-locked-inventory",
        "collector_version": 2,
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": "LOCKED_CURRENT",
        "generation": generation,
        "observed_at": observed_at.astimezone(timezone.utc).strftime(TIMESTAMP),
        "server_ref_sha256": hashlib.sha256(str(server_id).encode()).hexdigest(),
        "firewall_ref_sha256": hashlib.sha256(str(firewall_id).encode()).hexdigest(),
        "locked_policy_sha256": policy_sha256,
        "owner_ssh_cidr_sha256": hashlib.sha256(owner_ssh_cidr.encode()).hexdigest(),
        "locked_firewall_attached": True,
        "other_firewalls_attached": 0,
        "private_networks_attached": 0,
        "private_network_inventory_sha256": hashlib.sha256(
            canonical_json([])
        ).hexdigest(),
        "floating_ips_attached": 0,
        "broker_id": broker_id,
        "broker_version": broker_version,
        "broker_response_sha256": broker_response_sha256,
        **operation_binding,
    }


def memfd(payload: bytes, label: str) -> int:
    if not hasattr(os, "memfd_create"):
        raise ProviderInventoryError("provider broker verification requires memfd_create")
    descriptor = os.memfd_create(label, flags=0)
    os.write(descriptor, payload)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor


def verify_broker_signature(payload: bytes, signature: bytes, public_key: bytes) -> None:
    descriptors = [
        memfd(public_key, "provider-public-key"),
        memfd(payload, "provider-broker-response"),
        memfd(signature, "provider-broker-signature"),
    ]
    try:
        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                f"/proc/self/fd/{descriptors[0]}",
                "-rawin",
                "-in",
                f"/proc/self/fd/{descriptors[1]}",
                "-sigfile",
                f"/proc/self/fd/{descriptors[2]}",
            ],
            check=True,
            capture_output=True,
            pass_fds=tuple(descriptors),
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def evaluate_broker_response(
    response_bytes: bytes,
    request_bytes: bytes,
    manifest: dict[str, Any],
    *,
    now: datetime,
    verify: Any = verify_broker_signature,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime, str]:
    response = json.loads(response_bytes)
    required = {
        "schema_version",
        "broker_id",
        "broker_version",
        "request_sha256",
        "observed_at",
        "server",
        "firewalls",
        "account_context_sha256",
        "signature_base64",
    }
    if (
        not isinstance(response, dict)
        or set(response) != required
        or canonical_json(response) != response_bytes
    ):
        raise ProviderInventoryError("provider broker response fields are not exact")
    signed = {key: response[key] for key in required - {"signature_base64"}}
    signed_bytes = canonical_json(signed)
    if (
        response["schema_version"] != 1
        or response["broker_id"] != manifest.get("broker_id")
        or response["broker_version"] != manifest.get("broker_version")
        or response["account_context_sha256"]
        != manifest.get("account_context_sha256")
        or response["request_sha256"] != hashlib.sha256(request_bytes).hexdigest()
        or not isinstance(response["server"], dict)
        or not isinstance(response["firewalls"], list)
        or not all(isinstance(item, dict) for item in response["firewalls"])
    ):
        raise ProviderInventoryError("provider broker response binding is not exact")
    try:
        signature = base64.b64decode(
            str(response["signature_base64"]), validate=True
        )
        observed = datetime.strptime(
            str(response["observed_at"]), TIMESTAMP
        ).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ProviderInventoryError("provider broker signature/time is malformed") from exc
    age = now.astimezone(timezone.utc) - observed
    if age.total_seconds() < 0 or age.total_seconds() > 10:
        raise ProviderInventoryError("provider broker response is not current")
    verify(
        signed_bytes,
        signature,
        str(manifest["public_key_pem"]).encode("utf-8"),
    )
    return (
        response["server"],
        response["firewalls"],
        observed,
        hashlib.sha256(signed_bytes).hexdigest(),
    )


def read_capability_fd(descriptor: int, label: str, maximum: int) -> bytes:
    if descriptor < 3:
        raise ProviderInventoryError(f"{label} descriptor is not explicit")
    payload = bytearray()
    while chunk := os.read(descriptor, 65536):
        payload.extend(chunk)
        if len(payload) > maximum:
            raise ProviderInventoryError(f"{label} exceeds its maximum size")
    if not payload:
        raise ProviderInventoryError(f"{label} is empty")
    return bytes(payload)


def write_exact_fd(descriptor: int, payload: bytes, label: str) -> None:
    if descriptor < 3:
        raise ProviderInventoryError(f"{label} descriptor is not explicit")
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise ProviderInventoryError(f"{label} write is incomplete")
        offset += written


def read_exact_fd(descriptor: int, length: int, label: str) -> bytes:
    payload = bytearray()
    while len(payload) < length:
        chunk = os.read(descriptor, length - len(payload))
        if not chunk:
            raise ProviderInventoryError(f"{label} is incomplete")
        payload.extend(chunk)
    return bytes(payload)


def write_framed_fd(descriptor: int, payload: bytes, label: str) -> None:
    if not payload or len(payload) > 4 * 1024 * 1024:
        raise ProviderInventoryError(f"{label} length is invalid")
    write_exact_fd(descriptor, struct.pack("!I", len(payload)) + payload, label)


def read_framed_fd(descriptor: int, label: str, maximum: int) -> bytes:
    length = struct.unpack("!I", read_exact_fd(descriptor, 4, label))[0]
    if length <= 0 or length > maximum:
        raise ProviderInventoryError(f"{label} length is invalid")
    return read_exact_fd(descriptor, length, label)


def read_optional_framed_fd(
    descriptor: int, label: str, maximum: int
) -> bytes | None:
    first = os.read(descriptor, 4)
    if not first:
        return None
    header = first + read_exact_fd(descriptor, 4 - len(first), label)
    length = struct.unpack("!I", header)[0]
    if length <= 0 or length > maximum:
        raise ProviderInventoryError(f"{label} length is invalid")
    return read_exact_fd(descriptor, length, label)


def provider_api_get(path: str, provider_capability: bytes) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        headers={
            "Authorization": "Bearer " + provider_capability.decode("ascii"),
            "Accept": "application/json",
            "User-Agent": "adapteng-company-os-restore-broker/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ProviderInventoryError("provider broker API request failed") from exc
    if not isinstance(payload, dict):
        raise ProviderInventoryError("provider broker API response is not an object")
    return payload


def sign_broker_payload(payload: bytes, signing_capability: bytes) -> bytes:
    descriptors = [
        memfd(signing_capability, "provider-private-key"),
        memfd(payload, "provider-signed-inventory"),
    ]
    try:
        return subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                f"/proc/self/fd/{descriptors[0]}",
                "-rawin",
                "-in",
                f"/proc/self/fd/{descriptors[1]}",
            ],
            check=True,
            capture_output=True,
            pass_fds=tuple(descriptors),
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        ).stdout
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def broker_response(
    request_bytes: bytes,
    provider_capability: bytes,
    signing_capability: bytes,
    pinned_config: dict[str, Any],
) -> bytes:
    request = json.loads(request_bytes)
    required = {
        "schema_version",
        "generation",
        "owner_ssh_cidr_sha256",
        "operation",
    }
    if (
        not isinstance(request, dict)
        or set(request) != required
        or canonical_json(request) != request_bytes
        or request["schema_version"] != 1
        or request["generation"] not in {"A", "B", "C"}
        or not SHA256.fullmatch(str(request["owner_ssh_cidr_sha256"]))
    ):
        raise ProviderInventoryError("provider broker request is not exact")
    validate_operation_request(request["operation"], str(request["generation"]))
    config_fields = {
        "schema_version",
        "account_context_sha256",
        "server_ids",
        "firewall_id",
    }
    server_ids = pinned_config.get("server_ids")
    if (
        not isinstance(pinned_config, dict)
        or set(pinned_config) != config_fields
        or pinned_config.get("schema_version") != 1
        or not SHA256.fullmatch(str(pinned_config.get("account_context_sha256")))
        or not isinstance(server_ids, dict)
        or set(server_ids) != {"A", "B", "C"}
        or not all(isinstance(value, int) and value > 0 for value in server_ids.values())
        or not isinstance(pinned_config.get("firewall_id"), int)
        or int(pinned_config["firewall_id"]) <= 0
    ):
        raise ProviderInventoryError("pinned provider target configuration is not exact")
    server_id = int(server_ids[str(request["generation"])])
    server_payload = provider_api_get(
        f"/servers/{server_id}", provider_capability
    )
    firewall_payload = provider_api_get(
        "/firewalls?name="
        + urllib.parse.quote(LOCKED_FIREWALL_NAME, safe=""),
        provider_capability,
    )
    server = server_payload.get("server")
    firewalls = firewall_payload.get("firewalls")
    if not isinstance(server, dict) or not isinstance(firewalls, list):
        raise ProviderInventoryError("provider broker inventory response shape is invalid")
    if (
        server.get("id") != server_id
        or len(firewalls) != 1
        or firewalls[0].get("id") != pinned_config["firewall_id"]
    ):
        raise ProviderInventoryError("provider response does not match pinned targets")
    signed = {
        "schema_version": 1,
        "broker_id": "company-os-hetzner-inventory-broker",
        "broker_version": 1,
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "observed_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime(TIMESTAMP),
        "server": server,
        "firewalls": firewalls,
        "account_context_sha256": pinned_config["account_context_sha256"],
    }
    signature = sign_broker_payload(
        canonical_json(signed), signing_capability
    )
    return canonical_json(
        {
            **signed,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }
    )


def supervise_broker(
    request_descriptor: int,
    response_descriptor: int,
    provider_descriptor: int,
    signing_descriptor: int,
    config_descriptor: int,
) -> None:
    manifest = json.loads(
        secure_read(PROVIDER_MANIFEST, "provider manifest", restricted=False)
    )
    collector_descriptor = os.open(Path(__file__), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        collector = read_capability_fd(
            collector_descriptor, "provider collector", 1024 * 1024
        )
        if hashlib.sha256(collector).hexdigest() != manifest.get("collector_sha256"):
            raise ProviderInventoryError("provider broker executable is not sealed")
        for _ in range(64):
            request = read_optional_framed_fd(
                request_descriptor, "supervised provider request", 1024 * 1024
            )
            if request is None:
                return
            request_fd = memfd(
                struct.pack("!I", len(request)) + request,
                "one-shot-provider-request",
            )
            response_fd = memfd(b"\0\0\0\0", "one-shot-provider-response")
            os.ftruncate(response_fd, 0)
            os.lseek(response_fd, 0, os.SEEK_SET)
            for descriptor in (
                collector_descriptor,
                provider_descriptor,
                signing_descriptor,
                config_descriptor,
            ):
                os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        f"/proc/self/fd/{collector_descriptor}",
                        "broker-once",
                        "--request-fd",
                        str(request_fd),
                        "--response-fd",
                        str(response_fd),
                        "--token-fd",
                        str(provider_descriptor),
                        "--signing-key-fd",
                        str(signing_descriptor),
                        "--provider-config-fd",
                        str(config_descriptor),
                    ],
                    check=True,
                    capture_output=True,
                    pass_fds=(
                        collector_descriptor,
                        request_fd,
                        response_fd,
                        provider_descriptor,
                        signing_descriptor,
                        config_descriptor,
                    ),
                    env={
                        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                        "LANG": "C",
                        "LC_ALL": "C",
                    },
                    timeout=20,
                )
                if completed.stdout or completed.stderr:
                    raise ProviderInventoryError(
                        "one-shot provider child emitted unexpected diagnostics"
                    )
                os.lseek(response_fd, 0, os.SEEK_SET)
                response = read_framed_fd(
                    response_fd, "one-shot provider child response", 4 * 1024 * 1024
                )
                if os.read(response_fd, 1):
                    raise ProviderInventoryError(
                        "one-shot provider child emitted a second response"
                    )
                write_framed_fd(
                    response_descriptor, response, "supervised provider response"
                )
            finally:
                os.close(request_fd)
                os.close(response_fd)
        raise ProviderInventoryError("provider supervisor operation limit exceeded")
    finally:
        os.close(collector_descriptor)


def secure_read(path: Path, label: str, *, restricted: bool = True) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProviderInventoryError(f"{label} cannot be opened: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        forbidden_mode = 0o077 if restricted else 0o022
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & forbidden_mode
        ):
            requirement = "mode 0600" if restricted else "not group/world writable"
            raise ProviderInventoryError(
                f"{label} must be root-owned and {requirement}"
            )
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    value = b"".join(chunks).decode("utf-8").strip()
    if not value:
        raise ProviderInventoryError(f"{label} is empty")
    return value


def secure_read_fd(descriptor: int, label: str) -> str:
    info = os.fstat(descriptor)
    if (
        descriptor < 3
        or os.geteuid() != 0
        or info.st_uid != 0
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ProviderInventoryError(
            f"{label} descriptor must be root-owned mode 0600"
        )
    os.lseek(descriptor, 0, os.SEEK_SET)
    value = read_capability_fd(descriptor, label, 1024 * 1024).decode(
        "utf-8"
    ).strip()
    if not value:
        raise ProviderInventoryError(f"{label} is empty")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_subparsers(dest="mode", required=True)
    collect = modes.add_parser("collect")
    collect.add_argument("generation", choices=("A", "B", "C"))
    collect.add_argument("--owner-cidr-fd", required=True, type=int)
    collect.add_argument("--operation-request-fd", required=True, type=int)
    collect.add_argument("--broker-request-fd", required=True, type=int)
    collect.add_argument("--broker-response-fd", required=True, type=int)
    broker = modes.add_parser("broker-once")
    broker.add_argument("--request-fd", required=True, type=int)
    broker.add_argument("--response-fd", required=True, type=int)
    broker.add_argument("--token-fd", required=True, type=int)
    broker.add_argument("--signing-key-fd", required=True, type=int)
    broker.add_argument("--provider-config-fd", required=True, type=int)
    supervisor = modes.add_parser("broker-supervisor")
    supervisor.add_argument("--request-fd", required=True, type=int)
    supervisor.add_argument("--response-fd", required=True, type=int)
    supervisor.add_argument("--token-fd", required=True, type=int)
    supervisor.add_argument("--signing-key-fd", required=True, type=int)
    supervisor.add_argument("--provider-config-fd", required=True, type=int)
    args = parser.parse_args()
    try:
        if args.mode == "broker-supervisor":
            supervise_broker(
                args.request_fd,
                args.response_fd,
                args.token_fd,
                args.signing_key_fd,
                args.provider_config_fd,
            )
            return 0
        if args.mode == "broker-once":
            provider_capability = read_capability_fd(
                args.token_fd, "provider token", 4096
            ).strip()
            signing_capability = read_capability_fd(
                args.signing_key_fd, "provider signing key", 65536
            )
            pinned_config_bytes = read_capability_fd(
                args.provider_config_fd, "provider target configuration", 4096
            )
            pinned_config = json.loads(pinned_config_bytes)
            broker_manifest = json.loads(
                secure_read(PROVIDER_MANIFEST, "provider manifest", restricted=False)
            )
            if (
                canonical_json(pinned_config) != pinned_config_bytes
                or hashlib.sha256(pinned_config_bytes).hexdigest()
                != broker_manifest.get("provider_target_config_sha256")
                or pinned_config.get("account_context_sha256")
                != broker_manifest.get("account_context_sha256")
            ):
                raise ProviderInventoryError(
                    "provider target configuration is not canonical"
                )
            request_bytes = read_framed_fd(
                args.request_fd, "one-shot provider request", 1024 * 1024
            )
            response = broker_response(
                request_bytes,
                provider_capability,
                signing_capability,
                pinned_config,
            )
            write_framed_fd(args.response_fd, response, "one-shot provider response")
            return 0
        manifest = json.loads(
            secure_read(PROVIDER_MANIFEST, "provider manifest", restricted=False)
        )
        if (
            not isinstance(manifest, dict)
            or manifest.get("status") != "APPROVED"
            or manifest.get("schema_version") != 3
        ):
            raise ProviderInventoryError("provider manifest is NOT_CONFIGURED")
        operation = json.loads(
            secure_read_fd(args.operation_request_fd, "provider operation request")
        )
        owner_ssh_cidr = secure_read_fd(
            args.owner_cidr_fd, "owner SSH CIDR capability"
        )
        broker_request = canonical_json(
            {
                "schema_version": 1,
                "generation": args.generation,
                "owner_ssh_cidr_sha256": hashlib.sha256(
                    owner_ssh_cidr.encode("ascii")
                ).hexdigest(),
                "operation": operation,
            }
        )
        write_framed_fd(
            args.broker_request_fd, broker_request, "one-shot broker request"
        )
        response = read_framed_fd(
            args.broker_response_fd, "one-shot broker response", 4 * 1024 * 1024
        )
        server, firewalls, observed_at, broker_response_sha256 = evaluate_broker_response(
            response,
            broker_request,
            manifest,
            now=datetime.now(timezone.utc).replace(microsecond=0),
        )
        packet = evaluate_provider_state(
            server,
            firewalls,
            generation=args.generation,
            observed_at=observed_at,
            owner_ssh_cidr=owner_ssh_cidr,
            operation=operation,
            broker_response_sha256=broker_response_sha256,
            broker_id=str(manifest["broker_id"]),
            broker_version=int(manifest["broker_version"]),
        )
        sys.stdout.buffer.write(canonical_json(packet))
        return 0
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        ProviderInventoryError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
