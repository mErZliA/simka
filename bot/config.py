import os
from dotenv import load_dotenv

load_dotenv()

# ---------- Telegram ----------
API_ID     = int(os.getenv("API_ID", "0"))
API_HASH   = os.getenv("API_HASH", "")
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")

# ---------- Игра ----------
CHANNEL    = os.getenv("CHANNEL", "@sim_roulette").strip() or "@sim_roulette"
if CHANNEL == "@simroulette_news":  # migrate the former default channel
    CHANNEL = "@sim_roulette"
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com/")
WEBAPP_DIR = os.getenv("WEBAPP_DIR", "./webapp")
PORT       = int(os.getenv("PORT", "8080"))

# ---------- Админы ----------
ADMIN_IDS  = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

# ---------- Режим ----------
# Dev-auth допускается ТОЛЬКО при явном APP_ENV=development/test.
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()
DEV_MODE_REQUESTED = os.getenv("DEV_MODE", "0").strip().lower() not in ("0", "false", "no", "")
DEV_MODE = DEV_MODE_REQUESTED and APP_ENV in ("development", "dev", "test")

# Максимальный возраст Telegram initData (секунд) и допустимое расхождение часов.
INITDATA_MAX_AGE = int(os.getenv("INITDATA_MAX_AGE", "86400"))
INITDATA_FUTURE_SKEW = int(os.getenv("INITDATA_FUTURE_SKEW", "60"))

# Пути к SQLite и каталогам резервных копий настраиваются отдельно от исходников.
DB_PATH = os.getenv("DB_PATH", "simroulette.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
BACKUP_RETENTION_DAYS = max(1, int(os.getenv("BACKUP_RETENTION_DAYS", "14")))
BACKUP_INTERVAL_HOURS = max(1, int(os.getenv("BACKUP_INTERVAL_HOURS", "24")))
LOG_FILE = os.getenv("LOG_FILE", "./logs/simroulette.log")

# Автопост в новостной канал при старте/стопе техперерыва (1/0).
MAINTENANCE_NOTIFY = os.getenv("MAINTENANCE_NOTIFY", "1").strip().lower() not in ("0", "false", "no", "")

# ---------- Прокси ----------
# Прокси отключён для локальной разработки (прямое подключение к Telegram)
PROXY = None

# ---------- Валидация ----------
def validate() -> list[str]:
    errs = []
    if not API_ID:    errs.append("API_ID не задан")
    if not API_HASH:  errs.append("API_HASH не задан")
    if not BOT_TOKEN: errs.append("BOT_TOKEN не задан")
    if DEV_MODE_REQUESTED and not DEV_MODE:
        errs.append("DEV_MODE=1 разрешён только при APP_ENV=development/dev/test")
    if APP_ENV == "production" and not WEBAPP_URL.startswith("https://"):
        errs.append("WEBAPP_URL в production должен начинаться с https://")
    return errs
