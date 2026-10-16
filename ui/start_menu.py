"""Private-chat start menu UI."""

from html import escape
from ipaddress import ip_address
from urllib.parse import urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

_BUTTON_STYLES_SUPPORTED = "style" in InlineKeyboardButton.model_fields


def main_menu_text(first_name: str) -> str:
    return (
        "☢️ <b>BUNKER SURVIVAL</b>\n\n"
        f"Salom, <b>{escape(first_name)}</b>! 👋\n"
        "Apokalipsisdan omon qolish uchun jamoa tuzing, "
        "kartalaringizni oching va bunkerga kim loyiq ekanini hal qiling.\n\n"
        "O‘yinni boshlash uchun meni guruhga qo‘shing!"
    )


def _valid_webapp_url(url: str | None) -> str | None:
    if not url:
        return None
    candidate = url.strip()
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            return None
        if hostname.lower() == "localhost" or hostname.lower().endswith(".localhost"):
            return None
        try:
            if ip_address(hostname).is_loopback:
                return None
        except ValueError:
            pass
        _ = parsed.port
    except ValueError:
        return None
    return candidate


def main_menu_keyboard(
    bot_username: str,
    webapp_url: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "🎮 Bunker o‘yini",
        url=f"https://t.me/{bot_username}?startgroup=true",
        style="success",
    )
    _add_button(
        builder,
        "🛒 Shop",
        callback_data="menu:shop",
        style="primary",
    )
    _add_button(
        builder,
        "🪙 Coin sotib olish",
        callback_data="menu:buycoins",
        style="primary",
    )
    _add_button(
        builder,
        "👤 Profil",
        callback_data="menu:profile",
        style="primary",
    )
    valid_webapp_url = _valid_webapp_url(webapp_url)
    if valid_webapp_url:
        builder.add(
            InlineKeyboardButton(
                text="🎁 Para Gifts",
                web_app=WebAppInfo(url=valid_webapp_url),
            )
        )
    else:
        _add_button(
            builder,
            "🎁 Para Gifts",
            callback_data="menu:gifts_unconfigured",
            style="primary",
        )
    _add_button(
        builder,
        "📖 Qo‘llanma",
        callback_data="menu:guide",
        style="primary",
    )
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def guide_text() -> str:
    return (
        "📖 <b>BUNKER SURVIVAL — QO‘LLANMA</b>\n\n"
        "<b>1. O‘yinni ochish</b>\n"
        "Telegram guruhiga qo‘shilgach, guruhda <code>/bunker</code> "
        "buyrug‘ini yuboring. Buyruqni yuborgan kishi xona egasi bo‘ladi "
        "va avtomatik qatnashadi.\n\n"
        "<b>2. Guruhga qo‘shilish</b>\n"
        "Xonadagi <b>➕ Qo‘shilish</b> tugmasini bosing. Xonada "
        "4–16 nafar o‘yinchi bo‘lishi mumkin. Xona egasi kamida 4 kishi "
        "yig‘ilgach o‘yinni boshlaydi.\n\n"
        "<b>3. Kartalar</b>\n"
        "O‘yin boshlanganda kartalar ishtirokchilarga shaxsiy xabarda "
        "yuboriladi. Har raundda har bir o‘yinchi bitta yangi kartani "
        "tanlaydi; tanlangan karta guruhga e’lon qilinadi. Vaqtida "
        "tanlanmasa, bot ochilmagan kartalardan birini avtomatik ochadi.\n\n"
        "<b>4. Raund va muhokama</b>\n"
        "Kartalar ochilgach, guruhda 3 daqiqalik muhokama boshlanadi. "
        "Kartalardagi ma’lumotlarni muhokama qilib, kim bunkerda qolishi "
        "kerakligini kelishib oling.\n\n"
        "<b>5. Ovoz berish</b>\n"
        "Muhokamadan so‘ng har bir o‘yinchiga botning shaxsiy xabarida "
        "ovoz berish tugmalari keladi. 60 soniyada boshqa bir o‘yinchini "
        "tanlang; o‘zingizga ovoz berib bo‘lmaydi. Ovozlar teng bo‘lsa "
        "yoki hech kim ovoz bermasa, o‘sha raundda hech kim chiqarilmaydi.\n\n"
        "<b>6. Chiqarishni tasdiqlash</b>\n"
        "Eng ko‘p ovoz olgan o‘yinchini chiqarish uchun ovoz bergan "
        "ishtirokchilar tasdiqlash bosqichida <b>👍 Ha</b> yoki "
        "<b>👎 Yo‘q</b> ni tanlaydi. 30 soniyada “Ha” ovozlari “Yo‘q” "
        "ovozlaridan ko‘p bo‘lsagina o‘yinchi chiqariladi; aks holda u "
        "o‘yinda qoladi.\n\n"
        "<b>7. G‘oliblar</b>\n"
        "Ikki o‘yinchi qolganida o‘yin tugaydi va ular bunkerga sig‘gan "
        "ikki g‘olib deb e’lon qilinadi. Raundlar shu tartibda davom etadi."
    )


def guide_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "⬅️ Asosiy menyuga qaytish",
        callback_data="menu:home",
        style="primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def profile_text(
    full_name: str,
    username: str | None,
    user_id: int,
    *,
    total_games: int,
    wins: int,
    latest_game: tuple[str, int] | None,
    para_partner: tuple[str, str | None] | None,
) -> str:
    escaped_name = escape(full_name)
    escaped_username = escape(f"@{username}") if username else "Mavjud emas"
    if latest_game is None:
        latest_game_text = "Mavjud emas"
    else:
        status, round_number = latest_game
        status_labels = {
            "lobby": "Lobby",
            "active": "Faol",
            "completed": "Yakunlangan",
            "cancelled": "Bekor qilingan",
        }
        latest_game_text = (
            f"{status_labels.get(status, escape(status))}, "
            f"{round_number}-raund"
        )

    if para_partner is None:
        para_text = "💔 Hozircha para yo‘q"
    else:
        partner_name, partner_username = para_partner
        escaped_partner_name = escape(partner_name)
        escaped_partner_username = (
            escape(f"@{partner_username.lstrip('@')}")
            if partner_username
            else None
        )
        para_text = (
            f"💞 Para jufti: <b>{escaped_partner_name}</b>"
            + (
                f" ({escaped_partner_username})"
                if escaped_partner_username
                else ""
            )
        )

    return (
        "👤 <b>O‘YINCHI PROFILI</b>\n\n"
        f"🙋 Ism: <b>{escaped_name}</b>\n"
        f"🔗 Username: <b>{escaped_username}</b>\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n\n"
        f"🎮 O‘yinlar soni: <b>{total_games}</b>\n"
        f"🏆 G‘alabalar: <b>{wins}</b>\n"
        f"🎯 Oxirgi o‘yin: <b>{latest_game_text}</b>\n"
        f"{para_text}"
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "⬅️ Asosiy menyu",
        callback_data="menu:home",
        style="primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def _add_button(
    builder: InlineKeyboardBuilder,
    text: str,
    *,
    style: str,
    url: str | None = None,
    callback_data: str | None = None,
) -> None:
    kwargs: dict[str, str] = {"text": text}
    if url is not None:
        kwargs["url"] = url
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if _BUTTON_STYLES_SUPPORTED:
        kwargs["style"] = style
    builder.add(InlineKeyboardButton(**kwargs))
