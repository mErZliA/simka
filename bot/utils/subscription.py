"""Pooled Telegram membership checks with per-user caching and request coalescing."""
from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from aiohttp_socks import ProxyConnector

from bot.config import BOT_TOKEN, CHANNEL, PROXY

log = logging.getLogger("simroulette.subscription")

SUBSCRIBED_STATUSES = {"member", "administrator", "creator", "restricted"}
_CACHE_TTL_SECONDS = 60
_NEGATIVE_CACHE_TTL_SECONDS = 5
_CONNECTOR_LIMIT = 100
_subscription_cache: dict[int, tuple[bool, float]] = {}
_inflight: dict[int, asyncio.Task[bool]] = {}
_session: ClientSession | None = None
_last_cache_prune = 0.0


def _make_connector():
    """Create one reusable, bounded connector for Telegram Bot API requests."""
    if not PROXY:
        return TCPConnector(limit=_CONNECTOR_LIMIT, ttl_dns_cache=300, keepalive_timeout=30)
    try:
        url = (
            f"socks5://{PROXY['username']}:{PROXY['password']}"
            f"@{PROXY['addr']}:{PROXY['port']}"
        )
        return ProxyConnector.from_url(
            url, rdns=PROXY.get("rdns", True), limit=_CONNECTOR_LIMIT,
            keepalive_timeout=30,
        )
    except Exception:
        log.exception("Не удалось создать прокси-коннектор")
        return TCPConnector(limit=_CONNECTOR_LIMIT, ttl_dns_cache=300, keepalive_timeout=30)


async def _get_session() -> ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = ClientSession(
            connector=_make_connector(),
            timeout=ClientTimeout(total=8, connect=3),
        )
    return _session


async def close_subscription_client() -> None:
    """Cancel pending checks and close the pooled client during shutdown."""
    global _session
    tasks = list(set(_inflight.values()))
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _inflight.clear()
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def _fetch_subscription(user_id: int) -> bool:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не задан: невозможно проверить подписку на %s", CHANNEL)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
    try:
        session = await _get_session()
        async with session.get(
            url,
            params={"chat_id": CHANNEL, "user_id": user_id},
        ) as response:
            data = await response.json()
        if not data or not data.get("ok"):
            log.warning("getChatMember returned an error for channel %s", CHANNEL)
            return False

        member = data.get("result") or {}
        status = member.get("status")
        subscribed = status in SUBSCRIBED_STATUSES
        if status == "restricted":
            subscribed = bool(member.get("is_member"))

        checked_at = time.monotonic()
        ttl = _CACHE_TTL_SECONDS if subscribed else _NEGATIVE_CACHE_TTL_SECONDS
        _subscription_cache[user_id] = (subscribed, checked_at + ttl)
        return subscribed
    except Exception as exc:
        log.warning("getChatMember error: %s", exc)
        return False
    finally:
        now = time.monotonic()
        global _last_cache_prune
        if len(_subscription_cache) > 10_000 and now - _last_cache_prune >= 30:
            _last_cache_prune = now
            for cached_id, (_, expires_at) in list(_subscription_cache.items()):
                if expires_at <= now:
                    _subscription_cache.pop(cached_id, None)


async def check_subscription(user_id: int, *, force: bool = False) -> bool:
    """Verify membership, reusing a fresh result or an in-flight check for the same user."""
    uid = int(user_id)
    now = time.monotonic()
    cached = _subscription_cache.get(uid)
    if not force and cached and cached[1] > now:
        return cached[0]
    if not force and uid in _inflight:
        return await asyncio.shield(_inflight[uid])

    task = asyncio.create_task(_fetch_subscription(uid))
    _inflight[uid] = task
    try:
        return await asyncio.shield(task)
    finally:
        if _inflight.get(uid) is task:
            _inflight.pop(uid, None)
