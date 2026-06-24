import os
import asyncio
import json
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL")
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

USERS_FILE = "users.json"
users_data = {}


def load_users():
    global users_data
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r") as f:
                users_data = json.load(f)
    except Exception:
        users_data = {}


def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(users_data, f, ensure_ascii=False, indent=2)


def get_open_btn():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💊 Открыть трекер",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])


# ── HTTP API ──────────────────────────────────────────────────────────────────

async def handle_get_pills(request):
    """GET /pills?uid=123456 — получить данные пользователя"""
    uid = request.rel_url.query.get("uid")
    if not uid:
        return web.json_response({"error": "no uid"}, status=400)
    data = users_data.get(uid, {})
    pills = data.get("pills", [])
    return web.json_response({"pills": pills})


async def handle_save_pills(request):
    """POST /pills — сохранить данные пользователя"""
    try:
        body = await request.json()
        uid = str(body.get("uid"))
        pills = body.get("pills", [])
        if not uid:
            return web.json_response({"error": "no uid"}, status=400)
        if uid not in users_data:
            users_data[uid] = {}
        users_data[uid]["pills"] = pills
        # Автоматически обновляем расписание уведомлений
        schedule = {}
        for p in pills:
            t = p.get("takeTime", "")
            if t:
                if t not in schedule:
                    schedule[t] = []
                schedule[t].append(p.get("name", ""))
        users_data[uid]["schedules"] = schedule
        save_users()
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_health(request):
    return web.json_response({"status": "ok"})


def make_app():
    app = web.Application()
    # CORS для GitHub Pages
    async def cors_middleware(app, handler):
        async def middleware(request):
            if request.method == "OPTIONS":
                resp = web.Response()
                resp.headers["Access-Control-Allow-Origin"] = "*"
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
                return resp
            resp = await handler(request)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return resp
        return middleware
    app.middlewares.append(cors_middleware)
    app.router.add_get("/", handle_health)
    app.router.add_get("/pills", handle_get_pills)
    app.router.add_post("/pills", handle_save_pills)
    app.router.add_route("OPTIONS", "/pills", handle_health)
    return app


# ── BOT HANDLERS ─────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_data:
        users_data[uid] = {"pills": [], "schedules": {}, "name": message.from_user.first_name}
        save_users()
    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(
            text="💊 Витамины",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    )
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Открывай трекер кнопкой ниже — все данные сохраняются автоматически, "
        f"уведомления настраиваются по расписанию витаминов.",
        reply_markup=get_open_btn()
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    data = users_data.get(uid, {})
    pills = data.get("pills", [])
    schedules = data.get("schedules", {})
    if not pills:
        await message.answer("Витамины не добавлены. Открой трекер.", reply_markup=get_open_btn())
        return
    lines = [f"💊 Витаминов: {len(pills)}\n", "📋 Расписание уведомлений:"]
    for t, names in sorted(schedules.items()):
        lines.append(f"⏰ {t} — {', '.join(names)}")
    await message.answer("\n".join(lines), reply_markup=get_open_btn())


@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    uid = str(message.from_user.id)
    if uid in users_data:
        users_data[uid]["schedules"] = {}
        save_users()
    await message.answer("🔕 Уведомления отключены.\nЧтобы включить — открой трекер.", reply_markup=get_open_btn())


@dp.message()
async def handle_any(message: types.Message):
    await message.answer("Открывай трекер кнопкой ниже 👇", reply_markup=get_open_btn())


# ── REMINDERS ────────────────────────────────────────────────────────────────

async def send_reminders():
    now = datetime.now().strftime("%H:%M")
    for uid, data in list(users_data.items()):
        pills_now = data.get("schedules", {}).get(now, [])
        if pills_now:
            try:
                text = "💊 Время принять витамины!\n\n" + "\n".join(f"• {n}" for n in pills_now)
                await bot.send_message(
                    chat_id=int(uid),
                    text=text,
                    reply_markup=get_open_btn()
                )
            except Exception as e:
                print(f"Ошибка отправки {uid}: {e}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    load_users()
    scheduler.add_job(send_reminders, "cron", minute="*")
    scheduler.start()

    app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"HTTP сервер запущен на порту {PORT}")

    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
