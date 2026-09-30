import asyncio
import logging
import os
import threading

from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from google import genai

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Не заданы переменные окружения BOT_TOKEN или GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3.5-flash"

# --- FLASK (будет запущен в отдельном потоке) ---
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

@app.route("/health")
def health():
    return "OK"

def run_flask():
    """Запускает Flask в отдельном потоке. Flask — синхронный, поэтому ошибки set_wakeup_fd не будет."""
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- TELEGRAM BOT (в главном потоке) ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Отправь мне задание, и я передам его в Gemini. 🤖")

@dp.message()
async def handle_prompt(message: types.Message):
    if not message.text:
        return
    prompt = message.text.strip()
    if not prompt:
        return

    temp_message = await message.answer("🤔 Думаю...")
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(model=MODEL_NAME, contents=prompt)
        )
        answer = response.text
        if len(answer) > 4000:
            for i in range(0, len(answer), 4000):
                await message.answer(answer[i:i+4000])
            await temp_message.delete()
        else:
            await temp_message.edit_text(answer)
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        await temp_message.edit_text("😔 Ошибка при обращении к Gemini. Попробуйте позже.")

if __name__ == "__main__":
    # 1. Запускаем Flask в фоновом потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. Запускаем aiogram в главном потоке
    logger.info("Запуск Telegram-бота...")
    asyncio.run(bot.delete_webhook(drop_pending_updates=True))
    asyncio.run(dp.start_polling(bot))
