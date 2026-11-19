-- Only fictional boxes explicitly enabled by an operator. No actual payments.
BEGIN;
ALTER TABLE garages ADD COLUMN IF NOT EXISTS demo_payment boolean NOT NULL DEFAULT false;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS demo_payment_token uuid;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS demo_payment_expires_at timestamptz;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS demo_payment_status text;
CREATE UNIQUE INDEX IF NOT EXISTS bookings_demo_token ON bookings(demo_payment_token);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='bookings_demo_payment_state') THEN
  ALTER TABLE bookings ADD CONSTRAINT bookings_demo_payment_state CHECK (
   demo_payment_status IS NULL OR
   (demo_payment_token IS NOT NULL AND client_telegram_id IS NULL
    AND demo_payment_expires_at IS NOT NULL
    AND demo_payment_status IN ('awaiting','paid','expired','cancelled')
    AND (status <> 'confirmed' OR demo_payment_status='paid')));
 END IF;
END $$;

CREATE OR REPLACE FUNCTION gh_expire_demo_payments(p_garage_id bigint DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE gid bigint;
BEGIN
 FOR gid IN SELECT DISTINCT garage_id FROM bookings WHERE status='pending'
   AND demo_payment_status='awaiting' AND demo_payment_expires_at<=clock_timestamp()
   AND (p_garage_id IS NULL OR garage_id=p_garage_id) ORDER BY garage_id LOOP
  PERFORM pg_advisory_xact_lock(gid);
  UPDATE bookings SET status='cancelled', demo_payment_status='expired'
   WHERE garage_id=gid AND status='pending' AND demo_payment_status='awaiting'
    AND demo_payment_expires_at<=clock_timestamp();
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION gh_init_demo_payment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(NEW.garage_id);
 PERFORM gh_expire_demo_payments(NEW.garage_id);
 IF NEW.client_telegram_id IS NULL AND EXISTS
   (SELECT 1 FROM garages WHERE id=NEW.garage_id AND demo_payment) THEN
  NEW.demo_payment_token:=gen_random_uuid();
  NEW.demo_payment_status:='awaiting';
  NEW.demo_payment_expires_at:=least(now()+interval '10 minutes',NEW.start_time);
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS bookings_init_demo_payment ON bookings;
CREATE TRIGGER bookings_init_demo_payment BEFORE INSERT ON bookings
 FOR EACH ROW EXECUTE FUNCTION gh_init_demo_payment();

CREATE OR REPLACE FUNCTION gh_demo_payment(p_token uuid, p_action text DEFAULT 'status')
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
DECLARE b bookings;
BEGIN
 IF p_action NOT IN ('status','pay','cancel') THEN
  RAISE EXCEPTION 'bad_action' USING ERRCODE='22023';
 END IF;
 SELECT * INTO b FROM bookings WHERE demo_payment_token=p_token;
 IF NOT FOUND THEN RETURN; END IF;
 PERFORM pg_advisory_xact_lock(b.garage_id);
 SELECT * INTO b FROM bookings WHERE id=b.id FOR UPDATE;
 IF b.client_telegram_id IS NOT NULL OR NOT EXISTS
   (SELECT 1 FROM garages WHERE id=b.garage_id AND demo_payment) THEN RETURN; END IF;
 IF b.status='pending' AND b.demo_payment_status='awaiting' THEN
  IF b.demo_payment_expires_at<=clock_timestamp() THEN
   UPDATE bookings SET status='cancelled',demo_payment_status='expired'
    WHERE id=b.id RETURNING * INTO b;
  ELSIF p_action='pay' THEN
   UPDATE bookings SET status='confirmed',demo_payment_status='paid'
    WHERE id=b.id RETURNING * INTO b;
   INSERT INTO audit_log(actor_id,action,booking_id,reason)
    VALUES(0,'demo_payment_paid',b.id,'Simulation only: no money charged');
  ELSIF p_action='cancel' THEN
   UPDATE bookings SET status='cancelled',demo_payment_status='cancelled'
    WHERE id=b.id RETURNING * INTO b;
  END IF;
 END IF;
 RETURN NEXT b;
END $$;
REVOKE ALL ON FUNCTION gh_demo_payment(uuid,text),gh_expire_demo_payments(bigint),gh_init_demo_payment() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gh_demo_payment(uuid,text),gh_expire_demo_payments(bigint),gh_init_demo_payment() TO service_role;
CREATE OR REPLACE FUNCTION gh_readiness() RETURNS boolean LANGUAGE sql AS $$
 SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='bookings_no_overlap'
  AND conrelid='bookings'::regclass) AND to_regprocedure('gh_demo_payment(uuid,text)') IS NOT NULL;
$$;
NOTIFY pgrst,'reload schema';
COMMIT;
