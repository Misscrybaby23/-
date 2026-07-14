
import asyncio
import json
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup
)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "quiz.db"
QUESTIONS_PATH = BASE_DIR / "questions.json"

router = Router()
dp = Dispatcher()
dp.include_router(router)

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct INTEGER NOT NULL,
            image_path TEXT
        );
        CREATE TABLE IF NOT EXISTS players(
            telegram_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            photo_file_id TEXT,
            photo_path TEXT
        );
        CREATE TABLE IF NOT EXISTS results(
            telegram_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT,
            score INTEGER NOT NULL DEFAULT 0,
            answered INTEGER NOT NULL DEFAULT 0
        );
        """)
        count = con.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        if count == 0:
            items = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
            for q in items:
                con.execute(
                    """INSERT INTO questions(text, option_a, option_b, option_c, option_d, correct, image_path)
                       VALUES(?,?,?,?,?,?,?)""",
                    (q["text"], *q["options"], q["correct"], q["image"])
                )

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def main_menu(user_id: int):
    rows = [
        [KeyboardButton(text="▶️ Начать тест"), KeyboardButton(text="🏆 Рейтинг")],
        [KeyboardButton(text="📚 Правила")]
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❓ Вопросы"), KeyboardButton(text="👥 Игроки")],
            [KeyboardButton(text="🏁 Завершить игру"), KeyboardButton(text="🧹 Сбросить результаты")],
            [KeyboardButton(text="⬅️ Главное меню")]
        ],
        resize_keyboard=True
    )

class QuizState(StatesGroup):
    active = State()

class AddPlayer(StatesGroup):
    telegram_id = State()
    name = State()
    photo = State()

class EditQuestion(StatesGroup):
    choose = State()
    action = State()
    text = State()
    options = State()
    correct = State()
    image = State()

@router.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 Добро пожаловать в викторину «Всё о ДСЖ»!\n\n"
        "За каждый правильный ответ начисляется 100 баллов.",
        reply_markup=main_menu(message.from_user.id)
    )

@router.message(F.text == "📚 Правила")
async def rules(message: Message):
    await message.answer(
        "📚 Правила:\n"
        "• 12 вопросов;\n"
        "• 4 варианта ответа;\n"
        "• 100 баллов за правильный ответ;\n"
        "• повторно отвечать на один вопрос нельзя;\n"
        "• победитель определяется по количеству баллов."
    )

def get_questions():
    with db() as con:
        return con.execute("SELECT * FROM questions ORDER BY id").fetchall()

async def send_question(message: Message, state: FSMContext, index: int):
    qs = get_questions()
    if index >= len(qs):
        data = await state.get_data()
        score = data.get("score", 0)
        await state.clear()
        with db() as con:
            con.execute(
                """INSERT INTO results(telegram_id,name,username,score,answered)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                   name=excluded.name, username=excluded.username,
                   score=excluded.score, answered=excluded.answered""",
                (message.from_user.id, message.from_user.full_name,
                 message.from_user.username, score, len(qs))
            )
        await message.answer(
            f"✅ Тест завершён!\nВаш результат: {score} из {len(qs)*100} баллов.",
            reply_markup=main_menu(message.from_user.id)
        )
        return

    q = qs[index]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"А. {q['option_a']}", callback_data=f"ans:{index}:0")],
        [InlineKeyboardButton(text=f"Б. {q['option_b']}", callback_data=f"ans:{index}:1")],
        [InlineKeyboardButton(text=f"В. {q['option_c']}", callback_data=f"ans:{index}:2")],
        [InlineKeyboardButton(text=f"Г. {q['option_d']}", callback_data=f"ans:{index}:3")]
    ])
    caption = f"❓ Вопрос {index+1}/{len(qs)}\n\n{q['text']}"
    img = BASE_DIR / (q["image_path"] or "")
    if img.exists():
        await message.answer_photo(FSInputFile(img), caption=caption, reply_markup=kb)
    else:
        await message.answer(caption, reply_markup=kb)
    await state.update_data(index=index)

@router.message(F.text == "▶️ Начать тест")
async def quiz_start(message: Message, state: FSMContext):
    await state.set_state(QuizState.active)
    await state.set_data({"index": 0, "score": 0})
    await send_question(message, state, 0)

@router.callback_query(QuizState.active, F.data.startswith("ans:"))
async def answer(callback: CallbackQuery, state: FSMContext):
    _, idx_s, selected_s = callback.data.split(":")
    idx, selected = int(idx_s), int(selected_s)
    data = await state.get_data()
    if data.get("index") != idx:
        await callback.answer("На этот вопрос уже отвечали.", show_alert=True)
        return
    qs = get_questions()
    q = qs[idx]
    score = data.get("score", 0)
    if selected == q["correct"]:
        score += 100
        text = "✅ Верно! +100 баллов"
    else:
        letters = ["А", "Б", "В", "Г"]
        text = f"❌ Неверно. Правильный ответ: {letters[q['correct']]}"
    await callback.answer(text, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.update_data(index=idx+1, score=score)
    await send_question(callback.message, state, idx+1)

@router.message(F.text == "🏆 Рейтинг")
async def rating(message: Message):
    with db() as con:
        rows = con.execute("SELECT * FROM results ORDER BY score DESC, name LIMIT 10").fetchall()
    if not rows:
        await message.answer("Пока никто не завершил тест.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 Рейтинг"]
    for i, r in enumerate(rows):
        prefix = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{prefix} {r['name']} — {r['score']} баллов")
    await message.answer("\n".join(lines))

@router.message(F.text == "⚙️ Админ-панель")
async def admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⚙️ Админ-панель", reply_markup=admin_menu())

@router.message(F.text == "⬅️ Главное меню")
async def back(message: Message):
    await message.answer("Главное меню", reply_markup=main_menu(message.from_user.id))

@router.message(F.text == "👥 Игроки")
async def players_menu(message: Message):
    if not is_admin(message.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить игрока", callback_data="player:add")],
        [InlineKeyboardButton(text="📋 Список игроков", callback_data="player:list")]
    ])
    await message.answer("👥 Управление игроками", reply_markup=kb)

@router.callback_query(F.data == "player:add")
async def player_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(AddPlayer.telegram_id)
    await callback.message.answer("Отправьте Telegram ID игрока:")
    await callback.answer()

@router.message(AddPlayer.telegram_id)
async def player_id(message: Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Нужен числовой Telegram ID.")
        return
    await state.update_data(telegram_id=int(message.text.strip()))
    await state.set_state(AddPlayer.name)
    await message.answer("Введите имя игрока:")

@router.message(AddPlayer.name)
async def player_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddPlayer.photo)
    await message.answer("Отправьте фотографию игрока или напишите «Пропустить».")

@router.message(AddPlayer.photo)
async def player_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photo_file_id = message.photo[-1].file_id if message.photo else None
    if not photo_file_id and (message.text or "").lower() != "пропустить":
        await message.answer("Отправьте фото или напишите «Пропустить».")
        return
    with db() as con:
        con.execute(
            """INSERT INTO players(telegram_id,name,photo_file_id)
               VALUES(?,?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET
               name=excluded.name, photo_file_id=excluded.photo_file_id""",
            (data["telegram_id"], data["name"], photo_file_id)
        )
    await state.clear()
    await message.answer("✅ Игрок сохранён.", reply_markup=admin_menu())

@router.callback_query(F.data == "player:list")
async def player_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    with db() as con:
        rows = con.execute("SELECT * FROM players ORDER BY name").fetchall()
    text = "📋 Игроки:\n" + ("\n".join(f"• {r['name']} — ID {r['telegram_id']}" for r in rows) if rows else "Список пуст.")
    await callback.message.answer(text)
    await callback.answer()

@router.message(F.text == "❓ Вопросы")
async def questions_menu(message: Message):
    if not is_admin(message.from_user.id): return
    with db() as con:
        rows = con.execute("SELECT id,text FROM questions ORDER BY id").fetchall()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{r['id']}. {r['text'][:38]}", callback_data=f"qedit:{r['id']}")]
        for r in rows
    ])
    await message.answer("Выберите вопрос для редактирования:", reply_markup=kb)

@router.callback_query(F.data.startswith("qedit:"))
async def choose_q(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    qid = int(callback.data.split(":")[1])
    await state.update_data(qid=qid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Текст вопроса", callback_data="qa:text")],
        [InlineKeyboardButton(text="🔤 Варианты ответа", callback_data="qa:options")],
        [InlineKeyboardButton(text="✅ Правильный ответ", callback_data="qa:correct")],
        [InlineKeyboardButton(text="🖼 Изображение", callback_data="qa:image")]
    ])
    await callback.message.answer(f"Редактирование вопроса №{qid}", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("qa:"))
async def q_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    mapping = {
        "text": (EditQuestion.text, "Отправьте новый текст вопроса:"),
        "options": (EditQuestion.options, "Отправьте 4 варианта, каждый с новой строки:"),
        "correct": (EditQuestion.correct, "Отправьте номер правильного ответа: 1, 2, 3 или 4."),
        "image": (EditQuestion.image, "Отправьте новое изображение.")
    }
    st, prompt = mapping[action]
    await state.set_state(st)
    await callback.message.answer(prompt)
    await callback.answer()

@router.message(EditQuestion.text)
async def edit_text(message: Message, state: FSMContext):
    qid = (await state.get_data())["qid"]
    with db() as con:
        con.execute("UPDATE questions SET text=? WHERE id=?", (message.text.strip(), qid))
    await state.clear()
    await message.answer("✅ Текст обновлён.", reply_markup=admin_menu())

@router.message(EditQuestion.options)
async def edit_options(message: Message, state: FSMContext):
    opts = [x.strip() for x in message.text.splitlines() if x.strip()]
    if len(opts) != 4:
        await message.answer("Нужно ровно 4 строки.")
        return
    qid = (await state.get_data())["qid"]
    with db() as con:
        con.execute(
            "UPDATE questions SET option_a=?,option_b=?,option_c=?,option_d=? WHERE id=?",
            (*opts, qid)
        )
    await state.clear()
    await message.answer("✅ Варианты обновлены.", reply_markup=admin_menu())

@router.message(EditQuestion.correct)
async def edit_correct(message: Message, state: FSMContext):
    if message.text not in {"1","2","3","4"}:
        await message.answer("Введите число от 1 до 4.")
        return
    qid = (await state.get_data())["qid"]
    with db() as con:
        con.execute("UPDATE questions SET correct=? WHERE id=?", (int(message.text)-1, qid))
    await state.clear()
    await message.answer("✅ Правильный ответ обновлён.", reply_markup=admin_menu())

@router.message(EditQuestion.image, F.photo)
async def edit_image(message: Message, state: FSMContext, bot: Bot):
    qid = (await state.get_data())["qid"]
    path = BASE_DIR / f"q{qid:02}_custom.jpg"
    await bot.download(message.photo[-1], destination=path)
    with db() as con:
        con.execute("UPDATE questions SET image_path=? WHERE id=?", (str(path.relative_to(BASE_DIR)), qid))
    await state.clear()
    await message.answer("✅ Изображение обновлено.", reply_markup=admin_menu())

async def show_winner(message: Message):
    with db() as con:
        winner = con.execute("SELECT * FROM results ORDER BY score DESC, name LIMIT 1").fetchone()
        player = con.execute("SELECT * FROM players WHERE telegram_id=?", (winner["telegram_id"],)).fetchone() if winner else None
    if not winner:
        await message.answer("Нет завершённых результатов.")
        return
    name = player["name"] if player else winner["name"]
    caption = (
        "🏆 ПОБЕДИТЕЛЬ ВИКТОРИНЫ\n\n"
        f"🥇 {name}\n"
        f"⭐ {winner['score']} баллов\n"
        f"🆔 Telegram ID: {winner['telegram_id']}"
    )
    if player and player["photo_file_id"]:
        await message.answer_photo(player["photo_file_id"], caption=caption)
    else:
        # Try Telegram profile photo automatically
        try:
            photos = await message.bot.get_user_profile_photos(winner["telegram_id"], limit=1)
            if photos.total_count:
                await message.answer_photo(photos.photos[0][-1].file_id, caption=caption)
                return
        except Exception:
            pass
        await message.answer(caption + "\n\n📷 Фото не найдено. Добавьте его в разделе «Игроки».")

@router.message(F.text == "🏁 Завершить игру")
async def finish_game(message: Message):
    if not is_admin(message.from_user.id): return
    await show_winner(message)

@router.message(F.text == "🧹 Сбросить результаты")
async def reset(message: Message):
    if not is_admin(message.from_user.id): return
    with db() as con:
        con.execute("DELETE FROM results")
    await message.answer("✅ Результаты очищены.", reply_markup=admin_menu())

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Добавьте BOT_TOKEN в файл .env")
    init_db()
    bot = Bot(BOT_TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
