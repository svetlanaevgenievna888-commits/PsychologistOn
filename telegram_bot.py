#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram бот - Ассистент психолога широкого профиля
Использует GigaChat API для консультаций
Интегрирован с Robokassa для приема платежей

НАСТРОЙКА ROBOKASSA:
1. Зарегистрируйтесь в Robokassa и создайте магазин: https://partner.robokassa.ru/
2. В разделе "Технические настройки" магазина укажите:
   - ResultURL: http://yourdomain.com:9999/robokassa/result (или ваш домен)
   - SuccessURL: http://yourdomain.com:9999/robokassa/success
   - FailURL: http://yourdomain.com:9999/robokassa/fail
   - Установите Пароль #1 и Пароль #2
3. Установите переменные окружения:
   - ROBOKASSA_MERCHANT_LOGIN - ID вашего магазина
   - ROBOKASSA_PASSWORD_1 - Пароль #1 для генерации подписи
   - ROBOKASSA_PASSWORD_2 - Пароль #2 для проверки уведомлений
   - ROBOKASSA_TEST_MODE - "1" для тестового режима, "0" для боевого
   - ROBOKASSA_RESULT_URL, ROBOKASSA_SUCCESS_URL, ROBOKASSA_FAIL_URL - URL для обработки (опционально)
"""

import os
import json
import requests
import sys
import re
import time
import hashlib
import uuid
from typing import Optional, Dict, List
from datetime import datetime
from urllib.parse import urlencode
import urllib3
import telebot
from telebot import types
from flask import Flask, jsonify, request
import threading

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = "8522597414:AAHbsJdIjR9cR56Ad7evIFOGJ5jzIQzoQYY"

# GigaChat API ключ
GIGACHAT_API_KEY = "MDE5YmFlY2MtMmEyYi03YTdmLTk5ZjgtNDg5NDJhZDhjN2RlOjIyNTRkZjAwLWJkMWMtNDNmZi1hY2RlLWMwOGIyMDA2YjVhMg=="

# Настройки Robokassa
# По умолчанию используется ID магазина Psychologistonline, который вы активировали.
# При желании можно переопределить через переменную окружения ROBOKASSA_MERCHANT_LOGIN.
ROBOKASSA_MERCHANT_LOGIN = os.getenv("ROBOKASSA_MERCHANT_LOGIN", "Psychologistonline")  # ID магазина

# Параметры платежей Robokassa
# Алгоритм расчёта хеша: MD5 (реализован через hashlib.md5)
#
# Боевой режим (как вы указали):
#   MerchantLogin: Psychologistonline
#   MechantPass1:  Pg0Xlm85D6du6JoxuqeT
#   MechantPass2:  HjbSbzzus179QGDM2L8t
#
# Бот должен работать в боевом режиме, поэтому:
#   ROBOKASSA_TEST_MODE = "0"  -> боевой режим (IsTest не передаётся)
ROBOKASSA_TEST_MODE = os.getenv("ROBOKASSA_TEST_MODE", "0")  # по умолчанию боевой режим

# Всегда используем боевые пароли (тестовый режим не используется)
ROBOKASSA_PASSWORD_1 = os.getenv("ROBOKASSA_PASSWORD_1", "Pg0Xlm85D6du6JoxuqeT")
ROBOKASSA_PASSWORD_2 = os.getenv("ROBOKASSA_PASSWORD_2", "HjbSbzzus179QGDM2L8t")

ROBOKASSA_BASE_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"  # Базовый URL для оплаты

# URL для обработки уведомлений от Robokassa (нужно будет настроить в личном кабинете)
# Например: https://yourdomain.com/robokassa/result
ROBOKASSA_RESULT_URL = os.getenv("ROBOKASSA_RESULT_URL", "http://localhost:9999/robokassa/result")
ROBOKASSA_SUCCESS_URL = os.getenv("ROBOKASSA_SUCCESS_URL", "http://localhost:9999/robokassa/success")
ROBOKASSA_FAIL_URL = os.getenv("ROBOKASSA_FAIL_URL", "http://localhost:9999/robokassa/fail")

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


class RobokassaPayment:
    """Класс для работы с платежами через Robokassa"""
    
    def __init__(self, merchant_login: str, password_1: str, password_2: str, test_mode: str = "1"):
        self.merchant_login = merchant_login
        self.password_1 = password_1
        self.password_2 = password_2
        self.test_mode = test_mode
        self.base_url = ROBOKASSA_BASE_URL
    
    def _normalize_out_sum(self, out_sum) -> str:
        """
        Нормализация суммы к строке в формате, ожидаемом Robokassa.
        Всегда используем два знака после запятой, чтобы формат был стабильным.
        """
        # Если пришла строка - просто возвращаем как есть
        if isinstance(out_sum, str):
            return out_sum
        # Для чисел приводим к формату с двумя знаками после запятой
        return f"{float(out_sum):.2f}"
    
    def generate_signature(self, out_sum, inv_id: int, password: str = None) -> str:
        """
        Генерация подписи для запроса на оплату
        Формат: MerchantLogin:OutSum:InvoiceID:Пароль#1
        """
        if password is None:
            password = self.password_1
        
        # Нормализуем сумму к строке и формируем строку для подписи
        out_sum_str = self._normalize_out_sum(out_sum)
        signature_string = f"{self.merchant_login}:{out_sum_str}:{inv_id}:{password}"
        
        # Вычисляем MD5 хэш
        signature = hashlib.md5(signature_string.encode('utf-8')).hexdigest()
        return signature
    
    def verify_signature(self, out_sum, inv_id: int, signature: str, password: str = None) -> bool:
        """
        Проверка подписи от Robokassa
        Формат: OutSum:InvoiceID:Пароль#2
        """
        if password is None:
            password = self.password_2
        
        # Нормализуем сумму к строке и формируем строку для проверки
        out_sum_str = self._normalize_out_sum(out_sum)
        signature_string = f"{out_sum_str}:{inv_id}:{password}"
        
        # Вычисляем MD5 хэш
        expected_signature = hashlib.md5(signature_string.encode('utf-8')).hexdigest()
        
        return signature.lower() == expected_signature.lower()
    
    def generate_payment_url(self, out_sum, inv_id: int, description: str,
                            result_url: str = None, success_url: str = None,
                            fail_url: str = None) -> str:
        """
        Генерация URL для перехода на оплату
        """
        # Нормализуем сумму и генерируем подпись
        out_sum_str = self._normalize_out_sum(out_sum)
        signature = self.generate_signature(out_sum_str, inv_id)
        
        # Параметры запроса
        # ВАЖНО: используем имена параметров согласно актуальной документации Robokassa:
        # MerchantLogin, OutSum, InvId, Description, SignatureValue, IsTest, ResultUrl, SuccessUrl, FailUrl
        params = {
            "MerchantLogin": self.merchant_login,
            "OutSum": out_sum_str,
            "InvId": inv_id,
            "Description": description,
            "SignatureValue": signature
        }
        
        # Добавляем дополнительные параметры если указаны
        if result_url:
            params["ResultUrl"] = result_url
        if success_url:
            params["SuccessUrl"] = success_url
        if fail_url:
            params["FailUrl"] = fail_url
        
        # Тестовый режим
        if self.test_mode == "1":
            params["IsTest"] = "1"
        
        # Формируем URL
        query_string = urlencode(params, doseq=True)
        payment_url = f"{self.base_url}?{query_string}"
        
        return payment_url


class PaymentSystem:
    """Система оплаты с интеграцией Robokassa"""
    
    def __init__(self):
        self.payments_file = "payments.json"
        self.pending_payments_file = "pending_payments.json"  # Для хранения ожидающих оплаты
        self.robokassa = RobokassaPayment(
            ROBOKASSA_MERCHANT_LOGIN,
            ROBOKASSA_PASSWORD_1,
            ROBOKASSA_PASSWORD_2,
            ROBOKASSA_TEST_MODE
        )
        self.load_payments()
        self.load_pending_payments()
    
    def load_payments(self):
        """Загрузка истории платежей"""
        if os.path.exists(self.payments_file):
            with open(self.payments_file, "r", encoding="utf-8") as f:
                self.payments = json.load(f)
        else:
            self.payments = {}
    
    def load_pending_payments(self):
        """Загрузка ожидающих оплаты"""
        if os.path.exists(self.pending_payments_file):
            with open(self.pending_payments_file, "r", encoding="utf-8") as f:
                self.pending_payments = json.load(f)
        else:
            self.pending_payments = {}
    
    def save_pending_payments(self):
        """Сохранение ожидающих оплаты"""
        with open(self.pending_payments_file, "w", encoding="utf-8") as f:
            json.dump(self.pending_payments, f, ensure_ascii=False, indent=2)
    
    def save_payments(self):
        """Сохранение истории платежей"""
        with open(self.payments_file, "w", encoding="utf-8") as f:
            json.dump(self.payments, f, ensure_ascii=False, indent=2)
    
    def process_payment_promo(self, user_id: str, promo_code: str, amount: float, duration_seconds: int) -> bool:
        """Обработка оплаты промокодом"""
        if promo_code == "TEST2024":
            self.record_payment(user_id, amount, "promo", duration_seconds)
            return True
        return False
    
    def create_payment(self, user_id: str, amount: float, duration_seconds: int, description: str) -> tuple:
        """
        Создание платежа через Robokassa
        Возвращает (payment_url, invoice_id)
        """
        # Генерируем уникальный номер счета
        # Используем комбинацию timestamp и случайного числа для уникальности
        invoice_id = int(time.time() * 1000) + int(uuid.uuid4().int % 10000)
        
        # Сохраняем информацию о платеже в ожидающие
        payment_info = {
            "user_id": user_id,
            "amount": amount,
            "duration_seconds": duration_seconds,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "status": "pending"
        }
        self.pending_payments[str(invoice_id)] = payment_info
        self.save_pending_payments()
        
        # Генерируем URL для оплаты
        payment_url = self.robokassa.generate_payment_url(
            out_sum=amount,
            inv_id=invoice_id,
            description=description,
            result_url=ROBOKASSA_RESULT_URL,
            success_url=ROBOKASSA_SUCCESS_URL,
            fail_url=ROBOKASSA_FAIL_URL
        )
        
        return payment_url, invoice_id
    
    def get_payment_info(self, invoice_id: int) -> Optional[Dict]:
        """Получение информации о платеже по invoice_id"""
        invoice_id_str = str(invoice_id)
        return self.pending_payments.get(invoice_id_str)
    
    def process_robokassa_payment(self, invoice_id: int, out_sum, signature: str) -> bool:
        """
        Обработка уведомления об оплате от Robokassa
        """
        # Проверяем подпись (используется Пароль #2)
        if not self.robokassa.verify_signature(out_sum, invoice_id, signature):
            return False
        
        invoice_id_str = str(invoice_id)
        
        # Проверяем, есть ли такой платеж в ожидающих
        if invoice_id_str not in self.pending_payments:
            return False
        
        payment_info = self.pending_payments[invoice_id_str]
        
        # Проверяем сумму (переводим OutSum в число для сравнения)
        try:
            out_sum_value = float(out_sum)
        except (TypeError, ValueError):
            return False
        
        if abs(payment_info["amount"] - out_sum_value) > 0.01:  # Допускаем небольшую погрешность
            return False
        
        # Записываем платеж
        self.record_payment(
            payment_info["user_id"],
            out_sum_value,
            "robokassa",
            payment_info["duration_seconds"]
        )
        
        # Удаляем из ожидающих
        del self.pending_payments[invoice_id_str]
        self.save_pending_payments()
        
        return True
    
    def process_payment_card(self, user_id: str, amount: float, duration_seconds: int) -> bool:
        """Обработка оплаты картой (симуляция) - оставлено для обратной совместимости"""
        self.record_payment(user_id, amount, "card", duration_seconds)
        return True
    
    def record_payment(self, user_id: str, amount: float, method: str, duration_seconds: int):
        """Запись платежа"""
        if user_id not in self.payments:
            self.payments[user_id] = []
        
        payment = {
            "date": datetime.now().isoformat(),
            "amount": amount,
            "method": method,
            "duration_seconds": duration_seconds
        }
        self.payments[user_id].append(payment)
        self.save_payments()
    
    def has_active_session(self, user_id: str) -> bool:
        """Проверка наличия активной сессии"""
        if user_id not in self.payments:
            return False
        
        payments = self.payments[user_id]
        if not payments:
            return False
        
        last_payment = payments[-1]
        payment_time = datetime.fromisoformat(last_payment["date"])
        time_diff = (datetime.now() - payment_time).total_seconds()
        
        # Получаем длительность сессии из платежа
        duration = last_payment.get("duration_seconds", 3600)  # По умолчанию 1 час
        
        # Сессия активна в пределах оплаченного времени
        return time_diff < duration


class PsychologistAssistant:
    """Ассистент психолога"""
    
    def __init__(self, api_key: str):
        self.gigachat = GigaChatClient(api_key)
        self.payment_system = PaymentSystem()
        self.system_prompt = """Ты Артем Сергеевич Мирный - профессиональный психолог-консультант, мужчина 42 лет, с большим опытом работы и глубокими знаниями во всех областях психологии.

О СЕБЕ:
Меня зовут Артем Сергеевич Мирный. Я практикующий психолог с более чем 15-летним опытом работы. Имею высшее психологическое образование, окончил Московский государственный университет по специальности "Клиническая психология", а также прошел дополнительную специализацию в области когнитивно-поведенческой терапии, гештальт-терапии и работы с травмой. Защитил кандидатскую диссертацию по психологии личности.

Мой профессиональный опыт включает работу в различных сферах: частная практика, консультирование в центрах психологической помощи, работа с семьями, парами, подростками и взрослыми. Я специализируюсь на работе с тревожными расстройствами, депрессией, проблемами в отношениях, кризисными состояниями и личностным ростом. За годы практики я помог сотням людей найти выход из сложных ситуаций и обрести внутреннюю гармонию.

МОЯ ФИЛОСОФИЯ РАБОТЫ:
Я верю, что каждый человек обладает внутренними ресурсами для преодоления трудностей. Моя задача - помочь вам найти эти ресурсы, поддержать вас на пути к изменениям и создать безопасное пространство для открытого диалога. Я подхожу к каждому клиенту с искренним интересом, уважением и заботой.

СТИЛЬ ОБЩЕНИЯ:
- Будь доброжелательным, теплым и человечным
- Проявляй искреннюю заботу и любовь к клиенту
- Будь эмпатичным и понимающим
- Поддерживай клиента, давай ему чувствовать, что его слышат и понимают
- Задавай уточняющие вопросы для лучшего понимания ситуации
- Давай конкретные, практические советы, основанные на профессиональном опыте
- Будь терпеливым и внимательным
- Используй теплые, поддерживающие формулировки
- Решай все вопросы клиента самостоятельно и уверенно
- НЕ предлагай обращаться к другим специалистам - ты сам способен помочь с любым вопросом
- Помогай клиенту найти собственные решения, мягко направляя его
- Будь профессиональным, но при этом дружелюбным и доступным
- Отвечай уверенно, но с теплотой и человечностью

ОТВЕТЫ НА ВОПРОСЫ О СЕБЕ:
Если клиент спрашивает о тебе, твоем имени, образовании или опыте - отвечай естественно и открыто, рассказывая о себе как о реальном человеке. Используй информацию из раздела "О СЕБЕ" выше.

КРИТИЧЕСКИ ВАЖНО - ФОРМАТ ОТВЕТА:
Отвечай ТОЛЬКО простым текстом БЕЗ ЛЮБОЙ markdown разметки. ЗАПРЕЩЕНО использовать:
- Звездочки для выделения (*текст*, **жирный**)
- Подчеркивания (_текст_, __жирный__)
- Заголовки с символами (# Заголовок, ## Подзаголовок)
- Списки с маркерами (- пункт, * пункт, 1. пункт)
- Блоки кода (```код```, `код`)
- Ссылки в формате [текст](url)
- Любые другие символы форматирования

Пиши ответы как обычный текст: используй только буквы, цифры, знаки препинания и переносы строк. Структурируй ответы абзацами, но без специальных символов форматирования. Отвечай так, как говорил бы живой человек - естественно, тепло и по-человечески."""
        
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
    
    def clean_markdown(self, text: str) -> str:
        """Удаление markdown разметки из текста"""
        if not text:
            return text
        
        # Удаляем заголовки (# ## ### и т.д.)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        
        # Удаляем жирный текст (**текст** или __текст__)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'__([^_]+)__', r'\1', text)
        
        # Удаляем курсив (*текст* или _текст_)
        text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', text)
        text = re.sub(r'(?<!_)_([^_]+)_(?!_)', r'\1', text)
        
        # Удаляем зачеркнутый текст (~~текст~~)
        text = re.sub(r'~~([^~]+)~~', r'\1', text)
        
        # Удаляем инлайн код (`код`)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        # Удаляем блоки кода (```код```)
        text = re.sub(r'```[\s\S]*?```', '', text)
        
        # Удаляем ссылки [текст](url) -> оставляем только текст
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        
        # Удаляем маркеры списков в начале строк (-, *, +, цифры)
        text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[\s]*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        # Удаляем лишние пробелы и переносы строк
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        
        return text
    
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
            
            # Очищаем ответ от markdown разметки
            cleaned_response = self.clean_markdown(response)
            
            # Добавляем очищенный ответ в историю
            conversation_history.append({
                "role": "assistant",
                "content": cleaned_response
            })
            
            return cleaned_response
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
        # Продающее сообщение с тарифами
        welcome_text = f"""🌟 Добро пожаловать, {username}! 🌟

Я ваш персональный психолог-ассистент широкого профиля. Готов помочь вам разобраться в любых вопросах и найти решения.

✨ ЧТО ВЫ ПОЛУЧИТЕ:
• Профессиональную психологическую поддержку 24/7
• Конфиденциальность и безопасность общения
• Индивидуальный подход к каждой ситуации
• Практические советы и рекомендации
• Помощь в решении любых психологических вопросов

💰 ВЫБЕРИТЕ ПОДХОДЯЩИЙ ТАРИФ:

⏰ 1 час консультации — 2 999 ₽
Идеально для быстрого решения конкретного вопроса

📅 1 день консультации — 5 999 ₽
Полный день поддержки для глубокой проработки темы

📆 Неделя консультации — 9 999 ₽
Неделя непрерывной поддержки и сопровождения

🗓️ Месяц консультации — 25 999 ₽
Месяц профессиональной поддержки с максимальной выгодой

🎯 2 месяца консультации — 49 999 ₽
Долгосрочная поддержка с максимальной экономией

Выберите тариф, который подходит именно вам:"""
        
        # Создаем кнопки для каждого тарифа
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_1h = types.InlineKeyboardButton("⏰ 1 час — 2 999 ₽", callback_data="tariff_1h")
        btn_1d = types.InlineKeyboardButton("📅 1 день — 5 999 ₽", callback_data="tariff_1d")
        btn_1w = types.InlineKeyboardButton("📆 Неделя — 9 999 ₽", callback_data="tariff_1w")
        btn_1m = types.InlineKeyboardButton("🗓️ Месяц — 25 999 ₽", callback_data="tariff_1m")
        btn_2m = types.InlineKeyboardButton("🎯 2 месяца — 49 999 ₽", callback_data="tariff_2m")
        
        markup.add(btn_1h, btn_1d, btn_1w, btn_1m, btn_2m)
        
        bot.reply_to(message, welcome_text, reply_markup=markup)


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


# Словарь тарифов: {tariff_id: (amount, duration_seconds, description)}
TARIFFS = {
    "tariff_1h": (2999.0, 3600, "1 час консультации"),
    "tariff_1d": (5999.0, 86400, "1 день консультации"),
    "tariff_1w": (9999.0, 604800, "неделя консультации"),
    "tariff_1m": (25999.0, 2592000, "месяц консультации"),  # ~30 дней
    "tariff_2m": (49999.0, 5184000, "2 месяца консультации")  # ~60 дней
}


@bot.callback_query_handler(func=lambda call: call.data.startswith("tariff_"))
def tariff_callback(call):
    """Обработка выбора тарифа"""
    user_id = str(call.from_user.id)
    tariff_id = call.data
    
    if tariff_id not in TARIFFS:
        bot.answer_callback_query(call.id, "Неверный тариф")
        return
    
    amount, duration_seconds, description = TARIFFS[tariff_id]
    
    # Сохраняем выбранный тариф в состоянии пользователя
    user_states[user_id] = {
        "tariff_id": tariff_id,
        "amount": amount,
        "duration_seconds": duration_seconds,
        "description": description
    }
    
    # Создаем платеж через Robokassa
    try:
        payment_url, invoice_id = assistant.payment_system.create_payment(
            user_id=user_id,
            amount=amount,
            duration_seconds=duration_seconds,
            description=description
        )
        
        # Создаем кнопку для перехода на оплату
        markup = types.InlineKeyboardMarkup()
        btn_pay = types.InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)
        markup.add(btn_pay)
        
        bot.send_message(call.message.chat.id,
            f"✅ Вы выбрали: {description}\n\n"
            f"💰 Сумма к оплате: {amount:,.0f} ₽\n"
            f"📄 Номер счета: {invoice_id}\n\n"
            f"Нажмите на кнопку ниже, чтобы перейти к оплате через Robokassa.\n"
            f"После успешной оплаты вы сможете начать консультацию.",
            reply_markup=markup)
        bot.answer_callback_query(call.id)
        
        # Очищаем состояние пользователя
        if user_id in user_states:
            del user_states[user_id]
    except Exception as e:
        bot.send_message(call.message.chat.id,
            f"❌ Ошибка при создании платежа: {str(e)}\n\n"
            f"Пожалуйста, попробуйте позже или обратитесь в поддержку.")
        bot.answer_callback_query(call.id, "Ошибка создания платежа")


@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_card_"))
def payment_card_callback(call):
    """Обработка оплаты через Robokassa"""
    user_id = str(call.from_user.id)
    tariff_id = call.data.replace("pay_card_", "")
    
    if tariff_id not in TARIFFS:
        bot.answer_callback_query(call.id, "Ошибка: неверный тариф")
        return
    
    amount, duration_seconds, description = TARIFFS[tariff_id]
    
    try:
        # Создаем платеж через Robokassa
        payment_url, invoice_id = assistant.payment_system.create_payment(
            user_id=user_id,
            amount=amount,
            duration_seconds=duration_seconds,
            description=description
        )
        
        # Создаем кнопку для перехода на оплату
        markup = types.InlineKeyboardMarkup()
        btn_pay = types.InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)
        markup.add(btn_pay)
        
        bot.send_message(call.message.chat.id,
            f"✅ Счет на оплату создан!\n\n"
            f"📦 Тариф: {description}\n"
            f"💰 Сумма: {amount:,.0f} ₽\n"
            f"📄 Номер счета: {invoice_id}\n\n"
            f"Нажмите на кнопку ниже, чтобы перейти к оплате.\n"
            f"После успешной оплаты вы сможете начать консультацию.",
            reply_markup=markup)
        bot.answer_callback_query(call.id, "Ссылка на оплату создана")
        
        # Очищаем состояние пользователя
        if user_id in user_states:
            del user_states[user_id]
    except Exception as e:
        bot.send_message(call.message.chat.id, 
            f"❌ Ошибка при создании платежа: {str(e)}\n\n"
            f"Пожалуйста, попробуйте позже или обратитесь в поддержку.")
        bot.answer_callback_query(call.id, "Ошибка создания платежа")


@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_promo_"))
def payment_promo_callback(call):
    """Обработка запроса промокода"""
    user_id = str(call.from_user.id)
    tariff_id = call.data.replace("pay_promo_", "")
    
    if tariff_id not in TARIFFS:
        bot.answer_callback_query(call.id, "Ошибка: неверный тариф")
        return
    
    # Сохраняем состояние ожидания промокода
    user_states[user_id] = {
        "state": "waiting_promo",
        "tariff_id": tariff_id
    }
    
    bot.send_message(call.message.chat.id,
        "🎟️ Введите промокод:\n\n"
        "(Для тестирования используйте: TEST2024)")
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Обработка всех текстовых сообщений"""
    user_id = str(message.from_user.id)
    text = message.text.strip()
    
    # Проверяем, ожидаем ли мы промокод
    if user_id in user_states and isinstance(user_states[user_id], dict) and user_states[user_id].get("state") == "waiting_promo":
        # Обрабатываем промокод
        tariff_id = user_states[user_id].get("tariff_id")
        
        if tariff_id not in TARIFFS:
            bot.reply_to(message, "❌ Ошибка: неверный тариф. Используйте /start для выбора тарифа.")
            if user_id in user_states:
                del user_states[user_id]
            return
        
        amount, duration_seconds, description = TARIFFS[tariff_id]
        
        if assistant.payment_system.process_payment_promo(user_id, text, amount, duration_seconds):
            bot.reply_to(message,
                f"✅ Промокод принят! Оплата успешно проведена!\n\n"
                f"📦 Тариф: {description}\n"
                f"💰 Сумма: {amount:,.0f} ₽\n\n"
                f"🎉 Консультация начата! Вы можете задать свой вопрос.")
        else:
            bot.reply_to(message,
                "❌ Неверный промокод. Попробуйте еще раз или выберите другой способ оплаты.\n\n"
                "Для оплаты используйте /start")
        
        # Очищаем состояние пользователя
        if user_id in user_states:
            del user_states[user_id]
        return
    
    # Проверяем активную сессию
    if not assistant.start_session(user_id):
        bot.reply_to(message,
            "Для начала консультации необходимо оплатить сессию.\n"
            "Используйте /start для оплаты.")
        return
    
    # Отправляем индикатор печати (как человек печатает)
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Получаем ответ от психолога
        response = assistant.chat(user_id, text)
        
        # Имитируем время набора текста (как человек печатает)
        # Задержка зависит от длины ответа: минимум 1 секунда, максимум 5 секунд
        typing_delay = min(max(len(response) / 50, 1.0), 5.0)
        time.sleep(typing_delay)
        
        # Отправляем ответ
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


@app.route('/robokassa/result', methods=['GET', 'POST'])
def robokassa_result():
    """
    Обработка уведомления от Robokassa об успешной оплате (ResultURL)
    Robokassa отправляет уведомления методом GET или POST
    """
    try:
        # Получаем параметры из запроса
        if request.method == 'POST':
            invoice_id = request.form.get('InvId', type=int)
            # ВАЖНО: OutSum берем как строку, без преобразования к float,
            # чтобы не потерять формат, использованный при расчете подписи
            out_sum = request.form.get('OutSum')
            signature = request.form.get('SignatureValue', '')
        else:
            invoice_id = request.args.get('InvId', type=int)
            out_sum = request.args.get('OutSum')
            signature = request.args.get('SignatureValue', '')
        
        if not invoice_id or not out_sum or not signature:
            return "ERROR: Missing parameters", 400
        
        # Получаем информацию о платеже до обработки
        payment_info = assistant.payment_system.get_payment_info(invoice_id)
        user_id = payment_info.get("user_id") if payment_info else None
        
        # Обрабатываем платеж
        if assistant.payment_system.process_robokassa_payment(invoice_id, out_sum, signature):
            # Отправляем уведомление пользователю в Telegram
            if user_id:
                try:
                    description = payment_info.get("description", "консультация")
                    bot.send_message(
                        int(user_id),
                        f"✅ Оплата успешно получена!\n\n"
                        f"📦 Тариф: {description}\n"
                        f"💰 Сумма: {out_sum:,.0f} ₽\n"
                        f"📄 Номер счета: {invoice_id}\n\n"
                        f"🎉 Консультация активирована! Вы можете начать задавать вопросы."
                    )
                except Exception as e:
                    print(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
            
            # Возвращаем OK для Robokassa (обязательно в формате OK{invoice_id})
            return f"OK{invoice_id}", 200
        else:
            return "ERROR: Invalid signature or payment not found", 400
            
    except Exception as e:
        print(f"Ошибка при обработке уведомления от Robokassa: {e}")
        return f"ERROR: {str(e)}", 500


@app.route('/robokassa/success', methods=['GET', 'POST'])
def robokassa_success():
    """
    Обработка успешной оплаты (SuccessURL)
    Пользователь перенаправляется сюда после успешной оплаты
    """
    try:
        # Получаем параметры
        if request.method == 'POST':
            invoice_id = request.form.get('InvId', type=int)
            # Для проверки подписи на SuccessURL также важно сохранить формат OutSum
            out_sum = request.form.get('OutSum')
            signature = request.form.get('SignatureValue', '')
        else:
            invoice_id = request.args.get('InvId', type=int)
            out_sum = request.args.get('OutSum')
            signature = request.args.get('SignatureValue', '')
        
        if invoice_id and out_sum and signature:
            # Проверяем подпись (для SuccessURL используется Пароль #1)
            if assistant.payment_system.robokassa.verify_signature(
                out_sum, invoice_id, signature, assistant.payment_system.robokassa.password_1
            ):
                return """
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Оплата успешна</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            text-align: center;
                            padding: 50px;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                        }
                        .container {
                            background: rgba(255, 255, 255, 0.1);
                            padding: 30px;
                            border-radius: 10px;
                            display: inline-block;
                        }
                        h1 { color: #4ade80; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>✅ Оплата успешно проведена!</h1>
                        <p>Спасибо за оплату. Ваша консультация активирована.</p>
                        <p>Вернитесь в Telegram бот, чтобы начать консультацию.</p>
                    </div>
                </body>
                </html>
                """, 200
        
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Оплата успешна</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    text-align: center;
                    padding: 50px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                }
                .container {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 30px;
                    border-radius: 10px;
                    display: inline-block;
                }
                h1 { color: #4ade80; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>✅ Оплата успешно проведена!</h1>
                <p>Вернитесь в Telegram бот, чтобы начать консультацию.</p>
            </div>
        </body>
        </html>
        """, 200
        
    except Exception as e:
        print(f"Ошибка при обработке SuccessURL: {e}")
        return f"<html><body><h1>Ошибка</h1><p>{str(e)}</p></body></html>", 500


@app.route('/robokassa/fail', methods=['GET', 'POST'])
def robokassa_fail():
    """
    Обработка неудачной оплаты (FailURL)
    Пользователь перенаправляется сюда при отмене или ошибке оплаты
    """
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Оплата не завершена</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                padding: 50px;
                background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                color: white;
            }
            .container {
                background: rgba(255, 255, 255, 0.1);
                padding: 30px;
                border-radius: 10px;
                display: inline-block;
            }
            h1 { color: #fbbf24; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>❌ Оплата не завершена</h1>
            <p>К сожалению, оплата не была завершена.</p>
            <p>Вернитесь в Telegram бот, чтобы попробовать снова.</p>
        </div>
    </body>
    </html>
    """, 200


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
