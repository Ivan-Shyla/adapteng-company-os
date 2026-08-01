#!/usr/bin/env python3
"""Collect and validate the complete Docker isolation state of a restore host."""

from __future__ import annotations

import hashlib
import json
import math
import re
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
CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
ZERO_TIME = {"", "0001-01-01T00:00:00Z"}
DOCKER_INSPECT_SCHEMA_VERSION = 1
DOCKER_JSON_MAX_BYTES = 16 * 1024 * 1024
DOCKER_JSON_MAX_DEPTH = 64
DOCKER_JSON_MAX_ITEMS = 16384
DOCKER_JSON_MAX_STRING = 1024 * 1024
MOUNT_KEYS = {
    "Type", "Name", "Source", "Destination", "Driver", "Mode", "RW", "Propagation"
}
ENDPOINT_KEYS = {
    "IPAMConfig", "Links", "Aliases", "MacAddress", "DriverOpts", "GwPriority",
    "NetworkID", "EndpointID", "Gateway", "IPAddress", "IPPrefixLen",
    "IPv6Gateway", "GlobalIPv6Address", "GlobalIPv6PrefixLen", "DNSNames",
}
CONFIG_KEYS = {
    "Annotations", "ArgsEscaped", "AttachStderr", "AttachStdin", "AttachStdout",
    "Cmd", "Domainname", "Entrypoint", "Env", "ExposedPorts", "Healthcheck",
    "Hostname", "Image", "Labels", "MacAddress", "NetworkDisabled", "OnBuild",
    "OpenStdin", "Shell", "StdinOnce", "StopSignal", "StopTimeout", "Tty",
    "User", "Volumes", "WorkingDir",
}
IMAGE_INSPECT_KEYS = {
    "Architecture", "Author", "Comment", "Config", "Container",
    "ContainerConfig", "Created", "Descriptor", "DockerVersion", "GraphDriver",
    "Id", "Metadata", "Os", "OsVersion", "Parent", "RepoDigests", "RepoTags",
    "RootFS", "Size", "Variant", "VirtualSize",
}
NETWORK_SETTINGS_KEYS = {
    "Bridge", "EndpointID", "Gateway", "GlobalIPv6Address",
    "GlobalIPv6PrefixLen", "HairpinMode", "IPAddress", "IPPrefixLen",
    "IPv6Gateway", "LinkLocalIPv6Address", "LinkLocalIPv6PrefixLen",
    "MacAddress", "Networks", "Ports", "SandboxID", "SandboxKey",
    "SecondaryIPAddresses", "SecondaryIPv6Addresses",
}
KNOWN_HOST_SECURITY_KEYS = {
    "Annotations", "AutoRemove", "Binds", "BlkioDeviceReadBps",
    "BlkioDeviceReadIOps", "BlkioDeviceWriteBps", "BlkioDeviceWriteIOps",
    "BlkioWeight", "BlkioWeightDevice", "CapAdd", "CapDrop", "Cgroup",
    "CgroupParent", "CgroupnsMode", "ConsoleSize", "ContainerIDFile",
    "CpuCount", "CpuPercent",
    "CpuPeriod", "CpuQuota", "CpuRealtimePeriod", "CpuRealtimeRuntime",
    "CpuShares", "CpusetCpus", "CpusetMems", "DeviceCgroupRules",
    "DeviceRequests", "Devices", "Dns", "DnsOptions", "DnsSearch", "ExtraHosts",
    "GroupAdd", "IOMaximumBandwidth", "IOMaximumIOps", "Init", "IpcMode",
    "Isolation", "KernelMemory", "KernelMemoryTCP", "Links", "LogConfig",
    "MaskedPaths", "Memory",
    "MemoryReservation", "MemorySwap", "MemorySwappiness", "NanoCpus",
    "NetworkMode", "OomKillDisable", "OomScoreAdj", "PidMode", "PidsLimit",
    "PortBindings", "Privileged", "PublishAllPorts", "ReadonlyPaths",
    "ReadonlyRootfs", "RestartPolicy", "Runtime", "SecurityOpt", "ShmSize",
    "StorageOpt", "Sysctls", "Tmpfs", "UTSMode", "Ulimits", "UsernsMode",
    "VolumeDriver", "VolumesFrom",
}
TARGET_POLICY_KEYS = {
    "repo_digest",
    "config_id",
    "path",
    "entrypoint",
    "cmd",
    "user",
    "working_dir",
    "image_environment",
    "labels",
    "hostname_template",
    "runtime",
    "apparmor_profile",
    "masked_paths",
    "readonly_paths",
    "readonly_rootfs",
    "tmpfs",
    "healthcheck",
    "log_config",
}


class HostInventoryError(RuntimeError):
    """Fail-closed host inventory error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def strict_docker_json(payload: bytes | str) -> Any:
    raw = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    if not raw or len(raw) > DOCKER_JSON_MAX_BYTES:
        raise HostInventoryError("Docker inspection JSON size is invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        if len(items) > DOCKER_JSON_MAX_ITEMS:
            raise HostInventoryError("Docker inspection object is too large")
        result: dict[str, Any] = {}
        for key, value in items:
            if (
                key in result
                or len(key) > 1024
                or any(ord(character) < 0x20 for character in key)
            ):
                raise HostInventoryError(
                    "Docker inspection object keys are invalid/duplicated"
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise HostInventoryError("Docker inspection JSON number is invalid")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HostInventoryError("Docker inspection JSON is invalid") from exc
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > DOCKER_JSON_MAX_ITEMS * 8 or depth > DOCKER_JSON_MAX_DEPTH:
            raise HostInventoryError("Docker inspection JSON structure is too large")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > DOCKER_JSON_MAX_ITEMS:
                raise HostInventoryError("Docker inspection array is too large")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and (
            len(item) > DOCKER_JSON_MAX_STRING
            or any(ord(character) < 0x20 for character in item)
        ):
            raise HostInventoryError("Docker inspection string is invalid")
        elif isinstance(item, float) and not math.isfinite(item):
            raise HostInventoryError("Docker inspection JSON number is invalid")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise HostInventoryError("Docker inspection value type is unsupported")
    return value


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


def healthcheck_shape(value: Any) -> None:
    if value is not None:
        raise HostInventoryError("container healthcheck is not disabled")
    return None


def log_config_shape(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"Type", "Config"}
        or value.get("Type") != "json-file"
        or value.get("Config") != {}
    ):
        raise HostInventoryError("container log configuration is not the sealed default")
    return {"Type": value["Type"], "Config": value["Config"]}


def validate_target_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != TARGET_POLICY_KEYS:
        raise HostInventoryError("target policy fields are not exact")
    healthcheck_shape(value["healthcheck"])
    log_config_shape(value["log_config"])
    return value


def host_isolation_shape(host: dict[str, Any]) -> dict[str, Any]:
    empty_fields = (
        "Binds",
        "CapAdd",
        "CapDrop",
        "Devices",
        "DeviceRequests",
        "DeviceCgroupRules",
        "Dns",
        "DnsOptions",
        "DnsSearch",
        "ExtraHosts",
        "Links",
        "GroupAdd",
        "SecurityOpt",
        "Sysctls",
        "Ulimits",
        "VolumesFrom",
    )
    if set(host) - KNOWN_HOST_SECURITY_KEYS:
        raise HostInventoryError("container has an unknown HostConfig field")
    if any(host.get(key) not in (None, [], {}) for key in empty_fields):
        raise HostInventoryError("container has an elevated host capability")
    if host.get("StorageOpt") not in (None, {}) or host.get("Annotations") not in (
        None,
        {},
    ):
        raise HostInventoryError("container storage/host annotations are not empty")
    if (
        host.get("Privileged") is not False
        or host.get("PublishAllPorts") is not False
        or not isinstance(host.get("ReadonlyRootfs"), bool)
        or host.get("AutoRemove") is not False
        or host.get("PidMode") not in (None, "")
        or host.get("IpcMode") not in (None, "", "private")
        or host.get("UTSMode") not in (None, "")
        or host.get("UsernsMode") not in (None, "")
        or host.get("CgroupnsMode") not in (None, "", "private")
        or host.get("CgroupParent") not in (None, "")
        or host.get("OomKillDisable") not in (None, False)
        or host.get("Init") not in (None, False)
        or host.get("Isolation") not in (None, "", "default")
    ):
        raise HostInventoryError("container host isolation settings are not safe")
    restart = host.get("RestartPolicy")
    if not isinstance(restart, dict) or restart.get("Name") not in ("", "no") or int(
        restart.get("MaximumRetryCount", 0)
    ) != 0:
        raise HostInventoryError("container restart policy is not disabled")
    tmpfs = host.get("Tmpfs") or {}
    if not isinstance(tmpfs, dict) or not all(
        isinstance(path, str)
        and path.startswith("/")
        and isinstance(options, str)
        and options
        for path, options in tmpfs.items()
    ):
        raise HostInventoryError("container tmpfs policy is malformed")
    masked_paths = host.get("MaskedPaths") or []
    readonly_paths = host.get("ReadonlyPaths") or []
    if any(
        not isinstance(paths, list)
        or len(paths) != len(set(paths))
        or not all(isinstance(path, str) and path.startswith("/") for path in paths)
        for paths in (masked_paths, readonly_paths)
    ):
        raise HostInventoryError("container masked/readonly paths are malformed")
    log_config = log_config_shape(host.get("LogConfig"))
    return {
        "privileged": host["Privileged"],
        "cap_add": host.get("CapAdd"),
        "cap_drop": host.get("CapDrop"),
        "devices": host.get("Devices"),
        "device_requests": host.get("DeviceRequests"),
        "device_cgroup_rules": host.get("DeviceCgroupRules"),
        "security_opt": host.get("SecurityOpt"),
        "masked_paths": sorted(masked_paths),
        "readonly_paths": sorted(readonly_paths),
        "storage_opt": host.get("StorageOpt"),
        "annotations": host.get("Annotations"),
        "isolation": host.get("Isolation"),
        "pid_mode": host.get("PidMode"),
        "ipc_mode": host.get("IpcMode"),
        "uts_mode": host.get("UTSMode"),
        "userns_mode": host.get("UsernsMode"),
        "cgroupns_mode": host.get("CgroupnsMode"),
        "readonly_rootfs": host["ReadonlyRootfs"],
        "tmpfs": {key: tmpfs[key] for key in sorted(tmpfs)},
        "publish_all_ports": host["PublishAllPorts"],
        "auto_remove": host["AutoRemove"],
        "restart_policy": restart,
        "log_config": log_config,
    }


def container_execution_identity(container: dict[str, Any]) -> dict[str, Any]:
    config = container.get("Config")
    host = container.get("HostConfig")
    network_settings = container.get("NetworkSettings")
    if not isinstance(config, dict) or not isinstance(host, dict) or not isinstance(
        network_settings, dict
    ):
        raise HostInventoryError("container inspection is incomplete")
    if config.get("Annotations") not in (None, {}) or container.get(
        "Annotations"
    ) not in (None, {}):
        raise HostInventoryError("container annotations are not empty")
    if set(config) - CONFIG_KEYS or set(network_settings) - NETWORK_SETTINGS_KEYS:
        raise HostInventoryError("container Config/NetworkSettings schema is unknown")
    healthcheck = healthcheck_shape(config.get("Healthcheck"))
    networks = network_settings.get("Networks")
    mounts = container.get("Mounts")
    if not isinstance(networks, dict) or not isinstance(mounts, list):
        raise HostInventoryError("container network/mount inventory is incomplete")
    normalized_networks: dict[str, Any] = {}
    for name, endpoint in sorted(networks.items()):
        if not isinstance(endpoint, dict):
            raise HostInventoryError("container endpoint inventory is malformed")
        if set(endpoint) - ENDPOINT_KEYS:
            raise HostInventoryError("container endpoint has an unknown field")
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
        if set(mount) - MOUNT_KEYS:
            raise HostInventoryError("container mount has an unknown field")
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
        "docker_inspect_schema_version": DOCKER_INSPECT_SCHEMA_VERSION,
        "raw_inspect_sha256": hashlib.sha256(canonical_json(container)).hexdigest(),
        "id_sha256": ref_sha256(container.get("Id", "")),
        "name": container.get("Name"),
        "running": container.get("State", {}).get("Running"),
        "image_config_id": container.get("Image"),
        "config_image": config.get("Image"),
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "user": config.get("User"),
        "working_dir": config.get("WorkingDir"),
        "config_annotations": config.get("Annotations"),
        "top_level_annotations": container.get("Annotations"),
        "hostname_sha256": ref_sha256(config.get("Hostname", "")),
        "labels": config.get("Labels") or {},
        "environment": environment_shape(config.get("Env") or []),
        "healthcheck": healthcheck,
        "network_mode": host.get("NetworkMode"),
        "host_isolation": host_isolation_shape(host),
        "port_bindings": host.get("PortBindings") or {},
        "networks": normalized_networks,
        "mounts": sorted(
            normalized_mounts,
            key=lambda item: (str(item["destination"]), str(item["name_sha256"])),
        ),
    }


def image_execution_identity(image: dict[str, Any]) -> dict[str, Any]:
    config = image.get("Config")
    if (
        not isinstance(image, dict)
        or set(image) - IMAGE_INSPECT_KEYS
        or not isinstance(config, dict)
        or set(config) - CONFIG_KEYS
    ):
        raise HostInventoryError("image inspection schema is unknown")
    repo_digests = image.get("RepoDigests")
    if not isinstance(repo_digests, list) or len(repo_digests) != 1:
        raise HostInventoryError("image has absent/multiple RepoDigests")
    healthcheck = healthcheck_shape(config.get("Healthcheck"))
    return {
        "docker_inspect_schema_version": DOCKER_INSPECT_SCHEMA_VERSION,
        "raw_inspect_sha256": hashlib.sha256(canonical_json(image)).hexdigest(),
        "config_id": image.get("Id"),
        "repo_digest": repo_digests[0],
        "os": image.get("Os"),
        "architecture": image.get("Architecture"),
        "user": config.get("User"),
        "working_dir": config.get("WorkingDir"),
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "environment": environment_shape(config.get("Env") or []),
        "healthcheck": healthcheck,
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


def validate_sealed_target(
    *,
    container: dict[str, Any],
    image: dict[str, Any],
    expected_id: str,
    expected_name: str,
    expected_network: str,
    expected_host_network_mode: str,
    expected_volume: str,
    expected_pgdata: str,
    target_policy: dict[str, Any],
    generation: str,
    running: bool,
    forbidden_identifiers: set[str],
) -> dict[str, Any]:
    if not CONTAINER_ID.fullmatch(expected_id) or container.get("Id") != expected_id:
        raise HostInventoryError("target container ID is absent or changed")
    if container.get("Name") != f"/{expected_name}":
        raise HostInventoryError("target container name is not exact")
    state = container.get("State")
    if not isinstance(state, dict):
        raise HostInventoryError("target container state is incomplete")
    if running:
        if state.get("Status") != "running" or state.get("Running") is not True:
            raise HostInventoryError("target container did not enter running state")
    elif (
        state.get("Status") != "created"
        or state.get("Running") is not False
        or state.get("Pid") not in (0, None)
        or state.get("ExitCode") not in (0, None)
        or state.get("Error") not in ("", None)
        or state.get("StartedAt") not in ZERO_TIME
        or state.get("FinishedAt") not in ZERO_TIME
        or any(state.get(key) is True for key in ("Paused", "Restarting", "OOMKilled", "Dead"))
    ):
        raise HostInventoryError("target container is not pristine/never-started")
    if container.get("RestartCount") not in (0, None):
        raise HostInventoryError("target container has restarted")

    validate_target_policy(target_policy)
    config = container.get("Config")
    host = container.get("HostConfig")
    if not isinstance(config, dict) or not isinstance(host, dict):
        raise HostInventoryError("target container configuration is incomplete")
    expected_hostname = str(target_policy["hostname_template"]).format(
        generation_lower=generation.lower(),
        role="recovery" if "recovery" in expected_name else "final",
        target_name=expected_name,
    )
    exact_config = {
        "Image": target_policy["repo_digest"],
        "Entrypoint": target_policy["entrypoint"],
        "Cmd": target_policy["cmd"],
        "User": target_policy["user"],
        "WorkingDir": target_policy["working_dir"],
        "Env": target_policy["image_environment"],
        "Labels": target_policy["labels"],
        "Hostname": expected_hostname,
        "AttachStdin": False,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "OpenStdin": False,
        "StdinOnce": False,
        "Healthcheck": target_policy["healthcheck"],
    }
    if container.get("Image") != target_policy["config_id"]:
        raise HostInventoryError("target image config ID is not approved")
    if container.get("Path") != target_policy["path"]:
        raise HostInventoryError("target executable path is not approved")
    if (
        host.get("Runtime") != target_policy["runtime"]
        or container.get("AppArmorProfile") != target_policy["apparmor_profile"]
        or host.get("MaskedPaths") != target_policy["masked_paths"]
        or host.get("ReadonlyPaths") != target_policy["readonly_paths"]
        or host.get("ReadonlyRootfs") is not target_policy["readonly_rootfs"]
        or host.get("Tmpfs") != target_policy["tmpfs"]
        or host.get("LogConfig") != target_policy["log_config"]
    ):
        raise HostInventoryError("target runtime/security profile is not approved")
    if any(config.get(key) != value for key, value in exact_config.items()):
        raise HostInventoryError("target immutable configuration is not approved")
    measured_image = image_identity(image)
    if measured_image != {
        "config_id": target_policy["config_id"],
        "repo_digest": target_policy["repo_digest"],
        "os": "linux",
        "architecture": image.get("Architecture"),
    } or image.get("Architecture") not in {"amd64", "arm64"}:
        raise HostInventoryError("target image identity is not approved")
    if host.get("NetworkMode") != expected_host_network_mode:
        raise HostInventoryError("target network mode is not exact")
    isolation = host_isolation_shape(host)
    if host.get("PortBindings") not in (None, {}):
        raise HostInventoryError("target publishes a host port")

    networks = container.get("NetworkSettings", {}).get("Networks")
    if not isinstance(networks, dict) or set(networks) != {expected_network}:
        raise HostInventoryError("target network attachment is not exact")
    endpoint = networks[expected_network]
    if not isinstance(endpoint, dict):
        raise HostInventoryError("target network endpoint is malformed")
    aliases = endpoint.get("Aliases") or []
    expected_aliases = (
        set() if expected_network == "none" else {expected_name, expected_id[:12]}
    )
    if (
        not isinstance(aliases, list)
        or not all(isinstance(alias, str) for alias in aliases)
        or set(aliases) != expected_aliases
    ):
        raise HostInventoryError("target network aliases are not exact")
    if expected_network != "none" and (
        not str(endpoint.get("NetworkID", ""))
        or not str(endpoint.get("EndpointID", ""))
        or not str(endpoint.get("IPAddress", ""))
    ):
        raise HostInventoryError("target locked-network endpoint is incomplete")
    ports = container.get("NetworkSettings", {}).get("Ports")
    if ports not in (None, {}) and any(
        bindings not in (None, []) for bindings in ports.values()
    ):
        raise HostInventoryError("target exposes a host port")

    mounts = container.get("Mounts")
    if (
        not isinstance(mounts, list)
        or len(mounts) != 1
        or mounts[0].get("Type") != "volume"
        or mounts[0].get("Name") != expected_volume
        or mounts[0].get("Destination") != expected_pgdata
        or mounts[0].get("RW") is not True
    ):
        raise HostInventoryError("target PGDATA mount is not exact")
    serialized = json.dumps(container, sort_keys=True).lower()
    if "docker.sock" in serialized or any(
        identifier.lower() in serialized for identifier in forbidden_identifiers
    ):
        raise HostInventoryError("target contains a forbidden capability/identifier")
    identity = container_execution_identity(container)
    identity.update(
        {
            "container_id": expected_id,
            "path": container.get("Path"),
            "state_status": state.get("Status"),
            "restart_count": container.get("RestartCount", 0),
            "image_repo_digest": measured_image["repo_digest"],
            "working_dir": config.get("WorkingDir"),
            "hostname": config.get("Hostname"),
            "host_isolation": isolation,
        }
    )
    return identity


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
    return strict_docker_json(completed.stdout)


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
