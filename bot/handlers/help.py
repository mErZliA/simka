from telethon import events
from telethon.tl import types

from bot.config import WEBAPP_URL


def _webapp_button(text: str, url: str):
    btn = types.KeyboardButtonWebView(text=text, url=url)
    btn._is_inline = True
    return btn


def _row(*buttons):
    return types.KeyboardButtonRow(list(buttons))


def _inline_markup(*rows):
    return types.ReplyInlineMarkup(list(rows))


def register(bot):

    @bot.on(events.NewMessage(pattern=r"^/help$"))
    async def cmd_help(event: events.NewMessage.Event) -> None:
        markup = _inline_markup(_row(_webapp_button("🎰 Открыть игру", WEBAPP_URL)))
        await event.respond(
            "ℹ️ **SIM Roulette**\n\n"
            "**Команды**\n"
            "/start — меню\n"
            "/app — быстрый запуск\n"
            "/help — справка\n"
            "/me — профиль\n\n"
            "**Как играть**\n"
            "1. Открой игру → выбери страну\n"
            "2. Крути номер → он может быть редким\n"
            "3. Оставляй или продавай\n"
            "4. Собирай коллекцию и торгуй на рынке",
            buttons=markup,
            parse_mode="md",
        )

    @bot.on(events.NewMessage(pattern=r"^/app$"))
    async def cmd_app(event: events.NewMessage.Event) -> None:
        markup = _inline_markup(_row(_webapp_button("🎰 SIM Roulette", WEBAPP_URL)))
        await event.respond("Открываю игру...", buttons=markup)

    @bot.on(events.NewMessage(pattern=r"^/me$"))
    async def cmd_me(event: events.NewMessage.Event) -> None:
        sender = await event.get_sender()
        name = getattr(sender, "first_name", "") or ""
        uname = f"@{sender.username}" if getattr(sender, "username", None) else "—"

        markup = _inline_markup(_row(_webapp_button("📊 Открыть профиль", WEBAPP_URL)))
        await event.respond(
            f"👤 **Профиль**\n\n"
            f"**Имя**: {name}\n"
            f"**Юзернейм**: {uname}\n"
            f"**ID**: `{event.sender_id}`\n\n"
            f"_Полную статистику смотри в игре → вкладка «Профиль»_",
            buttons=markup,
            parse_mode="md",
        )