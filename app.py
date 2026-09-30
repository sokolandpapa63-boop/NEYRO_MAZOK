import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from google import genai

# --- ВСЕ ПЕРЕМЕННЫЕ ОБЯЗАТЕЛЬНЫ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

# Если чего-то нет — падаем с понятным сообщением
missing = [name for name, val in [
    ("BOT_TOKEN", BOT_TOKEN),
    ("GEMINI_API_KEY", GEMINI_API_KEY),
    ("RENDER_EXTERNAL_URL", RENDER_EXTERNAL_URL),
    ("WEBHOOK_SECRET", WEBHOOK_SECRET),
] if not val]

if missing:
    raise ValueError(f"Не заданы переменные окружения: {', '.join(missing)}")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-2.5-flash"

# --- BOT ---
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

# --- ВЕБХУК ---
async def on_startup(bot: Bot):
    """Ставим вебхук при старте."""
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    await bot.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,  # уже обязательный, None быть не может
        drop_pending_updates=True,
    )
    logger.info(f"Вебхук установлен: {webhook_url}")

async def on_shutdown(bot: Bot):
    """Убираем вебхук при остановке."""
    await bot.delete_webhook()
    logger.info("Вебхук удалён")

# --- ЗАПУСК ---
def main():
    app = web.Application()

    # Обработчик вебхука (secret_token обязателен)
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    )
    handler.register(app, path="/webhook")

    # Регистрируем startup/shutdown хуки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Health-check для UptimeRobot
    async def health(request):
        return web.Response(text="OK")
    app.router.add_get("/health", health)
    app.router.add_get("/", health)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
