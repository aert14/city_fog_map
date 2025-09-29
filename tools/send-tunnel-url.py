#!/usr/bin/env python3
"""
Скрипт для отправки уведомлений о новом URL туннеля в Telegram
"""

import os
import sys
import requests
import json
from pathlib import Path
from typing import Optional

class TelegramNotifier:
    def __init__(self):
        self.bot_token = self._get_bot_token()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.chat_id = None

    def _get_bot_token(self) -> str:
        """Получить токен бота из .env файла"""
        env_path = Path(__file__).parent.parent / ".env"

        if not env_path.exists():
            print(f"❌ Файл .env не найден: {env_path}")
            sys.exit(1)

        with open(env_path, 'r') as f:
            for line in f:
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    token = line.split('=', 1)[1].strip()
                    if token:
                        return token

        print("❌ TELEGRAM_BOT_TOKEN не найден в .env файле")
        sys.exit(1)

    def _find_chat_id_by_username(self, username: str) -> Optional[int]:
        """Найти chat_id по username через getUpdates (требует чтобы пользователь написал боту)"""
        try:
            response = requests.get(f"{self.base_url}/getUpdates", timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get('ok') and data.get('result'):
                for update in data['result']:
                    message = update.get('message', {})
                    chat = message.get('chat', {})
                    from_user = message.get('from', {})

                    # Проверяем username в чате или отправителе
                    if chat.get('username') == username or from_user.get('username') == username:
                        return chat.get('id')

        except Exception as e:
            print(f"⚠️  Ошибка при поиске chat_id: {e}")

        return None

    def _get_chat_id(self, username: str) -> int:
        """Получить chat_id пользователя"""
        # Сначала пытаемся найти через getUpdates
        chat_id = self._find_chat_id_by_username(username)

        if chat_id:
            print(f"✅ Найден chat_id для @{username}: {chat_id}")
            self.chat_id = chat_id
            return chat_id

        # Если не нашли, просим ввести вручную
        print(f"❌ Не удалось найти chat_id для пользователя @{username}")
        print("Возможные причины:")
        print("1. Пользователь никогда не писал боту")
        print("2. Бот не получал обновлений с момента последнего сообщения")
        print()
        print("Чтобы получить chat_id:")
        print(f"1. Напишите сообщение боту: https://t.me/{self._get_bot_username()}")
        print("2. Или введите chat_id пользователя вручную:")

        try:
            manual_chat_id = int(input("Введите chat_id: ").strip())
            self.chat_id = manual_chat_id
            return manual_chat_id
        except ValueError:
            print("❌ Неверный формат chat_id")
            sys.exit(1)

    def _get_bot_username(self) -> str:
        """Получить username бота"""
        try:
            response = requests.get(f"{self.base_url}/getMe", timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get('ok'):
                return data['result'].get('username', 'unknown')
        except Exception as e:
            print(f"⚠️  Ошибка при получении информации о боте: {e}")

        return "unknown"

    def send_message(self, username: str, tunnel_url: str) -> bool:
        """Отправить сообщение пользователю"""
        if not self.chat_id:
            self.chat_id = self._get_chat_id(username)

        message = f"🔄 Cloudflare туннель был перезапущен!\n\n🌐 Новый адрес: {tunnel_url}\n\nВремя: {self._get_current_time()}"

        try:
            data = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }

            response = requests.post(f"{self.base_url}/sendMessage", json=data, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get('ok'):
                print(f"✅ Сообщение отправлено пользователю @{username}")
                return True
            else:
                print(f"❌ Ошибка отправки: {result.get('description')}")
                return False

        except Exception as e:
            print(f"❌ Ошибка при отправке сообщения: {e}")
            return False

    def _get_current_time(self) -> str:
        """Получить текущее время в читаемом формате"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

def main():
    if len(sys.argv) != 3:
        print("Использование: python send-tunnel-url.py <username> <tunnel_url>")
        print("Пример: python send-tunnel-url.py aert14 https://abc123.trycloudflare.com")
        sys.exit(1)

    username = sys.argv[1]
    tunnel_url = sys.argv[2]

    print(f"📨 Отправляем уведомление пользователю @{username}")
    print(f"🌐 URL туннеля: {tunnel_url}")

    notifier = TelegramNotifier()
    success = notifier.send_message(username, tunnel_url)

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
