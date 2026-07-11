import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web
from openpyxl import Workbook
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "quiz.db"))
QUESTIONS_PATH = BASE_DIR / "questions.json"
BANNER_PATH = BASE_DIR / "assets" / "banner.png"
EXPORT_PATH = BASE_DIR / "rating_export.xlsx"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("raif_dsj_quiz")

with QUESTIONS_PATH.open("r", encoding="utf-8") as file:
    QUESTIONS: list[dict[str, Any]] = json.load(file)

active_games: dict[int, dict[str, Any]] = {}
LETTERS = ["А", "Б", "В", "Г"]


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('quiz_enabled', '1')"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_results_rank ON results(score DESC, duration_seconds ASC)"
        )
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with db_connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and str(user_id) == ADMIN_ID


def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶️ Начать викторину", callback_data="start_quiz")],
            [
                InlineKeyboardButton("🏆 Рейтинг", callback_data="leaderboard"),
                InlineKeyboardButton("📖 Правила", callback_data="rules"),
            ],
            [InlineKeyboardButton("ℹ️ О викторине", callback_data="about")],
        ]
    )


def answer_keyboard(question_index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"Ответ {letter}", callback_data=f"answer:{question_index}:{i}")]
         for i, letter in enumerate(LETTERS)]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    enabled = get_setting("quiz_enabled", "1") == "1"
    toggle_text = "⏸ Приостановить викторину" if enabled else "▶️ Включить викторину"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("🏆 Полный рейтинг", callback_data="admin_full_rating")],
            [InlineKeyboardButton("📤 Выгрузить Excel", callback_data="admin_export")],
            [InlineKeyboardButton(toggle_text, callback_data="admin_toggle_quiz")],
            [InlineKeyboardButton("🔄 Сбросить текущие игры", callback_data="admin_reset_games")],
            [InlineKeyboardButton("🗑 Очистить рейтинг", callback_data="admin_clear_confirm")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
        ]
    )


def format_duration(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def question_text(index: int) -> str:
    question = QUESTIONS[index]
    options = "\n\n".join(
        f"<b>{LETTERS[i]}.</b> {option}" for i, option in enumerate(question["options"])
    )
    return (
        f"❓ <b>Вопрос {index + 1}/{len(QUESTIONS)}</b>\n\n"
        f"<b>{question['question']}</b>\n\n{options}\n\n"
        "Выберите букву ответа ниже 👇"
    )


async def send_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🏦 <b>Викторина по ДСЖ</b>\n\n"
        "15 вопросов, без ограничения времени на отдельный вопрос.\n"
        "В рейтинге учитываются <b>баллы</b> и <b>общее время прохождения</b>."
    )
    message = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    if BANNER_PATH.exists() and not update.callback_query:
        with BANNER_PATH.open("rb") as image:
            await message.reply_photo(
                photo=InputFile(image), caption=text, parse_mode=ParseMode.HTML, reply_markup=menu_keyboard()
            )
    else:
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu_keyboard())


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_home(update, context)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return
    status = "включена ✅" if get_setting("quiz_enabled", "1") == "1" else "приостановлена ⏸"
    await update.effective_message.reply_text(
        f"👑 <b>Панель администратора</b>\n\nВикторина сейчас: <b>{status}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if get_setting("quiz_enabled", "1") != "1" and not is_admin(query.from_user.id):
        await query.message.reply_text("⏸ Викторина временно приостановлена администратором.")
        return
    active_games[query.from_user.id] = {
        "index": 0,
        "score": 0,
        "started_at": time.monotonic(),
        "answered": set(),
    }
    await send_question(query.message, query.from_user.id)


async def send_question(message, user_id: int) -> None:
    index = active_games[user_id]["index"]
    await message.reply_text(
        question_text(index),
        parse_mode=ParseMode.HTML,
        reply_markup=answer_keyboard(index),
    )


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, q_raw, a_raw = query.data.split(":")
        question_index, answer_index = int(q_raw), int(a_raw)
    except (ValueError, AttributeError):
        await query.answer("Некорректный ответ", show_alert=True)
        return

    user_id = query.from_user.id
    game = active_games.get(user_id)
    if not game:
        await query.answer()
        await query.message.reply_text(
            "Эта игра уже завершена.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Играть заново", callback_data="start_quiz")]]),
        )
        return
    if question_index != game["index"] or question_index in game["answered"]:
        await query.answer("Этот вопрос уже обработан")
        return

    await query.answer()
    game["answered"].add(question_index)
    question = QUESTIONS[question_index]
    correct = answer_index == question["correct"]
    if correct:
        game["score"] += 1
        result_text = "✅ <b>Правильно!</b>"
    else:
        correct_idx = question["correct"]
        result_text = (
            "❌ <b>Неверно.</b>\n"
            f"Правильный ответ: <b>{LETTERS[correct_idx]}. {question['options'][correct_idx]}</b>"
        )

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"{result_text}\n\n💡 {question['explanation']}", parse_mode=ParseMode.HTML
    )
    game["index"] += 1
    if game["index"] >= len(QUESTIONS):
        await finish_quiz(query.message, query.from_user, game)
        active_games.pop(user_id, None)
    else:
        await send_question(query.message, user_id)


async def finish_quiz(message, user, game: dict[str, Any]) -> None:
    duration = int(time.monotonic() - game["started_at"])
    score, total = int(game["score"]), len(QUESTIONS)
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO results(user_id, username, full_name, score, total, duration_seconds) VALUES(?,?,?,?,?,?)",
            (user.id, user.username or "", user.full_name or "Участник", score, total, duration),
        )
        conn.commit()
    level = "🌟 Эксперт по ДСЖ" if score == total else "🏆 Отличный результат" if score >= 12 else "👍 Хороший результат" if score >= 9 else "📚 Есть что повторить"
    await message.reply_text(
        "🎉 <b>Викторина завершена!</b>\n\n"
        f"Результат: <b>{score}/{total}</b>\n"
        f"Время: <b>{format_duration(duration)}</b>\n"
        f"Уровень: <b>{level}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏆 Рейтинг", callback_data="leaderboard")],
            [InlineKeyboardButton("🔄 Играть заново", callback_data="start_quiz")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
        ]),
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT full_name, username, score, total, duration_seconds FROM results "
            "ORDER BY score DESC, duration_seconds ASC, completed_at ASC LIMIT 10"
        ).fetchall()
    if not rows:
        text = "🏆 <b>Рейтинг пока пуст.</b>"
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 <b>ТОП-10 участников</b>\n"]
        for i, row in enumerate(rows):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            name = row["full_name"] + (f" (@{row['username']})" if row["username"] else "")
            lines.append(f"{prefix} <b>{name}</b> — {row['score']}/{row['total']} · {format_duration(row['duration_seconds'])}")
        text = "\n".join(lines)
    await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Начать викторину", callback_data="start_quiz")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
    ]))


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "📖 <b>Правила</b>\n\n• 15 вопросов.\n• Один ответ на вопрос.\n• Таймера на вопрос нет.\n• Учитывается общее время.\n• При равных баллах выше тот, кто быстрее.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="home")]]),
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "ℹ️ <b>О викторине</b>\n\nУчебный Telegram-бот для проверки знаний по ДСЖ.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="home")]]),
    )


def admin_stats_text() -> str:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) games, COUNT(DISTINCT user_id) users, "
            "AVG(score) avg_score, AVG(duration_seconds) avg_time, "
            "MAX(score) best_score, MIN(duration_seconds) best_time FROM results"
        ).fetchone()
    return (
        "📊 <b>Статистика</b>\n\n"
        f"Уникальных участников: <b>{row['users'] or 0}</b>\n"
        f"Завершённых игр: <b>{row['games'] or 0}</b>\n"
        f"Средний балл: <b>{(row['avg_score'] or 0):.1f}</b>\n"
        f"Среднее время: <b>{format_duration(int(row['avg_time'] or 0))}</b>\n"
        f"Лучший балл: <b>{row['best_score'] or 0}</b>\n"
        f"Лучшее время: <b>{format_duration(int(row['best_time'] or 0))}</b>\n"
        f"Активных игр сейчас: <b>{len(active_games)}</b>"
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    await query.message.reply_text(admin_stats_text(), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


async def admin_full_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT full_name, username, score, total, duration_seconds, completed_at FROM results "
            "ORDER BY score DESC, duration_seconds ASC, completed_at ASC LIMIT 50"
        ).fetchall()
    if not rows:
        text = "🏆 Рейтинг пока пуст."
    else:
        lines = ["🏆 <b>Полный рейтинг (до 50 записей)</b>\n"]
        for i, row in enumerate(rows, 1):
            name = row["full_name"] + (f" (@{row['username']})" if row["username"] else "")
            lines.append(f"{i}. {name} — {row['score']}/{row['total']} · {format_duration(row['duration_seconds'])}")
        text = "\n".join(lines)
    await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


def build_excel() -> Path:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT full_name, username, score, total, duration_seconds, completed_at FROM results "
            "ORDER BY score DESC, duration_seconds ASC, completed_at ASC"
        ).fetchall()
    wb = Workbook()
    ws = wb.active
    ws.title = "Рейтинг"
    ws.append(["Место", "Имя", "Username", "Баллы", "Всего", "Время", "Дата"])
    for i, row in enumerate(rows, 1):
        ws.append([i, row["full_name"], row["username"], row["score"], row["total"], format_duration(row["duration_seconds"]), row["completed_at"]])
    wb.save(EXPORT_PATH)
    return EXPORT_PATH


async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    path = build_excel()
    with path.open("rb") as file:
        await query.message.reply_document(InputFile(file, filename=f"rating_{datetime.now():%Y-%m-%d_%H-%M}.xlsx"), caption="📤 Выгрузка рейтинга")


async def admin_toggle_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    enabled = get_setting("quiz_enabled", "1") == "1"
    set_setting("quiz_enabled", "0" if enabled else "1")
    status = "приостановлена ⏸" if enabled else "включена ✅"
    await query.message.reply_text(f"Викторина {status}.", reply_markup=admin_keyboard())


async def admin_reset_games(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    count = len(active_games)
    active_games.clear()
    await query.message.reply_text(f"🔄 Сброшено активных игр: {count}", reply_markup=admin_keyboard())


async def admin_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    await query.message.reply_text(
        "⚠️ Точно удалить весь рейтинг? Это действие нельзя отменить.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Да, очистить", callback_data="admin_clear_rating")],
            [InlineKeyboardButton("Отмена", callback_data="admin_panel")],
        ]),
    )


async def admin_clear_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    with db_connect() as conn:
        conn.execute("DELETE FROM results")
        conn.commit()
    await query.message.reply_text("🗑 Рейтинг очищен.", reply_markup=admin_keyboard())


async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    await query.message.reply_text("👑 <b>Панель администратора</b>", parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    handlers = {
        "home": send_home,
        "start_quiz": start_quiz,
        "leaderboard": leaderboard,
        "rules": rules,
        "about": about,
        "admin_panel": admin_panel_callback,
        "admin_stats": admin_stats,
        "admin_full_rating": admin_full_rating,
        "admin_export": admin_export,
        "admin_toggle_quiz": admin_toggle_quiz,
        "admin_reset_games": admin_reset_games,
        "admin_clear_confirm": admin_clear_confirm,
        "admin_clear_rating": admin_clear_rating,
    }
    if data.startswith("answer:"):
        await handle_answer(update, context)
    elif data in handlers:
        await handlers[data](update, context)


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def run_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    return runner


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
    init_db()
    runner = await run_health_server()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(callback_router))
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started")
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
