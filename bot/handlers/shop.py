"""
Платежи Telegram Stars (XTR).

Поток:
  1. WebApp вызывает POST /api/stars/order → сервер шлёт sendInvoice в Bot API
  2. Пользователь оплачивает в Telegram → боту приходит UpdateBotPrecheckoutQuery
  3. После оплаты приходит service-сообщение MessageActionPaymentSentMe
  4. Товар начисляется через bot.web.server.apply_purchase()
"""
from __future__ import annotations

import logging

from telethon import events, functions, types

log = logging.getLogger("simroulette.shop")


def _payload_str(raw) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", "replace")
    return str(raw or "")


def _extract_payment(message) -> dict | None:
    """Достаёт данные успешной оплаты из service-сообщения."""
    action = getattr(message, "action", None)
    if isinstance(action, types.MessageActionPaymentSentMe):
        return {
            "currency": action.currency,
            "total_amount": action.total_amount,
            "payload": _payload_str(action.payload),
        }
    return None


def _is_payment(message) -> bool:
    return _extract_payment(message) is not None


def register(bot, apply_purchase) -> None:
    """apply_purchase(user_id, sku, payload) -> (ok, title) — корутина из server.py."""

    @bot.on(events.Raw(types.UpdateBotPrecheckoutQuery))
    async def pre_checkout(event: events.Raw.Event) -> None:
        upd = getattr(event, "update", event)
        payload = _payload_str(getattr(upd, "payload", b""))
        query_id = getattr(upd, "query_id", 0)

        if not payload.startswith("simroul:"):
            try:
                await bot(functions.messages.SetBotPrecheckoutResultsRequest(
                    query_id=query_id, success=False,
                    error="Неверный товар. Откройте игру и попробуйте ещё раз."))
            except Exception as e:
                log.warning("pre_checkout reject error: %s", e)
            return
        try:
            await bot(functions.messages.SetBotPrecheckoutResultsRequest(
                query_id=query_id, success=True))
            log.info("pre_checkout ok: %s", payload)
        except Exception as e:
            log.warning("pre_checkout accept error: %s", e)

    @bot.on(events.NewMessage(func=_is_payment))
    async def successful_payment(event: events.NewMessage.Event) -> None:
        pay = _extract_payment(event.message)
        if not pay:
            return
        parts = pay["payload"].split(":")
        if len(parts) < 3 or parts[0] != "simroul":
            log.warning("Платёж с неизвестным payload: %r", pay["payload"])
            return
        try:
            user_id, sku = int(parts[1]), parts[2]
        except Exception:
            log.warning("Битый payload: %r", pay["payload"])
            return

        try:
            ok, title = await apply_purchase(user_id, sku, pay["payload"])
        except Exception as e:
            log.error("apply_purchase failed: %s", e)
            ok, title = False, "ошибка"

        if ok:
            await event.respond(
                f"✅ Оплата прошла!\n\n"
                f"Товар: <b>{title}</b>\n"
                f"Оплачено: {pay['total_amount']} ⭐\n\n"
                f"Открывай игру — бонус уже зачислен.",
                parse_mode="html",
            )
            log.info("Stars-покупка: user=%s sku=%s", user_id, sku)
        else:
            await event.respond("⚠️ Товар не удалось зачислить. Напиши админу.")
            log.error("Не удалось зачислить: user=%s sku=%s", user_id, sku)
