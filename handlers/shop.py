"""Database-backed Survival Shop and inventory handlers."""

import logging
import os

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from database import shop as shop_service
from database.wallet import InsufficientFunds, WalletStorageError, get_balance
from ui import texts
from ui.keyboards import (
    inventory_keyboard,
    shop_after_purchase_keyboard,
    shop_confirmation_keyboard,
    shop_insufficient_funds_keyboard,
    shop_item_keyboard,
    shop_keyboard,
    shop_main_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="shop")


def _parse_callback(data: str | None) -> tuple[str, int, int | None] | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) not in {3, 4} or parts[0] != "sh":
        return None
    action = parts[1]
    if action not in {"m", "s", "l", "i", "d", "p", "c"}:
        return None
    try:
        telegram_user_id = int(parts[2])
        item_id = int(parts[3]) if len(parts) == 4 else None
    except ValueError:
        return None
    if telegram_user_id <= 0 or (item_id is not None and item_id <= 0):
        return None
    if (action in {"d", "p", "c"}) != (item_id is not None):
        return None
    if action in {"m", "s", "l", "i"} and item_id is not None:
        return None
    return action, telegram_user_id, item_id


async def _edit_callback_message(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    alert: str | None = None,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer(
            alert or "Bu xabarni yangilab bo‘lmadi.",
            show_alert=True,
        )
        return
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logger.info("Shop xabarini tahrirlash muvaffaqiyatsiz bo‘ldi")
        await callback.answer(
            alert or "Xabarni yangilab bo‘lmadi. Qayta urinib ko‘ring.",
            show_alert=True,
        )
        return
    await callback.answer(alert or "")


async def _shop_home_content(telegram_user_id: int):
    items = await shop_service.get_active_shop_items()
    balance_coins = await get_balance(telegram_user_id)
    survival_items = [item for item in items if item.category == "survival"]
    return (
        texts.shop_home_text(balance_coins, len(survival_items)),
        shop_main_keyboard(
            telegram_user_id,
            has_survival_items=bool(survival_items),
            webapp_url=os.getenv("PARA_GIFTS_WEBAPP_URL"),
        ),
    )


async def _show_shop_home_message(
    message: Message,
    telegram_user_id: int,
) -> None:
    try:
        text, keyboard = await _shop_home_content(telegram_user_id)
    except (shop_service.ShopStorageError, WalletStorageError) as error:
        logger.error(
            "Shop menyusini tayyorlashda xato: error_type=%s",
            type(error).__name__,
        )
        await message.answer("Shop’ni hozir ochib bo‘lmadi. Keyinroq urinib ko‘ring.")
        return
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _edit_shop_home(callback: CallbackQuery, telegram_user_id: int) -> None:
    try:
        text, keyboard = await _shop_home_content(telegram_user_id)
    except (shop_service.ShopStorageError, WalletStorageError) as error:
        logger.error(
            "Shop menyusini yangilashda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Shop’ni hozir ochib bo‘lmadi.", show_alert=True)
        return
    await _edit_callback_message(callback, text, reply_markup=keyboard)


async def _show_inventory_message(message: Message, telegram_user_id: int) -> None:
    try:
        entries = await shop_service.get_player_inventory(telegram_user_id)
    except shop_service.ShopStorageError as error:
        logger.error(
            "Inventarni olishda xato: error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            "Inventarni hozir olib bo‘lmadi. Keyinroq urinib ko‘ring."
        )
        return

    if not entries:
        await message.answer(
            texts.empty_inventory_text(),
            reply_markup=inventory_keyboard(telegram_user_id),
            parse_mode="HTML",
        )
        return

    await message.answer(
        texts.inventory_text(
            [(entry.name, entry.description, entry.quantity) for entry in entries]
        ),
        reply_markup=inventory_keyboard(telegram_user_id),
        parse_mode="HTML",
    )


@router.message(Command("shop"))
async def show_shop(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Foydalanuvchini aniqlab bo‘lmadi.")
        return
    await _show_shop_home_message(message, message.from_user.id)


@router.callback_query(F.data == "menu:shop")
async def open_shop_from_menu(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer("Shop menyusini ochib bo‘lmadi.", show_alert=True)
        return
    await _edit_shop_home(callback, callback.from_user.id)


@router.message(Command("inventory"))
async def show_inventory(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Foydalanuvchini aniqlab bo‘lmadi.")
        return
    await _show_inventory_message(message, message.from_user.id)


@router.callback_query(F.data.startswith("sh:"))
async def handle_shop_callback(callback: CallbackQuery) -> None:
    parsed = _parse_callback(callback.data)
    if parsed is None:
        await callback.answer("Tugma eskirgan yoki noto‘g‘ri.", show_alert=True)
        return

    action, owner_id, item_id = parsed
    if callback.from_user.id != owner_id:
        await callback.answer(
            "Bu tugma boshqa foydalanuvchi uchun.",
            show_alert=True,
        )
        return
    if not isinstance(callback.message, Message):
        await callback.answer(
            "Shop xabarini topib bo‘lmadi. /shop buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    if action == "l":
        try:
            items = await shop_service.get_active_shop_items()
            balance_coins = await get_balance(owner_id)
        except (shop_service.ShopStorageError, WalletStorageError) as error:
            logger.error(
                "Shop ro‘yxatini qayta olishda xato: error_type=%s",
                type(error).__name__,
            )
            await callback.answer(
                "Do‘konni hozir ochib bo‘lmadi.",
                show_alert=True,
            )
            return
        await _edit_callback_message(
            callback,
            texts.shop_catalog_text(balance_coins, len(items)),
            reply_markup=shop_keyboard(
                [(item.id, item.name, item.price_coins) for item in items],
                owner_id,
            ),
        )
        return

    if action == "m":
        await _edit_shop_home(callback, owner_id)
        return

    if action == "s":
        try:
            items = [
                item
                for item in await shop_service.get_active_shop_items()
                if item.category == "survival"
            ]
            balance_coins = await get_balance(owner_id)
        except (shop_service.ShopStorageError, WalletStorageError) as error:
            logger.error(
                "Survival katalogini ochishda xato: error_type=%s",
                type(error).__name__,
            )
            await callback.answer("Katalogni hozir ochib bo‘lmadi.", show_alert=True)
            return
        await _edit_callback_message(
            callback,
            texts.shop_catalog_text(balance_coins, len(items)),
            reply_markup=shop_keyboard(
                [(item.id, item.name, item.price_coins) for item in items],
                owner_id,
            ),
        )
        return

    if action == "i":
        try:
            entries = await shop_service.get_player_inventory(owner_id)
        except shop_service.ShopStorageError as error:
            logger.error(
                "Inventarni callback orqali olishda xato: error_type=%s",
                type(error).__name__,
            )
            await callback.answer("Inventarni hozir ochib bo‘lmadi.", show_alert=True)
            return
        if not entries:
            await _edit_callback_message(
                callback,
                texts.empty_inventory_text(),
                reply_markup=inventory_keyboard(owner_id),
            )
            return
        await _edit_callback_message(
            callback,
            texts.inventory_text(
                [
                    (entry.name, entry.description, entry.quantity)
                    for entry in entries
                ]
            ),
            reply_markup=inventory_keyboard(owner_id),
        )
        return

    if item_id is None:
        await callback.answer("Buyum tugmasi noto‘g‘ri.", show_alert=True)
        return

    try:
        if action == "c":
            message = callback.message
            idempotency_key = (
                f"shop:{owner_id}:{item_id}:"
                f"{message.chat.id}:{message.message_id}"
            )
            result = await shop_service.purchase_shop_item(
                owner_id,
                item_id,
                idempotency_key=idempotency_key,
            )
            await _edit_callback_message(
                callback,
                texts.shop_purchase_result_text(
                    result.item_name,
                    result.price_coins,
                    result.quantity,
                    result.balance_coins,
                    replayed=result.replayed,
                ),
                reply_markup=shop_after_purchase_keyboard(owner_id),
            )
            return

        item = await shop_service.get_active_shop_item(item_id)
        if item is None:
            await _edit_callback_message(
                callback,
                texts.shop_item_unavailable_text(),
                reply_markup=inventory_keyboard(owner_id),
            )
            return

        if action in {"d", "p"}:
            balance_coins = await get_balance(owner_id)
            if action == "d":
                await _edit_callback_message(
                    callback,
                    texts.shop_item_text(
                        item.name,
                        item.description,
                        item.category,
                        item.price_coins,
                        balance_coins,
                    ),
                    reply_markup=shop_item_keyboard(owner_id, item.id),
                )
                return
            await _edit_callback_message(
                callback,
                texts.shop_purchase_confirmation_text(
                    item.name,
                    item.price_coins,
                    balance_coins,
                ),
                reply_markup=shop_confirmation_keyboard(owner_id, item.id),
            )
            return
    except InsufficientFunds as error:
        await _edit_callback_message(
            callback,
            texts.shop_insufficient_funds_text(
                error.balance_coins,
                error.requested_coins,
            ),
            reply_markup=shop_insufficient_funds_keyboard(owner_id, item_id),
        )
        return
    except shop_service.ShopItemUnavailable:
        await _edit_callback_message(
            callback,
            texts.shop_item_unavailable_text(),
            reply_markup=inventory_keyboard(owner_id),
        )
        return
    except shop_service.ShopPurchaseConflict:
        await callback.answer(
            "Xarid allaqachon bajarilgan yoki tugma eskirgan.",
            show_alert=True,
        )
        return
    except (shop_service.ShopStorageError, WalletStorageError, SQLAlchemyError) as error:
        logger.error(
            "Shop callback bajarilmadi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "Amalni hozir bajarib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
