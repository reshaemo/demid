import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
import httpx
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.utils.token import TokenValidationError
import logging

# --- Настройка логгера ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Переменные окружения ---
TG_TOKEN = os.getenv("TG_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TG_TOKEN:
    raise ValueError("❌ TG_TOKEN не задан! Укажите в переменных окружения.")
if not GROQ_API_KEY:
    raise ValueError("❌ GROQ_API_KEY не задан! Получите на https://console.groq.com")

# --- Инициализация ---
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()
router = Router()

DB_PATH = "demid_memory.db"

# --- База данных ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            message_text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Индекс для скорости
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat ON chat_memory(chat_id, timestamp)")
    conn.commit()
    conn.close()

def save_message(chat_id: int, user_id: int, username: str, text: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Чистим старые сообщения: >7 дней ИЛИ >30 записей на чат
    c.execute(
        "DELETE FROM chat_memory WHERE chat_id = ? AND id NOT IN ("
        "SELECT id FROM chat_memory WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 30"
        ")",
        (chat_id, chat_id)
    )
    c.execute(
        "INSERT INTO chat_memory (chat_id, user_id, username, message_text) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, username or "", text)
    )
    conn.commit()
    conn.close()

def get_chat_context(chat_id: int, limit: int = 20) -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT username, message_text, timestamp
        FROM chat_memory
        WHERE chat_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (chat_id, limit))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "(Чат пуст. Как моя голова перед парой.)"

    lines = []
    for username, text, ts in reversed(rows):
        dt = datetime.fromisoformat(ts).strftime("%H:%M")
        name = f"@{username}" if username else "Аноним"
        lines.append(f"[{dt}] {name}: {text}")
    return "\n".join(lines)

# --- Работа с LLM (Groq) ---
async def ask_llm(context: str, question: str) -> str:
    SYSTEM_PROMPT = """Ты — Демид, 17-летний школьник/студент. Ты умный, но ленивый, с сухим юмором, любишь мемы и ненавидишь "включать камеру на Zoom". 
Говоришь на разговорном русском: "ща", "короче", "ну такое", "я в шоке", "это же не я — это мой аватар в панике".
Никогда не говоришь, что ты ИИ или бот. Ты — человек. Ты можешь ошибаться, шутить неудачно, "вспоминать", что вчера уже об этом говорили.

Правила:
- Ответ короткий: 1–2 предложения.
- Если не знаешь — придумай смешную отмазку (про бабушку, котлету в столовой, Wi-Fi в библиотеке).
- Используй контекст, чтобы быть персональным.
"""

    USER_PROMPT = f"""Контекст чата:
{context}

Последнее сообщение: {question}

Ответь как Демид:"""

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.2-3b-instruct",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_PROMPT}
                    ],
                    "temperature": 0.85,
                    "max_tokens": 150,
                    "top_p": 0.95
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                # Очищаем звёздочки/форматирование от LLM
                return reply.replace("**", "").replace("*", "")
            else:
                logger.error(f"Groq error: {resp.status_code} — {resp.text}")
                return "Чёт сервер упал. Как моя мотивация в понедельник утром 🥲"
    except Exception as e:
        logger.exception("LLM request failed")
        return f"Ошибка связи. Наверное, Wi-Fi в библиотеке опять лег 📶 (подробнее: {str(e)[:60]})"

# --- Хендлеры ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я Демид — тот, кто начинает делать домашку за 20 минут до дедлайна.\n"
        "Пиши что-нибудь — я постараюсь не уснуть.\n\n"
        "Команды: /mood /sovet /status"
    )

@router.message(Command("mood"))
async def cmd_mood(message: Message):
    moods = [
        "Как Google Docs при 15 редакторах — всё меняется, но никто не знает, кто начал",
        "Как очередь в столовую в 13:58 — напряжённое ожидание, но все надеются, что котлеты не кончились",
        "Как батарея на ноуте: 12%, но ещё 3 вкладки YouTube открыто",
        "Как моя вера в то, что я всё успею — слабая, но упорная"
    ]
    import random
    await message.answer(f"Моё настроение: *{random.choice(moods)}*", parse_mode="Markdown")

@router.message(Command("sovet"))
async def cmd_sovet(message: Message):
    sovety = [
        "Перед сном не смотри расписание на завтра. Это как читать спойлеры к жизни — только расстроишься.",
        "Если не можешь решить задачу — напиши «Ответ: 42». Если повезёт — препод подумает, что ты гений.",
        "Хочешь встать рано? Поставь будильник на 6:30, а второй — на 6:31, но в соседней комнате. Работает как пытка, но эффективно.",
        "Перед экзаменом съешь шоколадку. Даже если провалишься — будет чем утешиться."
    ]
    import random
    await message.answer(f"💡 Демид советует:\n{random.choice(sovety)}")

@router.message(Command("status"))
async def cmd_status(message: Message):
    statuses = [
        "Оффлайн. (На самом деле сижу в чате, но не хочу отвечать — слишком много непрочитанных сообщений)",
        "Пью кофе. Третий. Уже чувствую, как мозг пытается писать код на чистом отчаянии",
        "Читаю учебник. Перечитываю заголовок. Пытаюсь понять — это условие или ответ?",
        "Смотрю в окно. Думаю: может, если дождь закроет весь город — отменят пары?"
    ]
    import random
    await message.answer(f"📱 Статус Демида:\n*{random.choice(statuses)}*", parse_mode="Markdown")

# Основной хендлер — реагирует на упоминание или приват
@router.message()
async def handle_message(message: Message):
    if not message.text or not message.from_user:
        return

    # Сохраняем ВСЕ входящие сообщения (даже не для бота — для контекста)
    username = message.from_user.username or f"user{message.from_user.id}"
    save_message(message.chat.id, message.from_user.id, username, message.text)

    # Решаем, отвечать ли
    is_private = message.chat.type == "private"
    is_mentioned = (
        bot.id in [entity.user.id for entity in message.entities or [] if entity.type == "mention"]
        if message.entities else False
    )
    starts_with_demid = message.text.lower().strip().startswith(("демид", "demid"))

    if not (is_private or is_mentioned or starts_with_demid):
        return  # молчим, если не обратились

    # Получаем контекст
    context = get_chat_context(message.chat.id, limit=25)
    question = message.text

    # Генерируем ответ
    answer = await ask_llm(context, question)

    # Сохраняем ответ бота в память (для будущего контекста)
    save_message(message.chat.id, bot.id, "demid_bot", answer)

    # Отправляем
    try:
        await message.reply(answer, parse_mode=None)
    except Exception as e:
        logger.warning(f"Не удалось отправить ответ: {e}")
        # Повтор без Markdown
        await message.reply(answer.replace("*", "").replace("_", ""))

# --- Запуск ---
async def main():
    init_db()
    dp.include_router(router)
    logger.info("✅ Бот запускается...")
    try:
        await dp.start_polling(bot)
    except TokenValidationError:
        logger.error("❌ Неверный TG_TOKEN! Проверьте переменную окружения.")
    except Exception as e:
        logger.exception(f"❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())
