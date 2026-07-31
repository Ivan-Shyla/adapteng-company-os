DO $assert_fresh$
DECLARE
  v_sequence REGCLASS;
  v_sequence_last BIGINT;
  v_sequence_called BOOLEAN;
BEGIN
  IF EXISTS (SELECT 1 FROM public.source_identity_reservation)
     OR EXISTS (SELECT 1 FROM public.drive_bridge_replay_reservations)
     OR EXISTS (
       SELECT 1 FROM public.id_allocator_sequences WHERE prefix = 'AE-RHSL'
     ) THEN
    RAISE EXCEPTION 'generation C contains unexpected migrated test state';
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
    RAISE EXCEPTION 'generation C identity sequence is not fresh';
  END IF;
END
$assert_fresh$;
