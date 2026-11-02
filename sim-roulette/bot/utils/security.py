import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from bot.config import BOT_TOKEN


def verify_init_data(init_data: str) -> dict | None:
    """Официальная схема проверки WebApp initData."""
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", "")
        if not received_hash:
            return None

        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calc, received_hash):
            return None

        return json.loads(data.get("user", "{}"))
    except Exception:
        return None