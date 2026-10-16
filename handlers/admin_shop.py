"""Private, ADMIN_IDS-protected Survival Shop administration handlers."""

import logging
import secrets

from aiogram import F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database import admin_shop as admin_shop_service
from database.wallet import (
    IdempotencyConflict,
    InsufficientFunds,
    InvalidWalletOperation,
    WalletStorageError,
    add_coins,
    get_transaction_history,
    spend_coins,
)
from handlers.admin import AdminAccessFilter
from ui import texts
from ui.keyboards import (
    admin_coin_package_keyboard,
    admin_coin_packages_keyboard,
    admin_payment_history_keyboard,
    admin_shop_item_keyboard,
    admin_shop_items_keyboard,
    admin_wallet_adjust_confirmation_keyboard,
    admin_wallet_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_shop")


class AdminShopStates(StatesGroup):
    item_name = State()
    item_code = State()
    item_description = State()
    item_category = State()
    item_price = State()
    item_price_edit = State()
    package_name = State()
    package_code = State()
    package_coins = State()
    package_price = State()
    package_edit_coins = State()
    package_edit_price = State()
    wallet_id = State()
    wallet_adjust_amount = State()
    wallet_adjust_reason = State()


class PrivateAdminAccessFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not await AdminAccessFilter().__call__(event):
            return False
        if isinstance(event, Message) and event.chat.type != "private":
            await event.answer(
                "Admin panelidan faqat shaxsiy chatda foydalanish mumkin."
            )
            return False
        return True


def _parse_nonnegative_page(value: str) -> int | None:
    if not value.isascii() or not value.isdecimal():
        return None
    return int(value)


def _parse_positive_integer(value: str) -> int | None:
    if not value.isascii() or not value.isdecimal():
        return None
    result = int(value)
    return result if result > 0 else None


async def _edit_callback(
    callback: CallbackQuery,
    message_text: str,
    reply_markup=None,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    await callback.answer()


async def _show_item_page(callback: CallbackQuery, page: int) -> None:
    try:
        result = await admin_shop_service.get_shop_item_page(page)
    except admin_shop_service.AdminShopError as error:
        logger.error(
            "Admin Shop buyumlar sahifasi xatosi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Buyumlar ro‘yxatini olib bo‘lmadi.", show_alert=True)
        return
    entries = [
        (item.id, item.name, item.code, item.price_coins, item.is_active)
        for item in result.entries
    ]
    await _edit_callback(
        callback,
        texts.admin_shop_items_text(result.page, result.page_count, result.total),
        admin_shop_items_keyboard(entries, result.page, result.page_count),
    )


async def _show_item_detail(
    callback: CallbackQuery,
    item_id: int,
    page: int,
) -> None:
    try:
        item = await admin_shop_service.get_shop_item(item_id)
    except admin_shop_service.AdminShopError as error:
        logger.error(
            "Admin Shop buyumini olishda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Buyum ma’lumotini olib bo‘lmadi.", show_alert=True)
        return
    if item is None:
        await callback.answer("Buyum topilmadi.", show_alert=True)
        return
    await _edit_callback(
        callback,
        texts.admin_shop_item_text(item),
        admin_shop_item_keyboard(item.id, page, active=item.is_active),
    )


async def _show_package_page(callback: CallbackQuery, page: int) -> None:
    try:
        result = await admin_shop_service.get_coin_package_page(page)
    except admin_shop_service.AdminShopError as error:
        logger.error(
            "Admin coin paketlari sahifasi xatosi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Coin paketlari ro‘yxatini olib bo‘lmadi.", show_alert=True)
        return
    entries = [
        (item.id, item.name, item.code, item.coins, item.price_stars, item.is_active)
        for item in result.entries
    ]
    await _edit_callback(
        callback,
        texts.admin_coin_packages_text(
            result.page,
            result.page_count,
            result.total,
        ),
        admin_coin_packages_keyboard(entries, result.page, result.page_count),
    )


async def _show_package_detail(
    callback: CallbackQuery,
    package_id: int,
    page: int,
) -> None:
    try:
        package = await admin_shop_service.get_coin_package(package_id)
    except admin_shop_service.AdminShopError as error:
        logger.error(
            "Admin coin paketini olishda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Coin paketi ma’lumotini olib bo‘lmadi.", show_alert=True)
        return
    if package is None:
        await callback.answer("Coin paketi topilmadi.", show_alert=True)
        return
    await _edit_callback(
        callback,
        texts.admin_coin_package_text(package),
        admin_coin_package_keyboard(
            package.id,
            page,
            active=package.is_active,
        ),
    )


async def _show_payment_page(callback: CallbackQuery, page: int) -> None:
    try:
        result = await admin_shop_service.get_payment_history_page(page)
    except admin_shop_service.AdminShopError as error:
        logger.error(
            "Admin to‘lovlar tarixini olishda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("To‘lovlar tarixini olib bo‘lmadi.", show_alert=True)
        return
    rows = "\n".join(
        texts.admin_payment_history_entry(entry)
        for entry in result.entries
    )
    message_text = texts.admin_payment_history_text(
        result.page,
        result.page_count,
        result.total,
    )
    if rows:
        message_text += "\n\n" + rows
    await _edit_callback(
        callback,
        message_text,
        admin_payment_history_keyboard(result.page, result.page_count),
    )


async def _show_wallet(
    message: Message,
    telegram_user_id: int,
) -> None:
    try:
        wallet_record, transactions = await admin_shop_service.get_wallet_report(
            telegram_user_id
        )
    except admin_shop_service.AdminShopError as error:
        logger.error(
            "Admin wallet ma’lumotini olishda xato: error_type=%s",
            type(error).__name__,
        )
        await message.answer("Hamyon ma’lumotlarini olib bo‘lmadi.")
        return
    if wallet_record is None:
        await message.answer(
            texts.admin_wallet_not_found_text(telegram_user_id),
            reply_markup=admin_wallet_keyboard(telegram_user_id),
            parse_mode="HTML",
        )
        return
    await message.answer(
        texts.admin_wallet_text(
            telegram_user_id,
            wallet_record.balance_coins,
            transactions,
        ),
        reply_markup=admin_wallet_keyboard(telegram_user_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin:shop:"), PrivateAdminAccessFilter())
async def handle_admin_shop_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3 or parts[:2] != ["admin", "shop"]:
        await callback.answer("Admin Shop tugmasi noto‘g‘ri.", show_alert=True)
        return

    if parts[2] == "items" and len(parts) == 4:
        page = _parse_nonnegative_page(parts[3])
        if page is not None:
            await _show_item_page(callback, page)
            return
    elif parts[2] == "item":
        if len(parts) == 4 and parts[3] == "new":
            if isinstance(callback.message, Message):
                await state.clear()
                await state.set_state(AdminShopStates.item_name)
                await callback.message.edit_text(
                    "Yangi buyum nomini yuboring (255 belgigacha). /cancel — bekor qilish."
                )
            await callback.answer()
            return
        if len(parts) == 6 and parts[3] in {"view", "price", "toggle"}:
            item_id = _parse_positive_integer(parts[4])
            page = _parse_nonnegative_page(parts[5])
            if item_id is None or page is None:
                await callback.answer("Buyum tugmasi noto‘g‘ri.", show_alert=True)
                return
            if parts[3] == "view":
                await _show_item_detail(callback, item_id, page)
                return
            if parts[3] == "toggle":
                try:
                    item = await admin_shop_service.toggle_shop_item(item_id)
                except admin_shop_service.AdminShopError as error:
                    logger.error(
                        "Admin buyum holatini o‘zgartira olmadi: error_type=%s",
                        type(error).__name__,
                    )
                    await callback.answer("Buyum holatini o‘zgartirib bo‘lmadi.", show_alert=True)
                    return
                await _edit_callback(
                    callback,
                    texts.admin_shop_item_text(item),
                    admin_shop_item_keyboard(item.id, page, active=item.is_active),
                )
                return
            if isinstance(callback.message, Message):
                await state.clear()
                await state.update_data(shop_item_id=item_id, item_page=page)
                await state.set_state(AdminShopStates.item_price_edit)
                await callback.message.edit_text("Yangi coin narxini musbat butun son bilan yuboring.")
            await callback.answer()
            return
    elif parts[2] == "packages" and len(parts) == 4:
        page = _parse_nonnegative_page(parts[3])
        if page is not None:
            await _show_package_page(callback, page)
            return
    elif parts[2] == "package":
        if len(parts) == 4 and parts[3] == "new":
            if isinstance(callback.message, Message):
                await state.clear()
                await state.set_state(AdminShopStates.package_name)
                await callback.message.edit_text(
                    "Yangi coin paketi nomini yuboring. /cancel — bekor qilish."
                )
            await callback.answer()
            return
        if len(parts) == 6 and parts[3] in {"view", "edit", "toggle"}:
            package_id = _parse_positive_integer(parts[4])
            page = _parse_nonnegative_page(parts[5])
            if package_id is None or page is None:
                await callback.answer("Paket tugmasi noto‘g‘ri.", show_alert=True)
                return
            if parts[3] == "view":
                await _show_package_detail(callback, package_id, page)
                return
            if parts[3] == "toggle":
                try:
                    package = await admin_shop_service.toggle_coin_package(package_id)
                except admin_shop_service.AdminShopError as error:
                    logger.error(
                        "Admin paket holatini o‘zgartira olmadi: error_type=%s",
                        type(error).__name__,
                    )
                    await callback.answer("Paket holatini o‘zgartirib bo‘lmadi.", show_alert=True)
                    return
                await _edit_callback(
                    callback,
                    texts.admin_coin_package_text(package),
                    admin_coin_package_keyboard(
                        package.id,
                        page,
                        active=package.is_active,
                    ),
                )
                return
            if isinstance(callback.message, Message):
                await state.clear()
                await state.update_data(package_id=package_id, package_page=page)
                await state.set_state(AdminShopStates.package_edit_coins)
                await callback.message.edit_text("Yangi coin miqdorini musbat butun son bilan yuboring.")
            await callback.answer()
            return
    elif parts[2] == "wallet":
        if len(parts) == 3:
            await state.clear()
            await state.set_state(AdminShopStates.wallet_id)
            if isinstance(callback.message, Message):
                await callback.message.edit_text(texts.admin_wallet_search_prompt_text())
            await callback.answer()
            return
        if len(parts) == 6 and parts[3:5] == ["adjust", "add"]:
            await _begin_wallet_adjustment(callback, state, "add", parts[5])
            return
        if len(parts) == 6 and parts[3:5] == ["adjust", "spend"]:
            await _begin_wallet_adjustment(callback, state, "spend", parts[5])
            return
        if parts[3:5] == ["adjust", "confirm"] and len(parts) == 5:
            await _confirm_wallet_adjustment(callback, state)
            return
        if parts[3:5] == ["adjust", "cancel"] and len(parts) == 5:
            await state.clear()
            await callback.answer("Coin o‘zgarishi bekor qilindi.")
            if isinstance(callback.message, Message):
                await callback.message.edit_reply_markup(reply_markup=None)
            return
    elif parts[2] == "payments" and len(parts) == 4:
        page = _parse_nonnegative_page(parts[3])
        if page is not None:
            await _show_payment_page(callback, page)
            return

    await callback.answer("Admin Shop tugmasi noto‘g‘ri.", show_alert=True)


async def _begin_wallet_adjustment(
    callback: CallbackQuery,
    state: FSMContext,
    direction: str,
    telegram_user_id_text: str,
) -> None:
    telegram_user_id = _parse_positive_integer(telegram_user_id_text)
    if telegram_user_id is None:
        await callback.answer("Telegram ID noto‘g‘ri.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        adjustment_direction=direction,
        adjustment_user_id=telegram_user_id,
    )
    await state.set_state(AdminShopStates.wallet_adjust_amount)
    if isinstance(callback.message, Message):
        await callback.message.edit_text("O‘zgartiriladigan coin miqdorini yuboring.")
    await callback.answer()


async def _confirm_wallet_adjustment(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    draft = await state.get_data()
    admin_id = callback.from_user.id
    if draft.get("adjustment_admin_id") != admin_id:
        await callback.answer("Tasdiqlash ma’lumoti topilmadi.", show_alert=True)
        return
    user_id = draft.get("adjustment_user_id")
    amount = draft.get("adjustment_amount")
    direction = draft.get("adjustment_direction")
    reason = draft.get("adjustment_reason")
    key = draft.get("adjustment_key")
    if (
        type(user_id) is not int
        or type(amount) is not int
        or direction not in {"add", "spend"}
        or not isinstance(reason, str)
        or not isinstance(key, str)
    ):
        await callback.answer("Tasdiqlash ma’lumoti noto‘g‘ri.", show_alert=True)
        return

    note = f"Admin {admin_id}: {reason}"
    try:
        operation = add_coins if direction == "add" else spend_coins
        result = await operation(
            user_id,
            amount,
            reason="admin_adjustment",
            idempotency_key=key,
            note=note,
        )
    except InsufficientFunds as error:
        await callback.answer(
            f"Balans yetarli emas. Joriy balans: {error.balance_coins} coin.",
            show_alert=True,
        )
        return
    except (IdempotencyConflict, InvalidWalletOperation) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except WalletStorageError as error:
        logger.error(
            "Admin wallet adjustment saqlanmadi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Wallet o‘zgarishini saqlab bo‘lmadi.", show_alert=True)
        return

    await state.clear()
    try:
        transactions = await get_transaction_history(user_id, limit=10)
    except WalletStorageError as error:
        logger.error(
            "Admin adjustment tarixini olishda xato: error_type=%s",
            type(error).__name__,
        )
        transactions = []
    await _edit_callback(
        callback,
        texts.admin_wallet_text(
            user_id,
            result.balance_coins,
            transactions,
        ),
        admin_wallet_keyboard(user_id),
    )


@router.message(
    StateFilter(
        AdminShopStates.item_name,
        AdminShopStates.item_code,
        AdminShopStates.item_description,
        AdminShopStates.item_category,
        AdminShopStates.item_price,
        AdminShopStates.item_price_edit,
        AdminShopStates.package_name,
        AdminShopStates.package_code,
        AdminShopStates.package_coins,
        AdminShopStates.package_price,
        AdminShopStates.package_edit_coins,
        AdminShopStates.package_edit_price,
        AdminShopStates.wallet_id,
        AdminShopStates.wallet_adjust_amount,
        AdminShopStates.wallet_adjust_reason,
    ),
    PrivateAdminAccessFilter(),
)
async def receive_admin_shop_input(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    current = await state.get_state()
    if value.lower() == "/cancel":
        await state.clear()
        await message.answer("Kiritish bekor qilindi. /admin orqali menyuni oching.")
        return
    if not value:
        await message.answer("Qiymat bo‘sh bo‘lmasin. Qayta yuboring.")
        return

    data = await state.get_data()
    transitions = {
        AdminShopStates.item_name.state: (
            "item_name", AdminShopStates.item_code, "Buyum uchun noyob kod yuboring."
        ),
        AdminShopStates.item_code.state: (
            "item_code", AdminShopStates.item_description,
            "Tavsifni yuboring. Tavsifsiz qoldirish uchun - yuboring."
        ),
        AdminShopStates.item_description.state: (
            "item_description", AdminShopStates.item_category,
            "Buyum kategoriyasini yuboring."
        ),
        AdminShopStates.item_category.state: (
            "item_category", AdminShopStates.item_price,
            "Coin narxini musbat butun son bilan yuboring."
        ),
        AdminShopStates.package_name.state: (
            "package_name", AdminShopStates.package_code,
            "Paket uchun noyob kod yuboring."
        ),
        AdminShopStates.package_code.state: (
            "package_code", AdminShopStates.package_coins,
            "Paketdagi coin miqdorini musbat butun son bilan yuboring."
        ),
    }
    if current in transitions:
        key, next_state, prompt = transitions[current]
        await state.update_data(**{key: None if key == "item_description" and value == "-" else value})
        await state.set_state(next_state)
        await message.answer(prompt)
        return

    if current in {
        AdminShopStates.item_price.state,
        AdminShopStates.item_price_edit.state,
        AdminShopStates.package_coins.state,
        AdminShopStates.package_price.state,
        AdminShopStates.package_edit_coins.state,
        AdminShopStates.package_edit_price.state,
        AdminShopStates.wallet_id.state,
        AdminShopStates.wallet_adjust_amount.state,
    }:
        number = _parse_positive_integer(value)
        if number is None:
            await message.answer("Musbat butun son yuboring.")
            return
    else:
        number = None

    if current == AdminShopStates.item_price.state:
        try:
            item = await admin_shop_service.create_shop_item(
                name=data["item_name"],
                code=data["item_code"],
                description=data.get("item_description"),
                category=data["item_category"],
                price_coins=number,
            )
        except admin_shop_service.AdminShopError as error:
            await state.clear()
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer(
            texts.admin_shop_item_text(item),
            reply_markup=admin_shop_item_keyboard(item.id, 0, active=True),
            parse_mode="HTML",
        )
        return

    if current == AdminShopStates.item_price_edit.state:
        try:
            item = await admin_shop_service.update_shop_item_price(
                data["shop_item_id"],
                number,
            )
        except admin_shop_service.AdminShopError as error:
            await state.clear()
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer(
            texts.admin_shop_item_text(item),
            reply_markup=admin_shop_item_keyboard(
                item.id,
                data.get("item_page", 0),
                active=item.is_active,
            ),
            parse_mode="HTML",
        )
        return

    if current == AdminShopStates.package_price.state:
        try:
            package = await admin_shop_service.create_coin_package(
                name=data["package_name"],
                code=data["package_code"],
                coins=data["package_coins"],
                price_stars=number,
            )
        except admin_shop_service.AdminShopError as error:
            await state.clear()
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer(
            texts.admin_coin_package_text(package),
            reply_markup=admin_coin_package_keyboard(package.id, 0, active=True),
            parse_mode="HTML",
        )
        return

    if current == AdminShopStates.package_coins.state:
        await state.update_data(package_coins=number)
        await state.set_state(AdminShopStates.package_price)
        await message.answer("Stars narxini musbat butun son bilan yuboring.")
        return

    if current == AdminShopStates.package_edit_coins.state:
        await state.update_data(package_edit_coins=number)
        await state.set_state(AdminShopStates.package_edit_price)
        await message.answer("Yangi Stars narxini musbat butun son bilan yuboring.")
        return

    if current == AdminShopStates.package_edit_price.state:
        try:
            package = await admin_shop_service.update_coin_package(
                data["package_id"],
                coins=data["package_edit_coins"],
                price_stars=number,
            )
        except admin_shop_service.AdminShopError as error:
            await state.clear()
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer(
            texts.admin_coin_package_text(package),
            reply_markup=admin_coin_package_keyboard(
                package.id,
                data.get("package_page", 0),
                active=package.is_active,
            ),
            parse_mode="HTML",
        )
        return

    if current == AdminShopStates.wallet_id.state:
        await state.clear()
        await _show_wallet(message, number)
        return

    if current == AdminShopStates.wallet_adjust_amount.state:
        await state.update_data(adjustment_amount=number)
        await state.set_state(AdminShopStates.wallet_adjust_reason)
        await message.answer("Coin o‘zgarishining sababini majburiy ravishda yozing (255 belgigacha).")
        return

    if current == AdminShopStates.wallet_adjust_reason.state:
        reason = value.strip()
        if len(reason) < 3 or len(reason) > 200:
            await message.answer("Sabab 3–200 belgi oralig‘ida bo‘lishi kerak.")
            return
        admin_id = message.from_user.id
        await state.update_data(
            adjustment_reason=reason,
            adjustment_admin_id=admin_id,
            adjustment_key=(
                f"admin-adjust:{admin_id}:"
                f"{data['adjustment_user_id']}:"
                f"{secrets.token_urlsafe(12)}"
            ),
        )
        await message.answer(
            texts.admin_wallet_adjust_confirmation_text(
                data["adjustment_user_id"],
                data["adjustment_direction"],
                data["adjustment_amount"],
                reason,
            ),
            reply_markup=admin_wallet_adjust_confirmation_keyboard(),
            parse_mode="HTML",
        )

