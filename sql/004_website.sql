-- Сайт принимает заявки независимо от Telegram. Выполнять после 003, после backup.
BEGIN;
ALTER TABLE bookings ALTER COLUMN client_telegram_id DROP NOT NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS web_client_name text;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS web_client_phone text;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS web_request_id uuid;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS web_client_hash text;
CREATE UNIQUE INDEX IF NOT EXISTS bookings_web_request ON bookings(web_request_id);
CREATE INDEX IF NOT EXISTS bookings_web_phone ON bookings(web_client_phone) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS bookings_web_hash ON bookings(web_client_hash) WHERE status = 'pending';
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bookings_client_identity'
                   AND conrelid = 'bookings'::regclass) THEN
        ALTER TABLE bookings ADD CONSTRAINT bookings_client_identity CHECK (
          client_telegram_id IS NOT NULL OR
          (web_request_id IS NOT NULL AND web_client_name IS NOT NULL
           AND length(web_client_name) BETWEEN 2 AND 80 AND web_client_phone IS NOT NULL
           AND web_client_phone ~ '^\+[1-9][0-9]{9,14}$' AND web_client_hash IS NOT NULL));
    END IF;
END $$;

-- Только service_role: данные заполняет сервер после валидации запроса.
-- Отсутствующий Telegram ID не заменяется выдуманным пользователем.
CREATE OR REPLACE FUNCTION gh_booking_create(p_data jsonb, p_admins bigint[] DEFAULT '{}')
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
DECLARE b bookings; previous bookings; g garages; local_start timestamp; local_end timestamp;
BEGIN
    b := jsonb_populate_record(NULL::bookings, p_data);
    IF b.client_telegram_id IS NULL THEN
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
        total_price, status, web_client_name, web_client_phone, web_request_id, web_client_hash)
      VALUES(b.garage_id, b.client_telegram_id, b.client_username, b.start_time, b.end_time,
        extract(epoch FROM b.end_time - b.start_time) / 3600 * g.price_per_hour, 'pending',
        b.web_client_name, b.web_client_phone, b.web_request_id, b.web_client_hash)
      RETURNING * INTO b;
    INSERT INTO notification_outbox(event_key, recipient_id, payload)
      SELECT 'booking:' || b.id || ':created', a,
        jsonb_build_object('kind', 'booking_created', 'booking_id', b.id)
      FROM unnest(p_admins) a WHERE a > 0 ON CONFLICT DO NOTHING;
    RETURN NEXT b;
END $$;
REVOKE ALL ON FUNCTION gh_booking_create(jsonb, bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gh_booking_create(jsonb, bigint[]) TO service_role;

-- Guest bookings have no Telegram access grant or client Telegram notification.
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
        IF NOT ((b.client_telegram_id IS NOT NULL AND b.client_telegram_id = p_actor_id)
            OR (b.client_telegram_id IS NULL AND p_actor_id = ANY(p_admins)))
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
    IF p_status = 'confirmed' AND b.client_telegram_id IS NOT NULL THEN
        SELECT * INTO d FROM lock_devices WHERE garage_id = b.garage_id AND enabled;
        IF FOUND THEN
            INSERT INTO access_grants(booking_id, user_id, device_id, starts_at, ends_at)
              VALUES(b.id, b.client_telegram_id, d.id, b.start_time, b.end_time);
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
          FROM unnest(p_admins) a WHERE a > 0 ON CONFLICT DO NOTHING;
    ELSIF b.client_telegram_id IS NOT NULL THEN
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          VALUES('booking:' || b.id || ':' || p_status, b.client_telegram_id,
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
          SELECT b.id, b.client_telegram_id, d.id, b.start_time, b.end_time FROM bookings b
          WHERE b.client_telegram_id IS NOT NULL AND b.garage_id = p_garage_id AND b.status = 'confirmed' AND b.end_time > now()
          ON CONFLICT(booking_id) DO NOTHING RETURNING booking_id)
        INSERT INTO audit_log(actor_id, action, booking_id, device_id, reason)
          SELECT p_actor_id, 'grant_created', booking_id, d.id, 'device_connected' FROM granted;
    END IF;
    INSERT INTO audit_log(actor_id, action, device_id, reason)
      VALUES(p_actor_id, 'device_configured', d.id, p_reason);
    RETURN NEXT d;
END $$;

-- Immediate decision notifications should not consume an older admin alert instead.
CREATE OR REPLACE FUNCTION gh_claim_notification(p_booking_id bigint DEFAULT NULL)
RETURNS SETOF notification_outbox LANGUAGE sql AS $$
    UPDATE notification_outbox SET attempts = attempts + 1, lease_until = now() + interval '60 seconds'
    WHERE id = (SELECT id FROM notification_outbox WHERE sent_at IS NULL AND available_at <= now()
      AND (lease_until IS NULL OR lease_until <= now())
      AND (p_booking_id IS NULL OR payload->>'booking_id' = p_booking_id::text)
      ORDER BY CASE WHEN p_booking_id IS NOT NULL AND payload->>'kind' = 'booking_created' THEN 1 ELSE 0 END,
        id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *;
$$;

CREATE OR REPLACE FUNCTION gh_readiness() RETURNS boolean LANGUAGE sql AS $$
    SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bookings_no_overlap'
      AND conrelid = 'bookings'::regclass) AND to_regclass('lock_commands') IS NOT NULL
      AND to_regprocedure('gh_booking_create(jsonb,bigint[])') IS NOT NULL;
$$;
NOTIFY pgrst, 'reload schema';
COMMIT;

