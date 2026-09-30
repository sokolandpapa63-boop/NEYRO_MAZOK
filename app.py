import asyncio
import logging
import os
import io

from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from openai import OpenAI

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

missing = [name for name, val in [
    ("BOT_TOKEN", BOT_TOKEN),
    ("GROQ_API_KEY", GROQ_API_KEY),
    ("RENDER_EXTERNAL_URL", RENDER_EXTERNAL_URL),
    ("WEBHOOK_SECRET", WEBHOOK_SECRET),
] if not val]

if missing:
    raise ValueError(f"Не заданы переменные окружения: {', '.join(missing)}")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- КЛИЕНТ GROQ (для текста) ---
# Groq использует OpenAI-совместимый API [citation:5]
groq_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)
MODEL_NAME = "llama-3.1-8b-instant"  # быстрая модель с большими лимитами [citation:3][citation:9]

# --- TELEGRAM BOT ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ФУНКЦИЯ ГЕНЕРАЦИИ КАРТИНКИ (Pollinations) ---
async def generate_image(prompt: str) -> bytes:
    """
    Генерирует картинку через Pollinations и возвращает байты.
    Pollinations не требует API-ключа для базовых моделей [citation:8][citation:13].
    """
    # Кодируем промпт для URL
    import urllib.parse
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"

    async with ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"Pollinations вернул статус {response.status}")
            return await response.read()

# --- ХЭНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот с двумя режимами:\n\n"
        "💬 Просто напиши мне вопрос — отвечу текстом (Groq).\n"
        "🎨 Напиши /image <описание> — нарисую картинку (Pollinations)."
    )

@dp.message(Command("image"))
async def cmd_image(message: types.Message):
    """Обработчик команды /image"""
    # Извлекаем промпт после команды
    prompt = message.text.replace("/image", "").strip()
    if not prompt:
        await message.answer("Напиши, что нарисовать. Например: /image кот в космосе")
        return

    temp_message = await message.answer("🎨 Рисую...")

    try:
        image_bytes = await generate_image(prompt)

        # Отправляем картинку
        photo = types.BufferedInputFile(image_bytes, filename="image.png")
        await message.answer_photo(photo=photo, caption=f"🎨 {prompt}")
        await temp_message.delete()

    except Exception as e:
        logger.error(f"Ошибка генерации картинки: {type(e).__name__}: {e}")
        await temp_message.edit_text("😔 Не удалось нарисовать картинку. Попробуйте позже.")

@dp.message()
async def handle_prompt(message: types.Message):
    """Обработчик всех остальных текстовых сообщений (текстовый режим)"""
    if not message.text:
        return
    prompt = message.text.strip()
    if not prompt:
        return

    temp_message = await message.answer("🤔 Думаю...")
    try:
        # Groq — синхронный вызов, выносим в отдельный поток
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: groq_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}]
            )
        )
        answer = response.choices[0].message.content

        if not answer:
            await temp_message.edit_text("😔 Модель вернула пустой ответ.")
            return

        if len(answer) > 4000:
            for i in range(0, len(answer), 4000):
                await message.answer(answer[i:i+4000])
            await temp_message.delete()
        else:
            await temp_message.edit_text(answer)

    except Exception as e:
        logger.error(f"Ошибка Groq: {type(e).__name__}: {e}")
        await temp_message.edit_text("😔 Ошибка при обращении к Groq. Попробуйте позже.")

# --- ВЕБХУК ---
async def on_startup(bot: Bot):
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    await bot.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    logger.info(f"Вебхук установлен: {webhook_url}")

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()
    logger.info("Вебхук удалён")

# --- ЗАПУСК ---
def main():
    app = web.Application()

    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    )
    handler.register(app, path="/webhook")

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    setup_application(app, dp, bot=bot)

    async def health(request):
        return web.Response(text="OK")
    app.router.add_get("/health", health)
    app.router.add_get("/", health)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
