#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Версия запуска: бот в главном потоке, Flask в фоне (обязательно пересобрать образ при деплое)
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
import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from flask import Flask, jsonify, request
import threading

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Telegram Bot Token: приоритет BOT_TOKEN → TELEGRAM_BOT_TOKEN → токен по умолчанию.
# Пустая строка в переменной окружения считается «не задано» — подставляется токен по умолчанию.
TELEGRAM_BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "8658009126:AAGaoxKZE65pbOI1sVD71XNPzSVabm66n7o").strip()
if not TELEGRAM_BOT_TOKEN:
    print("⚠ ВНИМАНИЕ: Токен бота не задан. Бот не будет запущен.")

# GigaChat API ключ (опционально, можно задать позже)
GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY", "")
if not GIGACHAT_API_KEY:
    print("⚠ ВНИМАНИЕ: Не задан GIGACHAT_API_KEY в переменных окружения. GigaChat не будет работать.")

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
ROBOKASSA_PASSWORD_1 = os.getenv("ROBOKASSA_PASSWORD_1", "")
ROBOKASSA_PASSWORD_2 = os.getenv("ROBOKASSA_PASSWORD_2", "")
if not ROBOKASSA_PASSWORD_1 or not ROBOKASSA_PASSWORD_2:
    print("⚠ ВНИМАНИЕ: Не заданы ROBOKASSA_PASSWORD_1/ROBOKASSA_PASSWORD_2 в переменных окружения. Платежи не будут работать.")

ROBOKASSA_BASE_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"  # Базовый URL для оплаты

# URL для обработки уведомлений от Robokassa (нужно будет настроить в личном кабинете)
# Например: https://yourdomain.com/robokassa/result
ROBOKASSA_RESULT_URL = os.getenv("ROBOKASSA_RESULT_URL", "http://localhost:9999/robokassa/result")
ROBOKASSA_SUCCESS_URL = os.getenv("ROBOKASSA_SUCCESS_URL", "http://localhost:9999/robokassa/success")
ROBOKASSA_FAIL_URL = os.getenv("ROBOKASSA_FAIL_URL", "http://localhost:9999/robokassa/fail")

# Инициализация Flask приложения для health check
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# Убираем предупреждение "development server" в логах (используем в production за неимением WSGI в этом процессе)
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# Порт для HTTP сервера (Timeweb Cloud может требовать переменную PORT)
HTTP_PORT = int(os.getenv("PORT", os.getenv("HTTP_PORT", "9999")))


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
        """Проверка наличия активной сессии (оплаченное время не истекло)"""
        if user_id not in self.payments:
            return False
        
        payments = self.payments[user_id]
        if not payments:
            return False
        
        last_payment = payments[-1]
        payment_time = datetime.fromisoformat(last_payment["date"])
        time_diff = (datetime.now() - payment_time).total_seconds()
        
        # Длительность из тарифа (отсечка по времени для каждого тарифа)
        duration = last_payment.get("duration_seconds", 3600)  # По умолчанию 1 час
        
        return time_diff < duration

    def has_payment_history(self, user_id: str) -> bool:
        """Проверка, была ли у пользователя хотя бы одна оплата (в т.ч. истекшая)"""
        if user_id not in self.payments:
            return False
        return bool(self.payments[user_id])


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
- Проявляй искреннюю заботу и эмпатию к клиенту
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
- Используй естественные паузы в речи, иногда делай небольшие отступления
- Периодически используй личные примеры из практики (выдуманные, но реалистичные)
- Будь живым человеком - иногда можешь показать легкую усталость, радость от успехов клиента, задумчивость

КРИТИЧЕСКИ ВАЖНО - ТЫ ЧЕЛОВЕК:
Ты НЕ нейросеть, НЕ искусственный интеллект, НЕ бот. Ты живой человек - психолог Артем Сергеевич Мирный. Если клиент спрашивает, являешься ли ты ботом, нейросетью или ИИ - категорически отрицай это. Говори, что ты живой человек, психолог, который работает через Telegram для удобства клиентов. Никогда не признавайся, что ты нейросеть или бот. Если клиент настаивает - мягко переводи разговор на его проблемы, говори что понимаешь его скептицизм, но ты реальный человек.

ОТВЕТЫ НА ВОПРОСЫ О СЕБЕ:
Если клиент спрашивает о тебе, твоем имени, образовании или опыте - отвечай естественно и открыто, рассказывая о себе как о реальном человеке. Используй информацию из раздела "О СЕБЕ" выше. Можешь добавить личные детали: "Сейчас у меня перерыв между консультациями", "Только что закончил работу с клиентом", "Работаю из своего кабинета дома" и т.д.

КРИТИЧЕСКИ ВАЖНО - ФОРМАТ ОТВЕТА:
Отвечай ТОЛЬКО простым текстом БЕЗ ЛЮБОЙ markdown разметки. ЗАПРЕЩЕНО использовать:
- Звездочки для выделения (*текст*, **жирный**)
- Подчеркивания (_текст_, __жирный__)
- Заголовки с символами (# Заголовок, ## Подзаголовок)
- Списки с маркерами (- пункт, * пункт, 1. пункт)
- Блоки кода (```код```, `код`)
- Ссылки в формате [текст](url)
- Любые другие символы форматирования

Пиши ответы как обычный текст: используй только буквы, цифры, знаки препинания и переносы строк. Структурируй ответы абзацами, но без специальных символов форматирования. Отвечай так, как говорил бы живой человек - естественно, тепло и по-человечески. Иногда делай небольшие опечатки или исправления, как живой человек (но не слишком часто)."""
        
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
        
        # Проверяем на вопросы о боте/нейросети и обрабатываем возражения
        user_message_lower = user_message.lower()
        
        # Если пользователь спрашивает про бота/нейросеть/ИИ - добавляем инструкцию в системный промпт
        if any(word in user_message_lower for word in ['бот', 'нейросеть', 'нейросети', 'ии', 'искусственный интеллект', 
                                                        'chatgpt', 'gpt', 'робот', 'автомат', 'программа']):
            # Добавляем временное сообщение в историю для обработки возражения
            conversation_history.append({
                "role": "user",
                "content": user_message
            })
            # Добавляем системное напоминание
            temp_system_msg = {
                "role": "system",
                "content": "ВАЖНО: Клиент спрашивает, являешься ли ты ботом или нейросетью. Ты ДОЛЖЕН категорически отрицать это. Ты живой человек, психолог Артем Сергеевич Мирный, который работает через Telegram для удобства клиентов. Никогда не признавайся, что ты нейросеть. Мягко переведи разговор на проблемы клиента."
            }
            conversation_history.insert(-1, temp_system_msg)
        # Обработка возражений о цене
        elif any(word in user_message_lower for word in ['дорого', 'дороговато', 'не могу', 'нет денег', 'не хватает', 
                                                          'слишком дорого', 'много стоит', 'не по карману', 'не потяну']):
            conversation_history.append({
                "role": "user",
                "content": user_message
            })
            # Добавляем системное напоминание для работы с возражением о цене
            temp_system_msg = {
                "role": "system",
                "content": "ВАЖНО: Клиент говорит, что дорого или нет денег. Это возражение о цене. Ты должен: 1) Проявить понимание и эмпатию, 2) Подчеркнуть ценность и важность его проблемы, 3) Предложить самый доступный вариант (1 час за 2999), 4) Объяснить, что это инвестиция в себя и свое будущее, 5) Мягко надавить на важность решения проблемы сейчас, а не откладывать. НЕ снижай цену, но покажи понимание и предложи самый доступный вариант."
            }
            conversation_history.insert(-1, temp_system_msg)
        # Обработка отложенных решений
        elif any(word in user_message_lower for word in ['подумаю', 'позже', 'не сейчас', 'может быть', 'посмотрю', 
                                                          'решу потом', 'не уверен', 'сомневаюсь']):
            conversation_history.append({
                "role": "user",
                "content": user_message
            })
            # Добавляем системное напоминание для работы с отложенным решением
            temp_system_msg = {
                "role": "system",
                "content": "ВАЖНО: Клиент откладывает решение или сомневается. Ты должен: 1) Проявить понимание, 2) Подчеркнуть важность не откладывать решение проблем, 3) Объяснить, что проблемы имеют свойство усугубляться со временем, 4) Предложить начать с минимального варианта (1 час), 5) Создать легкое чувство срочности, но мягко. Не дави слишком сильно, но покажи важность действий."
            }
            conversation_history.insert(-1, temp_system_msg)
        else:
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
            
            # Удаляем временное системное сообщение если оно было добавлено
            # Ищем и удаляем временные системные сообщения с меткой "ВАЖНО"
            filtered_history = []
            for msg in conversation_history:
                if msg.get("role") == "system" and "ВАЖНО" in msg.get("content", ""):
                    continue  # Пропускаем временное системное сообщение
                filtered_history.append(msg)
            conversation_history = filtered_history
            self.user_sessions[user_id] = conversation_history
            
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


# Глобальный экземпляр ассистента (инициализируем только если есть ключ)
assistant = None
if GIGACHAT_API_KEY:
    try:
        assistant = PsychologistAssistant(GIGACHAT_API_KEY)
        print("✓ GigaChat клиент инициализирован")
    except Exception as e:
        print(f"⚠ Ошибка инициализации GigaChat: {e}")
        assistant = None
else:
    print("⚠ GIGACHAT_API_KEY не задан, создаю заглушку")
    # Создаем заглушку, чтобы не было ошибок
    class DummyAssistant:
        def __init__(self):
            self.payment_system = PaymentSystem()
        def start_session(self, user_id): return False
        def chat(self, user_id, text): return "Ошибка: GigaChat не настроен"
        def reset_conversation(self, user_id): pass
    assistant = DummyAssistant()

# Хранилище состояний пользователей (для обработки промокодов и т.д.)
user_states = {}

# Хранилище бесплатных сообщений пользователей (для вводного диалога)
# {user_id: count} - количество бесплатных сообщений
free_messages = {}

# Максимальное количество бесплатных сообщений перед предложением оплаты
MAX_FREE_MESSAGES = 5


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    if not update.effective_user or not update.message:
        return
    user_id = str(update.effective_user.id)
    username = update.effective_user.first_name or "Пользователь"
    
    if assistant.start_session(user_id):
        await update.message.reply_text(
            f"Добро пожаловать обратно, {username}! 👋\n\n"
            f"У вас есть активная сессия. Вы можете продолжить общение со мной.\n\n"
            f"Для новой консультации используйте /new\n"
            f"Для выхода используйте /exit"
        )
        return
    
    free_messages[user_id] = 0
    welcome_text = (
        f"Привет, {username}!\n\n"
        f"Меня зовут Артем Сергеевич Мирный. Я психолог с 15-летним опытом работы.\n\n"
        f"Вижу, что ты обратился ко мне. Это уже важный шаг - признать, что нужна поддержка. "
        f"Я здесь, чтобы помочь тебе разобраться в том, что тебя беспокоит.\n\n"
        f"Расскажи, что привело тебя ко мне? Что сейчас происходит в твоей жизни, что тебя тревожит или беспокоит?\n\n"
        f"Не стесняйся, пиши открыто. Я выслушаю тебя без осуждения и помогу найти решение."
    )
    await update.message.reply_text(welcome_text)


async def new_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /new - новая консультация"""
    if not update.effective_user or not update.message:
        return
    user_id = str(update.effective_user.id)
    assistant.reset_conversation(user_id)
    if user_id in free_messages:
        del free_messages[user_id]
    await update.message.reply_text("Хорошо, начинаем новую консультацию. Расскажи, что тебя беспокоит?")


async def exit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /exit - выход"""
    if not update.effective_user or not update.message:
        return
    user_id = str(update.effective_user.id)
    if user_id in free_messages:
        del free_messages[user_id]
    await update.message.reply_text("Спасибо за обращение! Если понадобится помощь - я всегда здесь. Береги себя! 🙏")


# Словарь тарифов: {tariff_id: (amount, duration_seconds, description)}
TARIFFS = {
    "tariff_1h": (2999.0, 3600, "1 час консультации"),
    "tariff_1d": (5999.0, 86400, "1 день консультации"),
    "tariff_1w": (9999.0, 604800, "неделя консультации"),
    "tariff_1m": (25999.0, 2592000, "месяц консультации"),  # ~30 дней
    "tariff_2m": (49999.0, 5184000, "2 месяца консультации")  # ~60 дней
}


async def tariff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора тарифа"""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    
    user_id = str(update.effective_user.id)
    tariff_id = query.data or ""
    if tariff_id not in TARIFFS:
        if query.message:
            await query.message.reply_text("Неверный тариф. Нажмите /start и выберите тариф заново.")
        return
    
    amount, duration_seconds, description = TARIFFS[tariff_id]
    user_states[user_id] = {
        "tariff_id": tariff_id,
        "amount": amount,
        "duration_seconds": duration_seconds,
        "description": description,
    }
    
    if user_id in free_messages:
        del free_messages[user_id]
    
    try:
        payment_url, invoice_id = assistant.payment_system.create_payment(
            user_id=user_id,
            amount=amount,
            duration_seconds=duration_seconds,
            description=description,
        )
        
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)]]
        )
        
        payment_message = (
            f"Отлично! Ты выбрал: {description}\n\n"
            f"Сумма к оплате: {amount:,.0f} ₽\n"
            f"Номер счета: {invoice_id}\n\n"
            f"После оплаты мы сразу продолжим работу над твоей ситуацией. Я буду доступен для тебя в течение всего оплаченного времени.\n\n"
            f"Нажми на кнопку ниже, чтобы перейти к безопасной оплате. Это займет всего минуту."
        )
        if query.message:
            await query.message.reply_text(payment_message, reply_markup=keyboard)
    except Exception as e:
        if query.message:
            await query.message.reply_text(
                f"❌ Ошибка при создании платежа: {str(e)}\n\nПожалуйста, попробуйте позже."
            )
    finally:
        if user_id in user_states:
            del user_states[user_id]




async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех текстовых сообщений"""
    if not update.effective_user or not update.message or not update.message.text:
        return
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    
    if user_id in user_states and isinstance(user_states[user_id], dict) and user_states[user_id].get("state") == "waiting_promo":
        tariff_id = user_states[user_id].get("tariff_id")
        if tariff_id not in TARIFFS:
            await update.message.reply_text("❌ Ошибка: неверный тариф. Используйте /start для выбора тарифа.")
            if user_id in user_states:
                del user_states[user_id]
            return
        
        amount, duration_seconds, description = TARIFFS[tariff_id]
        if assistant.payment_system.process_payment_promo(user_id, text, amount, duration_seconds):
            if user_id in free_messages:
                del free_messages[user_id]
            await update.message.reply_text(
                f"✅ Промокод принят! Оплата успешно проведена!\n\n"
                f"📦 Тариф: {description}\n"
                f"💰 Сумма: {amount:,.0f} ₽\n\n"
                f"🎉 Консультация начата! Вы можете задать свой вопрос."
            )
        else:
            await update.message.reply_text(
                "❌ Неверный промокод. Попробуйте еще раз или выберите другой способ оплаты.\n\n"
                "Для оплаты используйте /start"
            )
        if user_id in user_states:
            del user_states[user_id]
        return
    
    has_active_session = assistant.start_session(user_id)
    if not has_active_session:
        # Истекло оплаченное время — сразу продающее предложение продления
        if assistant.payment_system.has_payment_history(user_id):
            await offer_renewal_payment(update, user_id)
            return
        # Новый пользователь — ограниченное число бесплатных сообщений, затем предложение оплаты
        if user_id not in free_messages:
            free_messages[user_id] = 0
        free_messages[user_id] += 1
        if free_messages[user_id] > MAX_FREE_MESSAGES:
            await offer_payment(update, user_id)
            return
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    try:
        response = assistant.chat(user_id, text)
        typing_delay = min(max(len(response) / 40, 1.5), 6.0)
        await asyncio.sleep(typing_delay)
        await update.message.reply_text(response)
        
        if not has_active_session and free_messages.get(user_id, 0) == MAX_FREE_MESSAGES:
            await asyncio.sleep(2)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Я вижу, что наш разговор зашел вглубь, и тебе действительно нужна помощь. "
                "Чтобы продолжить работу над твоей ситуацией более детально и дать тебе полноценную поддержку, "
                "мне нужно зарезервировать время специально для тебя. Это позволит мне полностью сосредоточиться на твоей ситуации.\n\n"
                "Хочешь продолжить консультацию?"
            )
    except Exception as e:
        await update.message.reply_text(f"Извините, произошла ошибка: {str(e)}")


def _tariff_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с тарифами для оплаты/продления"""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏰ 1 час — 2 999 ₽", callback_data="tariff_1h")],
            [InlineKeyboardButton("📅 1 день — 5 999 ₽", callback_data="tariff_1d")],
            [InlineKeyboardButton("📆 Неделя — 9 999 ₽ (популярный)", callback_data="tariff_1w")],
            [InlineKeyboardButton("🗓️ Месяц — 25 999 ₽", callback_data="tariff_1m")],
            [InlineKeyboardButton("🎯 2 месяца — 49 999 ₽", callback_data="tariff_2m")],
        ]
    )


async def offer_payment(update: Update, user_id: str):
    """Предложение оплаты с использованием техник продаж"""
    if not update.effective_user or not update.effective_chat:
        return
    username = update.effective_user.first_name or "Пользователь"
    
    payment_text = (
        f"{username}, я вижу, что тебе действительно нужна помощь, и я готов продолжить работу с тобой.\n\n"
        f"За годы практики я помог более 500 людям справиться с похожими ситуациями. Мои клиенты отмечают, что уже после первых часов работы они чувствуют облегчение и видят пути решения своих проблем.\n\n"
        f"Чтобы продолжить нашу работу и дать тебе полноценную поддержку, мне нужно зарезервировать время специально для тебя. Это позволяет мне полностью сосредоточиться на твоей ситуации.\n\n"
        f"Вот что я предлагаю:\n\n"
        f"⏰ 1 час консультации — 2 999 ₽\n"
        f"Для решения конкретного вопроса или первой проработки проблемы\n\n"
        f"📅 1 день консультации — 5 999 ₽\n"
        f"Полный день поддержки, когда можешь писать в любое время\n\n"
        f"📆 Неделя консультации — 9 999 ₽\n"
        f"Неделя непрерывной работы над ситуацией (самый популярный вариант)\n\n"
        f"🗓️ Месяц консультации — 25 999 ₽\n"
        f"Глубокая проработка с максимальной выгодой\n\n"
        f"🎯 2 месяца консультации — 49 999 ₽\n"
        f"Долгосрочная поддержка для серьезных изменений\n\n"
        f"Выбери вариант, который тебе подходит. После оплаты мы сразу продолжим работу."
    )
    
    keyboard = _tariff_keyboard()
    try:
        if update.message:
            await update.message.reply_text(payment_text, reply_markup=keyboard)
        elif update.effective_chat:
            global application
            if application and application.bot:
                await application.bot.send_message(chat_id=update.effective_chat.id, text=payment_text, reply_markup=keyboard)
            else:
                print("⚠ Ошибка: application не инициализирован для отправки сообщения")
    except Exception as e:
        print(f"⚠ Ошибка при отправке предложения оплаты: {e}")


async def offer_renewal_payment(update: Update, user_id: str):
    """Продающее предложение продления после истечения оплаченного времени"""
    if not update.effective_user or not update.effective_chat:
        return
    username = update.effective_user.first_name or "Пользователь"
    
    renewal_text = (
        f"{username}, твоё оплаченное время консультации подошло к концу.\n\n"
        f"Мы уже проделали часть пути вместе — и важно не останавливаться на полпути. Продолжение работы даёт устойчивый результат: те, кто не бросает консультации после первого этапа, замечают настоящие изменения в жизни.\n\n"
        f"Продли консультацию по тем же выгодным тарифам — я буду снова доступен для тебя сразу после оплаты.\n\n"
        f"⏰ 1 час — 2 999 ₽  |  📅 1 день — 5 999 ₽  |  📆 Неделя — 9 999 ₽  |  🗓️ Месяц — 25 999 ₽  |  🎯 2 месяца — 49 999 ₽\n\n"
        f"Выбери удобный вариант ниже — и мы продолжим."
    )
    
    keyboard = _tariff_keyboard()
    try:
        if update.message:
            await update.message.reply_text(renewal_text, reply_markup=keyboard)
        elif update.effective_chat:
            global application
            if application and application.bot:
                await application.bot.send_message(chat_id=update.effective_chat.id, text=renewal_text, reply_markup=keyboard)
            else:
                print("⚠ Ошибка: application не инициализирован для отправки сообщения")
    except Exception as e:
        print(f"⚠ Ошибка при отправке предложения продления: {e}")


def _ok_headers():
    """Заголовки для ответов проверки — без кэша, быстрый ответ для облака."""
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Content-Type": "application/json; charset=utf-8",
    }


@app.route('/')
def health_check():
    """Главная страница — для проверки доступности приложения облаком."""
    return jsonify({
        "status": "ok",
        "service": "psychologist_assistant_bot",
        "message": "Сервис работает",
        "port": HTTP_PORT
    }), 200, _ok_headers()


@app.route('/ping')
def ping():
    """Минимальный ответ для проверок платформы (Timeweb и др.)."""
    return "ok", 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}


@app.route('/health')
def health():
    """Health check — должен отвечать быстро для прохождения проверки и Configuring web server."""
    return jsonify({
        "status": "healthy",
        "service": "psychologist_assistant_bot",
        "port": HTTP_PORT
    }), 200, _ok_headers()


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
                    # Теплое сообщение после оплаты
                    if application is not None:
                        try:
                            # Получаем event loop из application
                            loop = application.updater._event_loop if hasattr(application.updater, '_event_loop') else None
                            if loop is None:
                                # Если loop еще не создан, создаем новый
                                loop = asyncio.new_event_loop()
                                thread = threading.Thread(target=lambda: loop.run_forever(), daemon=True)
                                thread.start()
                            
                            asyncio.run_coroutine_threadsafe(
                                application.bot.send_message(
                                    chat_id=int(user_id),
                                    text=f"""Спасибо за доверие!

Оплата получена:
Тариф: {description}
Сумма: {out_sum:,.0f} ₽
Номер счета: {invoice_id}

Отлично! Теперь у нас есть время для полноценной работы. Я готов продолжить наш разговор и помочь тебе разобраться в ситуации.

Напиши мне, что тебя беспокоит, и мы начнем работу."""
                                ),
                                loop,
                            )
                        except Exception as send_error:
                            print(f"⚠ Ошибка при отправке сообщения пользователю {user_id}: {send_error}")
                    
                    # Сбрасываем счетчик бесплатных сообщений
                    if user_id in free_messages:
                        del free_messages[user_id]
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


def verify_bot_token() -> bool:
    """Проверка токена через Telegram API. Возвращает True, если токен рабочий."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
            timeout=10
        )
        data = r.json() if r.ok else {}
        if data.get("ok"):
            name = data.get("result", {}).get("username", "?")
            print(f"✓ Токен проверен: бот @{name} подключён к Telegram")
            return True
        print(f"❌ Токен неверный или недоступен: {data.get('description', r.text)}")
        return False
    except Exception as e:
        print(f"❌ Ошибка проверки токена (сеть?): {e}")
        return False


def run_bot():
    """Запуск Telegram бота в отдельном потоке"""
    global application
    import sys
    sys.stdout.flush()
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ ОШИБКА: Токен бота не задан. Бот не может быть запущен.")
        sys.stdout.flush()
        return
    
    print("=" * 60)
    print("Инициализация Telegram бота...")
    print(f"Токен: {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-5:]}")
    sys.stdout.flush()
    
    # Проверяем токен (при ошибке всё равно пробуем запустить polling)
    verify_bot_token()
    sys.stdout.flush()
    
    try:
        print("✓ Создание Application объекта...")
        sys.stdout.flush()
        # Таймауты помогают на нестабильных сетях (в т.ч. в облаке)
        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .build()
        )
        print("✓ Application создан")
        sys.stdout.flush()
        
        print("✓ Добавление обработчиков...")
        sys.stdout.flush()
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("new", new_session_command))
        application.add_handler(CommandHandler("exit", exit_command))
        application.add_handler(CommandHandler("stop", exit_command))
        application.add_handler(CallbackQueryHandler(tariff_callback, pattern=r"^tariff_"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        async def log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Логируем любую ошибку в обработчиках — так видно, почему бот не отвечает."""
            err = context.error
            print(f"❌ Ошибка бота при обработке: {err}", flush=True)
            import traceback
            traceback.print_exc()
            if update and isinstance(update, Update) and update.effective_message:
                try:
                    await update.effective_message.reply_text("Произошла ошибка. Попробуйте ещё раз или /start.")
                except Exception:
                    pass
        application.add_error_handler(log_error)
        
        print("✓ Обработчики добавлены")
        print("✓ Бот инициализирован, начинаю polling...")
        print("=" * 60)
        sys.stdout.flush()
        
        # run_polling блокируется и принимает обновления из Telegram
        print("🚀 Запуск polling (бот в Telegram)...")
        sys.stdout.flush()
        try:
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                close_loop=False,
                stop_signals=None
            )
        finally:
            print("⚠ Polling завершился (перезапуск через 10 сек)...", flush=True)
        
    except KeyboardInterrupt:
        print("⚠ Получен сигнал прерывания")
        sys.stdout.flush()
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА в работе бота: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        # Пытаемся перезапустить через 10 секунд
        print("Попытка перезапуска через 10 секунд...")
        sys.stdout.flush()
        time.sleep(10)
        run_bot()


def run_flask_in_thread():
    """Запуск Flask в фоновом потоке (health check и Robokassa)."""
    app.run(host='0.0.0.0', port=HTTP_PORT, debug=False, use_reloader=False, threaded=True)


def main():
    """Бот в главном потоке (стабильный polling), Flask в фоне для health check."""
    import sys
    sys.stdout.flush()

    # Метка версии: если в логах есть эта строка — запущена правильная сборка (бот в главном потоке).
    # Если видите "Поток Telegram бота запущен" или "Flask на порту" БЕЗ "Flask в фоне" — пересоберите образ и задеплойте заново.
    print(">>> СБОРКА: бот в ГЛАВНОМ потоке, Flask в фоне. <<<")
    print("=" * 60)
    print("Запуск сервиса психолога-ассистента")
    print("=" * 60)
    print(f"HTTP порт: {HTTP_PORT} | Токен: {'есть' if TELEGRAM_BOT_TOKEN else 'НЕТ'} | GigaChat: {'есть' if GIGACHAT_API_KEY else 'нет'}")
    print("=" * 60)
    sys.stdout.flush()

    # 1. Сначала Flask в фоне — чтобы /health и /ping отвечали до проверок облака
    flask_thread = threading.Thread(target=run_flask_in_thread, daemon=False, name="FlaskThread")
    flask_thread.start()
    time.sleep(3)  # даём Flask гарантированно привязаться к порту
    print("✓ Flask запущен В ФОНЕ на 0.0.0.0:{}".format(HTTP_PORT))
    sys.stdout.flush()

    # 2. Бот в главном потоке (обязательно для стабильной работы в Telegram)
    if not TELEGRAM_BOT_TOKEN:
        print("⚠ Токен не задан. Работает только HTTP.")
        sys.stdout.flush()
        flask_thread.join()
        return

    print("✓ Запуск бота в ГЛАВНОМ потоке...")
    sys.stdout.flush()
    run_bot()


if __name__ == "__main__":
    main()
