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
