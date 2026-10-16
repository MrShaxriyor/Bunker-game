"""Consistent HTML-formatted text for Bunker TG messages."""

from html import escape
from typing import Iterable, Mapping

HTML_PARSE_MODE = "HTML"

_CARD_LABELS = {
    "kasb": "💼 Kasb",
    "sogliq": "❤️ Sog‘liq",
    "yosh": "🎂 Yosh",
    "hobbi": "🎯 Hobbi",
    "xarakter": "🧠 Xarakter",
    "bagaj": "🎒 Bagaj",
    "sir": "🤫 Sir",
}


def welcome_text() -> str:
    return (
        "☢️ <b>BUNKER SURVIVAL</b>\n\n"
        "👋 Bunkerga xush kelibsiz!\n"
        "Do‘stlaringiz bilan jamoa tuzing, kartalarni oching va "
        "omon qolish uchun kurashing.\n\n"
        "<b>O‘yinni boshlash:</b> botni guruhga qo‘shib, "
        "<code>/bunker</code> buyrug‘ini yuboring."
    )


def error_text(message: str) -> str:
    return f"⚠️ <b>Xatolik</b>\n{escape(message)}"


def admin_menu_text() -> str:
    return "🔐 <b>Admin panel</b>\n\nKerakli bo‘limni tanlang."


def admin_shop_items_text(page: int, page_count: int, total: int) -> str:
    return (
        "🛍️ <b>Shop buyumlari</b>\n\n"
        f"Jami buyum: <b>{total}</b>\n"
        f"Sahifa: <b>{page + 1}/{max(page_count, 1)}</b>\n"
        "Buyumni tanlab narxini yoki faolligini boshqaring."
    )


def admin_shop_item_text(item) -> str:
    description = escape(item.description) if item.description else "Tavsif yo‘q"
    status = "Faol" if item.is_active else "Nofaol"
    return (
        f"🛍️ <b>{escape(item.name)}</b>\n\n"
        f"Kod: <code>{escape(item.code)}</code>\n"
        f"Kategoriya: {escape(item.item_type)}\n"
        f"Tavsif: {description}\n"
        f"Narx: <b>{item.price_coins}</b> coin\n"
        f"Holat: <b>{status}</b>"
    )


def admin_coin_packages_text(page: int, page_count: int, total: int) -> str:
    return (
        "💰 <b>Coin paketlari</b>\n\n"
        f"Jami paket: <b>{total}</b>\n"
        f"Sahifa: <b>{page + 1}/{max(page_count, 1)}</b>\n"
        "Paketni tanlab miqdori, narxi yoki faolligini boshqaring."
    )


def admin_coin_package_text(package) -> str:
    status = "Faol" if package.is_active else "Nofaol"
    return (
        f"💰 <b>{escape(package.name)}</b>\n\n"
        f"Kod: <code>{escape(package.code)}</code>\n"
        f"Coin: <b>{package.coins}</b>\n"
        f"Narx: <b>{package.price_stars}</b> ⭐\n"
        f"Holat: <b>{status}</b>"
    )


def admin_wallet_search_prompt_text() -> str:
    return "Hamyonni ko‘rish uchun musbat Telegram ID yuboring."


def admin_wallet_text(telegram_user_id: int, balance: int, transactions) -> str:
    if not transactions:
        transaction_text = "Tranzaksiyalar topilmadi."
    else:
        lines = []
        for transaction in transactions:
            sign = "+" if transaction.direction == "credit" else "−"
            note = f" · {escape(transaction.note)}" if transaction.note else ""
            lines.append(
                f"• {sign}{transaction.amount_coins} · "
                f"{escape(transaction.reason)}{note}"
            )
        transaction_text = "\n".join(lines)
    return (
        "👛 <b>Foydalanuvchi hamyoni</b>\n\n"
        f"Telegram ID: <code>{telegram_user_id}</code>\n"
        f"Balans: <b>{balance}</b> Bunker Coin\n\n"
        "<b>Oxirgi tranzaksiyalar:</b>\n"
        f"{transaction_text}"
    )


def admin_wallet_not_found_text(telegram_user_id: int) -> str:
    return (
        f"<code>{telegram_user_id}</code> uchun hamyon topilmadi. "
        "Coin o‘zgarishi wallet service orqali yangi hamyon yaratadi."
    )


def admin_wallet_adjust_confirmation_text(
    telegram_user_id: int,
    direction: str,
    amount: int,
    reason: str,
) -> str:
    action = "qo‘shish" if direction == "add" else "yechish"
    return (
        "⚠️ <b>Coin o‘zgarishini tasdiqlang</b>\n\n"
        f"Foydalanuvchi: <code>{telegram_user_id}</code>\n"
        f"Amal: <b>{action}</b>\n"
        f"Miqdor: <b>{amount}</b> coin\n"
        f"Sabab: {escape(reason)}\n\n"
        "Amal `admin_adjustment` sifatida audit jurnaliga yoziladi."
    )


def admin_payment_history_text(page: int, page_count: int, total: int) -> str:
    return (
        "🧾 <b>To‘lovlar tarixi</b>\n\n"
        f"Jami buyurtma: <b>{total}</b>\n"
        f"Sahifa: <b>{page + 1}/{max(page_count, 1)}</b>\n"
        "Holat yoki coin berishni bu bo‘limdan o‘zgartirib bo‘lmaydi."
    )


def admin_payment_history_entry(entry) -> str:
    return (
        f"• <b>#{entry.order_id}</b> · "
        f"<code>{entry.telegram_user_id}</code> · "
        f"{escape(entry.package_name)} · "
        f"{escape(entry.status)} · {entry.price_stars} ⭐ · "
        f"{entry.coins} coin"
    )


def admin_broadcast_prompt_text() -> str:
    return (
        "📢 Ommaviy xabar matnini yuboring.\n"
        "Xabar oddiy matn sifatida jo‘natiladi; bo‘sh matn qabul qilinmaydi."
    )


def admin_broadcast_invalid_text() -> str:
    return "Xabar matni bo‘sh bo‘lmasin. Matnni qayta yuboring."


def admin_broadcast_too_long_text() -> str:
    return (
        "Xabar Telegram’ning 4096 belgilik cheklovidan uzun. "
        "Qisqartirib qayta yuboring."
    )


def admin_broadcast_result_text(
    total_recipients: int,
    blocked_excluded: int,
    sent: int,
    failed: int,
    stopped_unprocessed: int,
    technical_unprocessed: int,
) -> str:
    result = (
        "📢 Ommaviy xabar yuborish yakunlandi.\n\n"
        f"Noyob oluvchilar: {total_recipients}\n"
        f"Bloklanganlar sabab chiqarildi: {blocked_excluded}\n"
        f"Muvaffaqiyatli yuborildi: {sent}\n"
        f"Muvaffaqiyatsiz yuborishlar: {failed}\n"
        f"Admin to‘xtatgani sabab yuborilmadi: {stopped_unprocessed}"
    )
    if technical_unprocessed:
        result += (
            "\n"
            f"Texnik xato sabab yuborilmadi: {technical_unprocessed}"
        )
    return result


def admin_stats_text(
    total_games: int,
    lobby_games: int,
    active_games: int,
    completed_games: int,
    cancelled_games: int,
    unique_players: int,
    total_rounds: int,
) -> str:
    return (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"🎮 Jami o‘yinlar: <b>{total_games}</b>\n"
        f"🏠 Lobby: <b>{lobby_games}</b>\n"
        f"🟢 Faol: <b>{active_games}</b>\n"
        f"🏁 Yakunlangan: <b>{completed_games}</b>\n"
        f"🚫 Bekor qilingan: <b>{cancelled_games}</b>\n"
        f"👥 Noyob o‘yinchilar: <b>{unique_players}</b>\n"
        f"🔄 Jami raundlar: <b>{total_rounds}</b>"
    )


def admin_games_text(page: int, total_pages: int, total_games: int) -> str:
    if total_games == 0:
        return "🎮 <b>Faol o‘yinlar</b>\n\nHozir lobby yoki faol o‘yinlar yo‘q."
    return (
        "🎮 <b>Faol o‘yinlar</b>\n\n"
        f"Sahifa: <b>{page + 1}/{total_pages}</b>\n"
        f"Ochiq o‘yinlar: <b>{total_games}</b>\n"
        "Tafsilotlarni ko‘rish uchun o‘yin tugmasini tanlang."
    )


def admin_game_list_item(
    game_id: int,
    chat_id: int,
    status: str,
    phase: str,
    round_number: int,
) -> str:
    return (
        f"#{game_id} · chat {chat_id} · {escape(status)} · "
        f"{escape(phase)} · raund {round_number}"
    )


def admin_game_detail_text(
    game_id: int,
    chat_id: int,
    status: str,
    phase: str,
    round_number: int,
    host_id: int,
    host_name: str | None,
    host_username: str | None,
    player_count: int,
) -> str:
    organizer = f"<code>{host_id}</code>"
    if host_name:
        organizer = f"{escape(host_name)} ({organizer})"
    if host_username:
        organizer += f" @{escape(host_username.lstrip('@'))}"
    return (
        "🎮 <b>O‘yin tafsilotlari</b>\n\n"
        f"ID: <code>{game_id}</code>\n"
        f"Chat ID: <code>{chat_id}</code>\n"
        f"Holati: <b>{escape(status)}</b>\n"
        f"Fazasi: <b>{escape(phase)}</b>\n"
        f"Raund: <b>{round_number}</b>\n"
        f"Tashkilotchi: {organizer}\n"
        f"DB’dagi o‘yinchilar: <b>{player_count}</b>"
    )


def admin_cancel_confirmation_text(game_id: int) -> str:
    return (
        "⚠️ <b>O‘yinni bekor qilishni tasdiqlaysizmi?</b>\n\n"
        f"O‘yin ID: <code>{game_id}</code>\n"
        "Bekor qilingach, o‘yin raundlari davom etmaydi."
    )


def admin_game_cancelled_text(game_id: int) -> str:
    return f"✅ <b>O‘yin bekor qilindi.</b>\nO‘yin ID: <code>{game_id}</code>"


def admin_player_search_prompt_text() -> str:
    return (
        "👤 <b>O‘yinchilarni qidirish</b>\n\n"
        "Telegram ID yoki username yuboring. Username’ni <code>@username</code> "
        "yoki <code>username</code> ko‘rinishida kiritish mumkin."
    )


def admin_player_search_text(
    query: str,
    page: int,
    total_pages: int,
    total_players: int,
) -> str:
    return (
        "👤 <b>O‘yinchilar qidiruvi</b>\n\n"
        f"So‘rov: <code>{escape(query)}</code>\n"
        f"Natijalar: <b>{total_players}</b>\n"
        f"Sahifa: <b>{page + 1}/{total_pages}</b>\n"
        "Profilni ko‘rish uchun o‘yinchini tanlang."
    )


def admin_player_search_empty_text(query: str) -> str:
    return (
        "👤 <b>O‘yinchilar qidiruvi</b>\n\n"
        f"<code>{escape(query)}</code> bo‘yicha bazada o‘yinchi topilmadi."
    )


def admin_player_search_invalid_text() -> str:
    return (
        "Qidiruv uchun musbat Telegram ID yoki 1–32 belgili username "
        "(lotin harflari, raqam va _) yuboring."
    )


def admin_player_profile_text(
    telegram_user_id: int,
    name: str,
    username: str | None,
    total_games: int,
    wins: int,
    recent_games: list[tuple[int, str, int, str]],
    blocked: bool,
    block_reason: str | None,
) -> str:
    username_text = f"@{escape(username.lstrip('@'))}" if username else "ko‘rsatilmagan"
    if recent_games:
        recent_lines = "\n".join(
            f"• <code>#{game_id}</code> — {escape(status)}, "
            f"{escape(player_status)}, raund {round_number}"
            for game_id, status, round_number, player_status in recent_games
        )
    else:
        recent_lines = "Oxirgi o‘yinlar topilmadi."

    block_status = (
        f"🚫 <b>Bloklangan.</b> Sabab: {escape(block_reason)}"
        if blocked and block_reason
        else "🚫 <b>Bloklangan.</b>"
        if blocked
        else "✅ Bloklanmagan."
    )
    return (
        "👤 <b>O‘yinchi profili</b>\n\n"
        f"Telegram ID: <code>{telegram_user_id}</code>\n"
        f"Ism: {escape(name)}\n"
        f"Username: {username_text}\n"
        f"{block_status}\n"
        f"Qatnashgan o‘yinlari: <b>{total_games}</b>\n"
        f"G‘alabalari: <b>{wins}</b>\n\n"
        "<b>Oxirgi o‘yinlari:</b>\n"
        f"{recent_lines}"
    )


def admin_player_profile_not_found_text() -> str:
    return "Bu o‘yinchi uchun profil ma’lumoti endi topilmadi."


def admin_player_block_confirmation_text(telegram_user_id: int) -> str:
    return (
        "⚠️ <b>O‘yinchini bloklash</b>\n\n"
        f"Telegram ID: <code>{telegram_user_id}</code>\n"
        "Bloklangan foydalanuvchi yangi lobby yarata olmaydi va lobbyga "
        "qo‘shila olmaydi. Hozirgi o‘yinlardan avtomatik chiqarilmaydi.\n\n"
        "Sababni kiritishingiz yoki sababsiz bloklashingiz mumkin."
    )


def admin_player_block_reason_prompt_text(telegram_user_id: int) -> str:
    return (
        f"<code>{telegram_user_id}</code> uchun blok sababini yuboring "
        "(500 belgigacha). Sababsiz davom etish uchun /skip yuboring."
    )


def admin_player_block_reason_confirmation_text(
    telegram_user_id: int,
    reason: str | None,
) -> str:
    reason_text = escape(reason) if reason else "Sabab ko‘rsatilmagan."
    return (
        "⚠️ <b>Bloklashni yakuniy tasdiqlang</b>\n\n"
        f"Telegram ID: <code>{telegram_user_id}</code>\n"
        f"Sabab: {reason_text}"
    )


def admin_player_blocked_text(telegram_user_id: int) -> str:
    return (
        "🚫 <b>O‘yinchi bloklandi.</b>\n"
        f"Telegram ID: <code>{telegram_user_id}</code>"
    )


def admin_player_already_blocked_text(telegram_user_id: int) -> str:
    return (
        "ℹ️ O‘yinchi allaqachon bloklangan.\n"
        f"Telegram ID: <code>{telegram_user_id}</code>"
    )


def admin_player_unblocked_text(telegram_user_id: int) -> str:
    return (
        "✅ <b>O‘yinchi blokdan chiqarildi.</b>\n"
        f"Telegram ID: <code>{telegram_user_id}</code>"
    )


def admin_player_not_blocked_text(telegram_user_id: int) -> str:
    return (
        "ℹ️ O‘yinchi bloklanmagan.\n"
        f"Telegram ID: <code>{telegram_user_id}</code>"
    )


def admin_player_self_block_error_text() -> str:
    return "O‘zingizni bloklay olmaysiz."


def admin_player_block_reason_too_long_text() -> str:
    return "Blok sababi 500 belgidan oshmasligi kerak."


def lobby_text(
    players: Iterable[str],
    player_count: int,
    min_players: int,
    max_players: int,
    organizer_name: str = "Noma’lum",
) -> str:
    player_lines = "\n".join(
        f"{index}. {escape(name)}"
        for index, name in enumerate(players, start=1)
    ) or "Hali o‘yinchi yo‘q."
    return (
        "☢️ <b>BUNKER SURVIVAL</b>\n\n"
        "🏚️ <b>Yangi o‘yin ochildi!</b>\n\n"
        f"👑 <b>O‘yin tashkilotchisi:</b> {escape(organizer_name)}\n\n"
        f"👥 <b>O‘yinchilar:</b> {player_count}/{max_players}\n"
        "🟢 <b>Holat:</b> O‘yinchilar kutilmoqda...\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "📌 <b>Qanday o‘ynash kerak?</b>\n\n"
        "1. Quyidagi tugma orqali o‘yinga qo‘shiling.\n"
        f"2. Kamida {min_players} o‘yinchi yig‘ilgach, tashkilotchi o‘yinni boshlaydi.\n"
        "3. Kartalaringizni ochib, bunkerda qolish uchun kurashing!\n\n"
        "<b>O‘yinchilar ro‘yxati:</b>\n"
        f"{player_lines}\n\n"
        "O‘yinga qo‘shilish uchun quyidagi tugmalardan foydalaning."
    )


def game_started_text(player_count: int) -> str:
    return (
        "☢️ <b>BUNKER O‘YINI BOSHLANDI!</b>\n\n"
        f"👥 <b>Ishtirokchilar:</b> {player_count}\n\n"
        "🃏 Har bir o‘yinchi uchun shaxsiy kartalar tayyorlanmoqda.\n\n"
        "⏳ <b>Keyingi bosqich:</b> 1-raund — kartalarni ochish "
        "(15 soniyadan keyin)."
    )


def cards_dealt_text(sent_count: int, failed_count: int) -> str:
    return (
        "🎴 <b>SHAXSIY KARTALAR YUBORILDI</b>\n\n"
        f"✅ <b>Yuborildi:</b> {sent_count}\n"
        f"⚠️ <b>Yuborilmadi:</b> {failed_count}\n\n"
        "Kartalaringiz bilan tanishib oling. "
        "⏳ Birinchi raund 15 soniyadan keyin boshlanadi."
    )


def private_cards_text(cards: Mapping[str, str]) -> str:
    lines = ["🎴 <b>SIZNING KARTALARINGIZ</b>", ""]
    for category, value in cards.items():
        label = _CARD_LABELS.get(category, escape(category.title()))
        lines.append(f"{label}: {escape(value)}")
    lines.extend(
        [
            "",
            "━━━━━━━━━━━━━━",
            "🤫 <i>Siringizni o‘zingiz ochmaguningizcha bot "
            "boshqa o‘yinchilarga ko‘rsatmaydi.</i>",
        ]
    )
    return "\n".join(lines)


def unrevealed_cards_text() -> str:
    return "🎴 <b>OCHILMAGAN KARTALARINGIZ</b>\n\nRaund uchun bitta kartani tanlang."


def all_cards_revealed_text() -> str:
    return "✅ <b>Barcha kartalaringiz ochilgan.</b>"


def round_started_text(
    round_number: int,
    living_players: Iterable[tuple[int, str, str | None]],
) -> str:
    player_lines = []
    for user_id, name, username in living_players:
        profile_url = (
            f"https://t.me/{escape(username.lstrip('@'), quote=True)}"
            if username
            else f"tg://user?id={user_id}"
        )
        player_lines.append(
            f'• <a href="{escape(profile_url, quote=True)}">{escape(name)}</a>'
        )
    roster = "\n".join(player_lines) or "Hozircha tirik o‘yinchi yo‘q."
    return (
        f"🎴 <b>{round_number}-RAUND BOSHLANDI</b>\n\n"
        f"❤️ <b>Tirik o‘yinchilar</b>\n{roster}\n\n"
        "Har bir o‘yinchi bittadan karta ochishi kerak."
    )


def card_prompt_text(round_number: int) -> str:
    return f"🎴 <b>{round_number}-raund</b>\n\nBitta kartani tanlang."


def card_revealed_text(round_number: int, category: str, value: str) -> str:
    label = _CARD_LABELS.get(category, escape(category.title()))
    return (
        f"✅ <b>{round_number}-raund uchun karta ochildi</b>\n\n"
        f"{label}: {escape(value)}\n\n"
        "Keyingi bosqichni kuting."
    )


def card_announcement_text(
    player_name: str,
    category: str,
    value: str,
    automatic: bool = False,
) -> str:
    label = _CARD_LABELS.get(category, escape(category.title()))
    opening = "avtomatik karta ochdi" if automatic else "kartasini ochdi"
    return (
        f"🎴 <b>{escape(player_name)}</b> {opening}.\n\n"
        f"{label}: {escape(value)}"
    )


def automatic_card_text(category: str, value: str) -> str:
    label = _CARD_LABELS.get(category, escape(category.title()))
    return (
        "⏰ <b>Vaqt tugadi — karta avtomatik ochildi.</b>\n\n"
        f"{label}: {escape(value)}"
    )


def discussion_started_text(round_number: int) -> str:
    return (
        f"🗣️ <b>{round_number}-RAUND: MUHOKAMA</b>\n\n"
        "⏳ Vaqt: <b>3 daqiqa</b>\n"
        "💬 Kartalaringizni muhokama qiling. Vaqt tugagach, "
        "ovoz berish boshlanadi."
    )


def discussion_finished_text() -> str:
    return "⏰ <b>MUHOKAMA TUGADI</b>\n\n🗳️ Keyingi bosqich — ovoz berish."


def voting_started_text(player_count: int) -> str:
    return (
        "🗳️ <b>OVOZ BERISH BOSHLANDI!</b>\n\n"
        "☢️ <b>Kim bunkerdan chiqarilsin?</b>\n\n"
        f"👥 <b>Tirik o‘yinchilar:</b> {player_count}\n"
        "⏳ <b>Ovoz berish vaqti:</b> 60 soniya\n\n"
        "Shaxsiy xabardagi tugmalardan bittasini tanlang."
    )


def vote_prompt_text() -> str:
    return (
        "🗳️ <b>OVOZ BERISH</b>\n\n"
        "☢️ Kim bunkerdan chiqarilsin?\n"
        "Quyidagi tirik o‘yinchilardan birini tanlang."
    )


def vote_announcement_text(voter_name: str, target_name: str) -> str:
    return (
        f"🗳️ <b>{escape(voter_name)}</b> → "
        f"<b>{escape(target_name)}</b> ga ovoz berdi."
    )


def voting_finished_text() -> str:
    return "⏰ <b>OVOZ BERISH TUGADI</b>"


def no_votes_text() -> str:
    return "⚠️ <b>Ovoz berilmadi.</b>\n\nBu raundda hech kim chiqarilmadi."


def tied_vote_text() -> str:
    return "🤝 <b>OVOZLAR TENGLASHDI</b>\n\nBu raundda hech kim chiqarilmadi."


def vote_leader_text(target_name: str, vote_count: int) -> str:
    return (
        f"📊 <b>Eng ko‘p ovoz olgan:</b> {escape(target_name)}\n"
        f"🗳️ <b>Ovozlar soni:</b> {vote_count}\n\n"
        "⚖️ Endi chiqarishni tasdiqlash bosqichi boshlanadi."
    )


def confirmation_text(
    target_name: str,
    profile_url: str | None = None,
    yes_count: int = 0,
    no_count: int = 0,
) -> str:
    candidate = escape(target_name)
    if profile_url:
        candidate = (
            f'<a href="{escape(profile_url, quote=True)}">{candidate}</a>'
        )
    return (
        "⚖️ <b>CHIQARISH BO‘YICHA QAROR</b>\n\n"
        f"👤 <b>Nomzod:</b> {candidate}\n\n"
        "🗳️ <b>Ovoz berish natijasi:</b>\n"
        f"👍 <b>Ha:</b> {yes_count}\n"
        f"👎 <b>Yo‘q:</b> {no_count}\n\n"
        "⏳ <b>Qaror 30 soniyadan keyin chiqariladi.</b>"
        "\n\n<i>Faqat dastlabki ovoz berishda qatnashganlar ovoz bera oladi.</i>"
    )


def confirmation_result_text(
    target_name: str,
    yes_count: int,
    no_count: int,
    eliminated: bool,
    cards: Mapping[str, str] | None = None,
) -> str:
    if eliminated:
        lines = [
            "🚪 <b>O‘YINCHI CHIQARILDI</b>",
            f"👤 {escape(target_name)}",
            f"👍 <b>Ha:</b> {yes_count}  |  👎 <b>Yo‘q:</b> {no_count}",
        ]
        if cards:
            lines.extend(["", "🎴 <b>OCHIQ KARTALARI</b>"])
            lines.extend(
                f"• {_CARD_LABELS.get(category, escape(category.title()))}: "
                f"{escape(value)}"
                for category, value in cards.items()
            )
        return "\n".join(lines)
    ending = "\n\n🤝 Ovozlar teng bo‘ldi." if yes_count == no_count else ""
    return (
        "🛡️ <b>O‘YINCHI O‘YINDA QOLADI</b>\n"
        f"👤 {escape(target_name)}\n"
        f"👍 <b>Ha:</b> {yes_count}  |  👎 <b>Yo‘q:</b> {no_count}"
        f"{ending}"
    )


def inactive_players_removed_text(player_names: Iterable[str]) -> str:
    names = "\n".join(f"• {escape(name)}" for name in player_names)
    return (
        "🚪 <b>O‘YINCHILAR CHIQARILDI</b>\n"
        "Ketma-ket ikki raundda karta ochilmagani uchun:\n"
        f"{names}"
    )


def winners_text(winners: Iterable[tuple[int, str, str | None]]) -> str:
    winner_lines = []
    for rank, (user_id, name, username) in enumerate(winners, start=1):
        profile_url = (
            f"https://t.me/{escape(username.lstrip('@'), quote=True)}"
            if username
            else f"tg://user?id={user_id}"
        )
        winner_lines.append(
            f'{"🥇" if rank == 1 else "🥈"} '
            f'<a href="{escape(profile_url, quote=True)}">{escape(name)}</a>'
        )
    return (
        "🏆 <b>BUNKER SURVIVAL — O‘YIN YAKUNLANDI!</b>\n\n"
        "☢️ <b>Bunkerda omon qolgan g‘oliblar:</b>\n\n"
        + ("\n".join(winner_lines) or "G‘oliblar aniqlanmadi.")
        + "\n\n🎮 <b>O‘yin yakunlandi!</b>\n"
        "👏 Barcha ishtirokchilarga rahmat!"
    )


def callback_notice_text(message: str) -> str:
    return escape(message)


def para_gifts_unconfigured_text() -> str:
    return (
        "🎁 Para Gifts WebApp hozircha sozlanmagan. "
        "Keyinroq qayta urinib ko‘ring."
    )


def shop_catalog_text(balance_coins: int, item_count: int) -> str:
    return (
        "🆘 <b>Survival buyumlari</b>\n\n"
        f"🪙 Balans: <b>{balance_coins:,}</b> coin\n"
        f"Faol buyumlar: <b>{item_count}</b>\n"
        "Tafsilotlarini ko‘rish uchun buyumni tanlang."
    ).replace(",", " ")


def shop_home_text(balance_coins: int, survival_item_count: int) -> str:
    survival_line = (
        f"\n🆘 Survival buyumlari: <b>{survival_item_count}</b>"
        if survival_item_count
        else ""
    )
    return (
        "🛍️ <b>Shop</b>\n\n"
        f"🪙 Balansingiz: <b>{balance_coins:,}</b> coin"
        f"{survival_line}\n\n"
        "Kerakli bo‘limni tanlang."
    ).replace(",", " ")


def shop_item_text(
    name: str,
    description: str | None,
    category: str,
    price_coins: int,
    balance_coins: int,
) -> str:
    description_text = escape(description) if description else "Tavsif berilmagan."
    category_labels = {
        "survival": "🆘 Survival",
        "para_gift": "🎁 Para sovg‘asi",
    }
    category_label = category_labels.get(category, category.title())
    return (
        f"🛍️ <b>{escape(name)}</b>\n\n"
        f"{description_text}\n\n"
        f"Kategoriya: <b>{escape(category_label)}</b>\n"
        f"Narxi: <b>{price_coins:,}</b> coin\n"
        f"Balans: <b>{balance_coins:,}</b> coin"
    ).replace(",", " ")


def shop_purchase_confirmation_text(
    name: str,
    price_coins: int,
    balance_coins: int,
) -> str:
    return (
        "⚠️ <b>Xaridni tasdiqlang</b>\n\n"
        f"Buyum: <b>{escape(name)}</b>\n"
        f"Narxi: <b>{price_coins:,}</b> coin\n"
        f"Joriy balans: <b>{balance_coins:,}</b> coin\n\n"
        "Xaridni amalga oshirasizmi?"
    ).replace(",", " ")


def shop_insufficient_funds_text(balance_coins: int, price_coins: int) -> str:
    return (
        "⚠️ <b>Coin yetarli emas</b>\n\n"
        f"Balans: <b>{balance_coins:,}</b> coin\n"
        f"Kerak: <b>{price_coins:,}</b> coin\n\n"
        "🪙 Coin sotib olish tugmasini bosib xaridni davom ettiring."
    ).replace(",", " ")


def shop_purchase_result_text(
    name: str,
    price_coins: int,
    quantity: int,
    balance_coins: int,
    *,
    replayed: bool,
) -> str:
    replay_note = "\nBu xarid avval bajarilgan." if replayed else ""
    return (
        f"✅ <b>{escape(name)}</b> xarid qilindi.\n"
        f"Sarflandi: <b>{price_coins}</b> Bunker Coin\n"
        f"Inventardagi soni: <b>{quantity}</b>\n"
        f"Qolgan balans: <b>{balance_coins}</b> Bunker Coin"
        f"{replay_note}"
    )


def inventory_text(entries: list[tuple[str, str | None, int]]) -> str:
    lines = []
    for name, description, quantity in entries:
        description_text = f"\n  {escape(description)}" if description else ""
        lines.append(
            f"• <b>{escape(name)}</b> × <b>{quantity}</b>{description_text}"
        )
    return "🎒 <b>Inventaringiz</b>\n\n" + "\n".join(lines)


def empty_inventory_text() -> str:
    return "🎒 <b>Inventaringiz bo‘sh.</b>\n\nDo‘kondan buyum tanlang."


def shop_item_unavailable_text() -> str:
    return "Bu buyum endi do‘konda mavjud emas yoki faol emas."


def coin_packages_text(packages: list[tuple[str, int, int]]) -> str:
    if not packages:
        return (
            "💰 <b>Coin paketlari</b>\n\n"
            "Hozircha sotuvda paket yo‘q. Keyinroq qayta tekshiring."
        )
    package_lines = "\n\n".join(
        f"⭐ <b>{escape(name)}</b>\n"
        f"🪙 {coins:,} coin — ⭐ {price_stars:,} Stars"
        for name, coins, price_stars in packages
    ).replace(",", " ")
    return (
        "🪙 <b>Coin paketlari</b>\n\n"
        f"{package_lines}\n\n"
        "To‘lov Telegram Stars (XTR) orqali amalga oshiriladi."
    )


def payment_support_text() -> str:
    return (
        "🧾 <b>To‘lov bo‘yicha yordam</b>\n\n"
        "To‘lovdan keyin coin kelmagan bo‘lsa, buyurtma va Telegram to‘lov "
        "chekini saqlang va support bilan bog‘laning."
    )


def payment_invoice_description(name: str, coins: int) -> str:
    return f"{name} paketi: {coins} Bunker Coin"


def payment_invoice_sent_text() -> str:
    return "To‘lov oynasi shaxsiy chatga yuborildi."


def payment_start_private_chat_text() -> str:
    return (
        "To‘lov oynasini yuborish uchun botning shaxsiy chatini ochib "
        "/start buyrug‘ini yuboring, so‘ng /buycoins ni qayta bosing."
    )


def payment_success_text(coins: int, balance_coins: int) -> str:
    return (
        "✅ <b>To‘lov tasdiqlandi!</b>\n"
        f"Hisobingizga <b>{coins}</b> Bunker Coin qo‘shildi.\n"
        f"Joriy balans: <b>{balance_coins}</b> coin."
    )


def payment_replayed_text() -> str:
    return "✅ Bu to‘lov avval qayd etilgan. Coin takroran qo‘shilmadi."


def payment_processing_text() -> str:
    return (
        "To‘lov Telegram tomonidan tasdiqlandi, ammo hisobga yozishda "
        "vaqtinchalik muammo yuz berdi. Chekni saqlang va /paysupport orqali murojaat qiling."
    )


def payment_rejected_text() -> str:
    return (
        "⚠️ To‘lovni buyurtma bilan tasdiqlay olmadik. Coin berilmadi. "
        "Yordam uchun /paysupport buyrug‘idan foydalaning."
    )
