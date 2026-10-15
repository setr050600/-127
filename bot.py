import asyncio
import os
import sqlite3
import calendar
import threading
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiohttp import web

TOKEN = os.getenv("TOKEN", "8970467114:AAF6gGr4AIaQtd529KaMNJxYCJiV31FbWLw")
ADMIN_IDS = [884058025]  # Укажите ваш Telegram ID

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- База данных ---
def init_db():
    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            lesson_num INTEGER,
            time_slot TEXT,
            subject TEXT,
            teacher TEXT,
            room TEXT
        )
    """
    )
    conn.commit()
    conn.close()

init_db()

# --- Вспомогательные функции дат ---
MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

DAYS_RU = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье"
}

def format_date_with_weekday(date_str: str) -> str:
    """Форматирует дату YYYY-MM-DD в 'День_недели DD.MM' (например, 'Вторник 13.10')"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = DAYS_RU[dt.weekday()]
    return f"{weekday} {dt.strftime('%d.%m')}"

# --- Главное меню ---
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📅 Расписание на сегодня"), KeyboardButton(text="📆 Расписание на завтра")],
        [KeyboardButton(text="🗓 Открыть календарь")],
        [KeyboardButton(text="⚙️ Админ-панель")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- Генератор Инлайн-Календаря ---
def generate_calendar_keyboard(year: int = None, month: int = None) -> InlineKeyboardMarkup:
    now = datetime.now()
    if not year:
        year = now.year
    if not month:
        month = now.month

    keyboard = []
    
    header_button = InlineKeyboardButton(
        text=f"📅 {MONTHS_RU[month]} {year}",
        callback_data="ignore"
    )
    keyboard.append([header_button])

    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(text=day, callback_data="ignore") for day in week_days])

    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal_date:{date_str}"))
        keyboard.append(row)

    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1

    nav_row = [
        InlineKeyboardButton(text="⬅️", callback_data=f"cal_nav:{prev_y}:{prev_m}"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="cal_close"),
        InlineKeyboardButton(text="➡️", callback_data=f"cal_nav:{next_y}:{next_m}")
    ]
    keyboard.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Получение расписания из БД ---
def get_schedule_by_date(date_str: str) -> str:
    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT lesson_num, time_slot, subject, teacher, room FROM schedule WHERE date = ? ORDER BY lesson_num",
        (date_str,)
    )
    rows = cursor.fetchall()
    conn.close()

    formatted_header_date = format_date_with_weekday(date_str)

    if not rows:
        return f"📌 **Расписание на {formatted_header_date}:**\n\nУроков нет 🎉"

    text = f"📌 **Расписание на {formatted_header_date}:**\n\n"
    for row in rows:
        num, time_slot, subject, teacher, room = row
        text += f"**{num}. {time_slot}** — {subject}\n"
        if teacher:
            text += f"   👤 Учитель: {teacher}\n"
        if room:
            text += f"   🚪 Каб.: {room}\n"
        text += "\n"
    return text

# --- FSM Мастер ---
class AddLessonWizard(StatesGroup):
    date = State()
    lesson_num = State()
    time_slot = State()
    subject = State()
    teacher = State()
    room = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я школьный бот с расписанием 🏫\nВыбери нужный раздел в меню ниже:",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "📅 Расписание на сегодня")
async def today_schedule(message: types.Message):
    today = datetime.now().strftime("%Y-%m-%d")
    await message.answer(get_schedule_by_date(today), parse_mode="Markdown")

@dp.message(F.text == "📆 Расписание на завтра")
async def tomorrow_schedule(message: types.Message):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    await message.answer(get_schedule_by_date(tomorrow), parse_mode="Markdown")

@dp.message(F.text == "🗓 Открыть календарь")
async def open_calendar(message: types.Message):
    await message.answer(
        "Выберите интересующий день в календаре:",
        reply_markup=generate_calendar_keyboard()
    )

@dp.callback_query(F.data.startswith("cal_date:"))
async def process_calendar_date(callback: types.CallbackQuery):
    date_str = callback.data.split(":")[1]
    schedule_text = get_schedule_by_date(date_str)
    await callback.message.answer(schedule_text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("cal_nav:"))
async def process_calendar_nav(callback: types.CallbackQuery):
    _, year, month = callback.data.split(":")
    await callback.message.edit_reply_markup(
        reply_markup=generate_calendar_keyboard(int(year), int(month))
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_close")
async def process_calendar_close(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "ignore")
async def process_ignore(callback: types.CallbackQuery):
    await callback.answer()

@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return
    await message.answer(
        "Команды администратора:\n➕ `/set` — Добавить урок по шагам",
        parse_mode="Markdown"
    )

@dp.message(Command("set"))
async def start_wizard(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен.")
        return
    await state.set_state(AddLessonWizard.date)
    await message.answer("Введи дату урока в формате **YYYY-MM-DD** (например, `2026-10-13`):", parse_mode="Markdown")

@dp.message(AddLessonWizard.date)
async def process_wizard_date(message: types.Message, state: FSMContext):
    await state.update_data(date=message.text.strip())
    await state.set_state(AddLessonWizard.lesson_num)
    await message.answer("Введи номер урока (цифра, например: `1`):")

@dp.message(AddLessonWizard.lesson_num)
async def process_wizard_num(message: types.Message, state: FSMContext):
    await state.update_data(lesson_num=int(message.text.strip()))
    await state.set_state(AddLessonWizard.time_slot)
    await message.answer("Введи время урока (например, `08:30 - 09:15`):")

@dp.message(AddLessonWizard.time_slot)
async def process_wizard_time(message: types.Message, state: FSMContext):
    await state.update_data(time_slot=message.text.strip())
    await state.set_state(AddLessonWizard.subject)
    await message.answer("Введи название предмета (например, `Математика`):")

@dp.message(AddLessonWizard.subject)
async def process_wizard_subject(message: types.Message, state: FSMContext):
    await state.update_data(subject=message.text.strip())
    await state.set_state(AddLessonWizard.teacher)
    await message.answer("Введи имя учителя (или пропусти, отправив `-`):")

@dp.message(AddLessonWizard.teacher)
async def process_wizard_teacher(message: types.Message, state: FSMContext):
    teacher = message.text.strip()
    if teacher == "-":
        teacher = ""
    await state.update_data(teacher=teacher)
    await state.set_state(AddLessonWizard.room)
    await message.answer("Введи кабинет (или пропусти, отправив `-`):")

@dp.message(AddLessonWizard.room)
async def process_wizard_room(message: types.Message, state: FSMContext):
    room = message.text.strip()
    if room == "-":
        room = ""
    
    data = await state.get_data()
    
    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO schedule (date, lesson_num, time_slot, subject, teacher, room) VALUES (?, ?, ?, ?, ?, ?)",
        (data["date"], data["lesson_num"], data["time_slot"], data["subject"], data["teacher"], room)
    )
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(f"✅ Урок по предмету **{data['subject']}** на дату `{data['date']}` успешно добавлен!", parse_mode="Markdown")

# --- Веб-сервер в потоке и запуск бота ---
def run_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)

async def main():
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()
    print("Веб-сервер запущен в фоне...")

    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
