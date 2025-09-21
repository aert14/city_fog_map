City Fog Map — FastAPI + Telegram WebApp (XS)

Быстрый старт

1) Установи зависимости

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

2) Экспортируй токен бота

```bash
export TELEGRAM_BOT_TOKEN=123456:ABC...
```

3) Запусти бэкенд

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info
```

Или через Makefile (прозрачный вывод):
```bash
export TELEGRAM_BOT_TOKEN=<ваш_бот_токен>
make -C /home/aert141414/city_fog_map backend
```

4) Туннель в интернет (пример с ngrok)

```bash
ngrok http 8000
```

Скопируй выданный HTTPS `https://<random>.ngrok-free.app`.

5) Настрой мини‑апп в Telegram

- В @BotFather: `/setdomain` → `https://<random>.ngrok-free.app`
- В кнопке `web_app` укажи URL `https://<random>.ngrok-free.app/webapp/`

Аутентификация

- Заголовок `X-Telegram-Init` (основной путь): клиент отправляет сырой `window.Telegram.WebApp.initData` в КАЖДОМ запросе к API. Сервер проверяет подпись (HMAC SHA256 c ключом `HMAC("WebAppData", BOT_TOKEN)`), валидирует `auth_date` и извлекает пользователя.
- Сессия (fallback): открой `/webapp/debug-auth.html` внутри Telegram Mini App — страница делает `POST /api/auth` с `initData`, сервер ставит cookie‑сессию. Обычные эндпоинты сначала пытаются аутентифицировать по сессии, и только если её нет — ждут заголовок.
- Требование фронтенда: подключите скрипт Telegram WebApp на странице — `https://telegram.org/js/telegram-web-app.js`.
- Если нет ни сессии, ни заголовка — будет `401`.

Мини‑API

- POST `/api/v1/visit` — вход `{ lat, lon }` → выход `{ added, circle, stats }`
- GET `/api/v1/circles?bbox=minLon,minLat,maxLon,maxLat` → `{ circles: [...] }`

Хранилище

- SQLite `db.sqlite3` рядом с проектом
- Таблицы: `users` и `circles` (PRIMARY KEY по `(user_id, geokey)`)

Фронтенд

- Открывается по `/webapp/`
- MapLibre GL + геолокация, кнопка «Открыть 100 м вокруг меня»
- Отрисовка кругов 100 м как GeoJSON-полигонов
- На странице должен быть подключён `https://telegram.org/js/telegram-web-app.js`.

Отладка

- 401 → страница открыта не из Telegram или не дошёл заголовок `X-Telegram-Init`
- hash mismatch → проверь сборку `data_check_string`
- stale auth_date → протухло время
- Логи: в корне проекта пишется `/home/aert141414/city_fog_map/server.log`. Для каждого запроса логируются метод, путь, клиентский IP и флаги `tg_init_present`, `tg_init_len`.

LocalTunnel (loca.lt) вместо ngrok (Linux)

1) Установите Node.js и npx (любой способ)
```bash
sudo apt-get update && sudo apt-get install -y nodejs npm
# или через nvm: https://github.com/nvm-sh/nvm
```

2) Запустите backend на :8000
```bash
source /home/aert141414/city_fog_map/.venv/bin/activate
export TELEGRAM_BOT_TOKEN=<ВАШ_БОТ_ТОКЕН>
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

3) Поднимите туннель
```bash
npx --yes localtunnel --port 8000
# или фоново с логами:
nohup npx --yes localtunnel --port 8000 > /tmp/cfm_lt.log 2>&1 & disown
grep -Eo "https://[a-z0-9-]+\.loca\.lt" /tmp/cfm_lt.log | head -n 1
```

4) Узнайте пароль туннеля (если запросит)
```bash
curl -s https://loca.lt/mytunnelpassword
```

5) Настройте бота
- `/setdomain` → `https://<subdomain>.loca.lt`
- URL кнопки web_app → `https://<subdomain>.loca.lt/webapp/`

Makefile (автоматизация)
```bash
cd /home/aert141414/city_fog_map
make venv
export TELEGRAM_BOT_TOKEN=<ВАШ_БОТ_ТОКЕН>
make start      # backend + localtunnel
make url        # показать текущий URL
make password   # пароль туннеля
make logs       # логи
make stop       # остановить
```
