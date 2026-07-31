#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

usage() {
  echo "usage: postgres_restore_transaction_probe.sh --procedure-manifest-sha256 SHA256 --runner-image DIGEST --pgpass-file FILE --evidence-dir DIR" >&2
  exit 64
}

PROCEDURE_MANIFEST_SHA256=''
RUNNER_IMAGE=''
PGPASS_FILE=''
EVIDENCE_DIR=''
while (($#)); do
  case "$1" in
    --procedure-manifest-sha256) PROCEDURE_MANIFEST_SHA256="${2-}"; shift 2 ;;
    --runner-image) RUNNER_IMAGE="${2-}"; shift 2 ;;
    --pgpass-file) PGPASS_FILE="${2-}"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="${2-}"; shift 2 ;;
    *) usage ;;
  esac
done
for value in \
  "$PROCEDURE_MANIFEST_SHA256" "$RUNNER_IMAGE" "$PGPASS_FILE" "$EVIDENCE_DIR"; do
  [[ -n "$value" ]] || usage
done
[[ "$RUNNER_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "STOP: runner image is not immutable" >&2
  exit 2
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROCEDURE_MANIFEST="$SCRIPT_DIR/postgres_restore_procedure_manifest.json"
PROBE="$SCRIPT_DIR/postgres_restore_transaction_probe.sql"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
mkdir -p -- "$EVIDENCE_DIR"
chmod 700 -- "$EVIDENCE_DIR"

python3 "$SCRIPT_DIR/postgres_restore_guard.py" \
  --verify-procedure-only \
  --procedure-manifest "$PROCEDURE_MANIFEST" \
  --procedure-manifest-sha256 "$PROCEDURE_MANIFEST_SHA256" \
  --root "$ROOT"

EXPECTED_PROBE_SHA256="$(
  python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["artifacts"]["scripts/postgres_restore_transaction_probe.sql"])' \
    "$PROCEDURE_MANIFEST"
)"
ACTUAL_PROBE_SHA256="$(sha256sum "$PROBE" | awk '{print $1}')"
[[ "$ACTUAL_PROBE_SHA256" == "$EXPECTED_PROBE_SHA256" ]] || {
  echo "STOP: transaction probe digest changed immediately before execution" >&2
  exit 2
}

docker run --rm --network pg-rehearsal \
  --mount "type=bind,src=$PROBE,dst=/transaction-probe.sql,readonly" \
  --mount "type=bind,src=$PGPASS_FILE,dst=/run/secrets/pgpass,readonly" \
  -e PGHOST=adapteng-db-b \
  -e PGPORT=5432 \
  -e PGUSER=postgres \
  -e PGDATABASE=adapteng_ops \
  -e PGSSLMODE=disable \
  -e PGPASSFILE=/run/secrets/pgpass \
  "$RUNNER_IMAGE" \
  /usr/lib/postgresql/16/bin/psql \
  --no-psqlrc -v ON_ERROR_STOP=1 --file=/transaction-probe.sql

printf '%s\n' "$ACTUAL_PROBE_SHA256" >"$EVIDENCE_DIR/transaction-probe.sha256"
printf 'transaction_probe_sha256=%s\n' "$ACTUAL_PROBE_SHA256"
printf 'transaction_result=rolled_back\n'
printf 'durable_synthetic_rows_or_allocator_state=0\n'
printf 'identity_sequence_unchanged=true\n'
