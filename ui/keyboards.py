"""Reusable inline keyboard builders for Bunker TG."""

from collections.abc import Mapping

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ui.start_menu import _valid_webapp_url

_BUTTON_STYLES_SUPPORTED = (
    "style" in InlineKeyboardButton.model_fields
)

_CARD_LABELS = {
    "kasb": "💼 Kasb",
    "sogliq": "❤️ Sog‘liq",
    "yosh": "🎂 Yosh",
    "hobbi": "🎯 Hobbi",
    "xarakter": "🧠 Xarakter",
    "bagaj": "🎒 Bagaj",
    "sir": "🤫 Sir",
}


def _add_button(
    builder: InlineKeyboardBuilder,
    text: str,
    callback_data: str,
    style: str,
) -> None:
    kwargs = {"text": text, "callback_data": callback_data}
    if _BUTTON_STYLES_SUPPORTED:
        kwargs["style"] = style
    builder.add(InlineKeyboardButton(**kwargs))


def create_game_keyboard() -> InlineKeyboardMarkup:
    """Prepared for a future create callback; current creation uses /bunker."""
    builder = InlineKeyboardBuilder()
    _add_button(builder, "🏚️ O‘yin yaratish", "bunker:create", "primary")
    builder.adjust(1)
    return builder.as_markup()


def lobby_keyboard(
    bot_username: str,
    game_id: int,
    chat_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="🟢 ➕ O‘yinga qo‘shilish",
            url=f"https://t.me/{bot_username}?start=join_{game_id}",
        )
    )
    _add_button(
        builder,
        "🚀 O‘yinni boshlash",
        f"bunker:start:{chat_id}",
        "primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def card_reveal_keyboard(
    chat_id: int,
    cards: Mapping[str, str],
    revealed: set[str] | frozenset[str],
) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()
    for category, value in cards.items():
        if category in revealed:
            continue
        label = _CARD_LABELS.get(category, category.title())
        _add_button(
            builder,
            f"{label}: {value}",
            f"bunker:reveal:{chat_id}:{category}",
            "primary",
        )
    if not builder.buttons:
        return None
    builder.adjust(1)
    return builder.as_markup()


def voting_keyboard(
    chat_id: int,
    voter_id: int,
    players: Mapping[int, str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target_id, name in players.items():
        if target_id == voter_id:
            continue
        _add_button(
            builder,
            f"🗳️ {name}",
            f"bunker:vote:{chat_id}:{voter_id}:{target_id}",
            "primary",
        )
    builder.adjust(1)
    return builder.as_markup()


def confirmation_keyboard(
    chat_id: int,
    target_id: int,
    yes_count: int = 0,
    no_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        f"👍 Ha ({yes_count})",
        f"bunker:confirm:{chat_id}:{target_id}:yes",
        "success",
    )
    _add_button(
        builder,
        f"👎 Yo‘q ({no_count})",
        f"bunker:confirm:{chat_id}:{target_id}:no",
        "danger",
    )
    builder.adjust(2)
    return builder.as_markup()


def para_invitation_keyboard(para_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "💍 Roziman",
        f"para:accept:{para_id}",
        "success",
    )
    _add_button(
        builder,
        "💔 Yo‘q",
        f"para:reject:{para_id}",
        "danger",
    )
    builder.adjust(2)
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(builder, "📊 Statistika", "admin:stats", "primary")
    _add_button(builder, "🎮 Faol o‘yinlar", "admin:games:0", "primary")
    _add_button(builder, "👤 O‘yinchilar", "admin:players", "primary")
    _add_button(builder, "📢 Ommaviy xabar", "admin:broadcast", "primary")
    _add_button(builder, "🛍️ Shop buyumlari", "admin:shop:items:0", "primary")
    _add_button(builder, "💰 Coin paketlari", "admin:shop:packages:0", "primary")
    _add_button(builder, "👛 Hamyon qidirish", "admin:shop:wallet", "primary")
    _add_button(builder, "🧾 To‘lovlar tarixi", "admin:shop:payments:0", "primary")
    _add_button(builder, "🔐 A’zolik talabi", "admin:membership", "primary")
    _add_button(builder, "🎁 Para Gifts", "admin:shop:gifts:0", "primary")
    _add_button(builder, "🔄 Yangilash", "admin:refresh", "success")
    _add_button(builder, "❌ Yopish", "admin:close", "danger")
    builder.adjust(1)
    return builder.as_markup()


def admin_broadcast_preview_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "✅ Yuborishni boshlash",
        "admin:broadcast:confirm",
        "success",
    )
    _add_button(
        builder,
        "❌ Bekor qilish",
        "admin:broadcast:cancel",
        "danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_broadcast_progress_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "❌ To‘xtatish",
        "admin:broadcast:stop",
        "danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_players_keyboard(
    players: list[tuple[int, str]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for telegram_user_id, label in players:
        _add_button(
            builder,
            label,
            f"admin:player:{telegram_user_id}:{page}",
            "primary",
        )
    if page > 0:
        _add_button(
            builder,
            "⬅️ Oldingi",
            f"admin:players:results:{page - 1}",
            "primary",
        )
    if page + 1 < total_pages:
        _add_button(
            builder,
            "Keyingi ➡️",
            f"admin:players:results:{page + 1}",
            "primary",
        )
    _add_button(builder, "🔎 Yangi qidiruv", "admin:players", "success")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_player_profile_keyboard(
    page: int,
    telegram_user_id: int,
    *,
    blocked: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if blocked:
        _add_button(
            builder,
            "✅ Blokdan chiqarish",
            f"admin:unblock:{telegram_user_id}:{page}",
            "success",
        )
    else:
        _add_button(
            builder,
            "🚫 Bloklash",
            f"admin:block:confirm:{telegram_user_id}:{page}",
            "danger",
        )
    _add_button(
        builder,
        "⬅️ Qidiruv natijalari",
        f"admin:players:results:{page}",
        "primary",
    )
    _add_button(builder, "🔎 Yangi qidiruv", "admin:players", "success")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_block_confirmation_keyboard(
    telegram_user_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "🚫 Sababsiz bloklash",
        f"admin:block:do:{telegram_user_id}:{page}",
        "danger",
    )
    _add_button(
        builder,
        "📝 Sabab kiritish",
        f"admin:block:reason:{telegram_user_id}:{page}",
        "primary",
    )
    _add_button(
        builder,
        "↩️ Profilga qaytish",
        f"admin:player:{telegram_user_id}:{page}",
        "success",
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_block_reason_confirmation_keyboard(
    telegram_user_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "✅ Bloklashni tasdiqlash",
        f"admin:block:do:{telegram_user_id}:{page}",
        "danger",
    )
    _add_button(
        builder,
        "↩️ Bekor qilish",
        f"admin:block:cancel:{telegram_user_id}:{page}",
        "primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_games_keyboard(
    games: list[tuple[int, str]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for game_id, label in games:
        _add_button(
            builder,
            label,
            f"admin:game:{game_id}:{page}",
            "primary",
        )

    if page > 0:
        _add_button(builder, "⬅️ Oldingi", f"admin:games:{page - 1}", "primary")
    if page + 1 < total_pages:
        _add_button(builder, "Keyingi ➡️", f"admin:games:{page + 1}", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "success")
    builder.adjust(1)
    return builder.as_markup()


def admin_game_detail_keyboard(
    game_id: int,
    page: int,
    *,
    cancellable: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if cancellable:
        _add_button(
            builder,
            "🛑 O‘yinni bekor qilish",
            f"admin:cancel:confirm:{game_id}:{page}",
            "danger",
        )
    _add_button(builder, "⬅️ Faol o‘yinlar", f"admin:games:{page}", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "success")
    builder.adjust(1)
    return builder.as_markup()


def admin_cancel_confirmation_keyboard(
    game_id: int,
    page: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "✅ Ha, bekor qilish",
        f"admin:cancel:do:{game_id}:{page}",
        "danger",
    )
    _add_button(
        builder,
        "↩️ Tafsilotlarga qaytish",
        f"admin:game:{game_id}:{page}",
        "primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def shop_keyboard(
    items: list[tuple[int, str, int]],
    telegram_user_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item_id, name, price_coins in items:
        label = f"🛍️ {name} · {price_coins:,} 🪙".replace(",", " ")
        if len(label) > 64:
            label = f"{label[:61]}…"
        _add_button(
            builder,
            label,
            f"sh:d:{telegram_user_id}:{item_id}",
            "primary",
        )
    _add_button(
        builder,
        "🎒 Inventar",
        f"sh:i:{telegram_user_id}",
        "success",
    )
    _add_button(
        builder,
        "💰 Coin sotib olish",
        f"pay:l:{telegram_user_id}",
        "primary",
    )
    _add_button(
        builder,
        "⬅️ Shop menyusi",
        f"sh:m:{telegram_user_id}",
        "primary",
    )
    builder.adjust(2)
    return builder.as_markup()


def shop_main_keyboard(
    telegram_user_id: int,
    *,
    has_survival_items: bool,
    webapp_url: str | None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    valid_webapp_url = _valid_webapp_url(webapp_url)
    if valid_webapp_url:
        builder.add(
            InlineKeyboardButton(
                text="🎁 Para sovg‘alari",
                web_app=WebAppInfo(url=valid_webapp_url),
            )
        )
    else:
        _add_button(
            builder,
            "🎁 Para sovg‘alari",
            "menu:gifts_unconfigured",
            "primary",
        )
    _add_button(builder, "🎒 Inventar", f"sh:i:{telegram_user_id}", "success")
    if has_survival_items:
        _add_button(
            builder,
            "🆘 Survival buyumlari",
            f"sh:s:{telegram_user_id}",
            "primary",
        )
    _add_button(
        builder,
        "🪙 Coin sotib olish",
        f"pay:l:{telegram_user_id}",
        "primary",
    )
    _add_button(builder, "⬅️ Asosiy menyu", "menu:home", "primary")
    builder.adjust(2)
    return builder.as_markup()


def shop_item_keyboard(
    telegram_user_id: int,
    item_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "🛒 Xaridni boshlash",
        f"sh:p:{telegram_user_id}:{item_id}",
        "success",
    )
    _add_button(
        builder,
        "⬅️ Do‘konga qaytish",
        f"sh:l:{telegram_user_id}",
        "primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def shop_confirmation_keyboard(
    telegram_user_id: int,
    item_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "✅ Xaridni tasdiqlash",
        f"sh:c:{telegram_user_id}:{item_id}",
        "success",
    )
    _add_button(
        builder,
        "↩️ Ortga",
        f"sh:d:{telegram_user_id}:{item_id}",
        "primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def shop_after_purchase_keyboard(
    telegram_user_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "🛍️ Do‘konga qaytish",
        f"sh:l:{telegram_user_id}",
        "primary",
    )
    _add_button(
        builder,
        "🎒 Inventar",
        f"sh:i:{telegram_user_id}",
        "success",
    )
    builder.adjust(1)
    return builder.as_markup()


def inventory_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "⬅️ Shop menyusi",
        f"sh:m:{telegram_user_id}",
        "primary",
    )
    builder.adjust(1)
    return builder.as_markup()


def shop_insufficient_funds_keyboard(
    telegram_user_id: int,
    item_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "🪙 Coin sotib olish",
        f"pay:l:{telegram_user_id}",
        "success",
    )
    _add_button(
        builder,
        "⬅️ Buyumga qaytish",
        f"sh:d:{telegram_user_id}:{item_id}",
        "primary",
    )
    builder.adjust(2)
    return builder.as_markup()


def coin_package_keyboard(
    packages: list[tuple[int, str, int, int]],
    telegram_user_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for package_id, name, coins, price_stars in packages:
        label = (
            f"{name} · 🪙{coins:,} · ⭐{price_stars:,}"
            .replace(",", " ")
        )
        if len(label) > 64:
            label = f"{label[:61]}…"
        _add_button(
            builder,
            label,
            f"pay:s:{telegram_user_id}:{package_id}",
            "primary",
        )
    _add_button(builder, "⬅️ Shop menyusi", f"sh:m:{telegram_user_id}", "primary")
    builder.adjust(2)
    return builder.as_markup()


def payment_support_keyboard(support_url: str | None) -> InlineKeyboardMarkup | None:
    if support_url is None:
        return None
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 To‘lov bo‘yicha yordam", url=support_url)
    return builder.as_markup()


def admin_shop_items_keyboard(
    entries: list[tuple[int, str, str, int, bool]],
    page: int,
    page_count: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for entry in entries:
        item_id, name, code = entry[:3]
        _add_button(
            builder,
            f"{name} · {code}"[:64],
            f"admin:shop:item:view:{item_id}:{page}",
            "primary",
        )
    _add_button(builder, "➕ Buyum yaratish", "admin:shop:item:new", "success")
    if page > 0:
        _add_button(builder, "⬅️ Oldingi", f"admin:shop:items:{page - 1}", "primary")
    if page + 1 < page_count:
        _add_button(builder, "Keyingi ➡️", f"admin:shop:items:{page + 1}", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_para_gifts_keyboard(
    entries: list[tuple[int, str, str, int, bool]],
    page: int,
    page_count: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item_id, name, code, price, active in entries:
        status = "✅" if active else "⏸️"
        _add_button(builder, f"{status} {name} · {price} 🪙"[:64],
                    f"admin:shop:item:view:{item_id}:{page}", "primary")
    _add_button(builder, "➕ Sovg‘a yaratish", "admin:shop:gift:new", "success")
    if page > 0:
        _add_button(builder, "⬅️ Oldingi", f"admin:shop:gifts:{page - 1}", "primary")
    if page + 1 < page_count:
        _add_button(builder, "Keyingi ➡️", f"admin:shop:gifts:{page + 1}", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_shop_item_keyboard(
    item_id: int,
    page: int,
    *,
    active: bool,
    para_gift: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(builder, "✏️ Nomini o‘zgartirish",
                f"admin:shop:item:name:{item_id}:{page}", "primary")
    _add_button(
        builder,
        "💰 Narxni o‘zgartirish",
        f"admin:shop:item:price:{item_id}:{page}",
        "primary",
    )
    _add_button(
        builder,
        "⏸️ Nofaol qilish" if active else "▶️ Faollashtirish",
        f"admin:shop:item:toggle:{item_id}:{page}",
        "danger" if active else "success",
    )
    _add_button(
        builder,
        "⬅️ Para Gifts" if para_gift else "⬅️ Buyumlar",
        f"admin:shop:gifts:{page}" if para_gift else f"admin:shop:items:{page}",
        "primary",
    )
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_coin_packages_keyboard(
    entries: list[tuple[int, str, str, int, int, bool]],
    page: int,
    page_count: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for entry in entries:
        package_id, name, code = entry[:3]
        _add_button(
            builder,
            f"{name} · {code}"[:64],
            f"admin:shop:package:view:{package_id}:{page}",
            "primary",
        )
    _add_button(builder, "➕ Paket yaratish", "admin:shop:package:new", "success")
    if page > 0:
        _add_button(builder, "⬅️ Oldingi", f"admin:shop:packages:{page - 1}", "primary")
    if page + 1 < page_count:
        _add_button(builder, "Keyingi ➡️", f"admin:shop:packages:{page + 1}", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_coin_package_keyboard(
    package_id: int,
    page: int,
    *,
    active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "🪙 Coin va Stars qiymatini o‘zgartirish",
        f"admin:shop:package:edit:{package_id}:{page}",
        "primary",
    )
    _add_button(
        builder,
        "⏸️ Nofaol qilish" if active else "▶️ Faollashtirish",
        f"admin:shop:package:toggle:{package_id}:{page}",
        "danger" if active else "success",
    )
    _add_button(builder, "⬅️ Paketlar", f"admin:shop:packages:{page}", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_wallet_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "➕ Coin qo‘shish",
        f"admin:shop:wallet:adjust:add:{telegram_user_id}",
        "success",
    )
    _add_button(
        builder,
        "➖ Coin yechish",
        f"admin:shop:wallet:adjust:spend:{telegram_user_id}",
        "danger",
    )
    _add_button(builder, "🔎 Boshqa ID qidirish", "admin:shop:wallet", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_wallet_adjust_confirmation_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _add_button(
        builder,
        "✅ O‘zgarishni tasdiqlash",
        "admin:shop:wallet:adjust:confirm",
        "success",
    )
    _add_button(
        builder,
        "❌ Bekor qilish",
        "admin:shop:wallet:adjust:cancel",
        "danger",
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_payment_history_keyboard(
    page: int,
    page_count: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if page > 0:
        _add_button(builder, "⬅️ Oldingi", f"admin:shop:payments:{page - 1}", "primary")
    if page + 1 < page_count:
        _add_button(builder, "Keyingi ➡️", f"admin:shop:payments:{page + 1}", "primary")
    _add_button(builder, "↩️ Admin menyu", "admin:menu", "primary")
    builder.adjust(1)
    return builder.as_markup()
