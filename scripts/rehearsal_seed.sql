-- Seed the disposable rehearsal cluster with deterministic content.
--
-- Nothing here is random or clock-derived: the same seed produces the same
-- content checksum on every run, so a checksum difference after a restore is a
-- restore defect rather than noise. The shapes deliberately echo the kinds of
-- state the production database actually holds (an id allocator, a reservation
-- table, a run ledger) so the rehearsal exercises rows, indexes, constraints
-- and sequences rather than one flat table.
--
-- Requires psql variables: run_id, run_attempt.

\set ON_ERROR_STOP on

CREATE SCHEMA rehearsal;

CREATE TABLE rehearsal.run_marker (
    run_id      text PRIMARY KEY,
    run_attempt text NOT NULL,
    phase       text NOT NULL
);

INSERT INTO rehearsal.run_marker (run_id, run_attempt, phase)
VALUES (:'run_id', :'run_attempt', 'pre-backup');

CREATE TABLE rehearsal.id_allocation (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_id  text NOT NULL UNIQUE,
    entity_kind  text NOT NULL,
    allocated_at timestamptz NOT NULL
);

INSERT INTO rehearsal.id_allocation (business_id, entity_kind, allocated_at)
SELECT
    format('AE-PRJ-%s', lpad(g::text, 6, '0')),
    (ARRAY['project', 'case', 'content', 'action'])[1 + (g % 4)],
    timestamptz '2026-01-01 00:00:00+00' + make_interval(secs => g)
FROM generate_series(1, 25000) AS g;

CREATE INDEX id_allocation_entity_kind_idx
    ON rehearsal.id_allocation (entity_kind, business_id);

CREATE TABLE rehearsal.lead_reservation (
    reservation_id uuid PRIMARY KEY,
    business_id    text NOT NULL REFERENCES rehearsal.id_allocation (business_id),
    payload        jsonb NOT NULL,
    reserved_at    timestamptz NOT NULL
);

INSERT INTO rehearsal.lead_reservation (reservation_id, business_id, payload, reserved_at)
SELECT
    md5(format('reservation-%s', g))::uuid,
    format('AE-PRJ-%s', lpad(g::text, 6, '0')),
    jsonb_build_object(
        'sequence', g,
        'digest', md5(format('payload-%s', g)),
        'channel', (ARRAY['web', 'referral', 'direct'])[1 + (g % 3)]
    ),
    timestamptz '2026-02-01 00:00:00+00' + make_interval(secs => g * 7)
FROM generate_series(1, 8000) AS g;

CREATE TABLE rehearsal.run_ledger (
    entry_id   bigserial PRIMARY KEY,
    task       text NOT NULL,
    outcome    text NOT NULL,
    body       bytea NOT NULL,
    recorded_at timestamptz NOT NULL,
    CONSTRAINT run_ledger_outcome_known CHECK (outcome IN ('ok', 'retry', 'failed'))
);

INSERT INTO rehearsal.run_ledger (task, outcome, body, recorded_at)
SELECT
    format('task-%s', lpad((g % 97)::text, 4, '0')),
    (ARRAY['ok', 'retry', 'failed'])[1 + (g % 3)],
    decode(md5(format('body-%s', g)), 'hex'),
    timestamptz '2026-03-01 00:00:00+00' + make_interval(secs => g * 11)
FROM generate_series(1, 12000) AS g;

ANALYZE;
