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
    
    # Таблица расписания
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            class_name TEXT,
            lesson_num INTEGER,
            time_slot TEXT,
            subject TEXT,
            teacher TEXT,
            room TEXT
        )
    """
    )
    
    # Таблица базы учителей
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT UNIQUE,
            teacher_name TEXT
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

def format_date_display(date_str: str) -> str:
    """Форматирует дату YYYY-MM-DD из БД в 'День_недели DD.MM.YY'"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    weekday = DAYS_RU[dt.weekday()]
    return f"{weekday} {dt.strftime('%d.%m.%y')}"

# --- Клавиатуры ---
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📅 Посмотреть расписание"), KeyboardButton(text="⚙️ Админ-панель")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# Список популярных классов (можно дополнять или брать из БД)
CLASSES_LIST = ["5А", "5Б", "6А", "7А", "8А", "9А", "10А", "11А"]

def get_classes_keyboard(action_prefix="class"):
    kb = []
    row = []
    for c in CLASSES_LIST:
        row.append(InlineKeyboardButton(text=c, callback_data=f"{action_prefix}:{c}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- Генератор Инлайн-Календаря ---
def generate_calendar_keyboard(class_name: str, year: int = None, month: int = None) -> InlineKeyboardMarkup:
    now = datetime.now()
    if not year:
        year = now.year
    if not month:
        month = now.month

    keyboard = []
    
    header_button = InlineKeyboardButton(
        text=f"📅 {MONTHS_RU[month]} {year} ({class_name})",
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
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal_date:{class_name}:{date_str}"))
        keyboard.append(row)

    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1

    nav_row = [
        InlineKeyboardButton(text="⬅️", callback_data=f"cal_nav:{class_name}:{prev_y}:{prev_m}"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="cal_close"),
        InlineKeyboardButton(text="➡️", callback_data=f"cal_nav:{class_name}:{next_y}:{next_m}")
    ]
    keyboard.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Работа с базой учителей ---
def get_teacher_for_subject(subject: str) -> str:
    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    cursor.execute("SELECT teacher_name FROM teachers WHERE LOWER(subject) = LOWER(?)", (subject.strip(),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else ""

# --- Получение расписания из БД ---
def get_schedule_by_date_and_class(date_str: str, class_name: str) -> str:
    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT lesson_num, time_slot, subject, teacher, room FROM schedule WHERE date = ? AND class_name = ? ORDER BY lesson_num",
        (date_str, class_name)
    )
    rows = cursor.fetchall()
    conn.close()

    formatted_header_date = format_date_display(date_str)

    if not rows:
        return f"📌 **Расписание для класса {class_name} на {formatted_header_date}:**\n\nУроков нет 🎉"

    text = f"📌 **Расписание для класса {class_name} на {formatted_header_date}:**\n\n"
    for row in rows:
        num, time_slot, subject, teacher, room = row
        text += f"**{num}. {time_slot}** — {subject}\n"
        if teacher:
            text += f"   👤 Учитель: {teacher}\n"
        if room:
            text += f"   🚪 Каб.: {room}\n"
        text += "\n"
    return text

# --- FSM Мастер для добавления расписания ---
class AddScheduleWizard(StatesGroup):
    class_name = State()
    date = State()
    lessons_text = State()

# --- FSM Мастер для добавления учителя ---
class AddTeacherWizard(StatesGroup):
    subject = State()
    teacher_name = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я школьный бот с расписанием 🏫\nВыбери нужный раздел в меню ниже:",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "📅 Посмотреть расписание")
async def choose_class_for_schedule(message: types.Message):
    await message.answer("Сначала выберите ваш класс:", reply_markup=get_classes_keyboard("user_class"))

@dp.callback_query(F.data.startswith("user_class:"))
async def process_user_class(callback: types.CallbackQuery):
    class_name = callback.data.split(":")[1]
    await callback.message.edit_text(
        f"Класс выбран: **{class_name}**.\nТеперь выберите день в календаре:",
        reply_markup=generate_calendar_keyboard(class_name),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("cal_date:"))
async def process_calendar_date(callback: types.CallbackQuery):
    _, class_name, date_str = callback.data.split(":")
    schedule_text = get_schedule_by_date_and_class(date_str, class_name)
    await callback.message.answer(schedule_text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("cal_nav:"))
async def process_calendar_nav(callback: types.CallbackQuery):
    _, class_name, year, month = callback.data.split(":")
    await callback.message.edit_reply_markup(
        reply_markup=generate_calendar_keyboard(class_name, int(year), int(month))
    )
    await callback.answer()

@dp.callback_query(F.data == "cal_close")
async def process_calendar_close(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "ignore")
async def process_ignore(callback: types.CallbackQuery):
    await callback.answer()

# --- Админ-панель ---
@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return
    await message.answer(
        "Панель администратора:\n\n"
        "➕ `/set` — Добавить расписание для класса\n"
        "👩‍🏫 `/addteacher` — Добавить учителя для предмета в базу\n"
        "📋 `/teachers` — Посмотреть список сохраненных учителей",
        parse_mode="Markdown"
    )

@dp.message(Command("teachers"))
async def list_teachers(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    cursor.execute("SELECT subject, teacher_name FROM teachers")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("📚 База учителей пуста. Добавьте учителей через `/addteacher`", parse_mode="Markdown")
        return

    text = "📚 **База учителей:**\n\n"
    for subj, teach in rows:
        text += f"• **{subj}** — {teach}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("addteacher"))
async def start_teacher_wizard(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AddTeacherWizard.subject)
    await message.answer("Введите название предмета (например, `Математика`):")

@dp.message(AddTeacherWizard.subject)
async def process_teacher_subject(message: types.Message, state: FSMContext):
    await state.update_data(subject=message.text.strip())
    await state.set_state(AddTeacherWizard.teacher_name)
    await message.answer("Введите ФИО учителя для этого предмета (например, `Иванов И.И.`):")

@dp.message(AddTeacherWizard.teacher_name)
async def process_teacher_name(message: types.Message, state: FSMContext):
    teacher_name = message.text.strip()
    data = await state.get_data()
    subject = data["subject"]

    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO teachers (subject, teacher_name) VALUES (?, ?)",
        (subject, teacher_name)
    )
    conn.commit()
    conn.close()
    await state.clear()

    await message.answer(f"✅ Учитель **{teacher_name}** успешно закреплен за предметом **{subject}**!", parse_mode="Markdown")

@dp.message(Command("set"))
async def start_wizard(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен.")
        return
    await message.answer("Выберите класс, для которого хотите добавить расписание:", reply_markup=get_classes_keyboard("admin_class"))

@dp.callback_query(F.data.startswith("admin_class:"))
async def process_admin_class(callback: types.CallbackQuery, state: FSMContext):
    class_name = callback.data.split(":")[1]
    await state.update_data(class_name=class_name)
    await state.message.edit_text(f"Выбран класс: **{class_name}**.\nТеперь введите дату в формате **ДД.ММ.ГГ** (например, `17.10.26`):", parse_mode="Markdown")
    await state.set_state(AddScheduleWizard.date)
    await callback.answer()

@dp.message(AddScheduleWizard.date)
async def process_wizard_date(message: types.Message, state: FSMContext):
    raw_date = message.text.strip()
    try:
        dt = datetime.strptime(raw_date, "%d.%m.%y")
        db_date = dt.strftime("%Y-%m-%d")
    except ValueError:
        await message.answer("❌ Неверный формат даты! Введите в формате **ДД.ММ.ГГ** (например, `17.10.26`):", parse_mode="Markdown")
        return

    await state.update_data(date=db_date)
    await state.set_state(AddScheduleWizard.lessons_text)
    await message.answer(
        "Теперь отправь список уроков **одним сообщением**.\n"
        "Формат строки:\n"
        "`Номер | Время | Предмет | Кабинет` *(учитель подтянется сам из базы!)*\n"
        "Или можете указать учителя вручную:\n"
        "`Номер | Время | Предмет | Учитель | Кабинет`\n\n"
        "Пример:\n"
        "1 | 08:30-09:15 | Математика | 302\n"
        "2 | 09:25-10:10 | Физика | Петров П.П. | 405",
        parse_mode="Markdown"
    )

@dp.message(AddScheduleWizard.lessons_text)
async def process_wizard_lessons(message: types.Message, state: FSMContext):
    data = await state.get_data()
    class_name = data["class_name"]
    db_date = data["date"]
    
    lines = message.text.strip().split("\n")
    conn = sqlite3.connect("schedule.db")
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM schedule WHERE date = ? AND class_name = ?", (db_date, class_name))

    added_count = 0
    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            try:
                lesson_num = int(parts[0])
                time_slot = parts[1]
                subject = parts[2]
                
                # Проверяем, указан ли учитель или подтягивать из базы
                if len(parts) >= 5:
                    teacher = parts[3]
                    room = parts[4]
                elif len(parts) == 4:
                    # Может быть либо учитель, либо кабинет. Проверим базу учителей.
                    auto_teacher = get_teacher_for_subject(subject)
                    if auto_teacher:
                        teacher = auto_teacher
                        room = parts[3]
                    else:
                        teacher = parts[3]
                        room = ""
                else:
                    teacher = get_teacher_for_subject(subject)
                    room = ""

                cursor.execute(
                    "INSERT INTO schedule (date, class_name, lesson_num, time_slot, subject, teacher, room) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (db_date, class_name, lesson_num, time_slot, subject, teacher, room)
                )
                added_count += 1
            except ValueError:
                continue

    conn.commit()
    conn.close()
    await state.clear()

    display_date = format_date_display(db_date)
    await message.answer(f"✅ Успешно добавлено уроков ({added_count}) для класса **{class_name}** на дату: **{display_date}**!", parse_mode="Markdown")

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
