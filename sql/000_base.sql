-- Исходная схема для новой базы. Для существующей базы таблицы не пересоздаются.
CREATE TABLE IF NOT EXISTS garages (
    id bigserial PRIMARY KEY,
    type text NOT NULL DEFAULT 'garage',
    name text NOT NULL,
    district text,
    address text,
    photos text[] NOT NULL DEFAULT '{}',
    price_per_hour numeric(12,2) NOT NULL DEFAULT 0,
    has_lift boolean NOT NULL DEFAULT false,
    has_pit boolean NOT NULL DEFAULT false,
    tool_availability text,
    box_size text,
    working_hours text,
    description text,
    owner_name text,
    owner_phone text,
    owner_contact text,
    owner_telegram_id bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bookings (
    id bigserial PRIMARY KEY,
    garage_id bigint NOT NULL REFERENCES garages(id),
    client_telegram_id bigint NOT NULL,
    client_username text,
    start_time timestamptz NOT NULL,
    end_time timestamptz NOT NULL,
    total_price numeric(12,2) NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    admin_comment text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (end_time > start_time),
    CHECK (status IN ('pending', 'confirmed', 'cancelled', 'rejected'))
);
ALTER TABLE garages ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
