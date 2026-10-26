import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from bot.config import BOT_TOKEN, INITDATA_FUTURE_SKEW, INITDATA_MAX_AGE


def verify_init_data(init_data: str) -> dict | None:
    """Официальная схема проверки WebApp initData + проверка свежести подписи.

    Возвращает объект user из initData или None, если подпись не совпала,
    данные устарели или payload повреждён.
    """
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", "")
        if not received_hash:
            return None

        # Защита от повторного использования перехваченного initData.
        try:
            auth_date = int(data.get("auth_date", "0"))
        except (TypeError, ValueError):
            return None
        now = time.time()
        if not auth_date or auth_date > now + INITDATA_FUTURE_SKEW:
            return None
        if INITDATA_MAX_AGE and now - auth_date > INITDATA_MAX_AGE:
            return None

        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calc, received_hash):
            return None

        user = json.loads(data.get("user", "{}"))
        if not isinstance(user, dict) or not isinstance(user.get("id"), int) or user["id"] <= 0:
            return None
        return user
    except Exception:
        return None
