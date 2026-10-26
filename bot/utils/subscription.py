import logging
import time

from aiohttp import ClientSession
from aiohttp_socks import ProxyConnector

from bot.config import BOT_TOKEN, CHANNEL, PROXY

log = logging.getLogger("simroulette.subscription")

SUBSCRIBED_STATUSES = {"member", "administrator", "creator", "restricted"}
_CACHE_TTL_SECONDS = 30
_NEGATIVE_CACHE_TTL_SECONDS = 5
_subscription_cache: dict[int, tuple[bool, float]] = {}


def _make_connector():
    """SOCKS5-коннектор для aiohttp, чтобы api.telegram.org шёл через прокси."""
    if not PROXY:
        return None
    try:
        url = (
            f"socks5://{PROXY['username']}:{PROXY['password']}"
            f"@{PROXY['addr']}:{PROXY['port']}"
        )
        return ProxyConnector.from_url(url, rdns=PROXY.get("rdns", True))
    except Exception as e:
        log.warning("Не удалось создать прокси-коннектор: %s", e)
        return None


async def check_subscription(user_id: int, *, force: bool = False) -> bool:
    """Проверяет подписку через Telegram Bot API; положительный ответ кэшируется на 30 секунд."""
    now = time.monotonic()
    cached = _subscription_cache.get(int(user_id))
    if not force and cached and cached[1] > now:
        return cached[0]
    _subscription_cache.pop(int(user_id), None)

    if not BOT_TOKEN:
        log.error("BOT_TOKEN не задан: невозможно проверить подписку на %s", CHANNEL)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
    connector = _make_connector()
    data = None

    try:
        async with ClientSession(connector=connector) as s:
            async with s.get(
                url,
                params={"chat_id": CHANNEL, "user_id": user_id},
                timeout=15,
            ) as r:
                data = await r.json()
    except Exception as e:
        log.warning("getChatMember error: %s", e)
        return False
    finally:
        if connector is not None:
            try:
                await connector.close()
            except Exception:
                pass

    if not data or not data.get("ok"):
        log.warning("getChatMember returned an error for channel %s", CHANNEL)
        return False

    member = data.get("result") or {}
    status = member.get("status")
    subscribed = status in SUBSCRIBED_STATUSES
    if status == "restricted":
        subscribed = bool(member.get("is_member"))

    # Положительный статус живёт 30 секунд; короткий отрицательный кэш снижает
    # нагрузку на Telegram при повторных запросах из закрытого приложения.
    ttl = _CACHE_TTL_SECONDS if subscribed else _NEGATIVE_CACHE_TTL_SECONDS
    checked_at = time.monotonic()
    _subscription_cache[int(user_id)] = (subscribed, checked_at + ttl)
    if len(_subscription_cache) > 10_000:
        for cached_id, (_, expires_at) in list(_subscription_cache.items()):
            if expires_at <= checked_at:
                _subscription_cache.pop(cached_id, None)
    return subscribed
