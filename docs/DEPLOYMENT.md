# Сервер GarageHub

В публичных примерах адрес сервера заменён на `SERVER_IP`, домены — на
`garagehub.example.com` и `app.garagehub.example.com`. Реальные значения остаются
в настройках установленного сервера; перед установкой на другую машину заменить примеры.

Состояние на 8 ноября 2026 года: инфраструктура установлена. **MVP ещё не запущен:**
нет настоящего токена Telegram и ID администратора; исходящие TCP-соединения сервера
с `api.telegram.org:443` завершаются таймаутом. Клиентский VPN не исправляет соединение
сервера с Telegram. API возвращает 503 до запуска приложения.

Приложение и worker теперь поддерживают `TELEGRAM_PROXY_URL` (SOCKS5 или HTTP CONNECT).
Фотографии используют ту же сессию бота. TLS до `api.telegram.org` сохраняется;
доверенные корневые сертификаты посредников не устанавливаются. База и публичный
сайт через прокси не направляются. Автоматического переключения на прямое соединение нет.

Проверен бесплатный официальный Cloudflare WARP: регистрация не проходит, его
`api.cloudflareclient.com` недоступен с сервера (TLS timeout). Пробный клиент удалён,
основной маршрут не менялся. Выбран платный запасной вариант: **PROXY6, индивидуальный
IPv4, Нидерланды, 1 IP, 1 месяц — 120 ₽** в форме заказа 08.11.2026.
[Форма покупки](https://proxy6.net/ru/), [прайс](https://proxy6.net/ru/price).
Заказ не оформлен: нужен аккаунт и оплата владельца. Работоспособность конкретного
выданного адреса ещё не проверена; после покупки выполнить проверку ниже до включения бота.

## Что работает

- Сервер Selectel `SERVER_IP`, Ubuntu 24.04, 2 vCPU, 4 ГБ RAM, диск 50 ГБ.
  Диск и ext4 расширены с 5 до 50 ГБ без переустановки.
- Код в `/opt/garagehub`, отдельное окружение Python 3.12, зависимости установлены.
- PostgreSQL 16.15 на `127.0.0.1:5432`, база `garagehub`; применены SQL 000–003.
- PostgREST 16.5 на `127.0.0.1:3000`. Доступ снаружи закрыт. Серверный JWT с ролью
  `service_role`; отдельный authenticator с SCRAM-паролем и NOINHERIT.
- Сайт: <https://garagehub.example.com>.
- Оболочка Mini App: <https://app.garagehub.example.com>.
  Каталог и авторизация пока недоступны: это публикация файлов, не готовый MVP.
- Let's Encrypt для обоих технических имён; автопродление с перезагрузкой nginx.
  Это бесплатные адреса стороннего DNS-сервиса; позже заменить собственным доменом.
- Firewall разрешает входящие TCP 22, 80, 443. База и REST слушают только loopback.
- Ежедневный `pg_dump -Fc` в `/var/backups/garagehub`, хранение около семи суток.
  Выполнено реальное восстановление в отдельную временную БД и проверен `gh_restore_gate`.
  Эти копии находятся на том же сервере: перенос копий в отдельное хранилище ещё нужен.

## Что требуется для включения бота

1. Записать настоящий `TELEGRAM_BOT_TOKEN` и свой `ADMIN_ID` в локальный файл
   `.env.telegram` (он исключён из Git). Токен выдаёт @BotFather отдельно от кода.
   Узнать свой числовой ID можно через Telegram-бота для определения ID.
2. Для выбранного варианта купить обычный **индивидуальный IPv4** в Нидерландах
   с SOCKS5 и записать в `.env.telegram` выданные реквизиты в формате:

   ```dotenv
   TELEGRAM_PROXY_URL=socks5://LOGIN:PASSWORD@HOST:PORT
   ```

   Логин и пароль с символами `@`, `:`, `#`, `%` кодировать как URL-компоненты.
   Нужен обычный SOCKS5/HTTP CONNECT, не MTProto. Один адрес, режим Shared не выбран.
   Токен бота в кабинете прокси не указывается. Привязать доступ к `SERVER_IP`,
   если провайдер позволяет ограничение по IP.
3. Перенести только эти значения в серверный `/opt/garagehub/.env`, сохранив
   сгенерированные ключи БД и webhook. Не копировать `.env.example` поверх этого файла.
   Сейчас токен пуст, `ADMIN_ID=0` — права ещё никому не выданы.
   `ADMIN_TG` и `ADMIN_PHONE` при необходимости указать явно: встроенных личных контактов нет.
4. Проверить сеть и токен (команда не отправляет сообщений и не печатает секреты):

   ```sh
   cd /opt/garagehub
   venv/bin/python -m tools.check_telegram
   ```

   После успешного `getMe` проверить HTTPS и `getWebhookInfo`, затем включить ровно один экземпляр:

   ```sh
   systemctl enable --now garagehub-app garagehub-worker
   ```

5. Проверить `/api/ready`, открытие Mini App из реального Telegram, создание объекта,
   бронь и её подтверждение/отмену. Сейчас база пустая: демонстрационные брони и
   устройства на сервер не переносились. `LOCK_PROVIDER=disabled`, `MINIAPP_DEV=0`.

## Подключение и обслуживание

С Mac владельца:

```sh
ssh -i ~/.ssh/garagehub_ed25519 root@SERVER_IP
```

На сервере:

```sh
systemctl status garagehub-postgrest garagehub-app garagehub-worker
journalctl -u garagehub-app -u garagehub-worker -n 100 --no-pager
systemctl list-timers garagehub-backup.timer certbot.timer
systemctl start garagehub-backup.service
nginx -t
df -h /
```

Секреты находятся в `/etc/garagehub/` и `/opt/garagehub/.env`; они не входят в Git
или релизный архив. Приложение и worker работают от пользователя `garagehub`,
а исходники принадлежат root. Записываемые каталоги — `cache/` и `logs/`.
После восстановления backup держать worker остановленным и выполнить `gh_restore_gate`
по [инструкции восстановления](LOCAL_CORE.md), прежде чем разрешать отправку команд.

Выполнены проверки конфигурации (18 сочетаний и защита вывода диагностической команды),
23 теста webhook и запуска,
два сетевых теста HTTP CONNECT (API + фото; отказ без прямого обхода), `pip check`,
проверка DB readiness, отказ анонимному RPC, восстановление backup и внешние HTTPS-запросы.
Живой Telegram, работа приложения/worker после перезагрузки и физические замки ещё не проверены.

## Конфигурации, сохранённые в репозитории

`deploy/garagehub-app.service`, `garagehub-worker.service`, `garagehub-postgrest.service`,
`garagehub-backup.service` и `garagehub-backup.timer` соответствуют установленным unit.
`deploy/nginx.selectel.conf.example` — конфигурация двух доменов с публичными значениями-примерами;
она содержит пути сертификатов, но не сертификаты или закрытые ключи.
`deploy/postgresql.conf.example` — применённые настройки PostgreSQL для 4 ГБ RAM.
`deploy/garagehub-backup.sh` устанавливается как `/usr/local/sbin/garagehub-backup`:

```sh
install -m 0750 deploy/garagehub-backup.sh /usr/local/sbin/garagehub-backup
```

`deploy/postgrest.env.example` — только образец; настоящий `/etc/garagehub/postgrest.env`
содержит случайные пароль БД и JWT-секрет и не публикуется. Не подменять им рабочий файл.
Unit PostgREST рассчитан на PostgreSQL 16 Ubuntu 24.04; при другой версии изменить имя
`postgresql@16-main.service`. `deploy/nginx.conf.example` остаётся общим шаблоном для
другого домена. Это снимок применённых настроек, а не автоматический установщик новой машины.
