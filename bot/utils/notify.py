"""Автоматические сообщения в новостной канал о техперерыве.

Использует Bot API sendMessage — не требует запущенного Telethon-клиента,
поэтому работает и из чистого HTTP-режима (превью/тесты).
"""
from __future__ import annotations

import datetime as dt
import html
import logging

from aiohttp import ClientSession, ClientTimeout

from bot.config import BOT_TOKEN, CHANNEL, MAINTENANCE_NOTIFY, WEBAPP_URL

log = logging.getLogger("simroulette.notify")


def _msk_time(ms: int) -> str:
    if not ms:
        return ""
    t = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc) + dt.timedelta(hours=3)
    return t.strftime("%H:%M")


def _text(kind: str, message: str, start_at: int, end_at: int) -> str:
    msg = html.escape(message or "Плановые технические работы")
    if kind == "scheduled":
        when = _msk_time(start_at)
        tail = f"⏳ Игра закроется в {when} МСК." if when else "⏳ Игра скоро закроется."
        return (f"🔧 <b>Технический перерыв</b>\n\n{msg}\n\n{tail}\n"
                "Успей забрать бонус дня и закончить сделки!")
    if kind == "start":
        until = f" Ориентировочное открытие: {_msk_time(end_at)} МСК." if end_at else ""
        return (f"🔧 <b>Технический перерыв</b>\n\n{msg}\n\n"
                f"Игра уже закрыта.{until}\nСообщим, когда всё заработает.")
    if kind == "auto_end":
        return ("✅ <b>Работы завершены</b>\n\n"
                "Игра снова открыта автоматически. Заходи — тебя ждёт бонус дня 🎁")
    return ("✅ <b>Работы завершены</b>\n\n"
            "Игра снова открыта! Заходи — тебя ждёт бонус дня 🎁")


async def notify_maintenance(kind: str, message: str = "",
                             start_at: int = 0, end_at: int = 0) -> bool:
    """Отправить пост в канал. Возвращает False при ошибке (игра не падает)."""
    if not MAINTENANCE_NOTIFY:
        return False
    if not BOT_TOKEN or BOT_TOKEN.startswith("preview"):
        return False
    body = {
        "chat_id": CHANNEL,
        "text": _text(kind, message, start_at, end_at),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [[{"text": "🎰 Играть", "url": WEBAPP_URL}]]},
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with ClientSession() as session:
            async with session.post(url, json=body, timeout=ClientTimeout(total=8)) as resp:
                data = await resp.json()
        if not data.get("ok"):
            log.warning("notify_maintenance: %s", data.get("description"))
            return False
        return True
    except Exception as e:
        log.warning("notify_maintenance error: %s", e)
        return False
