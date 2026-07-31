# Runbook - physical backup and isolated restore of `adapteng_ops`

`adapteng_ops` is the authoritative operational PostgreSQL database in the
Coolify standalone resource `adapteng-ops-db`: PostgreSQL 16 on Hetzner, local
Docker storage, internal network only, and no public database port.

> **Status - proposed, not configured.** This runbook selects an
> operator-managed pgBackRest design using provider-managed Backblaze B2 object
> storage. It is not a provider-managed PostgreSQL backup. It does not show that
> the required approved image manifests, image build, collectors, migration
> harness, pgBackRest, continuous WAL, B2 repository, schedules, alerts, quote,
> backup, or rehearsal exist. The guarded restore/probe/retention scripts in
> `scripts/` are versioned procedure code only; they have not been run against
> Docker or a provider. The
> 2026-07-25 Coolify logical backup does not satisfy this contract. Do not run
> approved-assets migrations until every gate below has a reviewed sanitized
> `PASS`. Current rollout-authorization status:
> `NOT_READY_PENDING_AUTOMATION_EVIDENCE_LIFECYCLE_PR`. The separate automation
> consumer dependency has not merged; no schema version, compatibility,
> configuration, execution, or readiness is claimed.

## Decision

Use an immutable PostgreSQL 16 image containing exactly pgBackRest **2.59.0**
to create encrypted physical full/differential backups and continuously archive
WAL to a private B2 **EU Central** bucket. Keep 12 completed weekly full
generations and their dependent differential/WAL data. Rehearse the accepted
full on three independently created clean, disposable Hetzner PostgreSQL-only
servers, each with one fresh empty Docker volume:

- generation A: exact pre-migration restore and baseline;
- generation B: independent restore, exact 007/Drive-008 apply, and explicit
  post-migration DML transaction rollback proof; and
- generation C: independent re-restore, apply only exact 007/Drive-008, and end
  in the exact migrated state.

This is the smallest accepted design because PostgreSQL physical backup plus WAL
is application-consistent and restorable, pgBackRest verifies and restores its
own repository format, B2 is off-host EU object storage, and a clean hourly
Hetzner server cannot boot copied production applications. It is **not free**:
the owner must complete and approve the quote worksheet below before creating
resources.

### Pinned pgBackRest contract

| Item | Required value |
|---|---|
| Release | `2.59.0` / tag `release/2.59.0` |
| Release commit | `f84c8357d49ea9452cd606531e9c4c322c41bc2e` |
| Distribution tarball SHA-256 | `faaf8faa14a6392279654ee216a493fcd07b0c513af4b55fe34faec062cb8875` |
| Repository format | `5` |
| Retention | `repo1-retention-full=12`, type `count`; archive retention explicitly aligned to 12 fulls |

The implementation must pin the built pgBackRest binary/package bytes as well
as the release artifact. A tag, package channel, or version string alone is not
immutable evidence.

### Rejected substitutions

| Option | Decision |
|---|---|
| Coolify **Scheduled Database Backup** | **Forbidden for this contract.** Coolify documents `pg_dump --format=custom`, which is a logical dump. It may remain an independent convenience export but cannot be the accepted set or rollback source. |
| Manually created, downloaded, or stored SQL/custom dump | **Forbidden.** Moving a logical dump to S3, Storage Box, Drive, or a laptop does not make it an approved physical/WAL backup. |
| Current Baserow all-in-one backup | **Wrong database.** It protects Baserow, not `adapteng-ops-db` / `adapteng_ops`. |
| Hetzner Backup/Snapshot of the running host | **Rejected.** Hetzner states that running-server consistency is not guaranteed. PostgreSQL would require an atomic snapshot of every data, WAL, and tablespace volume; that property is not established for this Docker layout. |
| Powered-off Hetzner server snapshot | Requires an intentional full-host outage and remains a duplicate application host. It is not the physical/WAL repository or rehearsal target. |
| Clone production and block it after boot | **Forbidden.** A cloned host may start Coolify, n8n, adapters, mail, or webhooks before controls are applied. Hetzner permits outbound traffic when no outbound firewall rule exists. |
| SaaS backup implemented with `pg_dump` | **Forbidden.** A managed dashboard does not change a logical export into physical/WAL backup. |
| Move now to managed PostgreSQL | A separate architecture migration, not a shortcut for this acceptance. |

## Non-negotiable stop conditions

Stop without backup acceptance or rehearsal SQL when any of these is true:

- an immutable source image digest or exact source runtime signature is
  unavailable;
- the derived image changes PostgreSQL binaries, libc, locale packages,
  collation behavior, installed extension versions, architecture, `$PGDATA`,
  UID/GID, or tablespace layout;
- pgBackRest is not exactly the pinned release and built artifact;
- a reviewed runtime collector, complete catalog collector, migration-status
  harness, approved image manifest, or procedure manifest is absent or
  unpinned;
- the repository is public, outside EU Central, or has bucket Object Lock
  enabled;
- `check` fails, `verify` does not report exactly `status: ok`, or archive
  health is not fail-closed healthy;
- the accepted set cannot remain normally restorable through the required
  validity date plus margin;
- the restore host contains a production snapshot, application, production
  private network, public PostgreSQL port, Docker socket mount, or production
  credential;
- any runtime/catalog comparison differs, migration state is `drifted`, or a
  gate prints false but exits zero; or
- cleanup, key revocation, or provider deletion cannot be proved.

Do not repair a failed scratch target and continue. Delete it, correct the
design, take a new full, and restart all generations.

## Phase 0 - land the separately reviewed implementation

Coolify documents only its logical backup for a standalone PostgreSQL resource.
Do not install pgBackRest interactively in the live container. Before touching
production, merge a separate implementation PR that supplies these immutable
artifacts:

1. A backup image derived from the exact current PostgreSQL 16 image digest.
   It must preserve the upstream entrypoint, health check, PostgreSQL binary
   bytes, `$PGDATA`, UID/GID, libc/locale packages, extensions, architecture,
   and tablespace behavior. It may add pgBackRest 2.59.0 and only its reviewed
   dependencies; it must not upgrade PostgreSQL, libc, locale, or extensions.
2. A migration-runner image containing automation commit
   `dbcf806ea7714b8e2a7415ae6cd788491924178d`, tree
   `8fc649a97963f38ce0a7592001a7fea4834eceea`, exact PostgreSQL 16 `psql`,
   and no application runtime. Populate the sealed runner manifest with the
   exact repo digest, config ID, platform, `psql` entrypoint, migration
   entrypoint and fixed commands. The tracked runner must measure that stopped
   networkless image before reading database credentials or executing SQL.
3. A status command that imports the exact `MIGRATION.status_sql` values from
   `apply_source_identity_007.py` and `apply_drive_bridge_008.py`, emits only
   `absent|exact|drifted`, and exits nonzero on `drifted`, invalid output, or an
   expected-state mismatch. For the two-expectation calls below it emits one
   `absent` line only when both expected states are absent, and one `exact` line
   when every supplied exact/mixed expectation matches. The tracked status gate
   captures output to a restricted file, rejects a nonzero command before
   comparing that one exact line, and therefore rejects a process that prints
   `exact` then exits 9. This separate harness is required because the
   Drive-008 runner enforces 007 as an exact prerequisite even for its own
   `status` mode.
4. A portable reviewed collector bundle that runs in both the current and
   derived PostgreSQL images without installing/upgrading packages, plus
   canonical compatibility and selected-full assertions with these exact
   interfaces:

   ```text
   capture-runtime-signature --database adapteng_ops --image-identity <measured-json> --output <file>
   capture-catalog-signature --database adapteng_ops --output <file>
   assert-runtime-compatible --before <file> --after <file> --allow-only pgbackrest-addition
   assert-selected-full --set <raw-local-label> --info <file> --not-before <RFC3339>
   migration-status --expect 007=<state> --expect drive-008=<state>
   ```

5. Two separately reviewed immutable image manifests: one for the measured
   source container and one for the derived backup image. Each must use the
   exact schema consumed by
   `scripts/postgres_restore_image_identity.py`: status `APPROVED`, one
   `repository@sha256:<manifest>` reference, exact Docker image config ID,
   `linux/<architecture>`, exact `PGDATA`, PostgreSQL `16`, pgBackRest `absent`
   for the pre-change source or exactly `2.59.0` for the derived image,
   binary/build hashes, reviewer and review time. Mutable
   tags, absent/multiple `RepoDigests`, or a caller-supplied image string fail.
6. The Company OS procedure bundle:
   `scripts/postgres_restore_generation.sh` is the **only permitted restore
   entrypoint**; its descriptor-bound guard/orchestrator, measured-image helper,
   signed provider collector policy, sealed runner, retention/status helpers,
   tracked transaction probe and generation-C assertion are enumerated in
   `scripts/postgres_restore_procedure_manifest.json`. The reviewed procedure
   manifest SHA-256 is
   `e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01`;
   the transaction-probe SHA-256 is
   `236097579a1711888828a69691f0ea69da1c3a5d65de39682a031c7ebff68872`.
   Any mismatch is a stop condition.
7. Fail-closed weekly full, daily differential, post-backup `check`, WAL health,
   disk/repository growth, and alert scheduling.
8. A rollback to the exact current image digest with no database major-version
   or volume change.

The runtime collector must produce canonical UTF-8/LF JSON with sorted keys and
arrays, exclude volatile capture time/host/container names from the signed
payload, and include:

- source and derived immutable image digests, OS, architecture, and image
  manifest architecture;
- `SELECT version()`, `server_version_num`, `postgres --version`, and SHA-256
  of the PostgreSQL server binary;
- pgBackRest version, repository format, binary SHA-256, and package/build
  artifact SHA-256;
- libc implementation/version, locale package/version, configured locales, and
  relevant OS release facts;
- database encoding, `LC_COLLATE`, `LC_CTYPE`, all used collation providers,
  configured collation versions, and actual collation versions;
- installed extension names and versions; and
- `$PGDATA`, architecture, and complete tablespace names/locations/layout.

The catalog collector must use one `REPEATABLE READ, READ ONLY` transaction and
produce canonical UTF-8/LF JSON. It must enumerate every non-system schema and
deterministically include:

- schemas and owners;
- tables, partitions, columns, types, defaults, identity/generated properties,
  and nullability;
- constraints and full definitions;
- indexes and full definitions;
- functions/procedures, argument and result types, complete definitions,
  languages, owners, security settings, configuration, and ACLs;
- table/schema/routine owners and ACLs;
- sequences, identity ownership/dependencies, data type, start/min/max,
  increment, cache, cycle, owner, ACL, and canonical `last_value/is_called`
  state;
- extensions and versions;
- tablespaces and layout; and
- every migration ledger/catalog present, including complete ordered entries.

Raw signatures are local restricted evidence and never enter Git. Only their
SHA-256 values and equality results are shareable. If the collectors cannot
capture this complete contract or the derived image cannot match the source,
**STOP** and require a separately reviewed image/collector design. This
documentation does not claim that design already exists or is viable.

Record the implementation values before proceeding:

```yaml
implementation:
  source_image_manifest_sha256: "<sha256 from separate reviewed build>"
  source_measured_image_identity_sha256: "<sha256>"
  backup_image_manifest_sha256: "<sha256 from separate reviewed build>"
  backup_measured_image_identity_sha256: "<sha256>"
  migration_runner_image_digest: "<immutable digest>"
  pgbackrest_binary_sha256: "<sha256>"
  runtime_collector_sha256: "<sha256>"
  catalog_collector_sha256: "<sha256>"
  runtime_compatibility_assertion_sha256: "<sha256>"
  selected_full_assertion_sha256: "<sha256>"
  migration_status_harness_sha256: "<sha256>"
  restore_procedure_manifest_sha256: "e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01"
  runner_manifest_sha256: "<sha256>"
  provider_manifest_sha256: "<sha256>"
  inventory_exporter_manifest_sha256: "<sha256>"
  transaction_probe_sha256: "236097579a1711888828a69691f0ea69da1c3a5d65de39682a031c7ebff68872"
  generation_c_final_assertion_sha256: "c19f862c21e00c62dbed57b496f3f05da8db357d58db2b58b5a6cdcd078c65f9"
```

Any placeholder or mutable tag is a stop condition.

## Phase 1 - approve the provider quote

Prices below are public reference prices checked on **2026-07-31**. Provider
checkout and the invoice are authoritative. Hetzner prices are excluding VAT;
Backblaze prices are in USD. Add VAT/reverse-charge treatment, foreign exchange,
and every checkout line before approval.

The selected restore candidate is Hetzner **CX23** in Germany: x86 shared
compute at EUR 0.0088/hour, capped at EUR 5.49/month, excluding IPv4 and VAT.
Use it only if the source signature is x86_64 and its included local disk shown
at checkout is at least twice the measured uncompressed cluster plus image,
WAL, and working-space headroom. Otherwise stop and quote the smallest
architecture-compatible SKU that meets the measured requirement.

The rehearsal uses three separately created servers, one each for A/B/C, for an
expected aggregate 12 server-hours and a hard aggregate cap of 24 server-hours
(8 hours per server). A Primary IPv4 is selected for owner-only SSH bootstrap at
EUR 0.0008/hour, capped at EUR 0.50/month per server. PostgreSQL remains
unpublished.
Use no paid Volume when measured capacity fits the included local disk; if a
Volume is required, quote its exact GB/hour/month price and remember that
Hetzner server Backups/Snapshots do not include Volumes.

Complete this worksheet using actual measured GB and provider UI quotes:

```yaml
quote:
  accessed_at_utc: "<RFC3339>"
  hetzner:
    location: "<Germany location>"
    sku: "CX23 or reviewed replacement"
    architecture_matches_source: true
    hourly_eur_ex_vat: 0.0088
    monthly_cap_eur_ex_vat: 5.49
    generation_server_count: 3
    expected_aggregate_server_hours: 12
    hard_cap_aggregate_server_hours: 24
    hard_cap_compute_eur_ex_vat: 0.2112
    included_local_disk_gb: "<checkout value>"
    measured_required_working_gb: "<2x cluster plus headroom>"
    paid_volume_gb: 0
    paid_volume_eur_ex_vat: 0.00
    primary_ipv4_hourly_eur_ex_vat: 0.0008
    primary_ipv4_hard_cap_eur_ex_vat: 0.0192
    other_checkout_cost_eur_ex_vat: "<amount>"
    vat_or_reverse_charge: "<account-specific treatment>"
  b2:
    active_average_gb: "<measured>"
    hidden_version_average_gb: "<measured>"
    storage_usd_per_gb_month: 0.00695
    active_storage_usd_month: "<active_average_gb * 0.00695>"
    hidden_storage_usd_month: "<hidden_version_average_gb * 0.00695>"
    verify_download_gb: "<measured selected-set read>"
    restore_download_gb_each: ["<A>", "<B>", "<C>"]
    free_egress_allowance_gb: "<3 * average monthly stored GB>"
    chargeable_egress_gb: "<max(0, verify + A + B + C - allowance)>"
    excess_egress_usd_per_gb: 0.01
    excess_egress_usd: "<chargeable_egress_gb * 0.01>"
    api_transactions: "Class A/B/C currently published as free; Class D not used"
    other_provider_cost_usd: "<amount>"
  approved_by: "Ivan"
  quote_sha256: "<sha256 of sanitized completed worksheet>"
```

B2 active and hidden versions are both billable stored bytes. Calculate gross
storage cost before applying any account allowance. The current published
egress allowance is three times average monthly storage; `verify` plus three
restores can exceed it. Current Class A/B/C API transactions may be entered as
zero only because the official transaction-pricing page currently lists them
as free. Do not generalize that to storage, egress, Class D, tax, or future
prices.

**Stop** if sizing, architecture, quote date, tax treatment, hard cap, B2 hidden
versions, egress, or any nonzero provider line is unknown.

## Phase 2 - create the off-host B2 repository

1. During Backblaze account creation choose **EU Central**. The account region
   cannot later be changed.
2. In **B2 Cloud Storage -> Buckets -> Create a Bucket**:
   - create a uniquely named **Private** bucket;
   - enable default server-side encryption (SSE-B2);
   - set the bucket **Object Lock feature itself to Disabled**;
   - apply hidden-version deletion after 35 days and unfinished-large-file
     cancellation after 7 days to only the pgBackRest repository prefix.
3. In **Application Keys -> Add a New Application Key**, create a standard key
   restricted to that bucket and prefix with only list/read/write/delete
   capabilities needed for backup and expiry. Do not use the master key.
4. Record the account's B2 S3 endpoint/region from the UI. Do not copy an
   example endpoint.
5. Store the runtime key ID/secret and a new independent repository cipher
   passphrase only in Coolify secrets. Store a recovery copy of the cipher
   passphrase in the company password manager.

Object Lock must not merely have default retention off. Backblaze allows Object
Lock to be enabled later, but once enabled on a bucket it cannot be disabled.
Locked objects can reject pgBackRest/lifecycle deletion and continue accruing
storage cost. Any future immutability control requires a separate repository
compatibility, expiry, restore, and cost rehearsal.

**Stop** if the account is outside EU Central, the bucket is public, Object Lock
is enabled, encryption is off, lifecycle overlaps outside the repository
prefix, a broad/master key is proposed, or the cipher passphrase lacks a
separate recovery copy.

## Phase 3 - configure, capture source signatures, and take the full

The reviewed non-secret configuration must have this shape:

```ini
[global]
repo1-type=s3
repo1-path=/postgres-physical-backup
repo1-s3-bucket=<private-bucket-name>
repo1-s3-endpoint=<B2-S3-endpoint>
repo1-s3-region=<B2-region>
repo1-s3-uri-style=path
repo1-storage-verify-tls=y
repo1-cipher-type=aes-256-cbc
repo1-retention-full=12
repo1-retention-full-type=count
repo1-retention-archive=12
repo1-retention-archive-type=full
compress-type=zst
start-fast=y

[adapteng-ops]
pg1-path=<verified-PGDATA>
pg1-port=5432
```

The reviewed PostgreSQL settings are:

```ini
wal_level = replica
archive_mode = on
archive_command = 'pgbackrest --stanza=adapteng-ops archive-push %p'
archive_timeout = 15min
```

`archive_mode` requires a restart. In Coolify:

1. Open `adapteng-ops-db`; record PostgreSQL 16, no public port, healthy state,
   exact current image digest, volume, `$PGDATA`, and tablespaces.
2. Stop the adapter and every governed database writer.
3. Stage the reviewed portable collectors and
   `scripts/postgres_restore_image_identity.py` without a package install or
   volume change. Verify the separately reviewed source-image manifest against
   the actual container `.Image`, image config ID, platform and sole
   `RepoDigest`; capture `/secure/pre-change-image-identity.json` and then
   `/secure/pre-change-runtime.json` against that measured packet and
   `/secure/pre-change-catalog.json`, hash both, then remove the staged
   collector. Keep writers stopped.
4. Deploy only the reviewed backup image/configuration and perform one planned
   restart.
5. Verify the separately reviewed derived-image manifest against the actual
   post-deploy container, then capture `source-image-identity.json`,
   `source-runtime.json` and `source-catalog.json`. Run
   `assert-runtime-compatible` and require exact equality for every protected
   PostgreSQL/libc/locale/collation/extension/architecture/tablespace field,
   allowing only the pinned pgBackRest addition and reviewed dependencies.
   Require byte equality between pre-change and post-deploy catalog signatures.
   Roll back immediately on any failure.
6. Keep consumers stopped until stanza creation, archive check, the selected
   full, post-backup check, repository verification, and archive assertion all
   pass.

The exact pre-change/post-deploy gate is:

```bash
set -euo pipefail
umask 077

python3 scripts/postgres_restore_image_identity.py \
  --container adapteng-ops-db \
  --approved-manifest /secure/source-image-manifest.json \
  --approved-manifest-sha256 "$SOURCE_IMAGE_MANIFEST_SHA256" \
  --output /secure/pre-change-image-identity.json
capture-runtime-signature \
  --database adapteng_ops \
  --image-identity /secure/pre-change-image-identity.json \
  --output /secure/pre-change-runtime.json
capture-catalog-signature \
  --database adapteng_ops \
  --output /secure/pre-change-catalog.json
sha256sum \
  /secure/pre-change-runtime.json \
  /secure/pre-change-catalog.json

# Deploy/restart only after the two pre-change files exist.

python3 scripts/postgres_restore_image_identity.py \
  --container adapteng-ops-db \
  --approved-manifest /secure/backup-image-manifest.json \
  --approved-manifest-sha256 "$BACKUP_IMAGE_MANIFEST_SHA256" \
  --output /secure/source-image-identity.json
capture-runtime-signature \
  --database adapteng_ops \
  --image-identity /secure/source-image-identity.json \
  --output /secure/source-runtime.json
capture-catalog-signature \
  --database adapteng_ops \
  --output /secure/source-catalog.json
assert-runtime-compatible \
  --before /secure/pre-change-runtime.json \
  --after /secure/source-runtime.json \
  --allow-only pgbackrest-addition
cmp -s /secure/pre-change-catalog.json /secure/source-catalog.json
sha256sum \
  /secure/pre-change-image-identity.json \
  /secure/source-image-identity.json \
  /secure/source-runtime.json \
  /secure/source-catalog.json
SOURCE_SIGNATURE_CAPTURED_AT="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
printf '%s\n' "$SOURCE_SIGNATURE_CAPTURED_AT" \
  > /secure/source-signatures-captured-at
```

Take and verify the selected full:

```bash
set -euo pipefail
umask 077
SOURCE_SIGNATURE_CAPTURED_AT="$(
  cat /secure/source-signatures-captured-at
)"
export SOURCE_SIGNATURE_CAPTURED_AT

pgbackrest --stanza=adapteng-ops stanza-create
pgbackrest --stanza=adapteng-ops check
pgbackrest --stanza=adapteng-ops --type=full backup

# Select the exact new full from local restricted info output. The raw label
# stays local; share only SET_REF_SHA256.
export SET='<exact-new-full-label>'
test -n "$SET"
printf '%s' "$SET" | sha256sum > /secure/selected-set-ref.sha256

pgbackrest --stanza=adapteng-ops --set="$SET" \
  --output=json info > /secure/selected-set-info.json
assert-selected-full \
  --set "$SET" \
  --info /secure/selected-set-info.json \
  --not-before "$SOURCE_SIGNATURE_CAPTURED_AT"
sha256sum /secure/selected-set-info.json

# check proves live stanza/archive flow; verify reads repository files.
pgbackrest --stanza=adapteng-ops check
pgbackrest --stanza=adapteng-ops --set="$SET" \
  --output=text --verbose verify > /secure/selected-set-verify.txt

# pgBackRest 2.59 verify reports repository errors in text while it can still
# exit zero. Successful status text requires --verbose. Require exactly one
# status line and exactly "status: ok".
status_lines="$(grep -c '^status:' /secure/selected-set-verify.txt || true)"
test "$status_lines" -eq 1
grep -qx 'status: ok' /secure/selected-set-verify.txt
sha256sum /secure/selected-set-verify.txt

psql --dbname=adapteng_ops --no-psqlrc -v ON_ERROR_STOP=1 <<'SQL'
DO $assert$
DECLARE
  s pg_stat_archiver%ROWTYPE;
BEGIN
  SELECT * INTO s FROM pg_stat_archiver;
  IF s.last_archived_time IS NULL THEN
    RAISE EXCEPTION 'archive health failed: no archived WAL';
  END IF;
  IF s.last_failed_time IS NOT NULL
     AND s.last_failed_time >= s.last_archived_time THEN
    RAISE EXCEPTION 'archive health failed: latest archive attempt failed';
  END IF;
END
$assert$;
SQL
```

`check` validates the configured live database and archive flow. `verify`
validates repository backup/archive files and metadata. They are separate
mandatory gates. Do not provision a restore server before both pass for the
selected set and the archive assertion exits zero.

Enable the reviewed schedule after `check`/`verify` and before retention
acceptance, so the exporter can measure both active approved timers:

- weekly: `pgbackrest --stanza=adapteng-ops --type=full backup`;
- daily between fulls:
  `pgbackrest --stanza=adapteng-ops --type=diff backup`;
- after every backup: `pgbackrest --stanza=adapteng-ops check`;
- alert on any command failure, `failed_count` increase, stale
  `last_archived_time`, WAL backlog, repository growth, or local disk pressure.

The selected backup-set identity is shared only as the SHA-256 of the raw label.
Raw label, info JSON, verify output, manifests, paths, and WAL remain restricted
local evidence. `assert-selected-full` must exit nonzero unless the JSON contains
exactly the requested newly completed **full** set, repository format 5, no
backup error, nonempty archive start/stop, and a completion time after the
source signatures.

## Phase 4 - prove retention validity

The accepted full must cover:

- rollout: 21 days;
- rollback window after rollout: 35 days; and
- safety margin: 14 days.

Required validity is therefore 70 days from the accepted full. With count-based
retention of 12, the accepted set expires only after 12 newer full backups have
successfully completed. A weekly schedule normally yields about 84 days, but
count retention is not a time guarantee: an extra successful full accelerates
expiry.

Run only the separately reviewed exporter pinned by
`postgres_restore_inventory_exporter_manifest.json`. Both restricted canonical
JSON documents must carry its exact `exporter_id`, `exporter_version`, and
`exporter_artifact_sha256`:

- scheduler inventory: generation time, exactly one UTC weekly full job, one
  pinned Monday-Saturday differential job, the next 12 exact full timestamps,
  and a digest of all other enabled systemd timer
  services/referenced executables, cron/at files, and Docker scheduler command
  surfaces; any additional pgBackRest backup command stops export;
- repository inventory: generation time, the digest of the root-owned
  pgBackRest config bytes, measured `retention_full=12`, measured type `count`,
  the raw selected set and every currently completed full
  label/completion/status.

The raw inventories and set label never enter Git. Derive the completion time
only from the already verified `selected-set-info.json`; do not type or copy it
into an argument. Export through the fixed artifact, which verifies the exact
root-owned full and differential systemd timer/service bytes, active/enabled
timers, exact `FragmentPath`, no effective drop-ins, exact `OnCalendar` values,
its own pinned digest, every enabled or currently active/transient timer, all
standard Debian
cron/anacron/at and Docker scheduler surfaces, and current `pgbackrest info`:

```bash
python3 scripts/postgres_restore_inventory_exporter.py \
  --selected-set "$SET" \
  --scheduler-output /secure/scheduler-inventory.json \
  --repository-output /secure/repository-inventory.json
SCHEDULER_INVENTORY_SHA256="$(
  sha256sum /secure/scheduler-inventory.json | cut -d' ' -f1
)"
REPOSITORY_INVENTORY_SHA256="$(
  sha256sum /secure/repository-inventory.json | cut -d' ' -f1
)"
```

Then run the tracked acceptance gate:

```bash
set -euo pipefail
install -d -o root -g root -m 0700 \
  /var/lib/adapteng/postgres-restore-rehearsal
python3 scripts/postgres_restore_retention.py \
  --mode acceptance \
  --selected-set "$SET" \
  --selected-info /secure/selected-set-info.json \
  --selected-info-sha256 "$SELECTED_INFO_SHA256" \
  --scheduler-inventory /secure/scheduler-inventory.json \
  --scheduler-inventory-sha256 "$SCHEDULER_INVENTORY_SHA256" \
  --repository-inventory /secure/repository-inventory.json \
  --repository-inventory-sha256 "$REPOSITORY_INVENTORY_SHA256"
ACCEPTED_RETENTION_PACKET_SHA256="$(
  sha256sum \
    /var/lib/adapteng/postgres-restore-rehearsal/retention/accepted.json |
    cut -d' ' -f1
)"
```

Acceptance creates the fixed root-owned
`/var/lib/adapteng/postgres-restore-rehearsal/retention` directory and writes
the selected-info bytes, scheduler bytes, repository bytes, and canonical
`accepted.json` with `O_EXCL`. It prints the accepted packet hash/reference.
Existing/symlinked paths fail. The packet binds exact source digests, exporter
identity/manifest digest, selected full hash and completion, observation time,
12 weekly slots/cadence, and count-based `retention_valid_until`. The gate
counts completed fulls newer than the selected set and treats the corresponding
future scheduled full as expiry; validity is one second before that transition.

Immediately before rollout authorization, export **fresh** scheduler and
repository inventories again and invoke:

```bash
python3 scripts/postgres_restore_inventory_exporter.py \
  --selected-set "$SET" \
  --scheduler-output /secure/authorization-scheduler-inventory.json \
  --repository-output /secure/authorization-repository-inventory.json
AUTH_SCHEDULER_SHA256="$(
  sha256sum /secure/authorization-scheduler-inventory.json | cut -d' ' -f1
)"
AUTH_REPOSITORY_SHA256="$(
  sha256sum /secure/authorization-repository-inventory.json | cut -d' ' -f1
)"
python3 scripts/postgres_restore_retention.py \
  --mode authorization \
  --selected-set "$SET" \
  --selected-info /secure/selected-set-info.json \
  --selected-info-sha256 "$SELECTED_INFO_SHA256" \
  --scheduler-inventory /secure/authorization-scheduler-inventory.json \
  --scheduler-inventory-sha256 "$AUTH_SCHEDULER_SHA256" \
  --repository-inventory /secure/authorization-repository-inventory.json \
  --repository-inventory-sha256 "$AUTH_REPOSITORY_SHA256" \
  --accepted-packet-sha256 "$ACCEPTED_RETENTION_PACKET_SHA256" \
  --rollout-start "$ACTUAL_ROLLOUT_START"
```

Authorization loads only the fixed accepted packet/source objects, re-verifies
their canonical hash and bound digests, and cannot accept caller-selected
replacement acceptance paths. It writes fixed `authorized.json` with `O_EXCL`.
Both current inventories must be at most 15 minutes old; actual rollout start
must be contemporaneous and no later than completion +21 days; validity must
cover actual start +35 rollback days +14 safety days. There is no prospective
`no_extra_full_or_policy_change` assertion: fresh repository/scheduler digests
capture changes and shorten or fail the horizon.

Failed fulls do not count. Hidden B2 versions and Object Lock are not normally
restorable pgBackRest sets and do not extend acceptance.

## Phase 5 - create one disposable host per generation

Never create a rehearsal host from a production Backup/Snapshot. Create three
independent clean hosts, `pg-restore-a`, `pg-restore-b`, and `pg-restore-c`; do
not reuse or rename one host across generations.

1. In **Hetzner Cloud -> Firewalls**, create:
   - `pg-restore-bootstrap`: inbound TCP 22 from the owner's current `/32`
     only; outbound DNS TCP/UDP 53 and HTTPS TCP 443 only.
   - `pg-restore-locked`: inbound TCP 22 from that same reviewed `/32` only,
     plus one outbound TCP/9 rule to `192.0.2.1/32`. Configured outbound rules
     are an allowlist, so this TEST-NET destination denies real egress while
     retaining owner-only transfer of the signed current-state packet. No
     PostgreSQL port is allowed.
2. For each generation, create the approved architecture-compatible server from
   a clean Debian image in Germany. Set the exact hostname and labels
   `purpose=postgres-restore-rehearsal,generation=<A|B|C>`, attach
   `pg-restore-bootstrap` at creation, and attach no production snapshot,
   private network, Volume, cloud-init payload, application image, or production
   identifier.
3. Install Docker from the official repository. Pull only the separately
   reviewed immutable PostgreSQL/pgBackRest and migration-runner repo digests.
   Transfer the reviewed procedure, root-owned mode-0600 configuration,
   temporary read/list B2 key, separate repository cipher, and exact manifests.
   Transfer the exact canonical `accepted.json` emitted by Phase 4 to a
   root-only temporary path and independently verify its printed SHA-256. The
   next step installs it at its fixed path after creating the parent directory.
4. Create root-owned private state plus one bootstrap and one internal network:

   ```bash
   install -d -o root -g root -m 0700 \
     /var/lib/adapteng/postgres-restore-rehearsal \
     /var/lib/adapteng/postgres-restore-rehearsal/retention \
     /run/adapteng/postgres-restore-provider
   install -o root -g root -m 0600 /secure/accepted.json \
     /var/lib/adapteng/postgres-restore-rehearsal/retention/accepted.json
   docker network create pg-restore-bootstrap
   docker network create --internal pg-rehearsal
   ```

5. The separately reviewed image/build work must populate the approved
   PostgreSQL image manifest, `postgres_restore_runner_manifest.json`,
   `postgres_restore_provider_manifest.json`, and
   `postgres_restore_inventory_exporter_manifest.json` with immutable
   identities. Their current `NOT_CONFIGURED` values are deliberate stop
   conditions. The provider manifest pins the collector digest, Ed25519 public
   key, owner-SSH `/32` digest, and 120-second freshness. Until all are
   independently reviewed and `APPROVED`, stop before restore.
6. Never publish a container port and never mount `/var/run/docker.sock`.

The final database uses no repository key/cipher, repository mount, application
credential, production DSN, or public port. The fixed runner credential file is
`/run/secrets/postgres-restore-runner.env`, root-owned mode 0600, and contains
only `PGHOST=adapteng-db-<generation-lower>`, `PGPORT=5432`,
`PGDATABASE=adapteng_ops`, `PGUSER=postgres_restore_runner`,
`PGOPTIONS=-c role=postgres`, `PGSSLMODE=disable`, and a generated
32-or-more-character scratch-only
`PGPASSWORD` using only `A-Za-z0-9_./+=:@%-`. No production credential is
copied. The sealed runner reads and
validates these exact descriptor-held bytes, passes `/proc/self/fd/<fd>` to
Docker, and binds only the non-secret target-identity digest to evidence.

For the host's single generation, create exactly one new local Docker volume
with the exact purpose/generation/new labels. Pre-create the recovery and final
containers **stopped on Docker network `none`** from the approved repo digest.
The one-shot helper alone receives repository config/secrets after the guard;
the final container receives only the same volume/PGDATA and scratch database
environment. It has no bind mounts. Evidence collectors write temporary
in-container files that the owner copies out with `docker cp` and immediately
deletes.

After independently verifying the approved manifest digest, create A as follows
(repeat only on its independent B or C host with exact generation names):

```bash
set -euo pipefail
GEN=a
docker volume create \
  --label adapteng.restore.purpose=postgres-restore-rehearsal \
  --label adapteng.restore.generation=A \
  --label adapteng.restore.new=true \
  adapteng-restore-a >/dev/null
docker create --name adapteng-recover-a \
  --network none \
  --mount "type=volume,src=adapteng-restore-a,dst=$APPROVED_POSTGRES_PGDATA" \
  "$APPROVED_REPO_DIGEST" >/dev/null
docker create --name adapteng-db-a \
  --network none \
  --mount "type=volume,src=adapteng-restore-a,dst=$APPROVED_POSTGRES_PGDATA" \
  --env-file /secure/database-a.env \
  "$APPROVED_REPO_DIGEST" >/dev/null
```

The restricted generation guard JSON uses the exact schema enforced by
`scripts/postgres_restore_guard.py`: purpose/generation; exact hostname;
root-owned host identity bound to `/etc/machine-id`, DMI product UUID and cloud
`instance_id`; exact container/volume/network names and PGDATA; B2
endpoint/bucket/region; config, restore environment and read/list-only
key-attestation digests; selected-set reference/info digest; image
manifest/platform; and restricted network/forbidden-identifier inventories.
Unknown/missing fields fail. The wrapper, not the caller, creates its fixed
generation directory and evidence objects with `O_EXCL`, parses the guard packet
once from held bytes, projects immutable values, copies verified config/secret
bytes into that root-owned directory, and never reopens a caller-replaceable
pathname before Docker restore.

The forbidden file must include `adapteng-ops-db`,
`postgres-adapteng-ops`, and every canonical production container ID/name,
endpoint/DNS name, volume and network ID from the restricted production
inventory. The guard scans config plus complete Docker
container/volume/network/port/mount/image inventory for those values. It also
requires only the two expected stopped containers, only the new empty normal
local volume, built-in plus two expected networks, no symlink/reparse/mount
escape, no port publication and no Docker socket.

Run the target-image helper separately for the source and every final A/B/C
container. It reads actual Docker `.Image`, image config ID, OS/architecture,
image `PGDATA` and exactly one `RepoDigest`; its measured packet, never a caller
image string, is the runtime collector input. Migration/status/probe commands do
not accept a caller image: the sealed runner creates a stopped networkless
measurement container, checks actual config ID/sole RepoDigest/platform, and
then forces the exact reviewed entrypoint and arguments.

```bash
set -euo pipefail
capture_generation() {
  GEN="$1"
  IDENTITY="/secure/${GEN}-image-identity.json"
  python3 scripts/postgres_restore_image_identity.py \
    --container "adapteng-db-${GEN}" \
    --approved-manifest /secure/backup-image-manifest.json \
    --approved-manifest-sha256 "$BACKUP_IMAGE_MANIFEST_SHA256" \
    --output "$IDENTITY"
  docker cp "$IDENTITY" \
    "adapteng-db-${GEN}:/tmp/${GEN}-image-identity.json"
  docker exec -u postgres "adapteng-db-${GEN}" \
    capture-runtime-signature \
    --database adapteng_ops \
    --image-identity "/tmp/${GEN}-image-identity.json" \
    --output "/tmp/${GEN}-runtime.json"
  docker exec -u postgres "adapteng-db-${GEN}" \
    capture-catalog-signature \
    --database adapteng_ops \
    --output "/tmp/${GEN}-catalog.json"
  docker cp "adapteng-db-${GEN}:/tmp/${GEN}-runtime.json" \
    "/secure/${GEN}-runtime.json"
  docker cp "adapteng-db-${GEN}:/tmp/${GEN}-catalog.json" \
    "/secure/${GEN}-catalog.json"
  docker exec -u postgres "adapteng-db-${GEN}" rm -f \
    "/tmp/${GEN}-image-identity.json" \
    "/tmp/${GEN}-runtime.json" \
    "/tmp/${GEN}-catalog.json"
}
```

**Stop and delete the generation server** if any application container/image exists, a
production network or snapshot is attached, PostgreSQL is published, the
Docker socket is mounted, SQL is accepted before egress lock, a locked HTTPS
probe succeeds, or the restore key can write/delete.

## Phase 6 - restore generation A

For every generation use the same accepted raw `$SET` and a genuinely fresh
empty volume. **Do not run `pgbackrest restore` directly.** The sole entrypoint
for A is:

```bash
set -euo pipefail
scripts/postgres_restore_generation.sh \
  --generation A \
  --guard-config /secure/generation-A.json \
  --guard-config-sha256 "$GENERATION_A_CONFIG_SHA256" \
  --selected-set "$SET" \
  --selected-info /secure/selected-set-info.json \
  --selected-info-sha256 "$SELECTED_INFO_SHA256" \
  --approved-image-manifest /secure/backup-image-manifest.json \
  --approved-image-manifest-sha256 "$BACKUP_IMAGE_MANIFEST_SHA256" \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --accepted-retention-packet-sha256 "$ACCEPTED_RETENTION_PACKET_SHA256"
```

For B/C change only the generation/config digest on that generation's independent
host. The wrapper passes the private staged config, exact stanza, repo `1`, set,
and generation PGDATA to pgBackRest; no ambient stanza/PGDATA is accepted. It
restores while both database containers remain stopped/networkless, then waits
up to ten minutes for a signed current provider packet before any SQL.

While it waits, the owner must detach `pg-restore-bootstrap`, attach only
`pg-restore-locked`, and prove an HTTPS request from the host fails. On a
separate trusted owner workstation with the pinned read-only Hetzner token:

```bash
set -euo pipefail
umask 077
python3 scripts/postgres_restore_provider_inventory.py \
  A "$HETZNER_SERVER_ID" "$OWNER_SSH_CIDR" \
  >generation-A.json
openssl pkeyutl -sign -inkey /secure/provider-inventory-ed25519.pem \
  -rawin -in generation-A.json -out generation-A.sig
scp generation-A.json generation-A.sig \
  "root@$RESTORE_HOST:/run/adapteng/postgres-restore-provider/"
ssh "root@$RESTORE_HOST" \
  'chown root:root /run/adapteng/postgres-restore-provider/generation-A.* &&
   chmod 0600 /run/adapteng/postgres-restore-provider/generation-A.*'
```

The wrapper reads each root-owned mode-0600 object once, verifies the Ed25519
signature against the sealed public key, collector digest/version, exact host
instance hash, generation, exclusive firewall attachment/policy, owner `/32`
digest and 120-second freshness. Only then does it connect recovery and final
containers solely to `pg-rehearsal`, run fail-closed recovery SQL, and emit
`RESTORED_LOCKED_READY`. A caller boolean or pre-lock attestation never permits
SQL. Bind provider/isolation, guard, selected-set and measured-image digests;
never raw provider IDs/endpoints.

For generation A:

1. Confirm no A state directory exists; create the exact stopped containers and
   fresh empty volume, then invoke the wrapper once.
2. Perform the provider lock, signed fresh measurement and transfer above.
   Continue only after the wrapper returns `RESTORED_LOCKED_READY`.
3. Confirm final A is running only on `pg-rehearsal`, with no published port.
4. Capture `a-runtime.json`; require byte-for-byte equality with
   `source-runtime.json` and record both SHA-256 values.
5. Capture `a-pre-catalog.json`; require byte-for-byte equality with
   `source-catalog.json` and record both SHA-256 values.
6. Run the pinned status harness:

   ```bash
   set -euo pipefail
   capture_generation a
   mv /secure/a-catalog.json /secure/a-pre-catalog.json
   cmp -s /secure/source-runtime.json /secure/a-runtime.json
   cmp -s /secure/source-catalog.json /secure/a-pre-catalog.json
   python3 scripts/postgres_restore_runner.py bootstrap-role --generation A \
     --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01
   scripts/postgres_restore_status_gate.sh --generation A \
     --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
     --expect-output absent \
     --expect 007=absent --expect drive-008=absent
   sha256sum /secure/a-runtime.json /secure/a-pre-catalog.json
   python3 scripts/postgres_restore_runner.py drop-role --generation A \
     --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01
   ```

Any comparison or status mismatch exits nonzero. Generation A proves only the
accepted pre-migration restore. It is not a migration rollback claim.

Delete the entire A host/volumes after preserving restricted comparison
signatures. B starts on its independently created clean host.

## Phase 7 - independent generation B and DML rollback

Restore B from the same accepted set into a new empty
`adapteng-restore-b` volume. Repeat the runtime match, pre-catalog match, and
both `absent` status assertions. Invoke the sole restore entrypoint on `pg-restore-b` exactly as Phase 6 with `--generation B`,
its reviewed guard digest and a freshly signed generation-B provider packet;
change no selected-set/image/procedure argument.

```bash
set -euo pipefail
capture_generation b
mv /secure/b-catalog.json /secure/b-pre-catalog.json
cmp -s /secure/source-runtime.json /secure/b-runtime.json
cmp -s /secure/source-catalog.json /secure/b-pre-catalog.json
python3 scripts/postgres_restore_runner.py bootstrap-role --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01
scripts/postgres_restore_status_gate.sh --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output absent \
  --expect 007=absent --expect drive-008=absent
```

Apply only the exact fixed runners from the pinned automation tree:

```bash
set -euo pipefail
scripts/postgres_restore_status_gate.sh --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output absent \
  --expect 007=absent --expect drive-008=absent
python3 scripts/postgres_restore_runner.py apply-007 --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  >/dev/null
scripts/postgres_restore_status_gate.sh --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output exact \
  --expect 007=exact --expect drive-008=absent

# Drive-008 contains its own BEGIN/COMMIT. Never wrap, edit, or reseal it.
python3 scripts/postgres_restore_runner.py apply-drive-008 --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  >/dev/null
scripts/postgres_restore_status_gate.sh --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output exact \
  --expect 007=exact --expect drive-008=exact
```

The runner pins raw migration bytes, the PostgreSQL 16 `psql` path, and exact
post-apply state. Migration 007 may use the runner's single transaction.
Drive-008 uses its embedded transaction and must not be wrapped or rewritten.
Do not apply migration 006, AI Gateway 008, or any other migration.

`bootstrap-role` first measures the sealed runner and current locked target,
then uses local `docker exec -u postgres` to create the scratch-only login from
the validated descriptor-held password. Normal runner sessions set
`role=postgres`, so migration objects do not become owned by the scratch login.
`drop-role` uses the same measured path and must succeed after final evidence;
failure proves unexpected ownership/dependency and stops cleanup acceptance.

Capture `b-post-catalog.json` after both statuses are `exact`. Do not compare it
to the pre-migration catalog; it is the expected migrated signature for C.

```bash
docker exec -u postgres adapteng-db-b \
  capture-catalog-signature \
  --database adapteng_ops \
  --output /tmp/b-post-catalog.json
docker cp adapteng-db-b:/tmp/b-post-catalog.json /secure/b-post-catalog.json
docker exec -u postgres adapteng-db-b rm -f /tmp/b-post-catalog.json
sha256sum /secure/b-post-catalog.json
```

The normative probe is the tracked
`scripts/postgres_restore_transaction_probe.sql` artifact at SHA-256
`236097579a1711888828a69691f0ea69da1c3a5d65de39682a031c7ebff68872`.
The readable rendering below is non-normative; never copy or retype it for
execution. The tracked artifact uses only synthetic values, prints no returned
ID, proves both writes visible inside the transaction, rolls them back, and
then proves zero durable test state:

```sql
BEGIN;

DO $probe$
DECLARE
  v_outcome TEXT;
  v_business_id TEXT;
  v_sequence REGCLASS;
  v_sequence_last BIGINT;
  v_sequence_called BOOLEAN;
BEGIN
  IF EXISTS (
       SELECT 1 FROM public.id_allocator_sequences WHERE prefix = 'AE-RHSL'
     )
     OR EXISTS (
       SELECT 1
       FROM public.source_identity_reservation
       WHERE id_prefix = 'AE-RHSL'
          OR source_hash = repeat('a', 64)
     )
     OR EXISTS (
       SELECT 1
       FROM public.drive_bridge_replay_reservations
       WHERE key_digest = repeat('b', 64)
     ) THEN
    RAISE EXCEPTION 'synthetic rehearsal state already exists';
  END IF;

  v_sequence := pg_get_serial_sequence(
    'public.source_identity_reservation',
    'reservation_id'
  )::REGCLASS;
  EXECUTE format(
    'SELECT last_value, is_called FROM %s',
    v_sequence
  ) INTO v_sequence_last, v_sequence_called;
  IF v_sequence_last <> 1 OR v_sequence_called THEN
    RAISE EXCEPTION 'identity sequence is not in fresh migration state';
  END IF;

  -- Explicit identity insertion does not call nextval(). The function then
  -- exercises its legal duplicate-update write path, which is transactional.
  INSERT INTO public.source_identity_reservation (
    reservation_id,
    id_prefix,
    source_hash,
    canonical_business_id
  )
  OVERRIDING SYSTEM VALUE
  VALUES (
    9000000000000000000,
    'AE-RHSL',
    repeat('a', 64),
    'AE-RHSL-9001'
  );

  SELECT r.outcome, r.canonical_business_id
    INTO STRICT v_outcome, v_business_id
    FROM public.reserve_source_identity('AE-RHSL', repeat('a', 64)) AS r;

  IF v_outcome <> 'duplicate'
     OR v_business_id <> 'AE-RHSL-9001'
     OR NOT EXISTS (
       SELECT 1
       FROM public.source_identity_reservation
       WHERE id_prefix = 'AE-RHSL'
         AND source_hash = repeat('a', 64)
         AND canonical_business_id = v_business_id
         AND attempt_count = 2
         AND last_outcome = 'duplicate'
     ) THEN
    RAISE EXCEPTION 'source reservation not visible in transaction';
  END IF;

  INSERT INTO public.drive_bridge_replay_reservations (
    key_digest,
    operation,
    payload_sha256,
    target_file_id
  )
  VALUES (
    repeat('b', 64),
    'restore_rehearsal',
    repeat('c', 64),
    'rehearsal_test_target'
  );

  IF NOT EXISTS (
    SELECT 1
    FROM public.drive_bridge_replay_reservations
    WHERE key_digest = repeat('b', 64)
      AND operation = 'restore_rehearsal'
      AND payload_sha256 = repeat('c', 64)
      AND target_file_id = 'rehearsal_test_target'
      AND completed = FALSE
      AND completed_at IS NULL
  ) THEN
    RAISE EXCEPTION 'Drive reservation not visible in transaction';
  END IF;
END
$probe$;

ROLLBACK;

DO $assert_rollback$
DECLARE
  v_sequence REGCLASS;
  v_sequence_last BIGINT;
  v_sequence_called BOOLEAN;
BEGIN
  IF EXISTS (
       SELECT 1 FROM public.id_allocator_sequences WHERE prefix = 'AE-RHSL'
     )
     OR EXISTS (
       SELECT 1
       FROM public.source_identity_reservation
       WHERE id_prefix = 'AE-RHSL'
          OR source_hash = repeat('a', 64)
     )
     OR EXISTS (
       SELECT 1
       FROM public.drive_bridge_replay_reservations
       WHERE key_digest = repeat('b', 64)
     ) THEN
    RAISE EXCEPTION 'transaction rollback left durable synthetic state';
  END IF;

  v_sequence := pg_get_serial_sequence(
    'public.source_identity_reservation',
    'reservation_id'
  )::REGCLASS;
  EXECUTE format(
    'SELECT last_value, is_called FROM %s',
    v_sequence
  ) INTO v_sequence_last, v_sequence_called;
  IF v_sequence_last <> 1 OR v_sequence_called THEN
    RAISE EXCEPTION 'transaction changed durable identity sequence state';
  END IF;
END
$assert_rollback$;
```

Execute only the tracked wrapper. It first verifies the pinned procedure
manifest and every member, opens and verifies the tracked SQL bytes, stages
those same bytes into a root-owned `O_EXCL` object, rewinds that held descriptor,
and streams it to the measured runner's forced `psql` stdin. Docker never
resolves a probe bind path:

```bash
set -euo pipefail
scripts/postgres_restore_transaction_probe.sh \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01
```

Only a zero exit from the complete script permits
`transaction_result=rolled_back`. This field describes the disposable-target
DML test after both migrations. It does **not** claim that either sealed
migration DDL was rolled back.

Re-run both exact status assertions after the probe. Stop/remove B only after
the post-migration catalog digest, transaction-probe digest/result, and exact
statuses are captured.

```bash
scripts/postgres_restore_status_gate.sh --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output exact \
  --expect 007=exact --expect drive-008=exact
python3 scripts/postgres_restore_runner.py drop-role --generation B \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01
```

## Phase 8 - independent generation C final exact state

Restore the same accepted set on the independent clean `pg-restore-c` host into
new empty `adapteng-restore-c`. C must not reuse an A/B host, data, copied
volume, or repaired target. Invoke the sole entrypoint with generation C's
reviewed config and fresh signed provider packet.

1. Repeat the exact runtime match to the source.
2. Capture `c-pre-catalog.json`; require equality with `source-catalog.json`.
3. Require 007 and Drive-008 `absent` with the pinned status harness.
4. Apply only the exact 007 runner and then the exact embedded-transaction
   Drive-008 runner.
5. Require both runner statuses `exact`.
6. Capture `c-final-catalog.json`; require byte-for-byte equality with
   `b-post-catalog.json`.
7. Fail-closed assert the new migration tables are empty and migration 007's
   identity sequence is still in its fresh `last_value=1,is_called=false`
   state.
8. Re-run both exact status assertions and record the final exact status before
   any cleanup.

Use the same fixed apply commands as B, with `GEN=c`, and no transaction probe.
The final comparison commands are mandatory:

```bash
set -euo pipefail
capture_generation c
mv /secure/c-catalog.json /secure/c-pre-catalog.json
cmp -s /secure/source-runtime.json /secure/c-runtime.json
cmp -s /secure/source-catalog.json /secure/c-pre-catalog.json
python3 scripts/postgres_restore_runner.py bootstrap-role --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01
scripts/postgres_restore_status_gate.sh --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output absent \
  --expect 007=absent --expect drive-008=absent
python3 scripts/postgres_restore_runner.py apply-007 --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  >/dev/null
scripts/postgres_restore_status_gate.sh --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output exact \
  --expect 007=exact --expect drive-008=absent
python3 scripts/postgres_restore_runner.py apply-drive-008 --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  >/dev/null
scripts/postgres_restore_status_gate.sh --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output exact \
  --expect 007=exact --expect drive-008=exact
docker exec -u postgres adapteng-db-c \
  capture-catalog-signature \
  --database adapteng_ops \
  --output /tmp/c-final-catalog.json
docker cp adapteng-db-c:/tmp/c-final-catalog.json /secure/c-final-catalog.json
docker exec -u postgres adapteng-db-c rm -f /tmp/c-final-catalog.json

python3 scripts/postgres_restore_c_final_assert.py \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01

cmp -s /secure/source-runtime.json /secure/c-runtime.json
cmp -s /secure/source-catalog.json /secure/c-pre-catalog.json
cmp -s /secure/b-post-catalog.json /secure/c-final-catalog.json
scripts/postgres_restore_status_gate.sh --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01 \
  --expect-output exact \
  --expect 007=exact --expect drive-008=exact
python3 scripts/postgres_restore_runner.py drop-role --generation C \
  --procedure-manifest-sha256 e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01
sha256sum \
  /secure/c-runtime.json \
  /secure/c-pre-catalog.json \
  /secure/c-final-catalog.json
```

Generation C is the independent re-restore and final migrated-state proof. A
pre-migration baseline is not a successful C result.

Any recovery, checksum, missing-WAL, page, runtime, locale, collation,
extension, tablespace, catalog, migration, constraint, transaction, or equality
failure is `FAIL`.

## Cross-repository rollout-evidence blocker

As inspected at automation `main` commit
`4afc3b13668e6a07187db2ee50ee4a283833d16d`, tree
`8bc031e0babd12ee05672ff85c0e32db2a899db4`, schema v2 at
`Ivan-Shyla/adapteng-automation-platform:schemas/approved-assets-rollout-evidence.schema.json`
sets `additionalProperties: false` and cannot carry the authentic
selected-set/retention or procedure/image/isolation bindings required here.
Company OS therefore remains **blocked, not executable end-to-end and not
rollout-ready** until a separately reviewed automation change updates that
schema, its validator/fixtures and the `APPROVED_ASSETS_MIGRATION_EVIDENCE_JSON`
consumer. That work belongs in a separate automation evidence-lifecycle PR,
not frozen authorization PR #93. Do not predict a final schema version or
claim compatibility before that separate PR is independently reviewed.

The expected contract adds these exact fields:

| Object | Field | Required semantics |
|---|---|---|
| `managed_backup` | `selected_set_ref_sha256` | SHA-256 of the raw selected full label; no raw label. |
| `managed_backup` | `selected_set_info_sha256` | Digest of the already verified selected-set info JSON. |
| `managed_backup` | `completed_at` | UTC completion derived only from that info JSON; never manual input. |
| `managed_backup` | `scheduler_inventory_sha256` | Fresh authorization-time exact scheduler export digest. |
| `managed_backup` | `scheduler_inventory_observed_at` | UTC generation/observation time read from that exact scheduler inventory. |
| `managed_backup` | `repository_inventory_sha256` | Fresh authorization-time completed-full/retention inventory digest. |
| `managed_backup` | `retention_valid_until` | One second before the count-based selected-set expiry transition. |
| `managed_backup` | `authorization_checked_at` | Exact UTC time the fresh-inventory gate ran. |
| `managed_backup` | `actual_rollout_start` | Actual authorized start; no later than selected completion +21 days. |
| `managed_backup` | `rollout_required_through` | Actual start +35 rollback days +14 safety days. |
| `managed_backup` | `retention_status` | Constant `AUTHORIZED`; valid only when the tracked gate exits zero. |
| `isolated_restore` | `procedure_manifest_sha256` | Pinned Company OS procedure-manifest digest. |
| `isolated_restore` | `approved_image_manifest_sha256` | Separately reviewed target-image manifest digest. |
| `isolated_restore` | `measured_image_identity_sha256` | Digest of Docker-measured identity common to accepted A/B/C targets. |
| `isolated_restore` | `isolation_inventory_sha256` | Digest binding sanitized host/container/network/port/mount/endpoint/volume guards. |
| `rehearsal.rollback` | `transaction_probe_sha256` | Exact tracked probe digest whose zero exit proves rollback. |
| `rehearsal.rollback` | `generation_c_final_catalog_sha256` | C's final exact migrated catalog, equal to B post-migration. |

`managed_backup.valid_until` and the top-level candidate `valid_until` may be
bounded by `retention_valid_until`, but they are not substitutes for the source
metadata, fresh inventory digests and authorization-time computation above.

## Evidence, redaction, and final result

Permitted shareable evidence:

- product, EU region, public price references, pinned version/digests, and
  automation commit/tree;
- SHA-256 of the selected-set raw reference, info JSON, verify output, quote,
  scheduler export, runtime signatures, complete catalog signatures, migration
  artifacts, transaction probe, and final result;
- `absent|exact` statuses, equality booleans, UTC timestamps, retention dates,
  zero counts, `transaction_result=rolled_back`, reviewer, and deletion times.

Forbidden:

- raw backup labels/set IDs, manifests, WAL, info JSON, verify logs, catalog or
  runtime documents;
- DSNs, passwords, host/IP names, bucket/account/key IDs, repository paths,
  object URLs, provider resource IDs, or downloadable evidence links;
- raw SQL rows, business IDs, source hashes, PII, payloads, filenames from
  business data, provider screenshots, or secret fingerprints;
- database dumps of any format; and
- success inferred from a dashboard badge or command exit when the documented
  output assertion was not also met.

Canonicalize the sanitized document as UTF-8 with LF endings, hash it with the
last field omitted, and commit only this result plus its digest:

```yaml
result: "PASS | FAIL"
status: "rehearsal-complete-not-production-migration"
performed_at_utc: "<RFC3339>"
reviewed_at_utc: "<RFC3339>"
backup:
  management: "operator-managed pgBackRest"
  object_storage: "provider-managed Backblaze B2"
  region: "EU Central"
  postgres_major: 16
  pgbackrest_version: "2.59.0"
  pgbackrest_release_artifact_sha256: "faaf8faa14a6392279654ee216a493fcd07b0c513af4b55fe34faec062cb8875"
  pgbackrest_binary_sha256: "<sha256>"
  repository_format: 5
  source_image_digest: "sha256:<digest>"
  source_image_manifest_sha256: "<sha256>"
  source_measured_image_identity_sha256: "<sha256>"
  backup_image_digest: "sha256:<digest>"
  backup_image_manifest_sha256: "<sha256>"
  backup_measured_image_identity_sha256: "<sha256>"
  source_signatures_captured_at_utc: "<RFC3339>"
  selected_set_ref_sha256: "<sha256; no raw label>"
  selected_set_info_sha256: "<sha256>"
  completed_at: "<RFC3339 derived from selected-set info>"
  post_backup_check: "passed"
  selected_set_verify_status: "ok"
  selected_set_verify_sha256: "<sha256>"
  wal_archive_health: "passed"
implementation_artifacts:
  migration_runner_image_digest: "sha256:<digest>"
  runtime_collector_sha256: "<sha256>"
  catalog_collector_sha256: "<sha256>"
  runtime_compatibility_assertion_sha256: "<sha256>"
  selected_full_assertion_sha256: "<sha256>"
  migration_status_harness_sha256: "<sha256>"
  restore_procedure_manifest_sha256: "e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01"
  transaction_probe_sha256: "236097579a1711888828a69691f0ea69da1c3a5d65de39682a031c7ebff68872"
compatibility:
  source_image_identity_sha256: "<sha256>"
  generation_a_image_identity_sha256: "<same approved target identity>"
  generation_b_image_identity_sha256: "<same approved target identity>"
  generation_c_image_identity_sha256: "<same approved target identity>"
  pre_change_runtime_sha256: "<sha256>"
  source_runtime_sha256: "<sha256>"
  generation_a_runtime_sha256: "<same sha256>"
  generation_b_runtime_sha256: "<same sha256>"
  generation_c_runtime_sha256: "<same sha256>"
  protected_pre_change_to_source_fields_exact: true
  all_runtime_signatures_exact: true
catalog:
  pre_change_catalog_sha256: "<sha256>"
  source_pre_migration_sha256: "<sha256>"
  generation_a_pre_migration_sha256: "<same sha256>"
  generation_b_pre_migration_sha256: "<same sha256>"
  generation_b_post_migration_sha256: "<sha256>"
  generation_c_pre_migration_sha256: "<same pre-migration sha256>"
  generation_c_final_post_migration_sha256: "<same B post-migration sha256>"
  pre_change_to_source_catalog_exact: true
  complete_signature_contract: true
migrations:
  automation_commit: "dbcf806ea7714b8e2a7415ae6cd788491924178d"
  automation_tree: "8fc649a97963f38ce0a7592001a7fea4834eceea"
  migration_007_sha256: "f7be6270b3cf617b709749dc086bfaeddaba1bbfdb2f5826a4935b21aa5256e3"
  drive_008_sha256: "1466710aad11a65461ee504ecfe257ab193146b85b8b3bdb25f3bf5498843ca0"
  generation_a_007: "absent"
  generation_a_drive_008: "absent"
  generation_b_007_final: "exact"
  generation_b_drive_008_final: "exact"
  generation_c_007_final: "exact"
  generation_c_drive_008_final: "exact"
  generation_a_measured_runner_identity_sha256: "<sha256>"
  generation_b_measured_runner_identity_sha256: "<sha256>"
  generation_c_measured_runner_identity_sha256: "<sha256>"
  generation_c_new_migration_tables_empty: true
  generation_c_identity_sequence_fresh: true
  generation_c_final_state_captured_before_cleanup: true
transaction_probe:
  artifact_sha256: "236097579a1711888828a69691f0ea69da1c3a5d65de39682a031c7ebff68872"
  generation: "B"
  transaction_result: "rolled_back"
  durable_synthetic_rows_or_allocator_state: 0
  identity_sequence_unchanged: true
retention:
  full_generations: 12
  rollout_days: 21
  rollback_days: 35
  safety_margin_days: 14
  selected_set_ref_sha256: "<same backup value>"
  selected_set_info_sha256: "<same backup value>"
  completed_at: "<same backup value>"
  accepted_packet_sha256: "<canonical fixed accepted packet sha256>"
  inventory_exporter_id: "<pinned id>"
  inventory_exporter_version: "<pinned version>"
  inventory_exporter_artifact_sha256: "<pinned sha256>"
  scheduler_inventory_sha256: "<fresh authorization-time sha256>"
  scheduler_inventory_observed_at: "<RFC3339 from that scheduler inventory>"
  repository_inventory_sha256: "<fresh authorization-time sha256>"
  authorization_checked_at: "<RFC3339>"
  actual_rollout_start: "<RFC3339>"
  rollout_required_through: "<actual start +35+14 days>"
  retention_valid_until: "<one second before count expiry transition>"
  retention_status: "AUTHORIZED"
cost:
  quote_sha256: "<sha256>"
  quote_accessed_at_utc: "<RFC3339>"
  nonzero_costs_included: true
isolation:
  procedure_manifest_sha256: "e165a220967e84f89a28c81a1ff2f50229e48e24a42eff8ea500140a348b4e01"
  approved_image_manifest_sha256: "<sha256>"
  measured_image_identity_sha256: "<sha256>"
  generation_a_inventory_sha256: "<sha256>"
  generation_b_inventory_sha256: "<sha256>"
  generation_c_inventory_sha256: "<sha256>"
  generation_a_provider_isolation_sha256: "<signed fresh packet sha256>"
  generation_b_provider_isolation_sha256: "<signed fresh packet sha256>"
  generation_c_provider_isolation_sha256: "<signed fresh packet sha256>"
  generation_a_locked_local_inventory_sha256: "<sha256>"
  generation_b_locked_local_inventory_sha256: "<sha256>"
  generation_c_locked_local_inventory_sha256: "<sha256>"
  restore_host_clean_base_image: true
  postgres_port_published: false
  production_applications_present: false
  production_private_network_attached: false
  production_credentials_present: false
  docker_socket_mounted: false
  bootstrap_removed_before_sql: true
  egress_blocked_during_sql: true
  repository_credentials_present_during_sql: false
  raw_business_rows_in_evidence: 0
rollout_evidence_contract:
  authorization_status: "NOT_READY_PENDING_AUTOMATION_EVIDENCE_LIFECYCLE_PR"
  automation_schema_sha256: "<absent until separate reviewed PR merges>"
  required_fields_accepted: false
repository_controls:
  bucket_visibility: "private"
  bucket_sse_b2: "enabled"
  bucket_object_lock_feature: "disabled"
  backup_runtime_key_capabilities: "bucket-prefix list/read/write/delete only"
  restore_key_capabilities: "bucket-prefix read/list only"
  repository_cipher_separate_from_provider_key: true
  repository_cipher_recovery_copy_verified: true
  hidden_version_lifecycle_days: 35
  unfinished_large_file_lifecycle_days: 7
cleanup:
  generation_a_server_deleted_at_utc: "<RFC3339>"
  generation_b_server_deleted_at_utc: "<RFC3339>"
  generation_c_server_deleted_at_utc: "<RFC3339>"
  scratch_volumes_remaining: 0
  temporary_restore_key_revoked_at_utc: "<RFC3339>"
  temporary_secret_files_remaining: 0
reviewer: "Ivan"
result_document_sha256: "<sha256 of document with this field omitted>"
```

## Cleanup

1. Capture C's final exact status and final catalog digest first.
2. On each host, stop/remove its containers, volume, networks, temporary
   auth/config, signed provider packet, raw runner checkout, and secret files.
3. Revoke the temporary bucket-scoped read/list key.
4. Delete all three Hetzner servers and paid Primary IPv4s in the Console.
   Powered-off servers and undeleted Primary IPs continue billing.
5. Confirm no Volume or firewall is attached to an unintended server. The
   reusable locked firewall may remain.
6. Keep the accepted pgBackRest set normally restorable under the 12-full
   policy through the evidence expiry date. Do not manually delete repository
   data to make a failed run appear clean.
7. Keep only the sanitized result and digest in Git. Restricted raw evidence is
   deleted under the approved evidence-retention decision after review; no raw
   backup artifact or database dump enters Git.

## Official sources and prices checked 2026-07-31

- PostgreSQL 16 continuous archiving and physical backup:
  <https://www.postgresql.org/docs/16/continuous-archiving.html>,
  <https://www.postgresql.org/docs/16/backup-file.html>
- Coolify logical PostgreSQL backup:
  <https://coolify.io/docs/databases/backups>
- pgBackRest release metadata, release, command/configuration references, and
  source:
  <https://api.github.com/repos/pgbackrest/pgbackrest/releases/latest>,
  <https://github.com/pgbackrest/pgbackrest/releases/tag/release/2.59.0>,
  <https://pgbackrest.org/command.html>,
  <https://pgbackrest.org/configuration.html>,
  <https://github.com/pgbackrest/pgbackrest/blob/release/2.59.0/src/command/verify/verify.c>,
  <https://github.com/pgbackrest/pgbackrest/blob/release/2.59.0/src/main.c>,
  <https://github.com/pgbackrest/pgbackrest/blob/release/2.59.0/src/version.h>
- Hetzner current cloud price adjustment, billing, Primary IPs, server
  networking, Volumes, and snapshot consistency:
  <https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/>,
  <https://docs.hetzner.com/cloud/billing/faq/>,
  <https://docs.hetzner.com/cloud/servers/primary-ips/overview/>,
  <https://docs.hetzner.com/cloud/servers/faq/>,
  <https://docs.hetzner.com/cloud/volumes/overview/>,
  <https://docs.hetzner.com/cloud/servers/backups-snapshots/faq/>
- Hetzner firewall allowlist behavior:
  <https://docs.hetzner.com/cloud/firewalls/getting-started/creating-a-firewall/>
- Backblaze B2 pricing, API transactions, EU region, Object Lock, file
  versions, lifecycle, S3 API, and application keys:
  <https://www.backblaze.com/cloud-storage/pricing>,
  <https://www.backblaze.com/cloud-storage/transaction-pricing>,
  <https://www.backblaze.com/docs/cloud-storage-data-regions>,
  <https://www.backblaze.com/docs/cloud-storage-object-lock>,
  <https://www.backblaze.com/docs/cloud-storage-file-versions>,
  <https://www.backblaze.com/docs/cloud-storage-lifecycle-rules>,
  <https://www.backblaze.com/docs/cloud-storage-s3-compatible-api>,
  <https://www.backblaze.com/docs/cloud-storage-application-keys>
