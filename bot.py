import os
import time
import json
import sqlite3
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
import pytz

# ── КОНФИГ ──────────────────────────────────────────────────
TOKEN = os.getenv('BOT_TOKEN', 'ТВОЙ_ТОКЕН')
PORT = int(os.getenv('PORT', 8080))
DB_NAME = 'pills.db'

# ── ИНИЦИАЛИЗАЦИЯ ─────────────────────────────────────────
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# ── БАЗА ДАННЫХ ────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # Таблица для витаминов
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                pills TEXT DEFAULT '[]'
            )
        ''')
        # Таблица для настроек (часовой пояс)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users_settings (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT DEFAULT 'Europe/Moscow'
            )
        ''')
        conn.commit()

def load_user_pills(user_id):
    with get_db() as conn:
        row = conn.execute('SELECT pills FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if row and row['pills']:
            return json.loads(row['pills'])
        return []

def save_user_pills(user_id, pills):
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO users (user_id, pills) VALUES (?, ?)
        ''', (user_id, json.dumps(pills, ensure_ascii=False)))
        conn.commit()

def get_user_timezone(user_id):
    with get_db() as conn:
        row = conn.execute('SELECT timezone FROM users_settings WHERE user_id = ?', (user_id,)).fetchone()
        if row and row['timezone']:
            return row['timezone']
        return 'Europe/Moscow'  # По умолчанию

def set_user_timezone(user_id, timezone):
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO users_settings (user_id, timezone) VALUES (?, ?)
        ''', (user_id, timezone))
        conn.commit()

# ── КНОПКА ОТКРЫТИЯ ПРИЛОЖЕНИЯ ──────────────────────────
def get_open_btn():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="💊 Открыть трекер",
                web_app=types.WebAppInfo(url=os.getenv('WEBAPP_URL', 'https://pills-app.vercel.app'))
            )
        ]]
    )
    return keyboard

# ── КОМАНДЫ ─────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    pills = load_user_pills(uid)
    if not pills:
        save_user_pills(uid, [])
    await message.answer(
        "💊 Привет! Я помогу тебе отслеживать приём витаминов.\n\n"
        "Нажми кнопку ниже, чтобы открыть трекер и настроить расписание.",
        reply_markup=get_open_btn()
    )

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    uid = message.from_user.id
    save_user_pills(uid, [])
    await message.answer(
        "🔕 Уведомления отключены. Все данные очищены.\n"
        "Чтобы начать снова — нажми /start",
        reply_markup=get_open_btn()
    )

@dp.message()
async def handle_any(message: types.Message):
    await message.answer(
        "Открывай трекер кнопкой ниже 👇",
        reply_markup=get_open_btn()
    )

# ── ОБРАБОТЧИКИ ВЕБХУКА (для мини-приложения) ──────────────
async def handle_pills(request):
    """Сохранение витаминов"""
    data = await request.json()
    uid = data.get('uid')
    pills = data.get('pills', [])
    
    if not uid:
        return web.json_response({'error': 'No uid'}, status=400)
    
    save_user_pills(int(uid), pills)
    return web.json_response({'status': 'ok'})

async def handle_get_pills(request):
    """Получение витаминов"""
    uid = request.query.get('uid')
    if not uid:
        return web.json_response({'error': 'No uid'}, status=400)
    
    pills = load_user_pills(int(uid))
    return web.json_response({'pills': pills})

async def handle_set_timezone(request):
    """Сохранение часового пояса пользователя"""
    data = await request.json()
    uid = data.get('uid')
    timezone = data.get('timezone', 'Europe/Moscow')
    
    if not uid:
        return web.json_response({'error': 'No uid'}, status=400)
    
    set_user_timezone(int(uid), timezone)
    return web.json_response({'status': 'ok'})

# ── РАССЫЛКА УВЕДОМЛЕНИЙ ────────────────────────────────────
async def send_reminders():
    """Проверяет расписание и отправляет уведомления с учётом часового пояса пользователя"""
    now_utc = datetime.now(pytz.UTC)
    
    with get_db() as conn:
        rows = conn.execute('SELECT user_id, pills FROM users').fetchall()
    
    for row in rows:
        try:
            uid = row['user_id']
            
            # Получаем часовой пояс пользователя
            user_tz_str = get_user_timezone(uid)
            user_tz = pytz.timezone(user_tz_str)
            
            # Текущее время в часовом поясе пользователя
            now_local = now_utc.astimezone(user_tz)
            now_str = now_local.strftime("%H:%M")
            
            pills = json.loads(row['pills'])
            
            # Находим витамины, которые нужно принять сейчас
            due_pills = []
            for p in pills:
                if p.get('takeTime') == now_str and not p.get('archived', False):
                    # Проверяем, не принял ли уже сегодня
                    checked_today = p.get('checked', [])
                    # Если есть хотя бы один неотмеченный приём
                    if any(not c for c in checked_today):
                        due_pills.append(p['name'])
            
            if due_pills:
                text = "💊 Время принять витамины!\n\n" + "\n".join(f"• {n}" for n in due_pills)
                await bot.send_message(
                    chat_id=uid,
                    text=text,
                    reply_markup=get_open_btn()
                )
                print(f"✅ Отправлено уведомление пользователю {uid}: {due_pills} в {now_str} ({user_tz_str})")
        except Exception as e:
            print(f"❌ Ошибка отправки {uid}: {e}")

# ── WEB СЕРВЕР ──────────────────────────────────────────────
async def handle_webhook(request):
    """Обработка запросов от Telegram"""
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response(status=200)

async def health_check(request):
    return web.Response(text="OK")

# ── MAIN ────────────────────────────────────────────────────
async def main():
    init_db()
    
    # Запускаем планировщик — каждую минуту
    scheduler.add_job(send_reminders, CronTrigger(minute="*"))
    scheduler.start()
    
    # Настраиваем веб-сервер
    app = web.Application()
    app.router.add_post(f'/{TOKEN}', handle_webhook)
    app.router.add_get('/pills', handle_get_pills)
    app.router.add_post('/pills', handle_pills)
    app.router.add_post('/set_timezone', handle_set_timezone)
    app.router.add_get('/health', health_check)
    
    # Запускаем сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Сервер запущен на порту {PORT}")
    
    # Устанавливаем вебхук
    webhook_url = f"https://{os.getenv('RAILWAY_STATIC_URL', 'localhost')}/{TOKEN}"
    await bot.set_webhook(webhook_url)
    print(f"✅ Вебхук установлен: {webhook_url}")
    print(f"✅ Бот запущен, уведомления привязаны к часовому поясу пользователя")
    
    await asyncio.Event().wait()  # Бесконечное ожидание

if __name__ == "__main__":
    asyncio.run(main())
