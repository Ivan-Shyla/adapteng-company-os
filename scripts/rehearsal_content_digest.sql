-- Deterministic content digest for every user table in a rehearsal cluster.
--
-- Emits one line per table as `schema.table|row_count|content_checksum`, which
-- scripts/rehearsal_digest_compare.py parses strictly. The checksum is the MD5
-- of the order-independent set of per-row MD5s, so it detects a changed value
-- in any column of any row, not merely a changed row count.
--
-- Every representation-affecting setting is pinned first. Without that, a
-- timestamptz would render against the cluster's TimeZone and two identical
-- databases could report different checksums for the same bytes.
SET timezone = 'UTC';
SET datestyle = 'ISO, MDY';
SET intervalstyle = 'postgres';
SET extra_float_digits = 3;
SET bytea_output = 'hex';

SELECT
    n.nspname || '.' || c.relname AS entry,
    (xpath(
        '/row/v/text()',
        query_to_xml(
            format('SELECT count(*) AS v FROM %I.%I', n.nspname, c.relname),
            false, true, ''
        )
    ))[1]::text AS row_count,
    (xpath(
        '/row/v/text()',
        query_to_xml(
            format(
                'SELECT md5(coalesce(string_agg(r, '','' ORDER BY r), '''')) AS v'
                ' FROM (SELECT md5(x.*::text) AS r FROM %I.%I x) s',
                n.nspname, c.relname
            ),
            false, true, ''
        )
    ))[1]::text AS content_checksum
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND c.relpersistence = 'p'
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\_%'
ORDER BY 1;
