#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

if (($# < 3)) || [[ "$1" != "--expect-output" ]]; then
  echo "usage: postgres_restore_status_gate.sh --expect-output absent|exact -- command ..." >&2
  exit 64
fi

EXPECTED="$2"
shift 2
[[ "$EXPECTED" =~ ^(absent|exact)$ ]] || {
  echo "STOP: invalid expected migration state" >&2
  exit 64
}
[[ "${1-}" == "--" ]] || {
  echo "STOP: command separator is required" >&2
  exit 64
}
shift
(($#)) || {
  echo "STOP: status command is required" >&2
  exit 64
}

OUTPUT="$(mktemp)"
trap 'rm -f -- "$OUTPUT"' EXIT
if ! "$@" >"$OUTPUT"; then
  echo "STOP: migration status command failed" >&2
  exit 2
fi
if [[ "$(wc -l <"$OUTPUT" | tr -d '[:space:]')" != "1" ]] ||
  ! grep -qx -- "$EXPECTED" "$OUTPUT"; then
  echo "STOP: migration status output is not exact" >&2
  exit 2
fi
cat "$OUTPUT"
