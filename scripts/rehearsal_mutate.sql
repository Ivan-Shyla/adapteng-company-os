-- Change the disposable cluster after the full backup has been taken.
--
-- This exists so the rehearsal can tell two things apart that a naive check
-- cannot: a restore that genuinely reconstructed the backed-up state, and a
-- restore that quietly ended up looking at live data. The state produced here
-- must NOT appear in a restore taken to the end of the backup, and MUST appear
-- in a restore that replays the archived WAL.

\set ON_ERROR_STOP on

UPDATE rehearsal.run_marker SET phase = 'post-backup';

INSERT INTO rehearsal.id_allocation (business_id, entity_kind, allocated_at)
SELECT
    format('AE-PRJ-%s', lpad(g::text, 6, '0')),
    'post-backup',
    timestamptz '2026-06-01 00:00:00+00' + make_interval(secs => g)
FROM generate_series(25001, 25750) AS g;

DELETE FROM rehearsal.run_ledger WHERE entry_id % 250 = 0;

CREATE TABLE rehearsal.post_backup_only (
    id   int PRIMARY KEY,
    note text NOT NULL
);

INSERT INTO rehearsal.post_backup_only (id, note)
SELECT g, md5(format('post-backup-%s', g))
FROM generate_series(1, 500) AS g;
