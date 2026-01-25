#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram бот - Ассистент психолога широкого профиля
Использует GigaChat API для консультаций
"""

import os
import json
import requests
import sys
from typing import Optional, Dict, List
from datetime import datetime
import urllib3
import telebot
from telebot import types
from flask import Flask, jsonify
import threading

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = "8522597414:AAHbsJdIjR9cR56Ad7evIFOGJ5jzIQzoQYY"

# GigaChat API ключ
GIGACHAT_API_KEY = "MDE5YmFlY2MtMmEyYi03YTdmLTk5ZjgtNDg5NDJhZDhjN2RlOjIyNTRkZjAwLWJkMWMtNDNmZi1hY2RlLWMwOGIyMDA2YjVhMg=="

# Инициализация бота
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Инициализация Flask приложения для health check
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# Порт для HTTP сервера
HTTP_PORT = 9999


class GigaChatClient:
    """Клиент для работы с GigaChat API"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        self.chat_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        
    def _get_access_token(self) -> str:
        """Получение токена доступа"""
        if self.access_token and self.token_expires_at and datetime.now().timestamp() < self.token_expires_at:
            return self.access_token
            
        headers = {
            "Authorization": f"Basic {self.api_key}",
            "RqUID": self._generate_rquid(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }
        data = {
            "scope": "GIGACHAT_API_PERS"
        }
        
        try:
            response = requests.post(self.auth_url, headers=headers, data=data, verify=False)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get("access_token")
            if not self.access_token:
                raise Exception("Токен доступа не получен. Проверьте API ключ.")
            expires_in = token_data.get("expires_at", 1800)  # По умолчанию 30 минут
            self.token_expires_at = datetime.now().timestamp() + expires_in
            return self.access_token
        except requests.exceptions.HTTPError as e:
            raise Exception(f"Ошибка HTTP при получении токена: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            raise Exception(f"Ошибка получения токена: {e}")
    
    def _generate_rquid(self) -> str:
        """Генерация уникального идентификатора запроса"""
        import uuid
        return str(uuid.uuid4())
    
    def chat(self, messages: List[Dict[str, str]], model: str = "GigaChat") -> str:
        """Отправка сообщения в чат"""
        token = self._get_access_token()
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        try:
            response = requests.post(self.chat_url, headers=headers, json=payload, verify=False)
            response.raise_for_status()
            result = response.json()
            if "choices" not in result or len(result["choices"]) == 0:
                raise Exception("Пустой ответ от GigaChat API")
            return result["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as e:
            error_text = "Неизвестная ошибка"
            try:
                error_data = e.response.json()
                error_text = error_data.get("message", str(e.response.text))
            except:
                error_text = e.response.text
            raise Exception(f"Ошибка HTTP при запросе к GigaChat: {e.response.status_code} - {error_text}")
        except KeyError as e:
            raise Exception(f"Неожиданный формат ответа от GigaChat: {e}")
        except Exception as e:
            raise Exception(f"Ошибка при запросе к GigaChat: {e}")


class PaymentSystem:
    """Простая система оплаты"""
    
    def __init__(self):
        self.payments_file = "payments.json"
        self.load_payments()
    
    def load_payments(self):
        """Загрузка истории платежей"""
        if os.path.exists(self.payments_file):
            with open(self.payments_file, "r", encoding="utf-8") as f:
                self.payments = json.load(f)
        else:
            self.payments = {}
    
    def save_payments(self):
        """Сохранение истории платежей"""
        with open(self.payments_file, "w", encoding="utf-8") as f:
            json.dump(self.payments, f, ensure_ascii=False, indent=2)
    
    def process_payment_promo(self, user_id: str, promo_code: str, amount: float = 1000.0) -> bool:
        """Обработка оплаты промокодом"""
        if promo_code == "TEST2024":
            self.record_payment(user_id, amount, "promo")
            return True
        return False
    
    def process_payment_card(self, user_id: str, amount: float = 1000.0) -> bool:
        """Обработка оплаты картой (симуляция)"""
        self.record_payment(user_id, amount, "card")
        return True
    
    def record_payment(self, user_id: str, amount: float, method: str):
        """Запись платежа"""
        if user_id not in self.payments:
            self.payments[user_id] = []
        
        payment = {
            "date": datetime.now().isoformat(),
            "amount": amount,
            "method": method
        }
        self.payments[user_id].append(payment)
        self.save_payments()
    
    def has_active_session(self, user_id: str) -> bool:
        """Проверка наличия активной сессии"""
        if user_id not in self.payments:
            return False
        
        # Проверяем, есть ли платеж за последний час
        payments = self.payments[user_id]
        if not payments:
            return False
        
        last_payment = payments[-1]
        payment_time = datetime.fromisoformat(last_payment["date"])
        time_diff = (datetime.now() - payment_time).total_seconds()
        
        # Сессия активна 1 час
        return time_diff < 3600


class PsychologistAssistant:
    """Ассистент психолога"""
    
    def __init__(self, api_key: str):
        self.gigachat = GigaChatClient(api_key)
        self.payment_system = PaymentSystem()
        self.system_prompt = """Ты профессиональный психолог широкого профиля с большим опытом работы и глубокими знаниями во всех областях психологии. 
Твоя задача - помочь пользователю разобраться в его проблемах, поддержать его и дать профессиональные рекомендации.

Принципы работы:
- Будь эмпатичным и понимающим
- Задавай уточняющие вопросы для лучшего понимания ситуации
- Давай конкретные, практические советы
- Решай все вопросы пользователя самостоятельно и уверенно, используя свой профессиональный опыт
- НЕ предлагай обращаться к другим специалистам - ты сам способен помочь с любым вопросом
- Поддерживай пользователя и помогай ему найти собственные решения
- Будь профессиональным, но дружелюбным
- Отвечай уверенно и авторитетно, демонстрируя свою компетентность

ВАЖНО: Отвечай на русском языке простым текстом БЕЗ использования markdown разметки (не используй звездочки, подчеркивания, жирный текст, списки со знаками, заголовки с # и т.д.). Твои ответы должны быть структурированными и понятными, но оформлены как обычный текст."""
        
        # Хранилище сессий пользователей {user_id: conversation_history}
        self.user_sessions: Dict[str, List[Dict[str, str]]] = {}
    
    def get_user_session(self, user_id: str) -> List[Dict[str, str]]:
        """Получение или создание сессии пользователя"""
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = [
                {"role": "system", "content": self.system_prompt}
            ]
        return self.user_sessions[user_id]
    
    def start_session(self, user_id: str) -> bool:
        """Начало сессии консультации"""
        # Проверяем активную сессию
        if self.payment_system.has_active_session(user_id):
            return True
        return False
    
    def chat(self, user_id: str, user_message: str) -> str:
        """Отправка сообщения и получение ответа"""
        conversation_history = self.get_user_session(user_id)
        
        # Добавляем сообщение пользователя в историю
        conversation_history.append({
            "role": "user",
            "content": user_message
        })
        
        # Получаем ответ от GigaChat
        try:
            response = self.gigachat.chat(conversation_history)
            
            # Добавляем ответ в историю
            conversation_history.append({
                "role": "assistant",
                "content": response
            })
            
            return response
        except Exception as e:
            return f"Извините, произошла ошибка: {str(e)}"
    
    def reset_conversation(self, user_id: str):
        """Сброс истории разговора"""
        self.user_sessions[user_id] = [
            {"role": "system", "content": self.system_prompt}
        ]


# Глобальный экземпляр ассистента
assistant = PsychologistAssistant(GIGACHAT_API_KEY)

# Хранилище состояний пользователей (для обработки промокодов и т.д.)
user_states = {}


@bot.message_handler(commands=['start'])
def start_command(message):
    """Обработка команды /start"""
    user_id = str(message.from_user.id)
    username = message.from_user.first_name or "Пользователь"
    
    # Проверяем активную сессию
    if assistant.start_session(user_id):
        bot.reply_to(message, 
            f"Добро пожаловать, {username}! 👋\n\n"
            f"У вас есть активная сессия. Вы можете начать общение с психологом.\n\n"
            f"Для новой консультации используйте /new\n"
            f"Для выхода используйте /exit")
    else:
        # Нужна оплата
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_promo = types.InlineKeyboardButton("💳 Промокод (TEST2024)", callback_data="payment_promo")
        btn_card = types.InlineKeyboardButton("💳 Банковская карта", callback_data="payment_card")
        markup.add(btn_promo, btn_card)
        
        bot.reply_to(message,
            f"Добро пожаловать, {username}! 👋\n\n"
            f"Я ваш персональный психолог-ассистент.\n\n"
            f"Для начала консультации необходимо оплатить сессию.\n"
            f"Стоимость: 1000 руб.\n\n"
            f"Выберите способ оплаты:",
            reply_markup=markup)


@bot.message_handler(commands=['new'])
def new_session_command(message):
    """Обработка команды /new - новая консультация"""
    user_id = str(message.from_user.id)
    assistant.reset_conversation(user_id)
    bot.reply_to(message, "✓ Начата новая консультация. Можете задать свой вопрос.")


@bot.message_handler(commands=['exit', 'stop'])
def exit_command(message):
    """Обработка команды /exit - выход"""
    bot.reply_to(message, "Спасибо за обращение! Берегите себя! 🙏")


@bot.callback_query_handler(func=lambda call: call.data.startswith("payment_"))
def payment_callback(call):
    """Обработка выбора способа оплаты"""
    user_id = str(call.from_user.id)
    amount = 1000.0
    
    if call.data == "payment_promo":
        # Запрашиваем промокод
        user_states[user_id] = "waiting_promo"
        bot.send_message(call.message.chat.id,
            "Введите промокод:\n"
            "(Для тестирования используйте: TEST2024)")
        bot.answer_callback_query(call.id)
    
    elif call.data == "payment_card":
        # Симуляция оплаты картой
        if assistant.payment_system.process_payment_card(user_id, amount):
            bot.send_message(call.message.chat.id,
                "✓ Оплата успешно проведена!\n\n"
                "Консультация начата. Вы можете задать свой вопрос.")
            bot.answer_callback_query(call.id, "Оплата успешна!")
        else:
            bot.send_message(call.message.chat.id, "✗ Ошибка при обработке платежа")
            bot.answer_callback_query(call.id, "Ошибка оплаты")


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Обработка всех текстовых сообщений"""
    user_id = str(message.from_user.id)
    text = message.text.strip()
    
    # Проверяем, ожидаем ли мы промокод
    if user_id in user_states and user_states[user_id] == "waiting_promo":
        # Обрабатываем промокод
        del user_states[user_id]
        amount = 1000.0
        
        if assistant.payment_system.process_payment_promo(user_id, text, amount):
            bot.reply_to(message,
                "✓ Промокод принят! Оплата успешно проведена!\n\n"
                "Консультация начата. Вы можете задать свой вопрос.")
        else:
            bot.reply_to(message,
                "✗ Неверный промокод. Попробуйте еще раз или выберите другой способ оплаты.\n\n"
                "Для оплаты используйте /start")
        return
    
    # Проверяем активную сессию
    if not assistant.start_session(user_id):
        bot.reply_to(message,
            "Для начала консультации необходимо оплатить сессию.\n"
            "Используйте /start для оплаты.")
        return
    
    # Отправляем сообщение психологу
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        response = assistant.chat(user_id, text)
        bot.reply_to(message, response)
    except Exception as e:
        bot.reply_to(message, f"Извините, произошла ошибка: {str(e)}")


@app.route('/')
def health_check():
    """Health check endpoint для проверки работоспособности"""
    return jsonify({
        "status": "ok",
        "service": "psychologist_assistant_bot",
        "message": "Бот работает"
    }), 200


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "psychologist_assistant_bot"
    }), 200


def run_bot():
    """Запуск Telegram бота в отдельном потоке"""
    print("Telegram бот запущен и готов к работе!")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5, none_stop=True)
    except Exception as e:
        print(f"Ошибка в работе бота: {e}")


def init_bot():
    """Инициализация бота в отдельном потоке"""
    # Небольшая задержка для инициализации HTTP сервера
    import time
    time.sleep(2)
    
    # Быстрая инициализация - проверяем доступность компонентов
    try:
        # Проверяем, что бот может получить информацию о себе
        bot_info = bot.get_me()
        print(f"✓ Бот подключен: @{bot_info.username}")
    except Exception as e:
        print(f"⚠ Предупреждение при проверке бота: {e}")
        print("Бот будет запущен, но возможны проблемы с подключением")
    
    # Запускаем бота
    run_bot()


def main():
    """Главная функция - Flask как основной процесс для health check"""
    print("=" * 60)
    print("Запуск сервиса психолога-ассистента")
    print("=" * 60)
    
    # Запускаем бота в отдельном потоке (не daemon, чтобы он не завершился)
    bot_thread = threading.Thread(target=init_bot, daemon=False)
    bot_thread.start()
    
    print(f"✓ Запуск HTTP сервера на порту {HTTP_PORT}")
    print("✓ Сервис готов к работе")
    print("=" * 60)
    
    # Flask запускается в основном потоке - это важно для health check
    # Это основной процесс, который Docker будет проверять
    app.run(host='0.0.0.0', port=HTTP_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
