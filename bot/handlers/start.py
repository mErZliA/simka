import asyncio

from telethon import events
from telethon.tl import types

from bot.config import CHANNEL, WEBAPP_URL
from bot.utils.subscription import check_subscription

CH_URL = f"https://t.me/{CHANNEL.lstrip('@')}"


# ---------- Хелперы для кнопок (в Telethon нет Button.web_app) ----------
def _webapp_button(text: str, url: str):
    """Inline-кнопка, открывающая WebApp."""
    btn = types.KeyboardButtonWebView(text=text, url=url)
    # На случай, если конструктор не проставил флаг inline
    btn._is_inline = True
    return btn


def _url_button(text: str, url: str):
    btn = types.KeyboardButtonUrl(text=text, url=url)
    btn._is_inline = True
    return btn


def _cb_button(text: str, data: bytes):
    btn = types.KeyboardButtonCallback(text=text, data=data)
    btn._is_inline = True
    return btn


def _row(*buttons):
    return types.KeyboardButtonRow(list(buttons))


def _inline_markup(*rows):
    return types.ReplyInlineMarkup(list(rows))


# ---------- Хендлеры ----------
def register(bot):

    @bot.on(events.NewMessage(pattern=r"^/start(?:\s|$)"))
    async def cmd_start(event: events.NewMessage.Event) -> None:
        sender = event.sender
        name = getattr(sender, "first_name", "друг") or "друг"
        # Warm the server-side subscription cache without delaying the reply.
        asyncio.create_task(check_subscription(event.sender_id))
        header = "🎰 **SIM Roulette**\n"
        hello = f"Привет, {name}! 👋\n\n"
        body = (
            "Добро пожаловать в мир коллекционных SIM-номеров!\n\n"
            "🔹 Крути рулетку и собирай редкие номера\n"
            "🔹 Продавай, обменивайся и улучшай коллекцию\n"
            "🔹 Прокачивай уровень и выполняй задания\n"
            "🔹 Соревнуйся с другими игроками в топе\n\n"
            f"Для входа подпишись на канал {CHANNEL} — проверка произойдёт в приложении."
        )

        markup = _inline_markup(
            _row(_webapp_button("🎰 Играть", WEBAPP_URL)),
            _row(
                _url_button("📣 Канал", CH_URL),
                _cb_button("ℹ️ Помощь", b"help"),
            ),
            _row(_cb_button("🏆 Топ игроков", b"top")),
        )

        await event.respond(
            f"{header}\n{hello}{body}",
            buttons=markup,
            parse_mode="md",
            link_preview=False,
        )

    @bot.on(events.CallbackQuery(data=b"help"))
    async def cb_help(event: events.CallbackQuery.Event) -> None:
        text = (
            "ℹ️ **Помощь**\n\n"
            "**Команды**\n"
            "`/start` — открыть меню\n"
            "`/app` — быстрый запуск игры\n"
            "`/help` — эта справка\n"
            "`/top` — топ игроков\n"
            "`/me` — ваш профиль\n\n"
            "**Основное**\n"
            "• Каждый номер уникален — `страна + оператор + номер`\n"
            "• Редкие номера стоят дороже обычных в десятки раз\n"
            "• Крутить можно в 3 скоростях: Обычная, Быстрая, Мгновенная\n"
            "• Апгрейд номера может повысить его на +1..+4 ранга\n"
        )
        markup = _inline_markup(_row(_cb_button("⬅️ Назад", b"back_main")))
        try:
            await event.answer()
            await event.edit(text, buttons=markup, parse_mode="md")
        except Exception:
            pass

    @bot.on(events.CallbackQuery(data=b"top"))
    async def cb_top(event: events.CallbackQuery.Event) -> None:
        await event.answer("🏆 Открывай вкладку «Рейтинг» в игре", alert=True)

    @bot.on(events.CallbackQuery(data=b"back_main"))
    async def cb_back(event: events.CallbackQuery.Event) -> None:
        try:
            await event.answer()
        except Exception:
            pass

        sender = await event.get_sender()
        name = getattr(sender, "first_name", "друг") or "друг"

        markup = _inline_markup(
            _row(_webapp_button("🎰 Играть", WEBAPP_URL)),
            _row(
                _url_button("📣 Канал", CH_URL),
                _cb_button("ℹ️ Помощь", b"help"),
            ),
            _row(_cb_button("🏆 Топ игроков", b"top")),
        )

        try:
            await event.edit(
                f"🎰 **SIM Roulette**\n\nПривет, {name}! 👋\n\nВыбери действие:",
                buttons=markup,
                parse_mode="md",
            )
        except Exception:
            pass