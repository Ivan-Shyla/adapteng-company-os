#!/usr/bin/env python3
"""Read and validate current Hetzner restore-host firewall state."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API_ROOT = "https://api.hetzner.cloud/v1"
TOKEN_PATH = Path("/run/secrets/hcloud-readonly-token")
LOCKED_FIREWALL_NAME = "pg-restore-locked"
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


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


def evaluate_provider_state(
    server: dict[str, Any],
    firewalls: list[dict[str, Any]],
    *,
    generation: str,
    observed_at: datetime,
    owner_ssh_cidr: str,
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
    server_id = server.get("id")
    if not isinstance(server_id, int) or server_id <= 0:
        raise ProviderInventoryError("server ID is invalid")

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
    attached_server_ids = {
        item.get("server", {}).get("id")
        for item in applied_to
        if isinstance(item, dict) and item.get("type") == "server"
    }
    if attached_server_ids != {server_id}:
        raise ProviderInventoryError("locked firewall is not attached only to this server")

    rules = firewall.get("rules")
    if rules != expected_locked_rules(owner_ssh_cidr):
        raise ProviderInventoryError("locked firewall policy is not exact")
    server_firewalls = server.get("public_net", {}).get("firewalls")
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
        "schema_version": 1,
        "collector_id": "company-os-hetzner-locked-inventory",
        "collector_version": 1,
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
    }


def get_json(url: str, auth_value: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + auth_value,
            "Accept": "application/json",
            "User-Agent": "adapteng-company-os-restore-inventory/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ProviderInventoryError(f"provider inventory request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProviderInventoryError("provider response is not an object")
    return payload


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


def main() -> int:
    if (
        len(sys.argv) != 4
        or sys.argv[1] not in {"A", "B", "C"}
        or not sys.argv[2].isdecimal()
        or int(sys.argv[2]) <= 0
    ):
        print(
            "usage: postgres_restore_provider_inventory.py "
            "A|B|C HETZNER_SERVER_ID OWNER_SSH_CIDR",
            file=sys.stderr,
        )
        return 64
    try:
        auth_value = secure_read(TOKEN_PATH, "Hetzner read-only token")
        instance_id = sys.argv[2]
        server_payload = get_json(f"{API_ROOT}/servers/{instance_id}", auth_value)
        firewall_payload = get_json(
            f"{API_ROOT}/firewalls?name={LOCKED_FIREWALL_NAME}", auth_value
        )
        server = server_payload.get("server")
        firewalls = firewall_payload.get("firewalls")
        if not isinstance(server, dict) or not isinstance(firewalls, list):
            raise ProviderInventoryError("provider inventory response shape is invalid")
        packet = evaluate_provider_state(
            server,
            firewalls,
            generation=sys.argv[1],
            observed_at=datetime.now(timezone.utc).replace(microsecond=0),
            owner_ssh_cidr=sys.argv[3],
        )
        sys.stdout.buffer.write(canonical_json(packet))
        return 0
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ProviderInventoryError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
