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
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

4) Туннель в интернет (пример с ngrok)

```bash
ngrok http 8000
```

Скопируй выданный HTTPS `https://<random>.ngrok-free.app`.

5) Настрой мини‑апп в Telegram

- В @BotFather: `/setdomain` → `https://<random>.ngrok-free.app`
- В кнопке `web_app` укажи URL `https://<random>.ngrok-free.app/webapp/`

Проверка Telegram initData

- Клиент отправляет сырой `window.Telegram.WebApp.initData` в заголовке `X-Telegram-Init`.
- Сервер проверяет подпись (HMAC SHA256 c ключом `HMAC("WebAppData", BOT_TOKEN)`), валидирует свежесть `auth_date` и извлекает пользователя.
- Без валидного заголовка сервер вернёт 401.

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

Отладка

- 401 → страница открыта не из Telegram или не дошёл заголовок `X-Telegram-Init`
- hash mismatch → проверь сборку `data_check_string`
- stale auth_date → протухло время
