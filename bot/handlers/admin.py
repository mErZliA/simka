import logging

from telethon import events
from telethon.tl import types

from bot.config import ADMIN_IDS, WEBAPP_URL

log = logging.getLogger("simroulette.admin")


def _webapp_button(text: str, url: str):
    btn = types.KeyboardButtonWebView(text=text, url=url)
    btn._is_inline = True
    return btn


def _row(*buttons):
    return types.KeyboardButtonRow(list(buttons))


def _inline_markup(*rows):
    return types.ReplyInlineMarkup(list(rows))


def is_admin(uid: int) -> bool:
    """Админы бывают из .env и добавленные через веб-админку — учитываем оба."""
    from bot import db
    return uid in ADMIN_IDS or db.is_admin(uid)


def register(bot):

    @bot.on(events.NewMessage(pattern=r"^/stats$"))
    async def cmd_stats(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            return
        try:
            dialogs = await bot.get_dialogs(limit=None)
            users = sum(1 for d in dialogs if d.is_user and not d.entity.bot)
        except Exception:
            users = "?"
        await event.respond(
            f"📊 **Статистика**\n\n"
            f"👥 Пользователей в диалогах: {users}\n"
            f"🆔 Ваш ID: `{event.sender_id}`"
        )

    @bot.on(events.NewMessage(pattern=r"^/broadcast(?:\s+)(.+)$"))
    async def cmd_broadcast(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            return

        text = event.pattern_match.group(1)
        markup = _inline_markup(_row(_webapp_button("🎰 Играть", WEBAPP_URL)))
        count = 0

        async for dialog in bot.iter_dialogs():
            if not dialog.is_user or dialog.entity.bot:
                continue
            try:
                await bot.send_message(
                    dialog.id,
                    f"📢 **Объявление**\n\n{text}",
                    buttons=markup,
                    parse_mode="md",
                )
                count += 1
            except Exception as e:
                log.warning("Не отправлено %s: %s", dialog.id, e)

        await event.respond(f"✅ Отправлено: {count}")