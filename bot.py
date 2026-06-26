import os
import json
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
import pytz
import urllib.request
import urllib.error

TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 8080))
TURSO_URL = os.getenv('TURSO_URL')
TURSO_TOKEN = os.getenv('TURSO_TOKEN')

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# ── TURSO HTTP API ───────────────────────────────────────────

def turso_execute(sql, args=None):
    url = f"{TURSO_URL}/v2/pipeline"
    headers = {
        'Authorization': f'Bearer {TURSO_TOKEN}',
        'Content-Type': 'application/json'
    }
    body = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": [{"type": "text", "value": str(a)} for a in (args or [])]
                }
            },
            {"type": "close"}
        ]
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def init_db():
    turso_execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        pills TEXT DEFAULT "[]"
    )''')
    turso_execute('''CREATE TABLE IF NOT EXISTS users_settings (
        user_id TEXT PRIMARY KEY,
        timezone TEXT DEFAULT "Europe/Moscow"
    )''')

def load_user_pills(user_id):
    try:
        result = turso_execute(
            'SELECT pills FROM users WHERE user_id = ?',
            [str(user_id)]
        )
        rows = result['results'][0]['response']['result']['rows']
        if rows:
            return json.loads(rows[0][0]['value'])
    except Exception as e:
        print(f'load_user_pills error: {e}')
    return []

def save_user_pills(user_id, pills):
    turso_execute(
        'INSERT OR REPLACE INTO users (user_id, pills) VALUES (?, ?)',
        [str(user_id), json.dumps(pills, ensure_ascii=False)]
    )

def get_user_timezone(user_id):
    try:
        result = turso_execute(
            'SELECT timezone FROM users_settings WHERE user_id = ?',
            [str(user_id)]
        )
        rows = result['results'][0]['response']['result']['rows']
        if rows:
            return rows[0][0]['value']
    except Exception as e:
        print(f'get_user_timezone error: {e}')
    return 'Europe/Moscow'

def set_user_timezone(user_id, timezone):
    turso_execute(
        'INSERT OR REPLACE INTO users_settings (user_id, timezone) VALUES (?, ?)',
        [str(user_id), timezone]
    )

def safe_uid(uid_str):
    if isinstance(uid_str, str) and uid_str.startswith('local_'):
        return uid_str
    try:
        return str(int(uid_str))
    except:
        return str(uid_str)

# ── BOT ─────────────────────────────────────────────────────

def get_open_btn():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="💊 Открыть трекер",
                web_app=types.WebAppInfo(
                    url=os.getenv('WEBAPP_URL', 'https://i1o606.github.io/Pills-app')
                    + '?v=' + str(int(datetime.now().timestamp()))
                )
            )
        ]]
    )
    return keyboard

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = str(message.from_user.id)
    pills = load_user_pills(uid)
    if not pills:
        save_user_pills(uid, [])
    await message.answer(
        "💊 Привет! Я помогу тебе отслеживать приём витаминов.\n\nНажми кнопку ниже, чтобы открыть трекер.",
        reply_markup=get_open_btn()
    )

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    uid = str(message.from_user.id)
    save_user_pills(uid, [])
    await message.answer("🔕 Данные очищены.", reply_markup=get_open_btn())

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    pills = load_user_pills(uid)
    if not pills:
        await message.answer("💊 Таблеток нет. Добавь через трекер.", reply_markup=get_open_btn())
        return
    lines = [f"💊 Твои таблетки ({len(pills)} шт.):\n"]
    for p in pills:
        if p.get('archived'):
            continue
        checked = p.get('checked', [])
        done = all(checked)
        status = "✅" if done else "⬜"
        lines.append(f"{status} {p.get('emoji','')} {p['name']} — {p.get('takeTime','?')}")
    lines.append(f"\nUID в базе: {uid}")
    await message.answer("\n".join(lines), reply_markup=get_open_btn())

@dp.message()
async def handle_any(message: types.Message):
    await message.answer("Открывай трекер кнопкой ниже 👇", reply_markup=get_open_btn())

# ── HTTP API ─────────────────────────────────────────────────

async def handle_get_pills(request):
    uid = request.query.get('uid')
    if not uid:
        return web.json_response({'error': 'No uid'}, status=400)
    pills = load_user_pills(safe_uid(uid))
    return web.json_response({'pills': pills})

async def handle_post_pills(request):
    data = await request.json()
    uid = data.get('uid')
    pills = data.get('pills', [])
    if not uid:
        return web.json_response({'error': 'No uid'}, status=400)
    save_user_pills(safe_uid(uid), pills)
    return web.json_response({'status': 'ok'})

async def handle_set_timezone(request):
    data = await request.json()
    uid = data.get('uid')
    timezone = data.get('timezone', 'Europe/Moscow')
    if not uid:
        return web.json_response({'error': 'No uid'}, status=400)
    set_user_timezone(safe_uid(uid), timezone)
    return web.json_response({'status': 'ok'})

async def handle_webhook(request):
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response(status=200)

async def health_check(request):
    return web.Response(text="OK")

# ── УВЕДОМЛЕНИЯ ──────────────────────────────────────────────


                                                                
async def send_reminders():
    now_utc = datetime.now(pytz.UTC)
    print(f"🕐 send_reminders запущен: {now_utc.strftime('%H:%M:%S')} UTC")
    try:
        result = turso_execute('SELECT user_id, pills FROM users')
        rows = result['results'][0]['response']['result']['rows']
        print(f"📋 UIDs в базе: {[r[0]['value'] for r in rows]}")
        print(f"👥 Пользователей в БД: {len(rows)}")
    except Exception as e:
        print(f'send_reminders fetch error: {e}')
        return

    for row in rows:
        try:
            uid = row[0]['value']
            if uid.startswith('local_'):
                continue
            user_tz = pytz.timezone(get_user_timezone(uid))
            now_local = now_utc.astimezone(user_tz)
            now_str = now_local.strftime("%H:%M")
            print(f"👤 uid={uid} tz={user_tz} now_local={now_str}")
            raw_val = row[1]['value']
            print(f"📦 raw_val type={type(raw_val)} val={raw_val[:100]}")
            pills = json.loads(raw_val)
            print(f"💊 Таблеток: {len(pills)}")
            for p in pills:
                print(f"  - {p['name']} takeTime={p.get('takeTime')} checked={p.get('checked')} archived={p.get('archived')}")
            due = [p['name'] for p in pills
                   if p.get('takeTime') == now_str
                   and not p.get('archived', False)
                   and any(not c for c in p.get('checked', []))]
            if due:
                text = "💊 Время принять витамины!\n\n" + "\n".join(f"• {n}" for n in due)
                await bot.send_message(chat_id=int(uid), text=text, reply_markup=get_open_btn())
                print(f"✅ Уведомление {uid}: {due}")
            else:
                print(f"⏭ Нет подходящих таблеток для {uid} в {now_str}")
        except Exception as e:
            print(f"❌ Ошибка {uid}: {e}")

# ── CORS ─────────────────────────────────────────────────────

@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        response = web.Response()
    else:
        response = await handler(request)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# ── MAIN ─────────────────────────────────────────────────────

async def main():
    init_db()
    scheduler.add_job(send_reminders, CronTrigger(minute="*"))
    scheduler.start()

    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post(f'/{TOKEN}', handle_webhook)
    app.router.add_get('/pills', handle_get_pills)
    app.router.add_post('/pills', handle_post_pills)
    app.router.add_post('/set_timezone', handle_set_timezone)
    app.router.add_get('/health', health_check)
    app.router.add_route('OPTIONS', '/pills', lambda r: web.Response())
    app.router.add_route('OPTIONS', '/set_timezone', lambda r: web.Response())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Сервер запущен на порту {PORT}")

    webhook_url = f"https://{os.getenv('RAILWAY_STATIC_URL')}/{TOKEN}"
    await bot.set_webhook(webhook_url)
    print(f"✅ Вебхук: {webhook_url}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
