-- Website clients may cancel their own upcoming booking using its private capability.
BEGIN;
CREATE OR REPLACE FUNCTION gh_cancel_website_booking(p_token uuid, p_admins bigint[] DEFAULT '{}')
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
DECLARE b bookings;
BEGIN
 SELECT * INTO b FROM bookings WHERE web_booking_token=p_token AND client_telegram_id IS NULL;
 IF NOT FOUND THEN RETURN; END IF;
 PERFORM pg_advisory_xact_lock(b.garage_id);
 PERFORM gh_expire_demo_payments(b.garage_id);
 SELECT * INTO b FROM bookings WHERE id=b.id FOR UPDATE;
 -- A retry after a lost HTTP response returns the already-cancelled booking without another event.
 IF b.status='cancelled' THEN RETURN NEXT b; RETURN; END IF;
 IF b.status NOT IN ('pending','confirmed') OR b.start_time<=clock_timestamp() THEN
  RAISE EXCEPTION 'cancellation_closed' USING ERRCODE='55000';
 END IF;
 SELECT * INTO b FROM gh_booking_decide(b.id,'cancelled',0,'website_client',array_prepend(0,p_admins));
 IF NOT FOUND THEN RAISE EXCEPTION 'cancellation_closed' USING ERRCODE='55000'; END IF;
 IF b.demo_payment_status='awaiting' THEN
  UPDATE bookings SET demo_payment_status='cancelled' WHERE id=b.id RETURNING * INTO b;
 END IF;
 RETURN NEXT b;
END $$;
REVOKE ALL ON FUNCTION gh_cancel_website_booking(uuid,bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gh_cancel_website_booking(uuid,bigint[]) TO service_role;
CREATE OR REPLACE FUNCTION gh_readiness() RETURNS boolean LANGUAGE sql AS $$
 SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='bookings_no_overlap'
   AND conrelid='bookings'::regclass)
  AND to_regprocedure('gh_booking_create(jsonb,bigint[])') IS NOT NULL
  AND to_regprocedure('gh_demo_payment(uuid,text)') IS NOT NULL
  AND to_regprocedure('gh_website_bookings(uuid[])') IS NOT NULL
  AND to_regprocedure('gh_cancel_website_booking(uuid,bigint[])') IS NOT NULL;
$$;
NOTIFY pgrst,'reload schema';
COMMIT;
