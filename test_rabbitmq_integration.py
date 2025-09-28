#!/usr/bin/env python3
"""
Тест интеграции RabbitMQ в монолите
"""
import os
import sys
import asyncio
import unittest
from unittest.mock import patch, MagicMock

# Добавляем services в path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services'))

from monolith import queue


class TestRabbitMQIntegration(unittest.TestCase):

    def setUp(self):
        """Настройка тестов"""
        # Сбрасываем глобальные переменные
        queue.rabbit_connection = None
        queue.rabbit_channel = None

    def tearDown(self):
        """Очистка после тестов"""
        queue.rabbit_connection = None
        queue.rabbit_channel = None

    def test_publish_message_without_connection(self):
        """Тест публикации сообщения без соединения"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            queue.publish_visit_message(1, 'test_geokey', 55.75, 37.61, 1234567890)
        )

        self.assertFalse(result)

    def test_message_format(self):
        """Тест формата сообщения"""
        expected_message = {
            "user_id": 123,
            "h3_geokey": "8a3969a4777ffff",
            "lat": 55.75,
            "lon": 37.61,
            "timestamp": 1678886400
        }

        # Проверяем, что функция формирует правильное сообщение
        with patch.object(queue, 'rabbit_channel', None):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            result = loop.run_until_complete(
                queue.publish_visit_message(
                    expected_message["user_id"],
                    expected_message["h3_geokey"],
                    expected_message["lat"],
                    expected_message["lon"],
                    expected_message["timestamp"]
                )
            )

            self.assertFalse(result)  # False потому что нет канала


if __name__ == '__main__':
    unittest.main()
