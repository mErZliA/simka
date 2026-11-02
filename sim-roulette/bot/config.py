import os

from dotenv import load_dotenv

load_dotenv()

# ---------- Telegram ----------
API_ID     = int(os.getenv("API_ID", "0"))
API_HASH   = os.getenv("API_HASH", "")
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")

# ---------- Игра ----------
CHANNEL    = os.getenv("CHANNEL", "@simroulette_news")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com/")
WEBAPP_DIR = os.getenv("WEBAPP_DIR", "./webapp")
PORT       = int(os.getenv("PORT", "8080"))

# ---------- Админы ----------
ADMIN_IDS  = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

# ---------- Режим ----------
# DEV_MODE=1 — авторизация по uid без initData Telegram (тестирование вне Telegram)
DEV_MODE   = os.getenv("DEV_MODE", "1").strip() not in ("0", "false", "no")

# ---------- Прокси ----------
# SOCKS5-прокси для Telegram (Telethon + aiohttp).
# Значения по умолчанию — боевой прокси; можно переопределить в .env.
PROXY = {
    "proxy_type": "socks5",
    "addr": os.getenv("PROXY_ADDR", "178.208.87.21"),
    "port": int(os.getenv("PROXY_PORT", "1080")),
    "username": os.getenv("PROXY_USER", "revolution"),
    "password": os.getenv("PROXY_PASS", "fZESz14Ay5GS"),
    "rdns": os.getenv("PROXY_RDNS", "1").strip() not in ("0", "false", "no"),
}

# Отключить прокси целиком: PROXY_ENABLED=0
if os.getenv("PROXY_ENABLED", "1").strip() in ("0", "false", "no"):
    PROXY = None


def proxy_url() -> str | None:
    """socks5://user:pass@host:port — для aiohttp/aiohttp-socks."""
    if not PROXY:
        return None
    auth = ""
    if PROXY.get("username"):
        auth = f"{PROXY['username']}:{PROXY['password']}@"
    return f"socks5://{auth}{PROXY['addr']}:{PROXY['port']}"


# ---------- Валидация ----------
def validate() -> list[str]:
    errs = []
    if not API_ID:    errs.append("API_ID не задан")
    if not API_HASH:  errs.append("API_HASH не задан")
    if not BOT_TOKEN: errs.append("BOT_TOKEN не задан")
    return errs
