#!/usr/bin/env python3
"""Collect and validate the complete Docker isolation state of a restore host."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
DEFAULT_NETWORKS = {"bridge", "host", "none"}
FORBIDDEN_ENV_PREFIXES = (
    "AWS_",
    "B2_",
    "PGBACKREST_",
)
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


class HostInventoryError(RuntimeError):
    """Fail-closed host inventory error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def ref_sha256(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def environment_shape(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise HostInventoryError("container environment inventory is malformed")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in values:
        if "=" not in item:
            raise HostInventoryError("container environment entry is malformed")
        key, value = item.split("=", 1)
        if not key or key in seen:
            raise HostInventoryError("container environment key is empty/duplicated")
        seen.add(key)
        result.append(
            {
                "key": key,
                "value_sha256": (
                    "REDACTED"
                    if key == "PGPASSWORD" or key.startswith(FORBIDDEN_ENV_PREFIXES)
                    else ref_sha256(value)
                ),
            }
        )
    return sorted(result, key=lambda item: item["key"])


def host_isolation_shape(host: dict[str, Any]) -> dict[str, Any]:
    empty_fields = (
        "Binds",
        "CapAdd",
        "CapDrop",
        "Devices",
        "DeviceRequests",
        "Dns",
        "DnsOptions",
        "DnsSearch",
        "ExtraHosts",
        "Links",
        "SecurityOpt",
        "VolumesFrom",
    )
    if any(host.get(key) not in (None, [], {}) for key in empty_fields):
        raise HostInventoryError("container has an elevated host capability")
    if (
        host.get("Privileged") is not False
        or host.get("PublishAllPorts") is not False
        or host.get("ReadonlyRootfs") is not False
        or host.get("AutoRemove") is not False
        or host.get("PidMode") not in (None, "")
        or host.get("IpcMode") not in (None, "", "private")
        or host.get("UTSMode") not in (None, "")
        or host.get("UsernsMode") not in (None, "")
        or host.get("CgroupnsMode") not in (None, "", "private")
        or host.get("OomKillDisable") not in (None, False)
        or host.get("Init") not in (None, False)
    ):
        raise HostInventoryError("container host isolation settings are not safe")
    restart = host.get("RestartPolicy")
    if not isinstance(restart, dict) or restart.get("Name") not in ("", "no") or int(
        restart.get("MaximumRetryCount", 0)
    ) != 0:
        raise HostInventoryError("container restart policy is not disabled")
    return {
        "privileged": False,
        "cap_add": [],
        "cap_drop": [],
        "devices": [],
        "device_requests": [],
        "security_opt": [],
        "pid_mode": "",
        "ipc_mode": "private",
        "uts_mode": "",
        "userns_mode": "",
        "cgroupns_mode": "private",
        "readonly_rootfs": False,
        "publish_all_ports": False,
        "auto_remove": False,
        "restart_policy": "no",
    }


def container_execution_identity(container: dict[str, Any]) -> dict[str, Any]:
    config = container.get("Config")
    host = container.get("HostConfig")
    network_settings = container.get("NetworkSettings")
    if not isinstance(config, dict) or not isinstance(host, dict) or not isinstance(
        network_settings, dict
    ):
        raise HostInventoryError("container inspection is incomplete")
    networks = network_settings.get("Networks")
    mounts = container.get("Mounts")
    if not isinstance(networks, dict) or not isinstance(mounts, list):
        raise HostInventoryError("container network/mount inventory is incomplete")
    normalized_networks: dict[str, Any] = {}
    for name, endpoint in sorted(networks.items()):
        if not isinstance(endpoint, dict):
            raise HostInventoryError("container endpoint inventory is malformed")
        aliases = endpoint.get("Aliases") or []
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) for alias in aliases
        ):
            raise HostInventoryError("container aliases are malformed")
        normalized_networks[name] = {
            "network_id_sha256": ref_sha256(endpoint.get("NetworkID", "")),
            "endpoint_id_sha256": ref_sha256(endpoint.get("EndpointID", "")),
            "aliases_sha256": ref_sha256("\n".join(sorted(aliases))),
        }
    normalized_mounts = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise HostInventoryError("container mount entry is malformed")
        normalized_mounts.append(
            {
                "type": mount.get("Type"),
                "name_sha256": ref_sha256(mount.get("Name", "")),
                "source_sha256": ref_sha256(mount.get("Source", "")),
                "destination": mount.get("Destination"),
                "rw": mount.get("RW"),
            }
        )
    return {
        "id_sha256": ref_sha256(container.get("Id", "")),
        "name": container.get("Name"),
        "running": container.get("State", {}).get("Running"),
        "image_config_id": container.get("Image"),
        "config_image": config.get("Image"),
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "user": config.get("User"),
        "hostname_sha256": ref_sha256(config.get("Hostname", "")),
        "labels": config.get("Labels") or {},
        "environment": environment_shape(config.get("Env") or []),
        "network_mode": host.get("NetworkMode"),
        "host_isolation": host_isolation_shape(host),
        "port_bindings": host.get("PortBindings") or {},
        "networks": normalized_networks,
        "mounts": sorted(
            normalized_mounts,
            key=lambda item: (str(item["destination"]), str(item["name_sha256"])),
        ),
    }


def image_identity(image: dict[str, Any]) -> dict[str, Any]:
    repo_digests = image.get("RepoDigests")
    if not isinstance(repo_digests, list) or len(repo_digests) != 1:
        raise HostInventoryError("host image has absent/multiple RepoDigests")
    return {
        "config_id": image.get("Id"),
        "repo_digest": repo_digests[0],
        "os": image.get("Os"),
        "architecture": image.get("Architecture"),
    }


def validate_host_inventory(
    *,
    containers: list[dict[str, Any]],
    images: list[dict[str, Any]],
    networks: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    expected_containers: dict[str, dict[str, Any]],
    expected_images: set[tuple[str, str]],
    expected_network: str,
    expected_volume: str,
    forbidden_identifiers: set[str],
    generation: str,
    stage: str,
    observed_at: datetime,
) -> dict[str, Any]:
    if stage not in {"PRE_SQL", "POST_SQL"}:
        raise HostInventoryError("host inventory stage is not exact")
    by_name: dict[str, dict[str, Any]] = {}
    for container in containers:
        name = str(container.get("Name", "")).removeprefix("/")
        if not name or name in by_name:
            raise HostInventoryError("container names are absent/duplicated")
        by_name[name] = container
    if set(by_name) != set(expected_containers):
        raise HostInventoryError("host contains an unexpected/missing container")

    identities: dict[str, dict[str, Any]] = {}
    for name, expected in expected_containers.items():
        identity = container_execution_identity(by_name[name])
        if identity != expected:
            raise HostInventoryError(f"container identity changed: {name}")
        if identity["port_bindings"]:
            raise HostInventoryError("container publishes a host port")
        for mount in identity["mounts"]:
            if "docker.sock" in str(mount):
                raise HostInventoryError("container mounts the Docker socket")
        identities[name] = identity

    measured_images = {
        (str(identity["config_id"]), str(identity["repo_digest"]))
        for identity in (image_identity(image) for image in images)
    }
    if measured_images != expected_images:
        raise HostInventoryError("host image inventory differs from the exact allowlist")

    network_names = {str(item.get("Name", "")) for item in networks}
    if network_names != DEFAULT_NETWORKS | {expected_network}:
        raise HostInventoryError("host network inventory differs from the exact allowlist")
    locked = [item for item in networks if item.get("Name") == expected_network]
    if (
        len(locked) != 1
        or locked[0].get("Internal") is not True
        or locked[0].get("Driver") != "bridge"
        or locked[0].get("Scope") != "local"
    ):
        raise HostInventoryError("locked Docker network is not exact/internal")

    volume_names = {str(item.get("Name", "")) for item in volumes}
    if volume_names != {expected_volume}:
        raise HostInventoryError("host volume inventory differs from the exact allowlist")

    serialized = canonical_json(
        {
            "containers": containers,
            "images": images,
            "networks": networks,
            "volumes": volumes,
        }
    ).decode("ascii").lower()
    for identifier in forbidden_identifiers:
        if identifier.lower() in serialized:
            raise HostInventoryError("production identifier found in current host state")

    packet = {
        "schema_version": 1,
        "status": "HOST_ISOLATION_CURRENT",
        "stage": stage,
        "generation": generation,
        "observed_at": observed_at.astimezone(timezone.utc).replace(
            microsecond=0
        ).strftime(TIMESTAMP),
        "container_identities_sha256": hashlib.sha256(
            canonical_json(identities)
        ).hexdigest(),
        "image_identities_sha256": hashlib.sha256(
            canonical_json(sorted(expected_images))
        ).hexdigest(),
        "network_inventory_sha256": hashlib.sha256(
            canonical_json(networks)
        ).hexdigest(),
        "volume_inventory_sha256": hashlib.sha256(
            canonical_json(volumes)
        ).hexdigest(),
        "containers_count": len(containers),
        "images_count": len(images),
        "networks_count": len(networks),
        "volumes_count": len(volumes),
        "published_ports": 0,
        "docker_socket_mounts": 0,
        "production_identifiers_found": 0,
    }
    return packet


def docker_json(
    arguments: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Any:
    completed = run(
        ["docker", *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=CLEAN_ENVIRONMENT,
    )
    return json.loads(completed.stdout)


def docker_lines(
    arguments: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    completed = run(
        ["docker", *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=CLEAN_ENVIRONMENT,
    )
    return [line for line in completed.stdout.splitlines() if line]


def collect_docker_inventory(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    container_ids = docker_lines(
        ["container", "ls", "--all", "--quiet", "--no-trunc"], run=run
    )
    containers = (
        docker_json(["container", "inspect", *container_ids], run=run)
        if container_ids
        else []
    )
    image_ids = docker_lines(["image", "ls", "--quiet", "--no-trunc"], run=run)
    images = docker_json(["image", "inspect", *image_ids], run=run) if image_ids else []
    network_ids = docker_lines(["network", "ls", "--quiet"], run=run)
    networks = (
        docker_json(["network", "inspect", *network_ids], run=run)
        if network_ids
        else []
    )
    volume_names = docker_lines(["volume", "ls", "--quiet"], run=run)
    volumes = (
        docker_json(["volume", "inspect", *volume_names], run=run)
        if volume_names
        else []
    )
    if not all(isinstance(value, list) for value in (containers, images, networks, volumes)):
        raise HostInventoryError("Docker host inventory is malformed")
    return containers, images, networks, volumes
