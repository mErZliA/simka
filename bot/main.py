import asyncio

from telethon import TelegramClient

from bot import db
from bot.config import ADMIN_IDS, API_ID, API_HASH, BOT_TOKEN, PROXY, validate
from bot.handlers import admin as h_admin
from bot.handlers import help as h_help
from bot.handlers import shop as h_shop
from bot.handlers import start as h_start
from bot.utils.logger import setup_logger
from bot.web import server as web_server

log = setup_logger()


async def main() -> None:
    errs = validate()
    if errs:
        for e in errs:
            log.error("❌ %s", e)
        return

    # БД + админы из .env
    db.connect()
    for admin_id in ADMIN_IDS:
        db.admin_add(admin_id)
    log.info("✅ БД готова: %s", db.DB_PATH)

    bot = TelegramClient(
        "simroulette_bot",
        API_ID,
        API_HASH,
        proxy=PROXY,
    )
    await bot.start(bot_token=BOT_TOKEN)

    me = await bot.get_me()
    log.info("✅ Бот @%s запущен", me.username)

    # Регистрация хендлеров
    h_start.register(bot)
    h_help.register(bot)
    h_admin.register(bot)
    h_shop.register(bot, web_server.apply_purchase)

    # HTTP-сервер (API + WebApp)
    runner = await web_server.start_server()

    try:
        await bot.run_until_disconnected()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("🛑 Бот остановлен")