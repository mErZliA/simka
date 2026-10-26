import logging

from aiohttp import ClientSession
from aiohttp_socks import ProxyConnector

from bot.config import BOT_TOKEN, CHANNEL, PROXY

log = logging.getLogger("simroulette.subscription")

SUBSCRIBED_STATUSES = {"member", "administrator", "creator", "restricted"}


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


async def check_subscription(user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал."""
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
        return False

    return data["result"].get("status") in SUBSCRIBED_STATUSES