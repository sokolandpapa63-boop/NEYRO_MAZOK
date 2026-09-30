import asyncio
import base64
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from google import genai

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

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

# --- КЛИЕНТ GEMINI ---
client = genai.Client(api_key=GEMINI_API_KEY)
TEXT_MODEL = "gemini-3.8-flash"
IMAGE_MODEL = "gemini-3.1-flash-image"

# --- BOT ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот на Gemini. Могу ответить текстом или нарисовать картинку.\n\n"
        "💬 Просто напиши вопрос.\n"
        "🎨 Напиши /image <описание> для генерации картинки."
    )

@dp.message(Command("image"))
async def cmd_image(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    if not prompt:
        await message.answer("Напиши, что нарисовать. Например: /image кот в космосе")
        return

    temp_message = await message.answer("🎨 Рисую...")

    try:
        loop = asyncio.get_running_loop()
        interaction = await loop.run_in_executor(
            None,
            lambda: client.interactions.create(
                model=IMAGE_MODEL,
                input=prompt,
                response_format={"type": "image"}
            )
        )

        image_bytes = None
        # Пробуем разные способы достать картинку
        if hasattr(interaction, 'output_image') and interaction.output_image:
            image_bytes = base64.b64decode(interaction.output_image.data)
        elif hasattr(interaction, 'steps'):
            for step in interaction.steps:
                if step.type == "model_output":
                    for content_block in step.content:
                        if content_block.type == "image":
                            image_bytes = base64.b64decode(content_block.data)
                            break

        if not image_bytes:
            raise Exception("Модель не вернула изображение")

        photo = types.BufferedInputFile(image_bytes, filename="image.png")
        await message.answer_photo(photo=photo, caption=f"🎨 {prompt}")
        await temp_message.delete()

    except Exception as e:
        logger.error(f"Ошибка генерации картинки: {type(e).__name__}: {e}")
        await temp_message.edit_text("😔 Не удалось нарисовать картинку. Попробуйте позже.")

@dp.message()
async def handle_prompt(message: types.Message):
    if not message.text:
        return
    prompt = message.text.strip()
    if not prompt:
        return

    temp_message = await message.answer("🤔 Думаю...")
    try:
        loop = asyncio.get_running_loop()
        interaction = await loop.run_in_executor(
            None,
            lambda: client.interactions.create(
                model=TEXT_MODEL,
                input=prompt
            )
        )
        answer = interaction.output_text

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
        logger.error(f"Ошибка Gemini (текст): {type(e).__name__}: {e}")
        await temp_message.edit_text("😔 Ошибка при обращении к Gemini. Попробуйте позже.")

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
