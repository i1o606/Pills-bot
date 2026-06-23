import os
import asyncio
from datetime import datetime, time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, MenuButtonWebApp
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import json

TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Хранилище пользователей и их времени уведомлений
# Формат: {user_id: {"times": ["08:00", "21:00"], "name": "Иван"}}
users_data = {}
USERS_FILE = "users.json"

def load_users():
    global users_data
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r") as f:
                users_data = json.load(f)
    except:
        users_data = {}

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(users_data, f)

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
        users_data[uid] = {"times": ["08:00"], "name": message.from_user.first_name}
        save_users()

    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(text="💊 Витамины", web_app=WebAppInfo(url=WEBAPP_URL))
    )

    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Я буду напоминать тебе о витаминах.\n\n"
        f"По умолчанию напоминание в *08:00*.\n"
        f"Чтобы изменить — напиши /remind\n\n"
        f"Открывай трекер кнопкой ниже 👇",
        parse_mode="Markdown",
        reply_markup=get_open_btn()
    )

@dp.message(Command("remind"))
async def cmd_remind(message: types.Message):
    await message.answer(
        "Напиши время напоминания в формате *ЧЧ:ММ*\n"
        "Можно несколько через запятую:\n\n"
        "Пример: `08:00` или `08:00, 13:00, 21:00`",
        parse_mode="Markdown"
    )

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    uid = str(message.from_user.id)
    if uid in users_data:
        users_data[uid]["times"] = []
        save_users()
    await message.answer("🔕 Напоминания отключены. Включить снова — /remind")

@dp.message(Command("open"))
async def cmd_open(message: types.Message):
    await message.answer("Открывай 👇", reply_markup=get_open_btn())

@dp.message()
async def handle_time(message: types.Message):
    uid = str(message.from_user.id)
    text = message.text.strip()

    # Парсим время
    parts = [p.strip() for p in text.split(",")]
    times = []
    for part in parts:
        try:
            t = datetime.strptime(part, "%H:%M")
            times.append(part)
        except:
            pass

    if times:
        if uid not in users_data:
            users_data[uid] = {"name": message.from_user.first_name}
        users_data[uid]["times"] = times
        save_users()
        times_str = ", ".join(times)
        await message.answer(
            f"✅ Напоминания установлены: *{times_str}*\n\n"
            f"Отключить — /stop",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "Не понял формат. Напиши время так: `08:00` или `08:00, 21:00`",
            parse_mode="Markdown",
            reply_markup=get_open_btn()
        )

async def send_reminders():
    now = datetime.now().strftime("%H:%M")
    for uid, data in users_data.items():
        if now in data.get("times", []):
            try:
                await bot.send_message(
                    chat_id=int(uid),
                    text="💊 Время принять витамины!",
                    reply_markup=get_open_btn()
                )
            except Exception as e:
                print(f"Не удалось отправить {uid}: {e}")

async def main():
    load_users()
    scheduler.add_job(send_reminders, "cron", minute="*")
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
