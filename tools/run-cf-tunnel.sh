#!/bin/bash

# Выход при любой ошибке для безопасности
set -e

# --- КОНФИГУРАЦИЯ ---
MAX_HOSTNAME_LENGTH=64
SERVICE_NAME="cloudflared"
# --- КОНЕЦ КОНФИГУРАЦИИ ---

echo "🔄 Запускаем процесс создания валидного Cloudflare туннеля..."

while true; do
  echo "-----------------------------------------------------"
  echo "1. Принудительно останавливаем и удаляем старый туннель (если существует)..."
  docker-compose stop $SERVICE_NAME > /dev/null 2>&1 || true
  docker-compose rm -f $SERVICE_NAME > /dev/null 2>&1 || true

  echo "2. Запрашиваем новый туннель (пересоздаем контейнер)..."
  # `up -d` с флагом --force-recreate гарантирует создание нового инстанса
  docker-compose up -d --force-recreate $SERVICE_NAME

  echo "3. Ожидаем 7 секунд, пока туннель установит соединение..."
  sleep 7

  echo "4. Извлекаем URL из логов..."
  URL=$(docker-compose logs $SERVICE_NAME | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' | head -n 1)

  if [ -z "$URL" ]; then
    echo "❌ Не удалось получить URL из логов. Повторяем попытку..."
    continue
  fi

  echo "   - Полученный URL: $URL"

  URL_LENGTH=${#URL}

  echo "5. Проверяем длину всей ссылки: '$URL' ($URL_LENGTH символов)."

  if [ $URL_LENGTH -le $MAX_HOSTNAME_LENGTH ]; then
    echo "✅ Успех! Длина ($URL_LENGTH) не превышает лимит в $MAX_HOSTNAME_LENGTH символов."
    echo "-----------------------------------------------------"
    echo "🎉 Ваш туннель готов: $URL"
    echo "-----------------------------------------------------"
    break
  else
    echo "❌ Невалидный URL. Длина ссылки ($URL_LENGTH) превышает лимит. Перезапрашиваем..."
  fi
done
