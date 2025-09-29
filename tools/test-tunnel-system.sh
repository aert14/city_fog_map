#!/bin/bash

# Тестовый скрипт для проверки системы мониторинга туннеля
# Запускает полный цикл проверки без реального перезапуска

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$PROJECT_DIR/tools"

echo "🧪 Тестирование системы мониторинга Cloudflare туннеля"
echo "=================================================="

cd "$PROJECT_DIR"

# 1. Тест скрипта мониторинга
echo "1. Тестируем скрипт мониторинга..."
if "$TOOLS_DIR/monitor-tunnel.sh"; then
    echo "✅ Скрипт мониторинга работает корректно"
else
    echo "❌ Скрипт мониторинга вернул ошибку"
    exit 1
fi

# 2. Проверяем наличие всех компонентов
echo
echo "2. Проверяем наличие компонентов..."
components=(
    "$TOOLS_DIR/monitor-tunnel.sh"
    "$TOOLS_DIR/send-tunnel-url.py"
    "$TOOLS_DIR/check-and-restart-tunnel.sh"
    "$TOOLS_DIR/run-cf-tunnel.sh"
    "$PROJECT_DIR/.env"
)

for component in "${components[@]}"; do
    if [ -f "$component" ]; then
        echo "✅ $(basename "$component") найден"
    else
        echo "❌ $(basename "$component") не найден"
        exit 1
    fi
done

# 3. Проверяем права на выполнение
echo
echo "3. Проверяем права на выполнение..."
executable_scripts=(
    "$TOOLS_DIR/monitor-tunnel.sh"
    "$TOOLS_DIR/send-tunnel-url.py"
    "$TOOLS_DIR/check-and-restart-tunnel.sh"
)

for script in "${executable_scripts[@]}"; do
    if [ -x "$script" ]; then
        echo "✅ $(basename "$script") исполняемый"
    else
        echo "❌ $(basename "$script") не исполняемый"
        exit 1
    fi
done

# 4. Проверяем cron-задание
echo
echo "4. Проверяем cron-задание..."
if crontab -l | grep -q "check-and-restart-tunnel.sh"; then
    echo "✅ Cron-задание настроено"
else
    echo "❌ Cron-задание не найдено"
    exit 1
fi

# 5. Проверяем Python зависимости
echo
echo "5. Проверяем Python зависимости..."
if python3 -c "import requests, json, sys; print('✅ Python зависимости OK')"; then
    echo "✅ Python зависимости доступны"
else
    echo "❌ Проблема с Python зависимостями"
    exit 1
fi

echo
echo "🎉 Все тесты пройдены! Система готова к работе."
echo
echo "📋 Следующие шаги для полного функционирования:"
echo "1. Напишите сообщение боту: https://t.me/city_fog_map_bot"
echo "2. Или укажите chat_id пользователя aert14 в коде скрипта"
echo "3. Система будет автоматически проверять туннель каждые 5 минут"
