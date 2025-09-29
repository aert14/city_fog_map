#!/bin/bash

# Скрипт для проверки работоспособности Cloudflare туннеля
# Возвращает:
#   0 - туннель работает нормально
#   1 - туннель вернул 503 ошибку или недоступен

set -e  # Выход при любой ошибке

# --- КОНФИГУРАЦИЯ ---
SERVICE_NAME="cloudflared"
PING_ENDPOINT="/api/ping"
TIMEOUT=10  # таймаут в секундах
# --- КОНЕЦ КОНФИГУРАЦИИ ---

echo "🔍 Проверяем работоспособность Cloudflare туннеля..."

# 1. Извлекаем URL туннеля из логов Docker
echo "   - Извлекаем URL из логов..."
URL=$(docker compose logs $SERVICE_NAME 2>/dev/null | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' | head -n 1)

if [ -z "$URL" ]; then
    echo "❌ Не удалось получить URL туннеля из логов"
    exit 1
fi

echo "   - Найден URL: $URL"

# 2. Проверяем доступность туннеля
echo "   - Проверяем доступность..."
HTTP_CODE=$(curl --max-time $TIMEOUT --silent --write-out '%{http_code}' --output /dev/null "$URL$PING_ENDPOINT")

echo "   - HTTP код ответа: $HTTP_CODE"

# 3. Анализируем результат
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Туннель работает нормально"
    exit 0
elif [ "$HTTP_CODE" = "503" ]; then
    echo "❌ Туннель вернул 503 ошибку (сервис недоступен)"
    exit 1
else
    echo "⚠️  Туннель вернул неожиданный HTTP код: $HTTP_CODE"
    # Для других кодов (404, 500 и т.д.) считаем что проблема есть
    exit 1
fi
