#!/bin/bash

# Главный скрипт для проверки и перезапуска Cloudflare туннеля
# Запускается по cron каждые 5 минут

set -e  # Выход при любой ошибке

# --- КОНФИГУРАЦИЯ ---
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$PROJECT_DIR/tools"
USERNAME="aert14"  # Пользователь для уведомлений
LOG_FILE="$PROJECT_DIR/tunnel-monitor.log"
# --- КОНЕЦ КОНФИГУРАЦИИ ---

# Функция для логирования
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"
}

# Переходим в директорию проекта
cd "$PROJECT_DIR"

log "🚀 Запуск проверки туннеля..."

# 1. Проверяем работоспособность туннеля
log "   Проверяем статус туннеля..."
if "$TOOLS_DIR/monitor-tunnel.sh"; then
    log "✅ Туннель работает нормально"
    exit 0
else
    log "❌ Туннель не работает, начинаем перезапуск..."
fi

# 2. Перезапускаем туннель
log "🔄 Перезапускаем туннель..."
if "$TOOLS_DIR/run-cf-tunnel.sh"; then
    log "✅ Туннель успешно перезапущен"
else
    log "❌ Ошибка при перезапуске туннеля"
    exit 1
fi

# 3. Получаем новый URL туннеля
log "   Получаем новый URL..."
NEW_URL=$(docker compose logs cloudflared 2>/dev/null | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' | tail -n 1)

if [ -z "$NEW_URL" ]; then
    log "❌ Не удалось получить новый URL туннеля"
    exit 1
fi

log "   Новый URL: $NEW_URL"

# 4. Отправляем уведомление
log "📨 Отправляем уведомление пользователю @$USERNAME..."
if python3 "$TOOLS_DIR/send-tunnel-url.py" "$USERNAME" "$NEW_URL"; then
    log "✅ Уведомление отправлено успешно"
else
    log "❌ Ошибка при отправке уведомления"
    exit 1
fi

log "🎉 Процесс завершен успешно"
