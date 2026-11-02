"""Проверка SOCKS5-прокси и связи с Telegram.

Запуск (из корня проекта, с активированным venv):

    python tests/check_proxy.py

Скрипт берёт параметры из bot/config.py (PROXY) и:
  1) устанавливает SOCKS5-туннель,
  2) делает запрос getMe к api.telegram.org через прокси,
  3) проверяет, что бот-токен валиден.

Ничего не пишет в БД и не запускает бота.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import ClientSession          # noqa: E402
from aiohttp_socks import ProxyConnector   # noqa: E402

from bot.config import BOT_TOKEN, PROXY, proxy_url  # noqa: E402


async def main() -> int:
    url = proxy_url()
    print("Прокси:", f"{PROXY['addr']}:{PROXY['port']}" if PROXY else "выключен")
    if url and PROXY:
        print("Логин :", PROXY["username"], "| rdns:", PROXY.get("rdns"))

    connector = ProxyConnector.from_url(url, rdns=PROXY.get("rdns", True)) if url else None
    try:
        async with ClientSession(connector=connector) as s:
            async with s.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=20
            ) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"❌ Ошибка сети через прокси: {type(e).__name__}: {e}")
        return 1
    finally:
        if connector is not None:
            await connector.close()

    if not data.get("ok"):
        print("❌ Telegram ответил ошибкой:", data.get("description"))
        return 2

    me = data["result"]
    print(f"🟢 Прокси работает. Бот: @{me.get('username')} (id {me.get('id')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
