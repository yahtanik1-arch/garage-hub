-- VK adapter. Apply 000–007 first. Existing Telegram IDs/permissions are unchanged.
-- Internal actor keys are positive for Telegram and negative for VK. They are not Telegram chat IDs.
-- VK's original ID is stored in client_vk_id, never in client_telegram_id.
BEGIN;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS client_vk_id bigint;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS vk_request_id uuid;
ALTER TABLE owner_requests ADD COLUMN IF NOT EXISTS vk_id bigint;
ALTER TABLE owner_requests ADD COLUMN IF NOT EXISTS vk_request_id uuid;
ALTER TABLE garages ADD COLUMN IF NOT EXISTS vk_request_id uuid;
CREATE UNIQUE INDEX IF NOT EXISTS bookings_vk_request ON bookings(vk_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS owner_requests_vk_request ON owner_requests(vk_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS garages_vk_request ON garages(vk_request_id);
CREATE INDEX IF NOT EXISTS bookings_vk_client_time ON bookings(client_vk_id,start_time DESC);
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_client_identity;
ALTER TABLE bookings ADD CONSTRAINT bookings_client_identity CHECK ((
 (client_telegram_id > 0 AND client_vk_id IS NULL) OR
 (client_telegram_id IS NULL AND client_vk_id > 0 AND web_request_id IS NULL) OR
 (client_telegram_id IS NULL AND client_vk_id IS NULL AND web_request_id IS NOT NULL
  AND web_client_name IS NOT NULL AND length(web_client_name) BETWEEN 2 AND 80
  AND web_client_phone IS NOT NULL AND web_client_phone ~ '^\+[1-9][0-9]{9,14}$'
  AND web_client_hash IS NOT NULL)) IS TRUE);
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_vk_identity;
ALTER TABLE bookings ADD CONSTRAINT bookings_vk_identity CHECK (
 (client_vk_id IS NULL OR (client_vk_id > 0 AND client_telegram_id IS NULL AND web_request_id IS NULL))
 AND (client_telegram_id IS NULL OR client_telegram_id > 0));

CREATE TABLE IF NOT EXISTS vk_events (
 id bigserial PRIMARY KEY,
 group_id bigint NOT NULL CHECK(group_id > 0),
 user_id bigint NOT NULL CHECK(user_id > 0),
 event_id text NOT NULL CHECK(length(event_id) BETWEEN 1 AND 200),
 payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 available_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz,
 attempts int NOT NULL DEFAULT 0,
 processed_at timestamptz,
 UNIQUE(group_id,event_id)
);
ALTER TABLE vk_events ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS vk_events_pending ON vk_events(group_id,id) WHERE processed_at IS NULL;
CREATE OR REPLACE FUNCTION gh_claim_vk_event(p_group_id bigint)
RETURNS SETOF vk_events LANGUAGE sql AS $$
 UPDATE vk_events SET attempts=attempts+1,lease_until=now()+interval '5 minutes'
 WHERE id=(SELECT candidate.id FROM vk_events candidate WHERE group_id=p_group_id AND processed_at IS NULL
  AND available_at<=now() AND (lease_until IS NULL OR lease_until<=now())
  AND NOT EXISTS(SELECT 1 FROM vk_events older WHERE older.group_id=candidate.group_id
    AND older.user_id=candidate.user_id AND older.id<candidate.id AND older.processed_at IS NULL)
  ORDER BY candidate.id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *;
$$;
CREATE OR REPLACE FUNCTION gh_finish_vk_event(p_id bigint,p_attempt int,p_success boolean)
RETURNS void LANGUAGE sql AS $$
 UPDATE vk_events SET processed_at=CASE WHEN p_success THEN now() ELSE NULL END,
  payload=CASE WHEN p_success THEN '{}'::jsonb ELSE payload END,
  available_at=now()+make_interval(secs=>least(300,p_attempt*5)),lease_until=NULL
 WHERE id=p_id AND attempts=p_attempt;
$$;
CREATE OR REPLACE FUNCTION gh_booking_create(p_data jsonb, p_admins bigint[] DEFAULT '{}')
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
DECLARE b bookings; previous bookings; g garages; local_start timestamp; local_end timestamp;
BEGIN
    b := jsonb_populate_record(NULL::bookings, p_data);
    IF b.client_telegram_id IS NULL AND b.client_vk_id IS NULL THEN
        IF b.web_request_id IS NULL OR b.web_client_hash IS NULL
           OR length(coalesce(b.web_client_name, '')) NOT BETWEEN 2 AND 80
           OR coalesce(b.web_client_phone, '') !~ '^\+[1-9][0-9]{9,14}$' THEN
            RAISE EXCEPTION 'bad_guest' USING ERRCODE = '22023';
        END IF;
        PERFORM pg_advisory_xact_lock(100, hashtext(b.web_request_id::text));
        SELECT * INTO previous FROM bookings WHERE web_request_id = b.web_request_id;
        IF FOUND THEN
            IF (previous.garage_id, previous.start_time, previous.end_time,
                previous.web_client_name, previous.web_client_phone)
              IS DISTINCT FROM (b.garage_id, b.start_time, b.end_time,
                b.web_client_name, b.web_client_phone) THEN
                RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '22023';
            END IF;
            RETURN NEXT previous;
            RETURN;
        END IF;
        -- Durable limits apply across processes and restarts, including concurrent requests.
        PERFORM pg_advisory_xact_lock(101, hashtext(b.web_client_phone));
        PERFORM pg_advisory_xact_lock(102, hashtext(b.web_client_hash));
        IF (SELECT count(*) FROM bookings WHERE web_client_phone = b.web_client_phone
              AND status = 'pending' AND end_time > now()) >= 5
           OR (SELECT count(*) FROM bookings WHERE web_client_hash = b.web_client_hash
              AND status = 'pending' AND end_time > now()) >= 5 THEN
            RAISE EXCEPTION 'too_many' USING ERRCODE = '22023';
        END IF;
    END IF;
    IF b.client_vk_id IS NOT NULL THEN
        IF b.client_vk_id <= 0 OR b.client_telegram_id IS NOT NULL OR b.vk_request_id IS NULL THEN
            RAISE EXCEPTION 'bad_vk_identity' USING ERRCODE='22023';
        END IF;
        PERFORM pg_advisory_xact_lock(103,hashtext(b.vk_request_id::text));
        SELECT * INTO previous FROM bookings WHERE vk_request_id=b.vk_request_id;
        IF FOUND THEN
            IF (previous.client_vk_id,previous.garage_id,previous.start_time,previous.end_time)
              IS DISTINCT FROM (b.client_vk_id,b.garage_id,b.start_time,b.end_time) THEN
                RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='22023';
            END IF;
            RETURN NEXT previous; RETURN;
        END IF;
        PERFORM pg_advisory_xact_lock(104,hashtext(b.client_vk_id::text));
        IF (SELECT count(*) FROM bookings WHERE client_vk_id=b.client_vk_id AND status='pending' AND end_time>now())>=5 THEN
            RAISE EXCEPTION 'too_many' USING ERRCODE='22023';
        END IF;
    END IF;
    PERFORM pg_advisory_xact_lock(b.garage_id);
    SELECT * INTO g FROM garages WHERE id = b.garage_id AND is_active FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unavailable' USING ERRCODE = 'P0002'; END IF;
    local_start := b.start_time AT TIME ZONE 'Europe/Moscow';
    local_end := b.end_time AT TIME ZONE 'Europe/Moscow';
    IF b.start_time IS NULL OR b.end_time IS NULL OR b.start_time <= now()
       OR b.end_time <= b.start_time
       OR local_start <> date_trunc('hour', local_start)
       OR local_end <> date_trunc('hour', local_end)
       OR local_start::date < (now() AT TIME ZONE 'Europe/Moscow')::date
       OR local_start::date >= (now() AT TIME ZONE 'Europe/Moscow')::date + 7
       OR local_start < local_start::date + make_interval(hours => coalesce(g.open_hour, 9))
       OR local_end > local_start::date + make_interval(hours => coalesce(g.close_hour, 22)) THEN
        RAISE EXCEPTION 'bad_time' USING ERRCODE = '22023';
    END IF;
    INSERT INTO bookings(garage_id, client_telegram_id, client_username, start_time, end_time,
        total_price, status, web_client_name, web_client_phone, web_request_id, web_client_hash, client_vk_id, vk_request_id)
      VALUES(b.garage_id, b.client_telegram_id, b.client_username, b.start_time, b.end_time,
        extract(epoch FROM b.end_time - b.start_time) / 3600 * g.price_per_hour, 'pending',
        b.web_client_name, b.web_client_phone, b.web_request_id, b.web_client_hash, b.client_vk_id, b.vk_request_id)
      RETURNING * INTO b;
    INSERT INTO notification_outbox(event_key, recipient_id, payload)
      SELECT 'booking:' || b.id || ':created', a,
        jsonb_build_object('kind', 'booking_created', 'booking_id', b.id)
      FROM unnest(p_admins) a WHERE a <> 0 ON CONFLICT DO NOTHING;
    RETURN NEXT b;
END $$;

CREATE OR REPLACE FUNCTION gh_booking_decide(p_booking_id bigint, p_status text,
    p_actor_id bigint, p_reason text DEFAULT NULL, p_admins bigint[] DEFAULT '{}')
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
DECLARE b bookings; d lock_devices;
BEGIN
    IF p_status NOT IN ('confirmed', 'rejected', 'cancelled') THEN
        RAISE EXCEPTION 'bad_status' USING ERRCODE = '22023';
    END IF;
    IF p_actor_id IS NULL THEN RAISE EXCEPTION 'access_denied' USING ERRCODE = '42501'; END IF;
    SELECT * INTO b FROM bookings WHERE id = p_booking_id;
    IF NOT FOUND THEN RETURN; END IF;
    -- Одинаковый порядок захвата для решения, регистрации, отправки и реестра.
    PERFORM pg_advisory_xact_lock(b.garage_id);
    SELECT * INTO b FROM bookings WHERE id = p_booking_id FOR UPDATE;
    IF p_status = 'cancelled' THEN
        IF NOT ((coalesce(b.client_telegram_id,-b.client_vk_id) IS NOT NULL
            AND coalesce(b.client_telegram_id,-b.client_vk_id) = p_actor_id)
            OR (b.client_telegram_id IS NULL AND b.client_vk_id IS NULL AND p_actor_id = ANY(p_admins)))
           OR b.status NOT IN ('pending', 'confirmed')
           OR b.end_time <= now() THEN RETURN; END IF;
    ELSE
        IF NOT p_actor_id = ANY(p_admins) THEN
            RAISE EXCEPTION 'access_denied' USING ERRCODE = '42501';
        END IF;
        IF b.status <> 'pending' THEN RETURN; END IF;
    END IF;
    UPDATE bookings SET status = p_status,
      admin_comment = CASE WHEN p_status = 'rejected' THEN p_reason ELSE admin_comment END
      WHERE id = b.id RETURNING * INTO b;
    IF p_status = 'confirmed' AND coalesce(b.client_telegram_id,-b.client_vk_id) IS NOT NULL THEN
        SELECT * INTO d FROM lock_devices WHERE garage_id = b.garage_id AND enabled;
        IF FOUND THEN
            INSERT INTO access_grants(booking_id, user_id, device_id, starts_at, ends_at)
              VALUES(b.id, coalesce(b.client_telegram_id,-b.client_vk_id), d.id, b.start_time, b.end_time);
            INSERT INTO audit_log(actor_id, action, booking_id, device_id, reason)
              VALUES(p_actor_id, 'grant_created', b.id, d.id, 'booking_confirmed');
        END IF;
    ELSE
        WITH revoked AS (
          UPDATE access_grants SET revoked_at = now(), version = version + 1
          WHERE booking_id = b.id AND revoked_at IS NULL RETURNING device_id
        ) INSERT INTO audit_log(actor_id, action, booking_id, device_id, reason)
          SELECT p_actor_id, 'grant_revoked', b.id, device_id, p_status FROM revoked;
        UPDATE lock_commands SET state = 'cancelled', updated_at = now(), result_code = 'grant_revoked'
          WHERE booking_id = b.id AND state = 'queued';
    END IF;
    INSERT INTO audit_log(actor_id, action, booking_id, reason)
      VALUES(p_actor_id, 'booking_' || p_status, b.id, p_reason);
    IF p_status = 'cancelled' THEN
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          SELECT 'booking:' || b.id || ':' || p_status, a,
          jsonb_build_object('kind', 'booking_cancelled', 'booking_id', b.id)
          FROM unnest(p_admins) a WHERE a <> 0 ON CONFLICT DO NOTHING;
    ELSIF coalesce(b.client_telegram_id,-b.client_vk_id) IS NOT NULL THEN
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          VALUES('booking:' || b.id || ':' || p_status, coalesce(b.client_telegram_id,-b.client_vk_id),
          jsonb_build_object('kind', 'booking_' || p_status, 'booking_id', b.id)) ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEXT b;
END $$;

CREATE OR REPLACE FUNCTION gh_set_device(p_garage_id bigint, p_provider text,
    p_provider_device_id text, p_enabled boolean, p_actor_id bigint, p_reason text)
RETURNS SETOF lock_devices LANGUAGE plpgsql AS $$
DECLARE d lock_devices;
BEGIN
    PERFORM pg_advisory_xact_lock(p_garage_id);
    SELECT * INTO d FROM lock_devices WHERE garage_id = p_garage_id;
    IF d.id IS NOT NULL AND (d.provider <> p_provider OR d.provider_device_id <> p_provider_device_id)
      AND (EXISTS (SELECT 1 FROM access_grants WHERE device_id = d.id AND revoked_at IS NULL AND ends_at > now())
        OR EXISTS (SELECT 1 FROM lock_commands WHERE device_id = d.id AND state IN ('queued','dispatching','cloud_accepted','unknown'))) THEN
        RAISE EXCEPTION 'device_in_use' USING ERRCODE = '55000';
    END IF;
    INSERT INTO lock_devices(garage_id, provider, provider_device_id, enabled, capabilities)
      VALUES(p_garage_id, p_provider, p_provider_device_id, p_enabled,
        CASE WHEN p_provider = 'simulator' THEN '{"remote_unlock":true,"correlated_events":true,"door_sensor":false}'::jsonb ELSE '{}'::jsonb END)
      ON CONFLICT(garage_id) DO UPDATE SET provider = excluded.provider,
        provider_device_id = excluded.provider_device_id, enabled = excluded.enabled,
        capabilities = CASE WHEN lock_devices.provider = excluded.provider THEN lock_devices.capabilities ELSE excluded.capabilities END
        RETURNING * INTO d;
    -- Подключение к уже подтверждённым броням: только при первоначальной привязке/включении.
    IF d.enabled THEN
        WITH granted AS (INSERT INTO access_grants(booking_id, user_id, device_id, starts_at, ends_at)
          SELECT b.id, coalesce(b.client_telegram_id,-b.client_vk_id), d.id, b.start_time, b.end_time FROM bookings b
          WHERE coalesce(b.client_telegram_id,-b.client_vk_id) IS NOT NULL AND b.garage_id = p_garage_id AND b.status = 'confirmed' AND b.end_time > now()
          ON CONFLICT(booking_id) DO NOTHING RETURNING booking_id)
        INSERT INTO audit_log(actor_id, action, booking_id, device_id, reason)
          SELECT p_actor_id, 'grant_created', booking_id, d.id, 'device_connected' FROM granted;
    END IF;
    INSERT INTO audit_log(actor_id, action, device_id, reason)
      VALUES(p_actor_id, 'device_configured', d.id, p_reason);
    RETURN NEXT d;
END $$;

CREATE OR REPLACE FUNCTION gh_issue_action(p_booking_id bigint, p_user_id bigint)
RETURNS SETOF unlock_actions LANGUAGE plpgsql AS $$
DECLARE b bookings; a unlock_actions; g access_grants;
BEGIN
    SELECT * INTO b FROM bookings WHERE id = p_booking_id AND coalesce(client_telegram_id,-client_vk_id) = p_user_id;
    IF NOT FOUND THEN RETURN; END IF;
    PERFORM pg_advisory_xact_lock(b.garage_id);
    SELECT * INTO g FROM access_grants WHERE booking_id = b.id AND user_id = p_user_id
      AND revoked_at IS NULL AND starts_at <= now() AND ends_at > now();
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM bookings WHERE id = b.id AND status = 'confirmed')
       OR NOT EXISTS (SELECT 1 FROM lock_devices WHERE id = g.device_id AND enabled) THEN RETURN; END IF;
    SELECT a0.* INTO a FROM unlock_actions a0 LEFT JOIN lock_commands c
      ON c.actor_id = p_user_id AND c.action_key = a0.id::text
      WHERE a0.booking_id = b.id AND a0.user_id = p_user_id AND a0.expires_at > now()
        AND (c.id IS NULL OR c.state IN ('queued', 'dispatching', 'cloud_accepted', 'unknown'))
      ORDER BY a0.created_at DESC LIMIT 1;
    IF NOT FOUND THEN
        INSERT INTO unlock_actions(booking_id, user_id, expires_at)
          VALUES(b.id, p_user_id, least(now() + interval '5 minutes', g.ends_at)) RETURNING * INTO a;
    END IF;
    RETURN NEXT a;
END $$;

CREATE OR REPLACE FUNCTION gh_register_unlock(p_actor_id bigint, p_action_key text,
    p_source text, p_booking_id bigint DEFAULT NULL, p_device_id uuid DEFAULT NULL,
    p_reason text DEFAULT NULL, p_allow_simulator boolean DEFAULT false)
RETURNS SETOF lock_commands LANGUAGE plpgsql AS $$
DECLARE b bookings; d lock_devices; g access_grants; c lock_commands; gid bigint;
BEGIN
    IF length(p_action_key) NOT BETWEEN 8 AND 128 OR p_source NOT IN ('bot', 'miniapp', 'admin') THEN
        RAISE EXCEPTION 'bad_action' USING ERRCODE = '22023';
    END IF;
    IF p_source = 'admin' THEN
        IF p_booking_id IS NOT NULL OR length(trim(coalesce(p_reason, ''))) NOT BETWEEN 3 AND 300 THEN
            RAISE EXCEPTION 'reason_required' USING ERRCODE = '22023';
        END IF;
        SELECT garage_id INTO gid FROM lock_devices WHERE id = p_device_id;
    ELSE
        IF p_device_id IS NOT NULL THEN RAISE EXCEPTION 'bad_device' USING ERRCODE = '22023'; END IF;
        SELECT garage_id INTO gid FROM bookings WHERE id = p_booking_id AND coalesce(client_telegram_id,-client_vk_id) = p_actor_id;
    END IF;
    IF gid IS NULL THEN RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0002'; END IF;
    PERFORM pg_advisory_xact_lock(gid);
    SELECT * INTO c FROM lock_commands WHERE actor_id = p_actor_id AND action_key = p_action_key;
    IF FOUND THEN
        IF c.booking_id IS DISTINCT FROM p_booking_id
           OR (p_source = 'admin' AND (c.device_id IS DISTINCT FROM p_device_id OR c.reason IS DISTINCT FROM p_reason))
           OR ((c.source = 'admin') <> (p_source = 'admin')) THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE = '23505';
        END IF;
        RETURN NEXT c; RETURN;
    END IF;
    IF p_source <> 'admin' THEN
        SELECT * INTO b FROM bookings WHERE id = p_booking_id AND coalesce(client_telegram_id,-client_vk_id) = p_actor_id;
        SELECT * INTO g FROM access_grants WHERE booking_id = b.id AND user_id = p_actor_id
          AND revoked_at IS NULL AND starts_at <= now() AND ends_at > now();
        IF NOT FOUND OR b.status <> 'confirmed' THEN
            RAISE EXCEPTION 'access_denied' USING ERRCODE = '42501';
        END IF;
        SELECT * INTO d FROM lock_devices WHERE id = g.device_id AND garage_id = b.garage_id AND enabled;
        IF p_source = 'bot' AND NOT EXISTS (SELECT 1 FROM unlock_actions WHERE id::text = p_action_key
            AND user_id = p_actor_id AND booking_id = b.id AND expires_at > now()) THEN
            RAISE EXCEPTION 'action_expired' USING ERRCODE = '42501';
        END IF;
    ELSE
        SELECT * INTO d FROM lock_devices WHERE id = p_device_id AND enabled;
    END IF;
    IF d.id IS NULL OR NOT coalesce((d.capabilities->>'remote_unlock')::boolean, false)
       OR (d.provider = 'simulator' AND NOT p_allow_simulator) THEN
        RAISE EXCEPTION 'device_unavailable' USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM lock_commands WHERE device_id = d.id
      AND state IN ('queued', 'dispatching', 'cloud_accepted', 'unknown')) THEN
        RAISE EXCEPTION 'device_busy' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (SELECT 1 FROM lock_commands WHERE device_id = d.id AND created_at > now() - interval '3 seconds') THEN
        RAISE EXCEPTION 'rate_limited' USING ERRCODE = '55000';
    END IF;
    INSERT INTO lock_commands(device_id, booking_id, grant_id, grant_version, actor_id, action_key,
      source, reason, simulated, expires_at)
      VALUES(d.id, p_booking_id, g.id, g.version, p_actor_id, p_action_key, p_source, p_reason,
        d.provider = 'simulator', least(now() + interval '30 seconds', coalesce(g.ends_at, now() + interval '30 seconds')))
      RETURNING * INTO c;
    INSERT INTO audit_log(actor_id, action, booking_id, device_id, command_id, reason)
      VALUES(p_actor_id, 'unlock_requested', p_booking_id, d.id, c.id, p_reason);
    PERFORM pg_notify('garagehub_commands', 'ready');
    RETURN NEXT c;
END $$;

CREATE OR REPLACE FUNCTION gh_init_demo_payment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(NEW.garage_id);
 PERFORM gh_expire_demo_payments(NEW.garage_id);
 IF NEW.client_telegram_id IS NULL AND NEW.client_vk_id IS NULL AND EXISTS
   (SELECT 1 FROM garages WHERE id=NEW.garage_id AND demo_payment) THEN
  NEW.demo_payment_token:=gen_random_uuid();
  NEW.demo_payment_status:='awaiting';
  NEW.demo_payment_expires_at:=least(now()+interval '10 minutes',NEW.start_time);
 END IF;
 RETURN NEW;
END $$;

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
 IF b.client_telegram_id IS NOT NULL OR b.client_vk_id IS NOT NULL OR NOT EXISTS
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

CREATE OR REPLACE FUNCTION gh_website_bookings(p_tokens uuid[])
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
BEGIN
 IF cardinality(p_tokens) > 100 THEN
  RAISE EXCEPTION 'too_many_tokens' USING ERRCODE='22023';
 END IF;
 PERFORM gh_expire_demo_payments();
 RETURN QUERY SELECT b.* FROM bookings b WHERE b.web_booking_token=ANY(p_tokens)
  AND b.client_telegram_id IS NULL AND b.client_vk_id IS NULL ORDER BY b.start_time DESC;
END $$;

CREATE OR REPLACE FUNCTION gh_cancel_website_booking(p_token uuid, p_admins bigint[] DEFAULT '{}')
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
DECLARE b bookings;
BEGIN
 SELECT * INTO b FROM bookings WHERE web_booking_token=p_token AND client_telegram_id IS NULL AND client_vk_id IS NULL;
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

CREATE OR REPLACE FUNCTION gh_claim_notification(p_booking_id bigint DEFAULT NULL)
RETURNS SETOF notification_outbox LANGUAGE sql AS $$
    UPDATE notification_outbox SET attempts = attempts + 1, lease_until = now() + interval '60 seconds'
    WHERE id = (SELECT id FROM notification_outbox WHERE recipient_id > 0 AND sent_at IS NULL AND available_at <= now()
      AND (lease_until IS NULL OR lease_until <= now())
      AND (p_booking_id IS NULL OR payload->>'booking_id' = p_booking_id::text)
      ORDER BY CASE WHEN p_booking_id IS NOT NULL AND payload->>'kind' = 'booking_created' THEN 1 ELSE 0 END,
        id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *;
$$;

CREATE OR REPLACE FUNCTION gh_claim_channel_notification(p_booking_id bigint DEFAULT NULL,p_channels text[] DEFAULT '{telegram}')
RETURNS SETOF notification_outbox LANGUAGE sql AS $$
    UPDATE notification_outbox SET attempts = attempts + 1, lease_until = now() + interval '60 seconds'
    WHERE id = (SELECT id FROM notification_outbox WHERE (CASE WHEN recipient_id<0 THEN 'vk' ELSE 'telegram' END)=ANY(p_channels) AND sent_at IS NULL AND available_at <= now()
      AND (lease_until IS NULL OR lease_until <= now())
      AND (p_booking_id IS NULL OR payload->>'booking_id' = p_booking_id::text)
      ORDER BY CASE WHEN p_booking_id IS NOT NULL AND payload->>'kind' = 'booking_created' THEN 1 ELSE 0 END,
        id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *;
$$;
REVOKE ALL ON TABLE vk_events FROM PUBLIC,anon;
GRANT ALL ON TABLE vk_events TO service_role;
GRANT USAGE,SELECT ON SEQUENCE vk_events_id_seq TO service_role;
REVOKE ALL ON FUNCTION gh_claim_vk_event(bigint),gh_finish_vk_event(bigint,int,boolean),
 gh_claim_channel_notification(bigint,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gh_claim_vk_event(bigint),gh_finish_vk_event(bigint,int,boolean),
 gh_claim_channel_notification(bigint,text[]) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
