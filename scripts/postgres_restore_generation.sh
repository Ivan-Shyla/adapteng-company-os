#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

usage() {
  cat >&2 <<'EOF'
Usage: postgres_restore_generation.sh \
  --generation A|B|C \
  --guard-config FILE --guard-config-sha256 SHA256 \
  --selected-set RAW_LOCAL_SET --selected-info FILE \
  --selected-info-sha256 SHA256 \
  --approved-image-manifest FILE \
  --approved-image-manifest-sha256 SHA256 \
  --procedure-manifest-sha256 SHA256 \
  --evidence-dir DIRECTORY
EOF
  exit 64
}

GENERATION=''
GUARD_CONFIG=''
GUARD_CONFIG_SHA256=''
SELECTED_SET=''
SELECTED_INFO=''
SELECTED_INFO_SHA256=''
APPROVED_IMAGE_MANIFEST=''
APPROVED_IMAGE_MANIFEST_SHA256=''
PROCEDURE_MANIFEST_SHA256=''
EVIDENCE_DIR=''

while (($#)); do
  case "$1" in
    --generation) GENERATION="${2-}"; shift 2 ;;
    --guard-config) GUARD_CONFIG="${2-}"; shift 2 ;;
    --guard-config-sha256) GUARD_CONFIG_SHA256="${2-}"; shift 2 ;;
    --selected-set) SELECTED_SET="${2-}"; shift 2 ;;
    --selected-info) SELECTED_INFO="${2-}"; shift 2 ;;
    --selected-info-sha256) SELECTED_INFO_SHA256="${2-}"; shift 2 ;;
    --approved-image-manifest) APPROVED_IMAGE_MANIFEST="${2-}"; shift 2 ;;
    --approved-image-manifest-sha256)
      APPROVED_IMAGE_MANIFEST_SHA256="${2-}"
      shift 2
      ;;
    --procedure-manifest-sha256)
      PROCEDURE_MANIFEST_SHA256="${2-}"
      shift 2
      ;;
    --evidence-dir) EVIDENCE_DIR="${2-}"; shift 2 ;;
    *) usage ;;
  esac
done

[[ "$GENERATION" =~ ^[ABC]$ ]] || usage
for value in \
  "$GUARD_CONFIG" \
  "$GUARD_CONFIG_SHA256" \
  "$SELECTED_SET" \
  "$SELECTED_INFO" \
  "$SELECTED_INFO_SHA256" \
  "$APPROVED_IMAGE_MANIFEST" \
  "$APPROVED_IMAGE_MANIFEST_SHA256" \
  "$PROCEDURE_MANIFEST_SHA256" \
  "$EVIDENCE_DIR"; do
  [[ -n "$value" ]] || usage
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
PROCEDURE_MANIFEST="$SCRIPT_DIR/postgres_restore_procedure_manifest.json"
mkdir -p -- "$EVIDENCE_DIR"
chmod 700 -- "$EVIDENCE_DIR"
GUARD_PACKET="$EVIDENCE_DIR/generation-${GENERATION}-guard.json"

python3 "$SCRIPT_DIR/postgres_restore_guard.py" \
  --generation "$GENERATION" \
  --guard-config "$GUARD_CONFIG" \
  --guard-config-sha256 "$GUARD_CONFIG_SHA256" \
  --selected-set "$SELECTED_SET" \
  --selected-info "$SELECTED_INFO" \
  --selected-info-sha256 "$SELECTED_INFO_SHA256" \
  --approved-image-manifest "$APPROVED_IMAGE_MANIFEST" \
  --approved-image-manifest-sha256 "$APPROVED_IMAGE_MANIFEST_SHA256" \
  --procedure-manifest "$PROCEDURE_MANIFEST" \
  --procedure-manifest-sha256 "$PROCEDURE_MANIFEST_SHA256" \
  --output "$GUARD_PACKET"

packet_value() {
  python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' \
    "$GUARD_PACKET" "$1"
}

IMAGE_CONFIG_ID="$(packet_value image_config_id)"
RECOVERY_CONTAINER="$(packet_value recovery_container)"
FINAL_CONTAINER="$(packet_value final_container)"
VOLUME="$(packet_value volume)"
BOOTSTRAP_NETWORK="$(packet_value bootstrap_network)"
RESTORE_PG1_PATH="$(packet_value restore_pg1_path)"
REPOSITORY_CONFIG_PATH="$(packet_value repository_config_path)"
RESTORE_ENV_PATH="$(packet_value restore_env_path)"
STANZA="$(packet_value stanza)"
REPO="$(packet_value repo)"
STATE_FILE="$(packet_value state_file)"

(
  set -o noclobber
  printf '%s\n' "$(sha256sum "$GUARD_PACKET" | awk '{print $1}')" >"$STATE_FILE"
) 2>/dev/null || {
  echo "STOP: generation use marker already exists" >&2
  exit 2
}

docker run --rm \
  --name "adapteng-pgbackrest-${GENERATION,,}" \
  --network "$BOOTSTRAP_NETWORK" \
  --mount "type=volume,src=$VOLUME,dst=$RESTORE_PG1_PATH" \
  --mount "type=bind,src=$REPOSITORY_CONFIG_PATH,dst=/etc/pgbackrest/pgbackrest.conf,readonly" \
  --env-file "$RESTORE_ENV_PATH" \
  --entrypoint pgbackrest \
  "$IMAGE_CONFIG_ID" \
  --config=/etc/pgbackrest/pgbackrest.conf \
  --stanza="$STANZA" \
  --repo="$REPO" \
  --pg1-path="$RESTORE_PG1_PATH" \
  --set="$SELECTED_SET" \
  --type=immediate \
  --target-action=promote \
  restore

docker start "$RECOVERY_CONTAINER" >/dev/null
for _ in $(seq 1 120); do
  if docker exec -u postgres "$RECOVERY_CONTAINER" \
    /usr/lib/postgresql/16/bin/pg_isready --quiet; then
    break
  fi
  sleep 1
done
docker exec -i -u postgres "$RECOVERY_CONTAINER" \
  /usr/lib/postgresql/16/bin/psql \
  --dbname=adapteng_ops --no-psqlrc -v ON_ERROR_STOP=1 <<'SQL'
DO $assert$
BEGIN
  IF pg_is_in_recovery() THEN
    RAISE EXCEPTION 'restore is still in recovery';
  END IF;
END
$assert$;
SQL
docker stop --time 30 "$RECOVERY_CONTAINER" >/dev/null
docker rm "$RECOVERY_CONTAINER" >/dev/null

# The final container was pre-created on the inspected --internal network and
# contains no repository credentials or public port bindings.
docker start "$FINAL_CONTAINER" >/dev/null
for _ in $(seq 1 60); do
  if docker exec -u postgres "$FINAL_CONTAINER" \
    /usr/lib/postgresql/16/bin/pg_isready --quiet; then
    break
  fi
  sleep 1
done
docker exec -i -u postgres "$FINAL_CONTAINER" \
  /usr/lib/postgresql/16/bin/psql \
  --dbname=adapteng_ops --no-psqlrc -v ON_ERROR_STOP=1 <<'SQL'
DO $assert$
BEGIN
  IF pg_is_in_recovery() THEN
    RAISE EXCEPTION 'final database is still in recovery';
  END IF;
END
$assert$;
SQL

printf 'generation=%s\n' "$GENERATION"
printf 'guard_packet_sha256=%s\n' "$(sha256sum "$GUARD_PACKET" | awk '{print $1}')"
printf 'inventory_sha256=%s\n' "$(packet_value inventory_sha256)"
printf 'measured_image_identity_sha256=%s\n' \
  "$(packet_value measured_image_identity_sha256)"
printf 'selected_set_ref_sha256=%s\n' "$(packet_value selected_set_ref_sha256)"
printf 'status=RESTORED_LOCKED_READY\n'
