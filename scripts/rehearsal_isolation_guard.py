#!/usr/bin/env python3
"""Fail-closed isolation gate for the disposable PostgreSQL restore rehearsal.

The rehearsal takes a real pgBackRest backup with the real object-store
credentials. Nothing else about it may be real. This gate is the structural
proof of that, and it runs before the rehearsal is allowed to touch anything:

* the rehearsal repository prefix must be run-scoped and must share no path
  lineage with the production pgBackRest repository, so a rehearsal expire can
  never reach production repository content;
* the repository pgBackRest will *actually* use must be the one this gate was
  told about. pgBackRest reads every ``PGBACKREST_<OPTION>`` environment
  variable as configuration, so ``PGBACKREST_REPO1_PATH`` in the environment
  decides where a backup lands regardless of what any caller passes here. A
  gate that only inspects its own arguments can be satisfied by a caller that
  declares one repository and exports another;
* every cluster directory must live inside the runner's ephemeral root, and the
  restore targets must be absent or empty before a restore writes into them;
* every cluster must be configured with an empty ``listen_addresses``, so the
  disposable instances have no TCP listener to reach and nothing can reach
  them either;
* no PostgreSQL connection configuration may name a non-loopback host, and the
  well-known production connection variables must be unset; and
* the pgBackRest configuration must describe a local PostgreSQL data directory
  only -- any ``pgN-host`` option would make pgBackRest operate against a
  different, possibly production, database server.

Every check is separately named and separately reported so a failure says which
property was violated. The command exits nonzero unless every check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


# A PostgreSQL connection URI anywhere in the environment is worth finding even
# when it lives in a variable this gate has never heard of.
CONNECTION_URI = re.compile(
    r"\bpostgres(?:ql)?://(?:(?P<userinfo>[^/@\s]*)@)?(?P<host>[^/?#\s:]+)",
    re.IGNORECASE,
)

# Connection variables libpq and the platform's own tooling would silently obey.
FORBIDDEN_CONNECTION_VARIABLES = (
    "ADAPTENG_OPS_DATABASE_URL",
    "APPROVED_ASSETS_DATABASE_URL",
    "DATABASE_URL",
    "PGHOST",
    "PGHOSTADDR",
    "PGSERVICE",
    "PGSERVICEFILE",
    "POSTGRES_HOST",
    "POSTGRES_URL",
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})

# Any pgBackRest option that points the tool at a PostgreSQL server it must
# reach over the network instead of the local data directory.
REMOTE_PG_OPTION = re.compile(r"^\s*pg\d*-host(?:-[a-z0-9-]+)?\s*=", re.IGNORECASE)

# The environment variable pgBackRest consults for the repository prefix. It is
# the value that decides where a backup is written; the command line this gate
# is given is only a claim about it.
EFFECTIVE_REPO_PATH_VARIABLE = "PGBACKREST_REPO1_PATH"

# A second configured repository would give pgBackRest another place to write,
# which this gate has proven nothing about. repo1 is the only one it vouches for.
ADDITIONAL_REPOSITORY = re.compile(r"^PGBACKREST_REPO(?:[2-9]|\d{2,})_", re.IGNORECASE)

SETTING = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?P<value>.*?)\s*$"
)

# ``str.splitlines()`` ends a line on eleven separators; an LF-delimited reader
# ends it on one. Where the two disagree this gate reads a different file than
# the tool it vouches for: given ``pg1-path = /mnt/ephemeral\x0c/../../etc``,
# splitlines() yields the allowed ``/mnt/ephemeral`` and the gate passes, while
# the whole value resolves to ``/etc``. That is a fail-open in a fail-closed
# gate, so this module refuses to guess which parser is right -- a separator
# that could move a line boundary makes the configuration unreadable instead.
#
# CR is deliberately absent. ``Path.read_text`` decodes in universal-newline
# mode, so a lone CR and a CRLF are both already LF by the time any of this
# runs -- measured, not assumed -- and every parser that reads these files
# treats CRLF as a line ending regardless. The eight below are the ones
# ``str.splitlines()`` honours and essentially nothing else does.
AMBIGUOUS_LINE_SEPARATORS = {
    "\v": "VT",
    "\f": "FF",
    "\x1c": "FS",
    "\x1d": "GS",
    "\x1e": "RS",
    "\x85": "NEL",
    "\u2028": "LS",
    "\u2029": "PS",
}


class GuardError(RuntimeError):
    """A rehearsal isolation property could not be established."""


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def parse_named_path(raw: str) -> tuple[str, Path]:
    name, separator, value = raw.partition("=")
    if not separator or not name.strip() or not value.strip():
        raise GuardError(f"expected NAME=PATH, received {raw!r}")
    return name.strip(), Path(value.strip())


def repository_segments(path: str) -> tuple[str, ...]:
    pure = PurePosixPath(path)
    if not pure.is_absolute():
        raise GuardError(f"repository path {path!r} is not absolute")
    return tuple(part for part in pure.parts[1:] if part not in ("", "."))


def shares_lineage(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """True when either repository path contains or equals the other."""

    shorter, longer = sorted((left, right), key=len)
    return longer[: len(shorter)] == shorter


def check_repository_disjoint(production: str, rehearsal: str) -> list[Check]:
    checks: list[Check] = []
    try:
        production_parts = repository_segments(production)
        production_absolute = True
    except GuardError as exc:
        production_parts = ()
        production_absolute = False
        checks.append(Check("production_repo_path_absolute", False, str(exc)))
    if production_absolute:
        checks.append(
            Check(
                "production_repo_path_absolute",
                bool(production_parts),
                "declared" if production_parts else "production path is the bucket root",
            )
        )
    try:
        rehearsal_parts = repository_segments(rehearsal)
    except GuardError as exc:
        checks.append(Check("rehearsal_repo_path_absolute", False, str(exc)))
        checks.append(
            Check("rehearsal_repo_path_disjoint_from_production", False, "path unusable")
        )
        return checks
    checks.append(Check("rehearsal_repo_path_absolute", bool(rehearsal_parts), "declared"))
    if not production_parts or not rehearsal_parts:
        checks.append(
            Check(
                "rehearsal_repo_path_disjoint_from_production",
                False,
                "a bucket-root repository path cannot be proven disjoint",
            )
        )
        return checks
    overlapping = shares_lineage(production_parts, rehearsal_parts)
    checks.append(
        Check(
            "rehearsal_repo_path_disjoint_from_production",
            not overlapping,
            "no shared path lineage"
            if not overlapping
            else "rehearsal and production repository paths overlap",
        )
    )
    return checks


def check_effective_repository(
    environment: dict[str, str],
    declared_rehearsal: str,
    production: str,
    *,
    required: bool = False,
) -> list[Check]:
    """Check the repository pgBackRest will really use, not the one it is told.

    ``check_repository_disjoint`` compares the two paths this gate is handed on
    the command line. That proves the caller's intent, not the outcome:
    pgBackRest takes ``PGBACKREST_REPO1_PATH`` straight from the environment, so
    a caller that exports the production prefix while passing a harmless-looking
    ``--rehearsal-repo-path`` would satisfy every other check here and still back
    up into production. These checks read the environment itself, which is the
    only thing pgBackRest will obey.

    Whenever that variable is set it is checked, with or without ``required`` --
    so the export above is caught even by a caller that never opted in. What
    ``required`` adds is the demand that it be set at all, which is true inside
    the rehearsal workflow and not true of a unit test evaluating a fixture.
    """

    effective = environment.get(EFFECTIVE_REPO_PATH_VARIABLE, "").strip()
    checks: list[Check] = []

    if required:
        checks.append(
            Check(
                "effective_repo_path_declared",
                bool(effective),
                f"{EFFECTIVE_REPO_PATH_VARIABLE}={effective}"
                if effective
                else f"{EFFECTIVE_REPO_PATH_VARIABLE} is unset, so the repository "
                "pgBackRest would use is not knowable here",
            )
        )

    if effective:
        agrees = effective == declared_rehearsal.strip()
        checks.append(
            Check(
                "effective_repo_path_matches_declared_rehearsal",
                agrees,
                "the environment and the declared rehearsal path agree"
                if agrees
                else f"the environment says {effective}, but this gate was told "
                f"{declared_rehearsal}",
            )
        )

        try:
            effective_parts = repository_segments(effective)
            production_parts = repository_segments(production)
        except GuardError as exc:
            checks.append(
                Check("effective_repo_path_disjoint_from_production", False, str(exc))
            )
        else:
            overlapping = (
                not effective_parts
                or not production_parts
                or shares_lineage(effective_parts, production_parts)
            )
            checks.append(
                Check(
                    "effective_repo_path_disjoint_from_production",
                    not overlapping,
                    "no shared path lineage with the production repository"
                    if not overlapping
                    else "the repository pgBackRest would use overlaps production",
                )
            )

    extra = sorted(name for name in environment if ADDITIONAL_REPOSITORY.match(name))
    checks.append(
        Check(
            "no_additional_pgbackrest_repository_configured",
            not extra,
            "repo1 is the only configured repository"
            if not extra
            else f"a second repository is configured by: {', '.join(extra)}",
        )
    )
    return checks


def check_scope_token(rehearsal: str, scope_token: str) -> Check:
    if not scope_token.strip():

        return Check("rehearsal_repo_path_run_scoped", False, "no scope supplied")
    try:
        parts = repository_segments(rehearsal)
    except GuardError as exc:
        return Check("rehearsal_repo_path_run_scoped", False, str(exc))
    scoped = scope_token.strip() in parts
    return Check(
        "rehearsal_repo_path_run_scoped",
        scoped,
        "prefix carries the run scope as a whole segment"
        if scoped
        else "prefix does not carry the run scope as a whole segment",
    )


def check_ephemeral(root: Path, clusters: dict[str, Path]) -> list[Check]:
    checks: list[Check] = []
    resolved_root = root.resolve()
    contained: list[str] = []
    for name in sorted(clusters):
        candidate = clusters[name].resolve()
        inside = candidate != resolved_root and resolved_root in candidate.parents
        checks.append(
            Check(
                f"cluster_path_ephemeral[{name}]",
                inside,
                "inside the ephemeral root" if inside else "outside the ephemeral root",
            )
        )
        contained.append(str(candidate))
    distinct = len(set(contained)) == len(contained) and bool(contained)
    checks.append(
        Check(
            "cluster_paths_distinct",
            distinct,
            "every declared cluster directory is distinct"
            if distinct
            else "cluster directories are missing or duplicated",
        )
    )
    return checks


def check_empty(paths: dict[str, Path]) -> list[Check]:
    checks: list[Check] = []
    for name in sorted(paths):
        target = paths[name]
        if not target.exists():
            checks.append(Check(f"restore_target_empty[{name}]", True, "absent"))
            continue
        if not target.is_dir():
            checks.append(
                Check(f"restore_target_empty[{name}]", False, "exists and is not a directory")
            )
            continue
        entries = sorted(item.name for item in target.iterdir())
        checks.append(
            Check(
                f"restore_target_empty[{name}]",
                not entries,
                "empty directory" if not entries else f"{len(entries)} existing entries",
            )
        )
    return checks


def config_lines(text: str, path: Path, name: str) -> list[str]:
    """Return LF-delimited lines, refusing any separator that could move a boundary.

    A configuration file whose line structure depends on which parser reads it
    is one this gate cannot establish a property about, so the ambiguity is
    fatal rather than silently resolved in the gate's favour. With those
    separators excluded, LF splitting and ``str.splitlines()`` agree on every
    surviving input, which is what makes the choice between them inert here.
    """
    for character, label in AMBIGUOUS_LINE_SEPARATORS.items():
        if character in text:
            raise GuardError(
                f"{name}: {path} contains {label} (U+{ord(character):04X}), which ends a "
                "line for some readers and not others; its line structure is ambiguous"
            )
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def read_settings(path: Path, name: str) -> list[tuple[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuardError(f"{name}: cannot read {path}") from exc
    settings: list[tuple[str, str]] = []
    for line in config_lines(text, path, name):
        stripped = line.split("#", 1)[0]
        match = SETTING.match(stripped)
        if match:
            settings.append((match.group("name").lower(), match.group("value").strip()))
    return settings


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def check_listen_addresses(configs: list[tuple[str, Path]]) -> list[Check]:
    declared: dict[str, bool] = {}
    violated: dict[str, str] = {}
    for name, path in configs:
        for setting, value in read_settings(path, name):
            if setting != "listen_addresses":
                continue
            if unquote(value) == "":
                declared[name] = True
            else:
                violated[name] = f"{path.name} publishes a TCP listener"
    checks: list[Check] = []
    for name in sorted({name for name, _ in configs}):
        if name in violated:
            checks.append(Check(f"cluster_has_no_listener[{name}]", False, violated[name]))
        elif declared.get(name):
            checks.append(
                Check(f"cluster_has_no_listener[{name}]", True, "listen_addresses is empty")
            )
        else:
            checks.append(
                Check(
                    f"cluster_has_no_listener[{name}]",
                    False,
                    "no configuration file sets listen_addresses to empty",
                )
            )
    return checks


def check_environment(environment: dict[str, str]) -> list[Check]:
    present = [
        name
        for name in FORBIDDEN_CONNECTION_VARIABLES
        if environment.get(name, "").strip()
    ]
    checks = [
        Check(
            "no_production_connection_variables",
            not present,
            "none set" if not present else f"set: {', '.join(sorted(present))}",
        )
    ]
    remote: list[str] = []
    for name, value in sorted(environment.items()):
        for match in CONNECTION_URI.finditer(value or ""):
            host = match.group("host").strip().lower()
            if host.split(":", 1)[0] not in LOOPBACK_HOSTS:
                remote.append(name)
                break
    checks.append(
        Check(
            "no_remote_connection_uri_in_environment",
            not remote,
            "none found" if not remote else f"non-loopback target named by: {', '.join(remote)}",
        )
    )
    return checks


def check_pgbackrest_config(path: Path, allowed: set[Path]) -> list[Check]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuardError(f"cannot read pgBackRest configuration {path}") from exc
    remote = [
        line.strip()
        for line in config_lines(text, path, "pgbackrest")
        if REMOTE_PG_OPTION.match(line)
    ]
    checks = [
        Check(
            "pgbackrest_targets_no_remote_postgresql",
            not remote,
            "no pgN-host option present"
            if not remote
            else f"{len(remote)} remote PostgreSQL host options present",
        )
    ]
    data_directories = [
        unquote(value)
        for setting, value in read_settings(path, "pgbackrest")
        if re.fullmatch(r"pg\d*-path", setting)
    ]
    unexpected = [
        candidate
        for candidate in data_directories
        if Path(candidate).resolve() not in allowed
    ]
    checks.append(
        Check(
            "pgbackrest_data_directory_is_declared_ephemeral",
            bool(data_directories) and not unexpected,
            "every pgN-path is a declared ephemeral cluster"
            if data_directories and not unexpected
            else "pgN-path is absent or names an undeclared directory",
        )
    )
    return checks


def evaluate(args: argparse.Namespace, environment: dict[str, str]) -> list[Check]:
    clusters = dict(parse_named_path(item) for item in args.cluster)
    restore_targets = dict(parse_named_path(item) for item in args.restore_target)
    configs = [parse_named_path(item) for item in args.cluster_config]

    checks: list[Check] = []
    checks.extend(check_repository_disjoint(args.production_repo_path, args.rehearsal_repo_path))
    checks.extend(
        check_effective_repository(
            environment,
            args.rehearsal_repo_path,
            args.production_repo_path,
            required=args.require_effective_repo_path,
        )
    )
    checks.append(check_scope_token(args.rehearsal_repo_path, args.scope_token))
    checks.extend(check_ephemeral(args.ephemeral_root, {**clusters, **restore_targets}))
    checks.extend(check_empty(restore_targets))
    if configs:
        checks.extend(check_listen_addresses(configs))
    checks.extend(check_environment(environment))
    if args.pgbackrest_config is not None:
        allowed = {path.resolve() for path in {**clusters, **restore_targets}.values()}
        checks.extend(check_pgbackrest_config(args.pgbackrest_config, allowed))
    return checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--production-repo-path", required=True)
    parser.add_argument("--rehearsal-repo-path", required=True)
    parser.add_argument(
        "--require-effective-repo-path",
        action="store_true",
        help=(
            f"demand that {EFFECTIVE_REPO_PATH_VARIABLE} is set in the environment. "
            "Whenever it is set it is checked regardless; this makes its absence a "
            "failure too, which is what the rehearsal workflow wants and what a "
            "unit test evaluating a fixture does not."
        ),
    )
    parser.add_argument("--scope-token", required=True)
    parser.add_argument("--ephemeral-root", required=True, type=Path)
    parser.add_argument("--cluster", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--restore-target", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--cluster-config", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--pgbackrest-config", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        checks = evaluate(args, dict(os.environ))
    except GuardError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    if not checks:
        print("STOP: no isolation property was evaluated", file=sys.stderr)
        return 2

    for check in sorted(checks, key=lambda item: item.name):
        print(f"{'pass' if check.passed else 'FAIL'} {check.name}: {check.detail}")

    payload = {
        "checks": {check.name: check.passed for check in checks},
        "checks_evaluated": len(checks),
        "isolated": all(check.passed for check in checks),
    }
    serialized = canonical_json(payload)
    payload_sha256 = hashlib.sha256(serialized).hexdigest()
    if args.output is not None:
        args.output.write_bytes(
            canonical_json({**payload, "evidence_sha256": payload_sha256})
        )
    print(f"evidence_sha256={payload_sha256}")

    if not payload["isolated"]:
        failed = sorted(check.name for check in checks if not check.passed)
        print(f"STOP: rehearsal isolation not established: {', '.join(failed)}", file=sys.stderr)
        return 2
    print(f"Rehearsal isolation established across {len(checks)} checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
