


import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ⚠️ Вставь сюда TOKEN от @BotFather
TOKEN = "8970467114:AAF6gGr4AIaQtd529KaMNJxYCJiV31FbWLw"

# ID администраторов (узнать через @userinfobot)
ADMIN_IDS = [884058025]

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ ДЛЯ ПОШАГОВОГО ДОБАВЛЕНИЯ УРОКА АДМИНИСТРАТОРОМ ---
class AdminForm(StatesGroup):
    class_name = State()
    date = State()
    lesson_num = State()
    time = State()
    details = State()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("school_schedule_style.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name TEXT,
            date TEXT,
            lesson_num TEXT,
            time TEXT,
            subject TEXT,
            teacher TEXT,
            room TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS classes (
            class_name TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def get_all_classes() -> list:
    conn = sqlite3.connect("school_schedule_style.db")
    cursor = conn.cursor()
    cursor.execute("SELECT class_name FROM classes")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def add_class(class_name: str):
    conn = sqlite3.connect("school_schedule_style.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO classes (class_name) VALUES (?)", (class_name,))
    conn.commit()
    conn.close()

def get_available_dates(class_name: str) -> list:
    conn = sqlite3.connect("school_schedule_style.db")
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM schedule WHERE class_name = ? ORDER BY date", (class_name,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_schedule_for_date(class_name: str, date: str) -> list:
    conn = sqlite3.connect("school_schedule_style.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT lesson_num, time, subject, teacher, room 
        FROM schedule 
        WHERE class_name = ? AND date = ? 
        ORDER BY lesson_num
    """, (class_name, date))
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_lesson(class_name: str, date: str, lesson_num: str, time: str, subject: str, teacher: str, room: str):
    add_class(class_name)
    conn = sqlite3.connect("school_schedule_style.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO schedule (class_name, date, lesson_num, time, subject, teacher, room)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (class_name, date, lesson_num, time, subject, teacher, room))
    conn.commit()
    conn.close()

# --- КЛАВИАТУРЫ ---

main_reply_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🏫 Выбрать класс"), KeyboardButton(text="📅 Выбрать дату")],
        [KeyboardButton(text="ℹ️ Помощь")]
    ],
    resize_keyboard=True
)

def get_classes_inline_keyboard():
    classes = get_all_classes()
    keyboard = []
    for cls in classes:
        keyboard.append([InlineKeyboardButton(text=cls, callback_data=f"class_{cls}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard) if classes else None

def get_dates_inline_keyboard(class_name: str):
    dates = get_available_dates(class_name)
    keyboard = []
    for d in dates:
        keyboard.append([InlineKeyboardButton(text=f"📅 {d}", callback_data=f"date_{d}")])
    keyboard.append([InlineKeyboardButton(text="🔄 Сменить класс", callback_data="change_class")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ---

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот с расписанием уроков.\n"
        "Используй кнопки внизу экрана для управления 👇",
        reply_markup=main_reply_keyboard
    )
    classes = get_all_classes()
    if not classes:
        await message.answer(
            "В боте пока нет расписания.\n"
            "Администратор может добавить урок, написав команду `/set`",
            parse_mode="Markdown"
        )
        return
    await message.answer("Выбери свой класс:", reply_markup=get_classes_inline_keyboard())

@dp.message(F.text == "🏫 Выбрать класс")
async def btn_choose_class(message: types.Message, state: FSMContext):
    await state.clear()
    classes = get_all_classes()
    if not classes:
        await message.answer("В боте пока нет добавленных классов.", reply_markup=main_reply_keyboard)
        return
    await message.answer("Выбери свой класс:", reply_markup=get_classes_inline_keyboard())

@dp.message(F.text == "📅 Выбрать дату")
async def btn_choose_date(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    class_name = user_data.get("selected_class")
    if not class_name:
        await message.answer("Сначала выбери класс с помощью кнопки «🏫 Выбрать класс»!", reply_markup=main_reply_keyboard)
        return
    dates_kb = get_dates_inline_keyboard(class_name)
    await message.answer(f"Выбран класс: **{class_name}**. Выбери дату расписания 👇", parse_mode="Markdown", reply_markup=dates_kb)

@dp.message(F.text == "ℹ️ Помощь")
async def btn_help(message: types.Message):
    help_text = (
        "📌 **Как пользоваться ботом:**\n\n"
        "1. Нажми **«🏫 Выбрать класс»** и выбери свой класс.\n"
        "2. Нажми **«📅 Выбрать дату»**, чтобы посмотреть расписание уроков.\n"
    )
    if message.from_user.id in ADMIN_IDS:
        help_text += "\n⚙️ **Для админа:** отправь команду `/set`, чтобы запустить удобный мастер добавления уроков."
    
    await message.answer(help_text, parse_mode="Markdown", reply_markup=main_reply_keyboard)

@dp.callback_query(F.data.startswith("class_"))
async def process_class_selection(callback: types.CallbackQuery, state: FSMContext):
    class_name = callback.data.split("_")[1]
    await state.update_data(selected_class=class_name)
    dates_kb = get_dates_inline_keyboard(class_name)
    if not get_available_dates(class_name):
        await callback.message.edit_text(f"✅ Класс: **{class_name}**\n\nДля этого класса пока не добавлено ни одной даты с расписанием.", parse_mode="Markdown")
        return
    await callback.message.edit_text(f"✅ Выбран класс: **{class_name}**\nТеперь выбери дату расписания 👇", parse_mode="Markdown", reply_markup=dates_kb)
    await callback.answer()

@dp.callback_query(F.data == "change_class")
async def change_class_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = get_classes_inline_keyboard()
    if not keyboard:
        await callback.message.edit_text("Классы еще не созданы.")
        return
    await callback.message.edit_text("Выбери класс:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("date_"))
async def process_date_selection(callback: types.CallbackQuery, state: FSMContext):
    date = callback.data.split("_", 1)[1]
    user_data = await state.get_data()
    class_name = user_data.get("selected_class")
    if not class_name:
        await callback.message.answer("Сначала выбери класс.")
        return

    lessons = get_schedule_for_date(class_name, date)
    
    response_text = f"📌 **Расписание {class_name}**\n\nДата: **{date}**\n\n"
    if lessons:
        for lesson_num, time, subject, teacher, room in lessons:
            response_text += f"🔹 **{lesson_num} урок ({time})**\n"
            response_text += f"📚 {subject}\n"
            if teacher:
                response_text += f"👨‍🏫 {teacher}\n"
            if room:
                response_text += f"🚪 {room}\n"
            response_text += "\n"
    else:
        response_text += "Уроков на этот день нет."

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к датам", callback_data=f"class_{class_name}")]
    ])

    await callback.message.edit_text(response_text, parse_mode="Markdown", reply_markup=back_kb)
    await callback.answer()

# --- ИНТЕРАКТИВНЫЙ МАСТЕР ДОБАВЛЕНИЯ УРОКОВ ДЛЯ АДМИНА ---

@dp.message(Command("set"))
async def admin_start_set(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав для изменения расписания.", reply_markup=main_reply_keyboard)
        return

    classes = get_all_classes()
    if classes:
        # Предложим выбрать существующий класс кнопками или ввести новый
        kb_list = [[InlineKeyboardButton(text=cls, callback_data=f"adm_cls_{cls}")] for cls in classes]
        kb_list.append([InlineKeyboardButton(text="✍️ Ввести новый класс", callback_data="adm_cls_new")])
        await message.answer("⚙️ **Добавление урока (Шаг 1 из 5)**\n\nВыберите класс для расписания:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))
    else:
        await message.answer("⚙️ **Добавление урока (Шаг 1 из 5)**\n\nВведите название класса (например, `10А`):", parse_mode="Markdown")
    
    await state.set_state(AdminForm.class_name)

@dp.callback_query(AdminForm.class_name, F.data.startswith("adm_cls_"))
async def admin_chosen_class_callback(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[2]
    if val == "new":
        await callback.message.edit_text("✍️ Введите название нового класса (например, `10А`):", parse_mode="Markdown")
        await callback.answer()
        return

    await state.update_data(class_name=val)
    await callback.message.edit_text(f"✅ Класс: **{val}**\n\n⚙️ **Шаг 2 из 5**: Введите дату расписания (например, `25.10`):", parse_mode="Markdown")
    await state.set_state(AdminForm.date)
    await callback.answer()

@dp.message(AdminForm.class_name)
async def admin_text_class(message: types.Message, state: FSMContext):
    class_name = message.text.strip()
    await state.update_data(class_name=class_name)
    await message.answer(f"✅ Класс: **{class_name}**\n\n⚙️ **Шаг 2 из 5**: Введите дату расписания (например, `25.10`):", parse_mode="Markdown")
    await state.set_state(AdminForm.date)

@dp.message(AdminForm.date)
async def admin_text_date(message: types.Message, state: FSMContext):
    date = message.text.strip()
    await state.update_data(date=date)
    await message.answer(f"✅ Дата: **{date}**\n\n⚙️ **Шаг 3 из 5**: Введите номер урока (например, `1`, `2`, `3`):", parse_mode="Markdown")
    await state.set_state(AdminForm.lesson_num)

@dp.message(AdminForm.lesson_num)
async def admin_text_lesson_num(message: types.Message, state: FSMContext):
    lesson_num = message.text.strip()
    await state.update_data(lesson_num=lesson_num)
    await message.answer(f"✅ Номер урока: **{lesson_num}**\n\n⚙️ **Шаг 4 из 5**: Введите время урока (например, `08:30-09:15`):", parse_mode="Markdown")
    await state.set_state(AdminForm.time)

@dp.message(AdminForm.time)
async def admin_text_time(message: types.Message, state: FSMContext):
    time_slot = message.text.strip()
    await state.update_data(time=time_slot)
    await message.answer(
        f"✅ Время: **{time_slot}**\n\n"
        f"⚙️ **Шаг 5 из 5**: Введите предмет, учителя и кабинет.\n"
        f"Можете написать просто предмет, либо через черту (`Предмет | Учитель | Кабинет`).\n"
        f"_Пример:_ `Алгебра | Иванов И.И. | Каб. 204`",
        parse_mode="Markdown"
    )
    await state.set_state(AdminForm.details)

@dp.message(AdminForm.details)
async def admin_text_details(message: types.Message, state: FSMContext):
    text = message.text.strip()
    details = [d.strip() for d in text.split("|")]
    subject = details[0]
    teacher = details[1] if len(details) > 1 else ""
    room = details[2] if len(details) > 2 else ""

    data = await state.get_data()
    class_name = data.get("class_name")
    date = data.get("date")
    lesson_num = data.get("lesson_num")
    time_slot = data.get("time")

    add_lesson(class_name, date, lesson_num, time_slot, subject, teacher, room)
    await state.clear()

    await message.answer(
        f"✅ Урок успешно добавлен!\n\n"
        f"🏫 Класс: **{class_name}** | Дата: **{date}**\n"
        f"🔹 {lesson_num} урок ({time_slot})\n"
        f"📚 {subject} | 👨‍🏫 {teacher} | 🚪 {room}",
        parse_mode="Markdown",
        reply_markup=main_reply_keyboard
    )

async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
