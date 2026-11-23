-- Private links for website bookings. No login by an unverified phone number.
BEGIN;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS web_booking_token uuid NOT NULL DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX IF NOT EXISTS bookings_web_access ON bookings(web_booking_token);
ALTER TABLE garages ADD COLUMN IF NOT EXISTS arrival_instructions text NOT NULL DEFAULT '';
-- POST RPC keeps private tokens out of PostgREST request URLs/access logs.
CREATE OR REPLACE FUNCTION gh_website_bookings(p_tokens uuid[])
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
BEGIN
 IF cardinality(p_tokens) > 100 THEN
  RAISE EXCEPTION 'too_many_tokens' USING ERRCODE='22023';
 END IF;
 PERFORM gh_expire_demo_payments();
 RETURN QUERY SELECT b.* FROM bookings b WHERE b.web_booking_token=ANY(p_tokens)
  AND b.client_telegram_id IS NULL ORDER BY b.start_time DESC;
END $$;
REVOKE ALL ON FUNCTION gh_website_bookings(uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gh_website_bookings(uuid[]) TO service_role;
CREATE OR REPLACE FUNCTION gh_readiness() RETURNS boolean LANGUAGE sql AS $$
 SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='bookings_no_overlap'
   AND conrelid='bookings'::regclass)
  AND to_regprocedure('gh_booking_create(jsonb,bigint[])') IS NOT NULL
  AND to_regprocedure('gh_demo_payment(uuid,text)') IS NOT NULL
  AND to_regprocedure('gh_website_bookings(uuid[])') IS NOT NULL
  AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
   AND table_name='bookings' AND column_name='web_booking_token');
$$;
NOTIFY pgrst,'reload schema';
COMMIT;
