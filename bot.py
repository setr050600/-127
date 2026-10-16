"""
Telegram-бот «Школьное расписание» (aiogram 3, Python 3.10+).

Что умеет:
  • подтягивает расписание из Google Таблицы (автоматически каждые N минут
    и по команде /sync) — админ просто правит таблицу, бот подхватывает;
  • админ добавляет / удаляет классы прямо в боте (/admin);
  • ученик выбирает класс, открывает календарь, жмёт на дату и видит уроки:
    начало–конец, предмет, кабинет.

Формат таблицы (первая строка — заголовки, порядок столбцов любой):
  Класс | День | № | Предмет | Кабинет | Начало | Конец
  «Начало» и «Конец» необязательны — если пусты, берутся из DEFAULT_BELLS ниже.
"""
from __future__ import annotations

import asyncio
import calendar
import csv
import html
import io
import logging
import os
import re
from contextlib import suppress
from datetime import date, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

# ════════════════════════════ НАСТРОЙКИ ════════════════════════════
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8970467114:AAF6gGr4AIaQtd529KaMNJxYCJiV31FbWLw").strip()
ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "884058025")) if x.strip().isdigit()}
SHEET_ID = os.getenv("SHEET_ID", "").strip()          # id между /d/ и /edit в ссылке на таблицу
SHEET_GID = os.getenv("SHEET_GID", "0").strip()       # id листа (число после gid= в ссылке)
SYNC_INTERVAL_MIN = int(os.getenv("SYNC_INTERVAL_MIN", "10"))  # 0 — выключить автообновление
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
DB_PATH = os.getenv("DB_PATH", "schedule.db")

# Расписание звонков по умолчанию (если в таблице не указаны «Начало»/«Конец»).
DEFAULT_BELLS: dict[int, tuple[str, str]] = {
    1: ("08:00", "08:40"),
    2: ("08:50", "09:30"),
    3: ("9:50", "10:30"),
    4: ("10:50", "11:30"),
    5: ("11:50", "12:30"),
    6: ("12:45", "13:25"),
    7: ("13:40", "14:20"),
    8: ("14:40", "15:20"),
}

MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

BTN_CAL = "📅 Календарь"
BTN_TODAY = "Сегодня"
BTN_TOMORROW = "Завтра"
BTN_CLASS = "🏫 Сменить класс"

# ════════════════════════════ ВСПОМОГАТЕЛЬНОЕ ════════════════════════════
class Lesson(NamedTuple):
    class_name: str
    weekday: int      # 0 = Пн … 6 = Вс
    lesson_no: int
    time_start: str   # "08:30"
    time_end: str     # "09:15"
    subject: str
    room: str


# Латиницу, похожую на кириллицу, превращаем в кириллицу («5A» → «5А»)
_LAT2CYR = str.maketrans("ABCEHKMOPTXabcehkmoptx", "АВСЕНКМОРТХавсенкмортх")
CLASS_RE = re.compile(r"\d{1,2}[А-ЯЁ0-9\-]{0,6}")


def norm_class(s: str) -> str:
    return re.sub(r"\s+", "", s).translate(_LAT2CYR).upper()


def class_sort_key(name: str):
    m = re.match(r"(\d+)(.*)", name)
    return (int(m.group(1)), m.group(2)) if m else (999, name)


WEEKDAY_ALIASES = {
    "пн": 0, "пон": 0, "понедельник": 0, "1": 0,
    "вт": 1, "вто": 1, "вторник": 1, "2": 1,
    "ср": 2, "сре": 2, "среда": 2, "3": 2,
    "чт": 3, "чет": 3, "четверг": 3, "4": 3,
    "пт": 4, "пят": 4, "пятница": 4, "5": 4,
    "сб": 5, "суб": 5, "суббота": 5, "6": 5,
    "вс": 6, "вос": 6, "воскресенье": 6, "7": 6,
}


def parse_weekday(s: str) -> int | None:
    return WEEKDAY_ALIASES.get(re.sub(r"[\s.]+", "", s.lower()))


def parse_time(s: str) -> str | None:
    m = re.match(r"\s*(\d{1,2})[:.](\d{2})", s or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def chunk(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def today_date() -> date:
    return datetime.now(TZ).date()


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


# ════════════════════════════ БАЗА ДАННЫХ ════════════════════════════
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS classes (name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS lessons (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                class_name TEXT    NOT NULL,
                weekday    INTEGER NOT NULL,
                lesson_no  INTEGER NOT NULL,
                time_start TEXT    NOT NULL,
                time_end   TEXT    NOT NULL,
                subject    TEXT    NOT NULL,
                room       TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_lessons ON lessons(class_name, weekday);
            CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, class_name TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        await db.commit()


async def db_fetchall(sql: str, params: tuple = ()) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        return list(await cur.fetchall())


async def db_execute(sql: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.rowcount


async def get_classes() -> list[str]:
    rows = await db_fetchall("SELECT name FROM classes")
    return sorted((r[0] for r in rows), key=class_sort_key)


async def add_class(name: str) -> bool:
    return await db_execute("INSERT OR IGNORE INTO classes(name) VALUES (?)", (name,)) > 0


async def delete_class(name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM lessons WHERE class_name = ?", (name,))
        await db.execute("DELETE FROM classes WHERE name = ?", (name,))
        await db.execute("UPDATE users SET class_name = NULL WHERE class_name = ?", (name,))
        await db.commit()


async def get_user_class(user_id: int) -> str | None:
    rows = await db_fetchall("SELECT class_name FROM users WHERE user_id = ?", (user_id,))
    return rows[0][0] if rows else None


async def set_user_class(user_id: int, class_name: str) -> None:
    await db_execute(
        "INSERT INTO users(user_id, class_name) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET class_name = excluded.class_name",
        (user_id, class_name),
    )


async def get_lessons(class_name: str, weekday: int) -> list[Lesson]:
    rows = await db_fetchall(
        "SELECT class_name, weekday, lesson_no, time_start, time_end, subject, room "
        "FROM lessons WHERE class_name = ? AND weekday = ? ORDER BY lesson_no, time_start",
        (class_name, weekday),
    )
    return [Lesson(*r) for r in rows]


async def get_meta(key: str) -> str | None:
    rows = await db_fetchall("SELECT value FROM meta WHERE key = ?", (key,))
    return rows[0][0] if rows else None


# ════════════════════════════ GOOGLE ТАБЛИЦА ════════════════════════════
HEADER_ALIASES = {
    "class": {"класс"},
    "weekday": {"день", "деньнедели"},
    "lesson_no": {"№", "номер", "урок", "№урока", "номерурока"},
    "subject": {"предмет", "дисциплина"},
    "room": {"кабинет", "каб", "аудитория"},
    "start": {"начало", "началоурока"},
    "end": {"конец", "окончание", "конецурока"},
}
REQUIRED_FIELDS = {"class": "Класс", "weekday": "День", "lesson_no": "№", "subject": "Предмет"}


async def fetch_sheet_csv() -> str:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            body = await resp.read()
    if status != 200 or "csv" not in ctype:
        raise RuntimeError(
            "не удалось скачать таблицу. Проверьте SHEET_ID / SHEET_GID и что у таблицы включён "
            "доступ «Все, у кого есть ссылка — Читатель»"
        )
    return body.decode("utf-8-sig")


def parse_schedule(csv_text: str) -> tuple[list[Lesson], list[str]]:
    """Разбирает CSV → (список уроков, список проблем по строкам)."""
    rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
    header_idx = next((i for i, r in enumerate(rows) if any(c.strip() for c in r)), None)
    if header_idx is None:
        raise ValueError("таблица пуста")

    cols: dict[str, int] = {}
    for j, cell in enumerate(rows[header_idx]):
        key = re.sub(r"[\s.]+", "", cell.lower())
        for field, aliases in HEADER_ALIASES.items():
            if key in aliases and field not in cols:
                cols[field] = j
    missing = [ru for f, ru in REQUIRED_FIELDS.items() if f not in cols]
    if missing:
        raise ValueError("в первой строке таблицы нет столбцов: " + ", ".join(missing))

    def cell(row: list[str], field: str) -> str:
        j = cols.get(field)
        return row[j].strip() if j is not None and j < len(row) else ""

    lessons: list[Lesson] = []
    problems: list[str] = []
    last_class: str | None = None
    last_day: int | None = None

    for n, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not any(c.strip() for c in row):
            continue

        # Пустые «Класс»/«День» наследуются сверху — удобно для объединённых ячеек
        raw_class = cell(row, "class")
        if raw_class:
            new_class = norm_class(raw_class)
            if new_class != last_class:
                last_day = None
            last_class = new_class

        raw_day = cell(row, "weekday")
        if raw_day:
            last_day = parse_weekday(raw_day)
            if last_day is None:
                problems.append(f"строка {n}: непонятный день «{raw_day}»")
                continue

        subject = cell(row, "subject")
        if not subject:          # «окно» или пустая строка
            continue
        if last_class is None or last_day is None:
            problems.append(f"строка {n}: не указан класс или день")
            continue

        m = re.match(r"\d+", cell(row, "lesson_no"))
        if not m:
            problems.append(f"строка {n}: не указан номер урока")
            continue
        no = int(m.group())

        default = DEFAULT_BELLS.get(no, (None, None))
        start = parse_time(cell(row, "start")) or default[0]
        end = parse_time(cell(row, "end")) or default[1]
        if not start or not end:
            problems.append(f"строка {n}: нет времени для урока №{no}")
            continue

        lessons.append(Lesson(last_class, last_day, no, start, end, subject, cell(row, "room")))

    return lessons, problems


sync_lock = asyncio.Lock()


async def run_sync() -> str:
    if not SHEET_ID:
        raise RuntimeError("не задан SHEET_ID в файле .env")
    async with sync_lock:
        lessons, problems = parse_schedule(await fetch_sheet_csv())
        if not lessons:
            raise ValueError("в таблице не найдено ни одного урока — расписание в боте не изменено")

        known = set(await get_classes())
        to_save = [l for l in lessons if l.class_name in known]
        skipped = sorted({l.class_name for l in lessons} - known, key=class_sort_key)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM lessons")
            await db.executemany(
                "INSERT INTO lessons(class_name, weekday, lesson_no, time_start, time_end, subject, room) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                to_save,
            )
            await db.execute(
                "INSERT INTO meta(key, value) VALUES ('last_sync', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (datetime.now(TZ).strftime("%d.%m.%Y %H:%M"),),
            )
            await db.commit()

    parts = [f"✅ Расписание обновлено: {len(to_save)} уроков, классов: {len({l.class_name for l in to_save})}."]
    if skipped:
        parts.append("В таблице есть классы, которых нет в боте (добавьте через /admin): " + ", ".join(skipped))
    if problems:
        text = "Пропущены строки таблицы:\n" + "\n".join(f"• {html.escape(p)}" for p in problems[:10])
        if len(problems) > 10:
            text += f"\n…и ещё {len(problems) - 10}"
        parts.append(text)
    return "\n\n".join(parts)


async def safe_sync() -> str:
    try:
        return await run_sync()
    except Exception as e:  # noqa: BLE001
        logging.exception("Ошибка синхронизации")
        return f"⚠️ Не удалось обновить расписание: {html.escape(str(e))}"


async def periodic_sync() -> None:
    while True:
        await asyncio.sleep(SYNC_INTERVAL_MIN * 60)
        logging.info("Автосинхронизация: %s", await safe_sync())


# ════════════════════════════ КЛАВИАТУРЫ И ТЕКСТЫ ════════════════════════════
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CAL)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TOMORROW)],
            [KeyboardButton(text=BTN_CLASS)],
        ],
        resize_keyboard=True,
    )


def classes_kb(classes: list[str], prefix: str, icon: str = "", per_row: int = 4,
               extra_row: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=f"{icon}{c}", callback_data=f"{prefix}:{c}") for c in classes]
    rows = chunk(buttons, per_row)
    if extra_row:
        rows.append(extra_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calendar_kb(year: int, month: int, today: date) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{MONTHS_RU[month - 1]} {year}", callback_data="noop")],
        [InlineKeyboardButton(text=w, callback_data="noop") for w in WEEKDAYS_SHORT],
    ]
    for week in calendar.monthcalendar(year, month):      # неделя начинается с понедельника
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
            else:
                d = date(year, month, day)
                label = f"•{day}•" if d == today else str(day)
                row.append(InlineKeyboardButton(text=label, callback_data=f"day:{d.isoformat()}"))
        rows.append(row)
    py, pm = shift_month(year, month, -1)
    ny, nm = shift_month(year, month, +1)
    rows.append([
        InlineKeyboardButton(text="◀️", callback_data=f"cal:{py:04d}-{pm:02d}"),
        InlineKeyboardButton(text="Сегодня", callback_data=f"day:{today.isoformat()}"),
        InlineKeyboardButton(text="▶️", callback_data=f"cal:{ny:04d}-{nm:02d}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_nav_kb(d: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️", callback_data=f"day:{(d - timedelta(days=1)).isoformat()}"),
        InlineKeyboardButton(text="🗓 Календарь", callback_data=f"cal:{d.year:04d}-{d.month:02d}"),
        InlineKeyboardButton(text="▶️", callback_data=f"day:{(d + timedelta(days=1)).isoformat()}"),
    ]])


def calendar_title(class_name: str) -> str:
    return f"📅 Класс <b>{class_name}</b> — выберите дату:"


def format_day(d: date, class_name: str, lessons: list[Lesson]) -> str:
    head = f"📅 <b>{WEEKDAYS_FULL[d.weekday()]}, {d.day} {MONTHS_GEN[d.month - 1]}</b>\nКласс: <b>{class_name}</b>\n"
    if not lessons:
        return head + "\nУроков нет 🎉"
    lines = []
    for l in lessons:
        room = f" (каб. {html.escape(l.room)})" if l.room else ""
        lines.append(f"<b>{l.lesson_no}.</b> {l.time_start}–{l.time_end} — {html.escape(l.subject)}{room}")
    return head + "\n" + "\n".join(lines)


async def safe_edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ════════════════════════════ ПОЛЬЗОВАТЕЛЬСКИЕ ХЭНДЛЕРЫ ════════════════════════════
router = Router()


async def ask_class(m: Message) -> None:
    classes = await get_classes()
    if not classes:
        await m.answer("Список классов пока пуст. Обратитесь к администратору.")
        return
    await m.answer("Выберите свой класс:", reply_markup=classes_kb(classes, "setclass"))


async def require_class(m: Message) -> str | None:
    cls = await get_user_class(m.from_user.id)
    if not cls:
        await ask_class(m)
    return cls


@router.message(CommandStart())
async def cmd_start(m: Message) -> None:
    cls = await get_user_class(m.from_user.id)
    if cls:
        await m.answer(
            f"Привет! Твой класс — <b>{cls}</b>.\n"
            "Жми «📅 Календарь», чтобы посмотреть уроки на любую дату.",
            reply_markup=main_menu(),
        )
    else:
        await m.answer("Привет! Я бот школьного расписания 📚")
        await ask_class(m)


@router.callback_query(F.data.startswith("setclass:"))
async def cb_setclass(cb: CallbackQuery) -> None:
    name = cb.data.split(":", 1)[1]
    if name not in await get_classes():
        await cb.answer("Такого класса уже нет", show_alert=True)
        return
    await set_user_class(cb.from_user.id, name)
    with suppress(TelegramBadRequest):
        await cb.message.delete()
    await cb.message.answer(f"✅ Класс <b>{name}</b> сохранён.", reply_markup=main_menu())
    await cb.answer()


@router.message(F.text == BTN_CLASS)
async def change_class(m: Message) -> None:
    await ask_class(m)


@router.message(F.text == BTN_CAL)
@router.message(Command("calendar"))
async def show_calendar(m: Message) -> None:
    cls = await require_class(m)
    if cls:
        t = today_date()
        await m.answer(calendar_title(cls), reply_markup=calendar_kb(t.year, t.month, t))


async def send_day(m: Message, d: date) -> None:
    cls = await require_class(m)
    if cls:
        await m.answer(format_day(d, cls, await get_lessons(cls, d.weekday())), reply_markup=day_nav_kb(d))


@router.message(F.text == BTN_TODAY)
@router.message(Command("today"))
async def show_today(m: Message) -> None:
    await send_day(m, today_date())


@router.message(F.text == BTN_TOMORROW)
@router.message(Command("tomorrow"))
async def show_tomorrow(m: Message) -> None:
    await send_day(m, today_date() + timedelta(days=1))


@router.callback_query(F.data.startswith("cal:"))
async def cb_calendar(cb: CallbackQuery) -> None:
    try:
        y, mth = map(int, cb.data[4:].split("-"))
        date(y, mth, 1)
    except ValueError:
        await cb.answer()
        return
    cls = await get_user_class(cb.from_user.id)
    if not cls:
        await cb.answer("Сначала выберите класс: /start", show_alert=True)
        return
    await safe_edit(cb.message, calendar_title(cls), calendar_kb(y, mth, today_date()))
    await cb.answer()


@router.callback_query(F.data.startswith("day:"))
async def cb_day(cb: CallbackQuery) -> None:
    try:
        d = date.fromisoformat(cb.data[4:])
    except ValueError:
        await cb.answer()
        return
    cls = await get_user_class(cb.from_user.id)
    if not cls:
        await cb.answer("Сначала выберите класс: /start", show_alert=True)
        return
    await safe_edit(cb.message, format_day(d, cls, await get_lessons(cls, d.weekday())), day_nav_kb(d))
    await cb.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


# ════════════════════════════ АДМИН-ПАНЕЛЬ ════════════════════════════
admin_router = Router()
admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


class AdminFlow(StatesGroup):
    adding_class = State()


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить класс", callback_data="adm:add")],
        [InlineKeyboardButton(text="🗑 Удалить класс", callback_data="adm:del")],
        [InlineKeyboardButton(text="🔄 Обновить из таблицы", callback_data="adm:sync")],
    ])


async def panel_text(note: str = "") -> str:
    classes = await get_classes()
    count = (await db_fetchall("SELECT COUNT(*) FROM lessons"))[0][0]
    last = await get_meta("last_sync") or "ещё не было"
    text = (
        "🛠 <b>Панель администратора</b>\n\n"
        f"Классы: {', '.join(classes) if classes else 'пока нет'}\n"
        f"Уроков в базе: {count}\n"
        f"Последнее обновление из таблицы: {last}"
    )
    if SHEET_ID:
        text += f'\n\n<a href="https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit">Открыть таблицу</a>'
    if note:
        text += f"\n\n{note}"
    return text


@admin_router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext) -> None:
    await state.clear()
    await m.answer(await panel_text(), reply_markup=admin_panel_kb(), disable_web_page_preview=True)


@admin_router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext) -> None:
    await state.clear()
    await m.answer("Отменено.")


@admin_router.message(Command("sync"))
async def cmd_sync(m: Message) -> None:
    await m.answer(await safe_sync())


@admin_router.callback_query(F.data == "adm:back")
async def adm_back(cb: CallbackQuery) -> None:
    await safe_edit(cb.message, await panel_text(), admin_panel_kb())
    await cb.answer()


@admin_router.callback_query(F.data == "adm:sync")
async def adm_sync(cb: CallbackQuery) -> None:
    await cb.answer("Обновляю…")
    report = await safe_sync()
    await safe_edit(cb.message, await panel_text(report), admin_panel_kb())


# ── добавление классов ──
@admin_router.callback_query(F.data == "adm:add")
async def adm_add(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.adding_class)
    await cb.message.answer(
        "Введите название класса (можно несколько через запятую), например: <code>5А, 5Б, 10</code>\n"
        "Отмена — /cancel"
    )
    await cb.answer()


@admin_router.message(AdminFlow.adding_class, F.text, ~F.text.startswith("/"))
async def adm_add_process(m: Message, state: FSMContext) -> None:
    added, existed, bad = [], [], []
    for token in (t for t in re.split(r"[,;\s]+", m.text) if t):
        name = norm_class(token)
        if not CLASS_RE.fullmatch(name):
            bad.append(token)
        elif await add_class(name):
            added.append(name)
        else:
            existed.append(name)

    if not added and not existed:
        await m.answer("Не удалось распознать название. Пример: <code>5А, 5Б, 10</code>. Отмена — /cancel")
        return

    await state.clear()
    lines = []
    if added:
        lines.append("✅ Добавлено: " + ", ".join(added))
    if existed:
        lines.append("Уже были: " + ", ".join(existed))
    if bad:
        lines.append("Не распознано: " + html.escape(", ".join(bad)))
    text = "\n".join(lines)
    if added and SHEET_ID:
        text += "\n\n" + await safe_sync()      # сразу подтянем уроки новых классов
    await m.answer(text)
    await m.answer(await panel_text(), reply_markup=admin_panel_kb(), disable_web_page_preview=True)


# ── удаление классов ──
@admin_router.callback_query(F.data == "adm:del")
async def adm_del(cb: CallbackQuery) -> None:
    classes = await get_classes()
    if not classes:
        await cb.answer("Классов пока нет", show_alert=True)
        return
    kb = classes_kb(classes, "delask", icon="🗑 ", per_row=3,
                    extra_row=[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back")])
    await safe_edit(cb.message, "Какой класс удалить?", kb)
    await cb.answer()


@admin_router.callback_query(F.data.startswith("delask:"))
async def adm_del_ask(cb: CallbackQuery) -> None:
    name = cb.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delyes:{name}"),
        InlineKeyboardButton(text="Отмена", callback_data="adm:back"),
    ]])
    await safe_edit(
        cb.message,
        f"Удалить класс <b>{name}</b>?\nВсе его уроки удалятся, а ученики этого класса "
        "должны будут выбрать класс заново.",
        kb,
    )
    await cb.answer()


@admin_router.callback_query(F.data.startswith("delyes:"))
async def adm_del_yes(cb: CallbackQuery) -> None:
    name = cb.data.split(":", 1)[1]
    await delete_class(name)
    await safe_edit(cb.message, await panel_text(f"🗑 Класс {name} удалён."), admin_panel_kb())
    await cb.answer()


# ════════════════════════════ ЗАПУСК ════════════════════════════
async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("Задайте BOT_TOKEN в файле .env")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст — админ-панель никому не доступна")

    await init_db()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)   # админский роутер — первым
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать / выбрать класс"),
        BotCommand(command="calendar", description="Календарь"),
        BotCommand(command="today", description="Расписание на сегодня"),
        BotCommand(command="tomorrow", description="Расписание на завтра"),
    ])

    sync_task = None
    if SHEET_ID:
        logging.info("Первичная синхронизация: %s", await safe_sync())
        if SYNC_INTERVAL_MIN > 0:
            sync_task = asyncio.create_task(periodic_sync())
    else:
        logging.warning("SHEET_ID не задан — автоимпорт из Google Таблицы отключён")

    try:
        await dp.start_polling(bot)
    finally:
        if sync_task:
            sync_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
