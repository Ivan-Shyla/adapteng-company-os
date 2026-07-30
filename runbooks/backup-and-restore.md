# Runbook — physical backup and isolated restore of `adapteng_ops`

`adapteng_ops` is the authoritative operational PostgreSQL database in the
Coolify standalone resource `adapteng-ops-db`: PostgreSQL 16 on Hetzner, local
Docker storage, internal network only, and no public database port.

> **Status — proposed, not configured.** This runbook selects an implementation
> and defines its acceptance rehearsal. It does not show that pgBackRest,
> continuous WAL archiving, Backblaze B2, scheduling, monitoring, or a restore
> has been configured. The 2026-07-25 Coolify backup does not satisfy this
> contract. No approved-assets migration may run until the owner completes this
> runbook and a reviewed status PR records a sanitized `PASS`.

## Decision

Use a pinned PostgreSQL 16 image containing
[pgBackRest](https://pgbackrest.org/) to create encrypted physical base backups
and continuously archive WAL to a private
[Backblaze B2](https://www.backblaze.com/cloud-storage) bucket in the **EU
Central** region. Run a weekly full backup, daily differential backup, and
`archive_timeout = 15min`; keep eight full backup generations and all dependent
differential/WAL data. Rehearse on a clean, disposable Hetzner server that
contains only PostgreSQL, never a clone of the Coolify host.

This is the smallest accepted design because:

- PostgreSQL online physical backup plus WAL is application-consistent and
  supports point-in-time recovery. PostgreSQL explicitly says `pg_dump` does
  not contain enough information for WAL replay.
- pgBackRest supplies backup manifests with file checksums, repository
  encryption, retention, S3-compatible storage, end-to-end `check`, and restore.
- B2 is managed off-host object storage, supports S3, EU-only account placement,
  encryption, lifecycle rules, version history and Object Lock. At the
  2026-07-30 public price it is **US$6.95/TB-month**, the first 10 GB is free,
  and egress up to three times average monthly storage is free. Provider
  checkout/invoice remains authoritative. The current scale is expected to
  remain within that allowance, but the first physical backup decides actual
  usage.
- A clean hourly-billed rehearsal server is disposable and cannot start copied
  production applications because no application image or host snapshot is
  installed.

### Rejected substitutions

| Option | Decision |
|---|---|
| Coolify **Scheduled Database Backup** | **Forbidden for this contract.** Coolify documents `pg_dump --format=custom`: a logical dump. It may remain an independent convenience export, but it is not the approved backup identifier or rollback source. |
| Manually created, downloaded or stored SQL/custom dump | **Forbidden.** A manual logical dump does not become approved by uploading it to S3, Storage Box, Drive or a laptop. |
| Current Baserow all-in-one backup | **Wrong database.** It protects Baserow, not `adapteng-ops-db` / `adapteng_ops`, and must never be cited as PostgreSQL evidence. |
| Hetzner Backup/Snapshot of the running host | **Rejected as primary backup.** Hetzner says consistency is not guaranteed while the server runs. PostgreSQL accepts only a correctly atomic snapshot containing every data/WAL/tablespace volume; that property is not established for this Docker layout. |
| Powered-off Hetzner server snapshot | Consistent only after an intentional full-host outage. It is an optional disaster image, not the physical/WAL backup and not the rehearsal target. A server created from it is a duplicate host that may boot Coolify and outbound-capable applications. |
| Clone the production snapshot and “block later” | **Forbidden.** Hetzner firewalls allow all outbound traffic when no outbound rule exists. Isolation must exist before first boot; a copied application stack must never start. |
| Hetzner Object Storage | Technically suitable: EU S3, versioning/lifecycle/Object Lock, **€6.49/month** base price including the published quota. Rejected for this small database only because B2 EU Central is expected to remain within its 10 GB free allowance. Re-evaluate if company procurement prefers one vendor. |
| Hetzner Storage Box | Not S3-compatible and therefore not a direct pgBackRest/Coolify S3 target. A custom transfer path adds work without improving this design. |
| SaaS services that run `pg_dump` | **Forbidden.** A provider dashboard does not turn a logical export into a physical/WAL backup. |
| Move now to a managed PostgreSQL service | Would provide provider-managed physical backup/PITR, but changes the live database and network architecture. Treat as a separate migration decision, not a rehearsal shortcut. |

Object Lock is supported by B2 but is **not enabled with default retention in
the first rollout**. pgBackRest has no documented Object Lock integration and
must delete expired repository objects. First use B2 version history plus a
35-day hidden-version lifecycle; test Object Lock/expiration compatibility in a
separate repository before tightening this control.

## Required identifiers and secret names

Use these names; values never enter Git, PR text, logs or evidence:

| Item | Value/reference |
|---|---|
| PostgreSQL resource | `adapteng-ops-db` |
| Database | `adapteng_ops` |
| pgBackRest stanza | `adapteng-ops` |
| B2 bucket | globally unique private name chosen by owner |
| B2 runtime key | `PGBACKREST_REPO1_S3_KEY` |
| B2 runtime secret | `PGBACKREST_REPO1_S3_KEY_SECRET` |
| Repository cipher passphrase | `PGBACKREST_REPO1_CIPHER_PASS` |
| Restore-only B2 key | temporary, bucket-scoped read/list key; revoke after rehearsal |

Store the three runtime values as Coolify secrets and the cipher passphrase in
the company password manager/recovery record. Losing the cipher passphrase makes
the backup unrestorable.

## Phase 0 — approve the runtime change

Coolify documents only its `pg_dump` backup for a standalone PostgreSQL
resource; it does not document persistent installation of pgBackRest in that
managed container. **Do not `apt install` interactively in the live container.**
The installation would disappear on replacement and would not be reviewable.

Before touching production, merge a separate implementation PR that:

1. derives from the exact current PostgreSQL 16 image digest;
2. installs a pinned pgBackRest release and preserves the upstream entrypoint,
   data directory, user and health check;
3. mounts non-secret `pgbackrest.conf`, injects only the three secret names
   above, and runs pgBackRest as the PostgreSQL OS user;
4. supplies fail-closed host scheduling and alerting for weekly full, daily
   differential, `check`, and `pg_stat_archiver` failures;
5. pins the resulting image by digest and proves it against a disposable
   database before proposing the one planned production restart; and
6. documents a rollback to the current image digest without changing the
   PostgreSQL major version or data volume.

**Stop** if the current image digest, `$PGDATA`, PostgreSQL UID/GID, volume,
socket path, pgBackRest package provenance, or rollback image is unknown.

## Phase 1 — owner creates the off-host repository

1. In a new or empty Backblaze account, choose **EU Central** during account
   creation. Backblaze says the account region cannot later be changed.
2. In **B2 Cloud Storage → Buckets → Create a Bucket**:
   - create a uniquely named **Private** bucket;
   - enable default server-side encryption (SSE-B2);
   - leave default Object Lock retention **off** for the initial rollout;
   - add a lifecycle rule that deletes hidden versions after **35 days** and
     cancels unfinished large files after **7 days**.
3. In **Application Keys → Add a New Application Key**, create a standard key
   restricted to this bucket and the pgBackRest repository prefix. Grant only
   list/read/write/delete capabilities required for backup and expiry. Do not
   use the master key.
4. Record the bucket's B2 S3 endpoint and region from the provider UI. For EU
   Central the endpoint is expected to resemble
   `s3.eu-central-003.backblazeb2.com`; use the value shown for the account, not
   a copied example.
5. Put the key ID, application key and a new random repository cipher
   passphrase into the named Coolify secrets. Put the cipher passphrase into the
   recovery record. Never paste any value into this repository.

**Stop** if the account is not EU Central, the bucket is public, a master/all-
bucket key is proposed, encryption is off, lifecycle would delete current
objects, or the cipher passphrase lacks an independent recovery copy.

## Phase 2 — owner configures and proves backup

The reviewed runtime change should render the following non-secret shape. Exact
paths, endpoint and region come from the approved image and B2 account:

```ini
[global]
repo1-type=s3
repo1-path=/adapteng-ops-db
repo1-s3-bucket=<private-bucket-name>
repo1-s3-endpoint=<B2-S3-endpoint>
repo1-s3-region=<B2-region>
repo1-s3-uri-style=path
repo1-storage-verify-tls=y
repo1-cipher-type=aes-256-cbc
repo1-retention-full=8
compress-type=zst
start-fast=y

[adapteng-ops]
pg1-path=<verified-PGDATA>
pg1-port=5432
```

The secrets are environment variables, not lines in that file. The approved
PostgreSQL settings are:

```ini
wal_level = replica
archive_mode = on
archive_command = 'pgbackrest --stanza=adapteng-ops archive-push %p'
archive_timeout = 15min
```

`archive_mode` requires a restart. In Coolify:

1. open `adapteng-ops-db`, confirm PostgreSQL **16**, no public port, current
   healthy state, image digest and volume;
2. stop the adapter and every governed workflow that can write to this database;
3. deploy the reviewed image/configuration and perform the single planned
   restart;
4. keep consumers stopped until `stanza-create`, `check` and the first full
   backup below succeed, then re-enable them; and
5. roll back immediately if PostgreSQL does not become healthy or its major
   version, `$PGDATA`, volume, ownership or internal-only exposure differs.

Run inside the container as the PostgreSQL OS user:

```bash
pgbackrest --stanza=adapteng-ops stanza-create
pgbackrest --stanza=adapteng-ops check
pgbackrest --stanza=adapteng-ops --type=full backup
pgbackrest --stanza=adapteng-ops --output=json info > /tmp/pgbackrest-info.json
sha256sum /tmp/pgbackrest-info.json
psql --dbname=adapteng_ops --no-psqlrc --tuples-only --command \
  "SELECT archived_count, failed_count, last_archived_time IS NOT NULL,
          COALESCE(last_failed_time < last_archived_time, true)
     FROM pg_stat_archiver;"
rm -f /tmp/pgbackrest-info.json
```

The first restart may briefly log failed archive attempts before `stanza-create`
exists. They are acceptable only if the subsequent `check` succeeds, a newer
WAL segment archives successfully, and `last_failed_time` is empty or older than
`last_archived_time`. Otherwise stop.

Then enable the reviewed schedule:

- Sunday: `pgbackrest --stanza=adapteng-ops --type=full backup`
- Monday–Saturday: `pgbackrest --stanza=adapteng-ops --type=diff backup`
- after each backup: `pgbackrest --stanza=adapteng-ops check`
- alert on any non-zero exit, any `failed_count` increase, stale
  `last_archived_time`, WAL backlog growth, or local disk pressure.

The accepted backup identifier is the pgBackRest **backup label** from
`info --output=json`. Its evidence hash is the SHA-256 of that exact JSON
document. The repository's encrypted backup manifest retains per-file checksums;
the isolated restore below is the acceptance proof. A bucket object name,
Coolify logical-backup filename, screenshot, manual dump or “Success” badge is
not a substitute.

**Stop and keep approved-assets blocked** if `stanza-create`, `check`, backup or
archive status fails; the backup has no label/WAL range; the label predates the
planned rehearsal; the info JSON leaks secrets; monitoring is absent; or
consumers cannot be stopped and safely re-enabled.

## Phase 3 — prepare exact sealed inputs

On the owner workstation, fetch the two migration files byte-for-byte from the
reviewed automation commit, never by copy/paste. Use the merged commit currently
recorded in the registry, not a branch:

```powershell
$commit = 'dbcf806ea7714b8e2a7415ae6cd788491924178d'
$files = @(
  '007_source_identity_reservation.sql',
  '008_drive_bridge_replay_reservations.sql'
)
foreach ($file in $files) {
  $encoded = (gh api `
    "repos/Ivan-Shyla/adapteng-automation-platform/contents/database/migrations/$file`?ref=$commit" `
    --jq .content) -join ''
  [IO.File]::WriteAllBytes($file, [Convert]::FromBase64String($encoded))
}
$expected = @{
  '007_source_identity_reservation.sql' =
    'f7be6270b3cf617b709749dc086bfaeddaba1bbfdb2f5826a4935b21aa5256e3'
  '008_drive_bridge_replay_reservations.sql' =
    '1466710aad11a65461ee504ecfe257ab193146b85b8b3bdb25f3bf5498843ca0'
}
foreach ($file in $files) {
  $actual = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
  if ($actual -ne $expected[$file]) {
    throw "sealed migration hash mismatch: $file"
  }
  "$file $actual"
}
```

The pinned values above are the SHA-256 values fetched from commit
`dbcf806ea7714b8e2a7415ae6cd788491924178d` on 2026-07-30. Record only filename,
commit and SHA-256. `008_drive_bridge_replay_reservations.sql` is the permitted
Drive-008; `008_ai_gateway_runtime_hardening.sql` is unrelated and forbidden.

**Stop** on any hash mismatch, mutable branch ref, renamed file, unreviewed SQL,
or missing expected digest.

## Phase 4 — create a disposable isolated restore host

Never create this host from a production Backup/Snapshot.

1. In **Hetzner Cloud → Firewalls**, create:
   - `pg-restore-bootstrap`: inbound TCP 22 from the owner's current `/32` only;
     outbound DNS TCP/UDP 53 and HTTPS TCP 443 only.
   - `pg-restore-locked`: no inbound rules; one harmless explicit outbound rule
     to `192.0.2.1/32`, TCP port 9. Hetzner treats outbound rules as an
     allowlist, so this deliberately unmatched TEST-NET rule blocks all real
     egress. An empty outbound rule set would allow all outbound and is unsafe.
2. Create the smallest current-generation server that can hold at least twice
   the uncompressed cluster size, from a clean Debian image in an EU location.
   Apply `pg-restore-bootstrap` **at creation**, label it
   `purpose=postgres-restore-rehearsal`, and do not attach a production private
   network, snapshot, volume, cloud-init, SSH key set or application image that
   could start a production service.
3. Install Docker from the official repository. Transfer only:
   - the approved PostgreSQL 16 + pgBackRest image digest;
   - non-secret pgBackRest configuration;
   - a temporary bucket-scoped read/list B2 key;
   - the repository cipher passphrase; and
   - the two sealed migration files.
4. Open the Hetzner web console and keep it open. Leave
   `pg-restore-bootstrap` attached only until the recovery-only container has
   fetched required WAL, reached a consistent non-recovery state, and stopped.
   No application or SQL-rehearsal container may run in this state.

The temporary host has never contained Coolify, n8n, the adapter, mail, webhooks
or application credentials. The locked firewall is defense in depth, not the
primary reason duplicate applications are absent.

**Stop and delete the host** if it was created from a production image, any
application container exists, a public database port is mapped, a production
private network is attached, a database accepts SQL before recovery egress is
removed, the locked egress probe succeeds, or the restore key can write/delete.

## Phase 5 — restore, rehearse, roll back, and re-restore

The approved image must already be present before egress is locked. Use one
empty Docker volume per restore generation. pgBackRest recovery may need
archived WAL after the files are restored, so complete recovery while the clean
host has bootstrap HTTPS egress; do not expose a port or run SQL. Then stop that
container, remove all repository credentials, lock host egress, and start the
recovered cluster with no network or archiving:

```bash
set -euo pipefail
export IMAGE='<approved-image>@sha256:<digest>'
export SET='<exact-pgBackRest-backup-label>'

docker volume create adapteng-restore-a
docker run --rm \
  -v adapteng-restore-a:<verified-PGDATA> \
  "$IMAGE" \
  sh -c 'chown postgres:postgres <verified-PGDATA> &&
         chmod 700 <verified-PGDATA>'

docker run --rm --user postgres \
  --env-file /root/restore-only.env \
  -v adapteng-restore-a:<verified-PGDATA> \
  -v /root/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro \
  "$IMAGE" \
  pgbackrest --stanza=adapteng-ops --set="$SET" \
  --type=immediate --target-action=promote restore

# Recovery-only start: no published port, no SQL, no application container.
docker run -d --name adapteng-recover-a \
  --env-file /root/restore-only.env \
  -v adapteng-restore-a:<verified-PGDATA> \
  -v /root/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro \
  "$IMAGE" postgres \
  -c listen_addresses='' -c archive_mode=off -c archive_command=''

# Wait at most two minutes, then verify recovery completed. Stop here on any
# recovery/archive/checksum/WAL error; do not run other SQL.
ready=0
for i in $(seq 1 120); do
  if docker exec -u postgres adapteng-recover-a \
    pg_isready --dbname=adapteng_ops; then
    ready=1
    break
  fi
  sleep 1
done
test "$ready" -eq 1
docker exec -u postgres adapteng-recover-a \
  psql --dbname=adapteng_ops --no-psqlrc --tuples-only --command \
  "SELECT NOT pg_is_in_recovery();"
docker stop --time 60 adapteng-recover-a
docker rm adapteng-recover-a

# Remove restore-only.env, detach pg-restore-bootstrap, attach
# pg-restore-locked, and prove an HTTPS request fails before this final start.
rm -f /root/restore-only.env
docker run -d --name adapteng-restore-a --network none \
  -v adapteng-restore-a:<verified-PGDATA> \
  "$IMAGE" postgres \
  -c listen_addresses='' -c archive_mode=off -c archive_command=''
```

The restore helper and recovery-only PostgreSQL container use the temporary
read/list key while bootstrap HTTPS egress exists. Neither publishes a port or
runs application/migration SQL. After recovery reaches a consistent,
non-recovery state, stop it, delete the environment file, remove
`pg-restore-bootstrap`, attach `pg-restore-locked`, and prove from the web
console that an HTTPS request fails before the final `docker run ... postgres`
command. From that point SSH is unreachable. The final SQL-running container
has no repository credentials, configuration mount or network.

### A. Baseline

Copy the sealed SQL into the no-network container and prove only booleans and
counts. `--type=immediate` above binds every generation to the selected backup's
first consistent point; it must not replay later production WAL:

```bash
docker cp 007_source_identity_reservation.sql adapteng-restore-a:/tmp/
docker cp 008_drive_bridge_replay_reservations.sql adapteng-restore-a:/tmp/
docker exec -u postgres adapteng-restore-a \
  psql --dbname=adapteng_ops --no-psqlrc --tuples-only --command \
  "SELECT current_setting('server_version_num')::int / 10000 = 16,
          to_regclass('public.id_allocator_sequences') IS NOT NULL,
          to_regclass('public.lead_identity_reservation') IS NOT NULL,
          to_regprocedure(
            'public.reserve_lead_identity(text,text,text,text,text,text)'
          ) IS NOT NULL,
          to_regclass('public.source_identity_reservation') IS NULL,
          to_regclass('public.drive_bridge_replay_reservations') IS NULL,
          (
            to_regclass('agent_task') IS NULL
            AND to_regclass('public.agent_task') IS NULL
            AND to_regclass('approval_request') IS NULL
            AND to_regclass('public.approval_request') IS NULL
            AND to_regclass('ai_gateway_call') IS NULL
            AND to_regclass('public.ai_gateway_call') IS NULL
            AND to_regclass('integrity.projection_manifest') IS NULL
          );"
```

Expected: seven `true` values — PostgreSQL 16; migration 001 table and migration
004 table/function present; 007 and Drive-008 absent; and representative objects
from migrations 002/003/005/006 and AI-008 all absent. Stop if the baseline
differs.

### B. Exact 007/Drive-008 apply

Do not edit either file. Set the session search path externally so unqualified
objects land in `public`, as required by the migration runbook:

```bash
docker exec -u postgres \
  -e PGOPTIONS='-c search_path=public' adapteng-restore-a \
  psql --dbname=adapteng_ops --no-psqlrc -v ON_ERROR_STOP=1 \
  --file=/tmp/007_source_identity_reservation.sql

docker exec -u postgres \
  -e PGOPTIONS='-c search_path=public' adapteng-restore-a \
  psql --dbname=adapteng_ops --no-psqlrc -v ON_ERROR_STOP=1 \
  --file=/tmp/008_drive_bridge_replay_reservations.sql

docker exec -u postgres adapteng-restore-a \
  psql --dbname=adapteng_ops --no-psqlrc --tuples-only --command \
  "SELECT to_regclass('public.source_identity_reservation') IS NOT NULL,
          to_regprocedure('public.reserve_source_identity(text,text)') IS NOT NULL,
          to_regclass('public.drive_bridge_replay_reservations') IS NOT NULL,
          (SELECT count(*) = 1
             FROM pg_constraint
            WHERE conrelid =
                  'public.drive_bridge_replay_reservations'::regclass
              AND contype = 'p');"
```

Expected: four `true` values. Do not apply migration 006, AI Gateway 008, or any
other migration.

### C. Rollback proof

Rollback means discarding the mutated scratch cluster and restoring the sealed
backup, never hand-dropping migration objects:

```bash
docker rm -f adapteng-restore-a
docker volume rm adapteng-restore-a
```

Repeat the restore/start commands into `adapteng-restore-b`, then repeat the
baseline query. Expected: 001/004 present and 007/Drive-008 absent. This is the
rollback proof. For each new restore generation: ensure no PostgreSQL container
is running, temporarily attach `pg-restore-bootstrap` only for restore and
recovery, permit no SQL beyond the recovery-state boolean, then remove the
credentials, restore `pg-restore-locked`, and prove egress blocked before the
SQL-running container starts.

### D. Independent re-restore

Delete generation B. Restore the same backup label once more into
`adapteng-restore-c`, start it with the same no-network/no-archive controls, and
repeat the baseline query. This proves that restoration is repeatable rather
than a one-off successful volume.

Any PostgreSQL start, recovery, checksum, missing-WAL, page, collation,
extension, migration, constraint or hash error is a **FAIL**. Do not repair the
scratch database and continue; preserve only sanitized diagnostics, delete the
host, correct the design, take a new backup, and start again.

## Evidence and redaction

Permitted in the reviewed status PR:

- provider/product/region, pgBackRest and PostgreSQL versions;
- approved image digest;
- backup label, start/stop timestamps, WAL start/stop and byte counts;
- SHA-256 of exact `pgbackrest info --output=json`;
- automation commit and migration-file SHA-256 values;
- boolean/count-only baseline, apply, rollback and re-restore outcomes;
- UTC timestamps, PASS/FAIL, reviewer and deletion timestamps; and
- SHA-256 of the final canonical sanitized result document.

Forbidden:

- secret values or fingerprints that aid guessing;
- DSNs, host/IP names, bucket name, account ID, key ID, object URLs or
  repository path;
- raw SQL rows, business IDs, source hashes, PII, payloads, filenames from
  business data, screenshots containing provider metadata, or logs/config files;
- database dump files, backup manifests, WAL files or downloadable evidence
  links; and
- “success” inferred from a provider badge without restore evidence.

Use this final template and replace every placeholder. Canonicalize it as UTF-8
with LF endings, hash it, and put only the document plus its SHA-256 in the
status PR:

```yaml
result: PASS | FAIL
status: "rehearsal-complete-not-production-migration"
performed_at_utc: "<RFC3339>"
reviewed_at_utc: "<RFC3339>"
backup:
  engine: "pgBackRest <version>"
  provider: "Backblaze B2"
  region: "EU Central"
  postgres_major: 16
  image_digest: "sha256:<digest>"
  label: "<pgBackRest-label>"
  info_json_sha256: "<sha256>"
  wal_range_present: true
migrations:
  automation_commit: "dbcf806ea7714b8e2a7415ae6cd788491924178d"
  migration_007_sha256: "f7be6270b3cf617b709749dc086bfaeddaba1bbfdb2f5826a4935b21aa5256e3"
  drive_008_sha256: "1466710aad11a65461ee504ecfe257ab193146b85b8b3bdb25f3bf5498843ca0"
checks:
  initial_restore_baseline: true
  exact_007_apply: true
  exact_drive_008_apply: true
  unrelated_migrations_applied: 0
  rollback_restore_baseline: true
  independent_rerestore_baseline: true
  postgres_network_disabled_during_sql: true
  archive_disabled_on_restores: true
  raw_business_rows_in_evidence: 0
cleanup:
  scratch_server_deleted_at_utc: "<RFC3339>"
  scratch_volumes_remaining: 0
  temporary_restore_key_revoked_at_utc: "<RFC3339>"
  temporary_secret_files_remaining: 0
reviewer: "Ivan"
result_document_sha256: "<sha256-of-document-with-this-field-omitted>"
```

## Cleanup and retention

1. Stop/remove every restore container and volume.
2. Securely remove the temporary environment/config files from the scratch
   host, then delete the entire Hetzner server in the Console.
3. Revoke the temporary restore-only B2 key. Keep the production backup key.
4. Confirm no firewall remains attached to an unintended server; the reusable
   locked firewall may remain.
5. Keep the accepted backup generation through the approved-assets rollout and
   for at least 35 days afterward. Eight weekly full generations provide about
   56 days; therefore the sanitized `PASS` expires if the rollout does not
   complete within 21 days of the accepted full. If it slips, take a new full
   and repeat the rehearsal. Do not reduce retention or manually expire the
   accepted set during its rollback window.
6. Keep only the sanitized result document and digest in Git. Provider backup
   data follows pgBackRest retention; hidden B2 versions follow the 35-day
   lifecycle. Delete no backup manually to make a failed run look clean.

## Official sources and prices checked 2026-07-30

- PostgreSQL 16 backup/PITR:
  <https://www.postgresql.org/docs/16/continuous-archiving.html>
- PostgreSQL filesystem snapshot restrictions:
  <https://www.postgresql.org/docs/16/backup-file.html>
- PostgreSQL `pg_basebackup`:
  <https://www.postgresql.org/docs/16/app-pgbasebackup.html>
- pgBackRest user guide, configuration and command references:
  <https://pgbackrest.org/user-guide.html>,
  <https://pgbackrest.org/configuration.html>,
  <https://pgbackrest.org/command.html>
- Coolify PostgreSQL backup (`pg_dump`) and S3 support:
  <https://coolify.io/docs/databases/backups>,
  <https://coolify.io/docs/knowledge-base/s3/introduction>
- Hetzner Backup/Snapshot consistency and clone behavior:
  <https://docs.hetzner.com/cloud/servers/backups-snapshots/faq/>
- Hetzner firewall default/allowlist behavior:
  <https://docs.hetzner.com/cloud/firewalls/getting-started/creating-a-firewall/>
- Hetzner backup pricing (20% of server price) and snapshot billing:
  <https://docs.hetzner.com/cloud/billing/faq/>
- Hetzner Object Storage capabilities and current pricing:
  <https://docs.hetzner.com/storage/object-storage/overview/>,
  <https://www.hetzner.com/storage/object-storage/>
- Hetzner Storage Box protocols:
  <https://docs.hetzner.com/storage/storage-box/general/>
- Backblaze B2 current pricing and EU region:
  <https://www.backblaze.com/cloud-storage/pricing>,
  <https://www.backblaze.com/docs/cloud-storage-data-regions>
- Backblaze S3 API, Object Lock, lifecycle and application keys:
  <https://www.backblaze.com/docs/cloud-storage-s3-compatible-api>,
  <https://www.backblaze.com/docs/cloud-storage-object-lock>,
  <https://www.backblaze.com/docs/cloud-storage-lifecycle-rules>,
  <https://www.backblaze.com/docs/cloud-storage-application-keys>
