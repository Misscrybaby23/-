import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from aiohttp import web
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "quiz.db"))
QUESTIONS_PATH = BASE_DIR / "questions.json"
BANNER_PATH = BASE_DIR / "assets" / "banner.png"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("raif_dsj_quiz")

with QUESTIONS_PATH.open("r", encoding="utf-8") as file:
    QUESTIONS: list[dict[str, Any]] = json.load(file)

active_games: dict[int, dict[str, Any]] = {}


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
            "CREATE INDEX IF NOT EXISTS idx_results_rank "
            "ON results(score DESC, duration_seconds ASC)"
        )
        conn.commit()


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
    letters = ["А", "Б", "В", "Г"]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{letters[i]}. {option}",
                    callback_data=f"answer:{question_index}:{i}",
                )
            ]
            for i, option in enumerate(QUESTIONS[question_index]["options"])
        ]
    )


def format_duration(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


async def send_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🏦 <b>Викторина по ДСЖ</b>\n\n"
        "15 вопросов, без ограничения времени на отдельный вопрос.\n"
        "В рейтинге учитываются <b>баллы</b> и <b>общее время прохождения</b>."
    )

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=menu_keyboard(),
        )
        return

    if BANNER_PATH.exists():
        with BANNER_PATH.open("rb") as image:
            await update.effective_message.reply_photo(
                photo=InputFile(image),
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=menu_keyboard(),
            )
    else:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=menu_keyboard(),
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_home(update, context)


async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    active_games[user_id] = {
        "index": 0,
        "score": 0,
        "started_at": time.monotonic(),
        "answered": set(),
    }
    await send_question(query.message, user_id)


async def send_question(message, user_id: int) -> None:
    game = active_games[user_id]
    index = game["index"]
    question = QUESTIONS[index]

    text = (
        f"❓ <b>Вопрос {index + 1}/{len(QUESTIONS)}</b>\n\n"
        f"{question['question']}"
    )
    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=answer_keyboard(index),
    )


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, question_index_raw, answer_index_raw = query.data.split(":")
        question_index = int(question_index_raw)
        answer_index = int(answer_index_raw)
    except (ValueError, AttributeError):
        await query.answer("Некорректный ответ", show_alert=True)
        return

    user_id = query.from_user.id
    game = active_games.get(user_id)

    if not game:
        await query.message.reply_text(
            "Эта игра уже завершена. Нажмите «Играть заново».",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 Играть заново", callback_data="start_quiz")]]
            ),
        )
        return

    if question_index != game["index"]:
        await query.answer("Этот вопрос уже обработан", show_alert=False)
        return

    if question_index in game["answered"]:
        await query.answer("Ответ уже принят", show_alert=False)
        return

    game["answered"].add(question_index)
    question = QUESTIONS[question_index]
    correct = answer_index == question["correct"]

    if correct:
        game["score"] += 1
        result_text = "✅ <b>Правильно!</b>"
    else:
        letters = ["А", "Б", "В", "Г"]
        correct_idx = question["correct"]
        correct_text = question["options"][correct_idx]
        result_text = (
            "❌ <b>Неверно.</b>\n"
            f"Правильный ответ: <b>{letters[correct_idx]}. {correct_text}</b>"
        )

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"{result_text}\n\n💡 {question['explanation']}",
        parse_mode=ParseMode.HTML,
    )

    game["index"] += 1
    if game["index"] >= len(QUESTIONS):
        await finish_quiz(query.message, query.from_user, game)
        active_games.pop(user_id, None)
    else:
        await send_question(query.message, user_id)


async def finish_quiz(message, user, game: dict[str, Any]) -> None:
    duration = int(time.monotonic() - game["started_at"])
    score = int(game["score"])
    total = len(QUESTIONS)
    username = user.username or ""
    full_name = user.full_name or "Участник"

    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO results (
                user_id, username, full_name, score, total, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user.id, username, full_name, score, total, duration),
        )
        conn.commit()

    if score == total:
        level = "🌟 Эксперт по ДСЖ"
    elif score >= 12:
        level = "🏆 Отличный результат"
    elif score >= 9:
        level = "👍 Хорошее знание продукта"
    else:
        level = "📚 Есть что повторить"

    text = (
        "🎉 <b>Викторина завершена!</b>\n\n"
        f"Результат: <b>{score}/{total}</b>\n"
        f"Время: <b>{format_duration(duration)}</b>\n"
        f"Уровень: <b>{level}</b>"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏆 Посмотреть рейтинг", callback_data="leaderboard")],
            [InlineKeyboardButton("🔄 Играть заново", callback_data="start_quiz")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
        ]
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT full_name, username, score, total, duration_seconds
            FROM results
            ORDER BY score DESC, duration_seconds ASC, completed_at ASC
            LIMIT 10
            """
        ).fetchall()

    if not rows:
        text = "🏆 <b>Рейтинг пока пуст.</b>\nСтаньте первым участником!"
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 <b>ТОП-10 участников</b>\n"]
        for i, row in enumerate(rows):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            display_name = row["full_name"]
            if row["username"]:
                display_name += f" (@{row['username']})"
            lines.append(
                f"{prefix} <b>{display_name}</b> — "
                f"{row['score']}/{row['total']} · {format_duration(row['duration_seconds'])}"
            )
        text = "\n".join(lines)

    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶️ Начать викторину", callback_data="start_quiz")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
            ]
        ),
    )


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text = (
        "📖 <b>Правила</b>\n\n"
        "• 15 вопросов с четырьмя вариантами ответа.\n"
        "• На каждый вопрос можно ответить один раз.\n"
        "• Ограничения времени на вопрос нет.\n"
        "• Считается общее время прохождения.\n"
        "• В рейтинге выше участник с большим количеством баллов.\n"
        "• При равных баллах выше тот, кто прошёл быстрее."
    )
    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Главное меню", callback_data="home")]]
        ),
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text = (
        "ℹ️ <b>О викторине</b>\n\n"
        "Учебный Telegram-бот для командной проверки знаний по ДСЖ.\n"
        "Результаты сохраняются локально в базе SQLite."
    )
    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Главное меню", callback_data="home")]]
        ),
    )


async def reset_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID:
        await update.effective_message.reply_text("Команда доступна только администратору.")
        return

    with db_connect() as conn:
        conn.execute("DELETE FROM results")
        conn.commit()

    await update.effective_message.reply_text("✅ Рейтинг очищен.")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    if data == "home":
        await send_home(update, context)
    elif data == "start_quiz":
        await start_quiz(update, context)
    elif data == "leaderboard":
        await leaderboard(update, context)
    elif data == "rules":
        await rules(update, context)
    elif data == "about":
        await about(update, context)
    elif data.startswith("answer:"):
        await handle_answer(update, context)


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def run_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health server started on port %s", PORT)
    return runner


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

    init_db()
    runner = await run_health_server()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("rating", leaderboard))
    application.add_handler(CommandHandler("reset_rating", reset_rating))
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
