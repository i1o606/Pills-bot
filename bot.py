import os
import asyncio
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL")

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
        json.dump(users_data, f, ensure_ascii=False)


def get_open_btn():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💊 Открыть трекер",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users_data:
        users_data[uid] = {"schedules": {}, "name": message.from_user.first_name}
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
        f"Открывай трекер кнопкой ниже.\n"
        f"Добавь витамины — бот автоматически получит расписание и будет напоминать.",
        reply_markup=get_open_btn()
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    uid = str(message.from_user.id)
    schedules = users_data.get(uid, {}).get("schedules", {})
    if not schedules:
        await message.answer(
            "Расписание не синхронизировано.\nОткрой трекер и добавь витамины.",
            reply_markup=get_open_btn()
        )
        return
    lines = ["📋 Твоё расписание:\n"]
    for t, names in sorted(schedules.items()):
        lines.append(f"⏰ {t} — {', '.join(names)}")
    await message.answer("\n".join(lines), reply_markup=get_open_btn())


@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    uid = str(message.from_user.id)
    if uid in users_data:
        users_data[uid]["schedules"] = {}
        save_users()
    await message.answer(
        "🔕 Уведомления отключены.\nЧтобы включить — открой трекер.",
        reply_markup=get_open_btn()
    )


@dp.message()
async def handle_message(message: types.Message):
    uid = str(message.from_user.id)
    text = (message.text or "").strip()

    if text.startswith("SYNC:"):
        try:
            payload = json.loads(text[5:])
            if uid not in users_data:
                users_data[uid] = {"name": message.from_user.first_name}
            users_data[uid]["schedules"] = payload
            save_users()
            lines = ["✅ Расписание синхронизировано!\n"]
            for t, names in sorted(payload.items()):
                lines.append(f"⏰ {t} — {', '.join(names)}")
            lines.append("\nБуду напоминать автоматически 👍")
            await message.answer("\n".join(lines), reply_markup=get_open_btn())
        except Exception:
            await message.answer("Ошибка синхронизации. Попробуй ещё раз.", reply_markup=get_open_btn())
    else:
        await message.answer(
            "Открывай трекер кнопкой ниже 👇",
            reply_markup=get_open_btn()
        )


async def send_reminders():
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    for uid, data in list(users_data.items()):
        pills_now = data.get("schedules", {}).get(now, [])
        if pills_now:
            try:
                await bot.send_message(
                    chat_id=int(uid),
                    text=f"💊 Время принять витамины!\n\n" + "\n".join(f"• {n}" for n in pills_now),
                    reply_markup=get_open_btn()
                )
            except Exception as e:
                print(f"Ошибка отправки {uid}: {e}")


async def main():
    load_users()
    scheduler.add_job(send_reminders, "cron", minute="*")
    scheduler.start()
    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
