-- Выполнить в Supabase SQL Editor перед включением webhook.
-- События хранятся отдельно для каждого бота; полный текст сообщений не сохраняется.
CREATE TABLE IF NOT EXISTS telegram_updates (
    bot_id bigint NOT NULL,
    update_id bigint NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, update_id)
);
ALTER TABLE telegram_updates ENABLE ROW LEVEL SECURITY;
-- Публичных политик нет: доступ только серверным service_role ключом.
