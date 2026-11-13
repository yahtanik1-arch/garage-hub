-- Выполнять после 000–002. Вся миграция атомарна.
-- Существующие пересекающиеся активные брони нужно разобрать ДО применения.
BEGIN;
CREATE EXTENSION IF NOT EXISTS btree_gist;
-- Exclusion constraint сохраняет целостность, а захват ресурса до вставки исключает
-- взаимные speculative-insert ожидания двух одновременных INSERT (40P01).
CREATE OR REPLACE FUNCTION gh_booking_insert_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(NEW.garage_id);
    RETURN NEW;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'bookings_resource_lock'
                   AND tgrelid = 'bookings'::regclass) THEN
        CREATE TRIGGER bookings_resource_lock BEFORE INSERT ON bookings
          FOR EACH ROW EXECUTE FUNCTION gh_booking_insert_lock();
    END IF;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bookings_no_overlap'
                   AND conrelid = 'bookings'::regclass) THEN
        ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap EXCLUDE USING gist
          (garage_id WITH =, tstzrange(start_time, end_time, '[)') WITH &&)
          WHERE (status IN ('pending', 'confirmed'));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS bookings_client_time ON bookings(client_telegram_id, start_time DESC);
CREATE INDEX IF NOT EXISTS bookings_status_time ON bookings(status, start_time);

CREATE TABLE IF NOT EXISTS lock_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    garage_id bigint NOT NULL UNIQUE REFERENCES garages(id),
    provider text NOT NULL CHECK (provider IN ('simulator', 'tuya')),
    provider_device_id text NOT NULL CHECK (length(provider_device_id) BETWEEN 1 AND 128),
    enabled boolean NOT NULL DEFAULT false,
    capabilities jsonb NOT NULL DEFAULT '{}',
    telemetry jsonb NOT NULL DEFAULT '{}',
    observed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(provider, provider_device_id)
);
CREATE TABLE IF NOT EXISTS access_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id bigint NOT NULL UNIQUE REFERENCES bookings(id),
    user_id bigint NOT NULL,
    device_id uuid NOT NULL REFERENCES lock_devices(id),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL CHECK (ends_at > starts_at),
    version int NOT NULL DEFAULT 1,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS unlock_actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id bigint NOT NULL REFERENCES bookings(id),
    user_id bigint NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS unlock_actions_owner ON unlock_actions(user_id, booking_id, created_at DESC);
CREATE TABLE IF NOT EXISTS lock_commands (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES lock_devices(id),
    booking_id bigint REFERENCES bookings(id),
    grant_id uuid REFERENCES access_grants(id),
    grant_version int,
    actor_id bigint NOT NULL,
    action_key text NOT NULL CHECK (length(action_key) BETWEEN 8 AND 128),
    source text NOT NULL CHECK (source IN ('bot', 'miniapp', 'admin')),
    reason text,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN
      ('queued', 'dispatching', 'cloud_accepted', 'device_confirmed', 'failed', 'expired', 'cancelled', 'unknown')),
    simulated boolean NOT NULL,
    attempts int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    dispatched_at timestamptz,
    lease_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    result_code text,
    UNIQUE(actor_id, action_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS lock_commands_one_unresolved ON lock_commands(device_id)
  WHERE state IN ('queued', 'dispatching', 'cloud_accepted', 'unknown');
CREATE INDEX IF NOT EXISTS lock_commands_queue ON lock_commands(state, created_at);
CREATE INDEX IF NOT EXISTS lock_commands_booking ON lock_commands(booking_id, created_at DESC);
CREATE TABLE IF NOT EXISTS device_events (
    id bigserial PRIMARY KEY,
    provider text NOT NULL,
    event_id text NOT NULL CHECK (length(event_id) BETWEEN 1 AND 128),
    device_id uuid NOT NULL REFERENCES lock_devices(id),
    kind text NOT NULL CHECK (kind IN ('unlocked', 'door_open', 'telemetry')),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    command_id uuid REFERENCES lock_commands(id),
    data jsonb NOT NULL DEFAULT '{}',
    UNIQUE(provider, event_id)
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    id bigserial PRIMARY KEY,
    event_key text NOT NULL,
    recipient_id bigint NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    attempts int NOT NULL DEFAULT 0,
    sent_at timestamptz,
    last_error text,
    UNIQUE(event_key, recipient_id)
);
CREATE INDEX IF NOT EXISTS notification_outbox_pending ON notification_outbox(available_at) WHERE sent_at IS NULL;
CREATE TABLE IF NOT EXISTS audit_log (
    id bigserial PRIMARY KEY,
    actor_id bigint,
    action text NOT NULL,
    booking_id bigint,
    device_id uuid,
    command_id uuid,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS service_heartbeats (
    name text PRIMARY KEY,
    seen_at timestamptz NOT NULL DEFAULT now()
);

-- RPC доступны только серверу. actor_id берётся из проверенного Telegram-события,
-- никогда не из пользовательского JSON. Функции SECURITY INVOKER: публичного обхода RLS нет.
CREATE OR REPLACE FUNCTION gh_booking_decide(p_booking_id bigint, p_status text,
    p_actor_id bigint, p_reason text DEFAULT NULL, p_admins bigint[] DEFAULT '{}')
RETURNS SETOF bookings LANGUAGE plpgsql AS $$
DECLARE b bookings; d lock_devices;
BEGIN
    IF p_status NOT IN ('confirmed', 'rejected', 'cancelled') THEN
        RAISE EXCEPTION 'bad_status' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO b FROM bookings WHERE id = p_booking_id;
    IF NOT FOUND THEN RETURN; END IF;
    -- Одинаковый порядок захвата для решения, регистрации, отправки и реестра.
    PERFORM pg_advisory_xact_lock(b.garage_id);
    SELECT * INTO b FROM bookings WHERE id = p_booking_id FOR UPDATE;
    IF p_status = 'cancelled' THEN
        IF b.client_telegram_id <> p_actor_id OR b.status NOT IN ('pending', 'confirmed')
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
    IF p_status = 'confirmed' THEN
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
          FROM unnest(p_admins) a ON CONFLICT DO NOTHING;
    ELSE
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          VALUES('booking:' || b.id || ':' || p_status, b.client_telegram_id,
          jsonb_build_object('kind', 'booking_' || p_status, 'booking_id', b.id)) ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEXT b;
END $$;

CREATE OR REPLACE FUNCTION gh_issue_action(p_booking_id bigint, p_user_id bigint)
RETURNS SETOF unlock_actions LANGUAGE plpgsql AS $$
DECLARE b bookings; a unlock_actions; g access_grants;
BEGIN
    SELECT * INTO b FROM bookings WHERE id = p_booking_id AND client_telegram_id = p_user_id;
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
        SELECT garage_id INTO gid FROM bookings WHERE id = p_booking_id AND client_telegram_id = p_actor_id;
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
        SELECT * INTO b FROM bookings WHERE id = p_booking_id AND client_telegram_id = p_actor_id;
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

CREATE OR REPLACE FUNCTION gh_claim_command()
RETURNS SETOF lock_commands LANGUAGE plpgsql AS $$
DECLARE candidate record; c lock_commands; g access_grants; d lock_devices; valid boolean;
BEGIN
    -- Блокировка команды не удерживается во время сетевого вызова.
    FOR candidate IN SELECT c0.id, d0.garage_id FROM lock_commands c0
      JOIN lock_devices d0 ON d0.id = c0.device_id WHERE c0.state = 'queued'
      ORDER BY c0.created_at LIMIT 50 LOOP
        IF NOT pg_try_advisory_xact_lock(candidate.garage_id) THEN CONTINUE; END IF;
        SELECT * INTO c FROM lock_commands WHERE id = candidate.id AND state = 'queued' FOR UPDATE SKIP LOCKED;
        IF NOT FOUND THEN CONTINUE; END IF;
        SELECT * INTO d FROM lock_devices WHERE id = c.device_id;
        valid := d.enabled;
        IF c.booking_id IS NOT NULL THEN
            SELECT * INTO g FROM access_grants WHERE id = c.grant_id;
            valid := valid AND g.revoked_at IS NULL AND g.version = c.grant_version
              AND g.starts_at <= now() AND g.ends_at > now() AND g.device_id = c.device_id
              AND EXISTS (SELECT 1 FROM bookings WHERE id = c.booking_id AND status = 'confirmed');
        END IF;
        IF c.expires_at <= now() OR NOT coalesce(valid, false) THEN
            UPDATE lock_commands SET state = CASE WHEN c.expires_at <= now() THEN 'expired' ELSE 'cancelled' END,
              updated_at = now(), result_code = 'access_or_expiry' WHERE id = c.id;
            INSERT INTO notification_outbox(event_key, recipient_id, payload)
              VALUES('command:' || c.id || ':not_sent', c.actor_id,
                jsonb_build_object('kind', 'command', 'command_id', c.id)) ON CONFLICT DO NOTHING;
            CONTINUE;
        END IF;
        UPDATE lock_commands SET state = 'dispatching', attempts = attempts + 1,
          dispatched_at = now(), lease_until = now() + interval '60 seconds', updated_at = now()
          WHERE id = c.id RETURNING * INTO c;
        RETURN NEXT c; RETURN;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION gh_finish_command(p_command_id uuid, p_state text, p_code text,
    p_admins bigint[] DEFAULT '{}') RETURNS SETOF lock_commands LANGUAGE plpgsql AS $$
DECLARE c lock_commands; gid bigint;
BEGIN
    IF p_state NOT IN ('cloud_accepted', 'failed', 'unknown') THEN
        RAISE EXCEPTION 'bad_result' USING ERRCODE = '22023';
    END IF;
    SELECT d.garage_id INTO gid FROM lock_commands c0 JOIN lock_devices d ON d.id = c0.device_id WHERE c0.id = p_command_id;
    IF gid IS NULL THEN RETURN; END IF;
    PERFORM pg_advisory_xact_lock(gid);
    UPDATE lock_commands SET state = p_state, result_code = left(p_code, 80), updated_at = now(),
      lease_until = CASE WHEN p_state = 'cloud_accepted' THEN now() + interval '2 minutes' ELSE NULL END
      WHERE id = p_command_id AND state = 'dispatching' RETURNING * INTO c;
    IF NOT FOUND THEN RETURN; END IF;
    INSERT INTO notification_outbox(event_key, recipient_id, payload)
      VALUES('command:' || c.id || ':' || c.state, c.actor_id,
        jsonb_build_object('kind', 'command', 'command_id', c.id)) ON CONFLICT DO NOTHING;
    IF c.state IN ('failed', 'unknown') THEN
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          SELECT 'command:' || c.id || ':' || c.state, a,
          jsonb_build_object('kind', 'command_alert', 'command_id', c.id) FROM unnest(p_admins) a ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEXT c;
END $$;

CREATE OR REPLACE FUNCTION gh_recover_commands(p_admins bigint[] DEFAULT '{}')
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE c lock_commands; total int := 0; candidate record;
BEGIN
    FOR candidate IN SELECT c0.id, d.garage_id FROM lock_commands c0
      JOIN lock_devices d ON d.id = c0.device_id
      WHERE c0.state IN ('dispatching', 'cloud_accepted') AND c0.lease_until <= now() LOOP
        IF NOT pg_try_advisory_xact_lock(candidate.garage_id) THEN CONTINUE; END IF;
        UPDATE lock_commands SET state = 'unknown', result_code = 'recovery_required', updated_at = now(), lease_until = NULL
          WHERE id = candidate.id AND state IN ('dispatching', 'cloud_accepted') AND lease_until <= now() RETURNING * INTO c;
        IF NOT FOUND THEN CONTINUE; END IF;
        total := total + 1;
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          SELECT 'command:' || c.id || ':unknown', a,
          jsonb_build_object('kind', 'command_alert', 'command_id', c.id)
          FROM unnest(array_append(p_admins, c.actor_id)) a ON CONFLICT DO NOTHING;
    END LOOP;
    RETURN total;
END $$;

CREATE OR REPLACE FUNCTION gh_device_event(p_device_id uuid, p_event_id text, p_kind text,
    p_occurred_at timestamptz, p_command_id uuid DEFAULT NULL, p_data jsonb DEFAULT '{}')
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE d lock_devices; c lock_commands; eid bigint;
BEGIN
    SELECT * INTO d FROM lock_devices WHERE id = p_device_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0002'; END IF;
    PERFORM pg_advisory_xact_lock(d.garage_id);
    IF p_occurred_at > now() + interval '30 seconds' THEN
        RAISE EXCEPTION 'future_event' USING ERRCODE = '22023';
    END IF;
    IF p_command_id IS NOT NULL THEN
        SELECT * INTO c FROM lock_commands WHERE id = p_command_id AND device_id = d.id;
        IF NOT FOUND THEN RAISE EXCEPTION 'wrong_correlation' USING ERRCODE = '22023'; END IF;
    END IF;
    INSERT INTO device_events(provider, event_id, device_id, kind, occurred_at, command_id, data)
      VALUES(d.provider, p_event_id, d.id, p_kind, p_occurred_at, p_command_id, p_data)
      ON CONFLICT DO NOTHING RETURNING id INTO eid;
    IF eid IS NULL THEN RETURN false; END IF;
    UPDATE lock_devices SET observed_at = p_occurred_at,
      telemetry = p_data || jsonb_build_object('event', p_kind)
      WHERE id = d.id AND (observed_at IS NULL OR observed_at <= p_occurred_at);
    IF p_kind = 'unlocked' AND c.id IS NOT NULL AND c.state IN ('dispatching', 'cloud_accepted', 'unknown')
       AND c.dispatched_at <= p_occurred_at THEN
        UPDATE lock_commands SET state = 'device_confirmed', result_code = 'device_event',
          lease_until = NULL, updated_at = now() WHERE id = c.id;
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          VALUES('command:' || c.id || ':device_confirmed', c.actor_id,
            jsonb_build_object('kind', 'command', 'command_id', c.id)) ON CONFLICT DO NOTHING;
        INSERT INTO audit_log(action, device_id, command_id, reason)
          VALUES('device_confirmed', d.id, c.id, p_event_id);
    END IF;
    RETURN true;
END $$;

CREATE OR REPLACE FUNCTION gh_resolve_command(p_command_id uuid, p_actor_id bigint,
    p_state text, p_reason text) RETURNS SETOF lock_commands LANGUAGE plpgsql AS $$
DECLARE c lock_commands; gid bigint;
BEGIN
    IF p_state NOT IN ('device_confirmed', 'failed') OR length(trim(coalesce(p_reason, ''))) NOT BETWEEN 3 AND 300 THEN
        RAISE EXCEPTION 'resolution_required' USING ERRCODE = '22023';
    END IF;
    SELECT d.garage_id INTO gid FROM lock_commands c0 JOIN lock_devices d ON d.id = c0.device_id WHERE c0.id = p_command_id;
    IF gid IS NULL THEN RETURN; END IF;
    PERFORM pg_advisory_xact_lock(gid);
    UPDATE lock_commands SET state = p_state, result_code = 'operator_resolution', updated_at = now()
      WHERE id = p_command_id AND state = 'unknown' RETURNING * INTO c;
    IF NOT FOUND THEN RETURN; END IF;
    INSERT INTO audit_log(actor_id, action, device_id, command_id, reason)
      VALUES(p_actor_id, 'unknown_resolved_' || p_state, c.device_id, c.id, p_reason);
    INSERT INTO notification_outbox(event_key, recipient_id, payload)
      VALUES('command:' || c.id || ':resolved', c.actor_id,
        jsonb_build_object('kind', 'command', 'command_id', c.id)) ON CONFLICT DO NOTHING;
    RETURN NEXT c;
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
          WHERE b.garage_id = p_garage_id AND b.status = 'confirmed' AND b.end_time > now()
          ON CONFLICT(booking_id) DO NOTHING RETURNING booking_id)
        INSERT INTO audit_log(actor_id, action, booking_id, device_id, reason)
          SELECT p_actor_id, 'grant_created', booking_id, d.id, 'device_connected' FROM granted;
    END IF;
    INSERT INTO audit_log(actor_id, action, device_id, reason)
      VALUES(p_actor_id, 'device_configured', d.id, p_reason);
    RETURN NEXT d;
END $$;

CREATE OR REPLACE FUNCTION gh_claim_notification(p_booking_id bigint DEFAULT NULL)
RETURNS SETOF notification_outbox LANGUAGE sql AS $$
    UPDATE notification_outbox SET attempts = attempts + 1, lease_until = now() + interval '60 seconds'
    WHERE id = (SELECT id FROM notification_outbox WHERE sent_at IS NULL AND available_at <= now()
      AND (lease_until IS NULL OR lease_until <= now())
      AND (p_booking_id IS NULL OR payload->>'booking_id' = p_booking_id::text)
      ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *;
$$;
CREATE OR REPLACE FUNCTION gh_finish_notification(p_id bigint, p_attempt int, p_sent boolean)
RETURNS void LANGUAGE sql AS $$
    UPDATE notification_outbox SET sent_at = CASE WHEN p_sent THEN now() ELSE NULL END,
      lease_until = NULL, available_at = now() + make_interval(secs => least(300, attempts * 10)),
      last_error = CASE WHEN p_sent THEN NULL ELSE 'delivery_failed' END
      WHERE id = p_id AND attempts = p_attempt AND sent_at IS NULL;
$$;
CREATE OR REPLACE FUNCTION gh_readiness() RETURNS boolean LANGUAGE sql AS $$
    SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bookings_no_overlap'
      AND conrelid = 'bookings'::regclass) AND to_regclass('lock_commands') IS NOT NULL;
$$;

CREATE OR REPLACE FUNCTION gh_worker_tick(p_admins bigint[] DEFAULT '{}')
RETURNS void LANGUAGE plpgsql AS $$
DECLARE issue text; bucket text := floor(extract(epoch FROM now()) / 300)::text;
BEGIN
    INSERT INTO service_heartbeats(name) VALUES('worker')
      ON CONFLICT(name) DO UPDATE SET seen_at = now();
    FOR issue IN SELECT 'queue_delayed' WHERE EXISTS
        (SELECT 1 FROM lock_commands WHERE state = 'queued' AND created_at < now() - interval '1 minute')
      UNION ALL SELECT 'notifications_delayed' WHERE EXISTS
        (SELECT 1 FROM notification_outbox WHERE sent_at IS NULL AND created_at < now() - interval '2 minutes'
         AND payload->>'kind' <> 'ops_alert')
      UNION ALL SELECT 'telemetry_stale' WHERE EXISTS
        (SELECT 1 FROM lock_devices WHERE enabled AND coalesce(observed_at, created_at) < now() - interval '2 minutes') LOOP
        INSERT INTO notification_outbox(event_key, recipient_id, payload)
          SELECT 'ops:' || issue || ':' || bucket, a, jsonb_build_object('kind', 'ops_alert', 'code', issue)
          FROM unnest(p_admins) a ON CONFLICT DO NOTHING;
    END LOOP;
END $$;

-- Только при остановленном worker после восстановления backup. Любая команда из
-- снимка могла уже исполниться после создания backup: её нельзя отправлять вслепую.
CREATE OR REPLACE FUNCTION gh_restore_gate(p_actor_id bigint, p_reason text)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE c lock_commands; candidate record; total int := 0;
BEGIN
    IF length(trim(coalesce(p_reason, ''))) < 3 THEN
        RAISE EXCEPTION 'reason_required' USING ERRCODE = '22023';
    END IF;
    FOR candidate IN SELECT c0.id, d.garage_id FROM lock_commands c0 JOIN lock_devices d ON d.id = c0.device_id
      WHERE c0.state IN ('queued', 'dispatching', 'cloud_accepted') ORDER BY d.garage_id LOOP
        PERFORM pg_advisory_xact_lock(candidate.garage_id);
        UPDATE lock_commands SET state = CASE WHEN state = 'queued' AND expires_at <= now() THEN 'expired' ELSE 'unknown' END,
          updated_at = now(), lease_until = NULL, result_code = 'restored_requires_review'
          WHERE id = candidate.id AND state IN ('queued', 'dispatching', 'cloud_accepted') RETURNING * INTO c;
        IF FOUND THEN
            INSERT INTO audit_log(actor_id, action, command_id, device_id, reason)
              VALUES(p_actor_id, 'restore_gate', c.id, c.device_id, p_reason);
            total := total + 1;
        END IF;
    END LOOP;
    RETURN total;
END $$;

DO $$ DECLARE tab text; fn record; BEGIN
    FOREACH tab IN ARRAY ARRAY['lock_devices', 'access_grants', 'unlock_actions', 'lock_commands',
      'device_events', 'notification_outbox', 'audit_log', 'service_heartbeats'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tab);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', tab);
        EXECUTE format('GRANT ALL ON %I TO service_role', tab);
    END LOOP;
    FOR fn IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname LIKE 'gh\_%' ESCAPE '\' LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', fn.signature);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role', fn.signature);
    END LOOP;
END $$;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
