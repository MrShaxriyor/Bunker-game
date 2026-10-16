"""Telegram Stars coin package payment handlers."""

import asyncio
import logging
import os
import re
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from database import payments as payment_service
from database.payments import PaymentRejected, PaymentStorageError
from handlers.admin import get_admin_ids
from ui import texts
from ui.keyboards import coin_package_keyboard, payment_support_keyboard

logger = logging.getLogger(__name__)
router = Router(name="payments")
PAYMENT_PROCESSING_ATTEMPTS = 3
INVOICE_CLICK_COOLDOWN_SECONDS = 10
_recent_invoice_clicks: dict[tuple[int, int], float] = {}
_invoice_click_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _parse_callback(data: str | None) -> tuple[str, int, int | None] | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) not in {3, 4} or parts[0] != "pay":
        return None
    action = parts[1]
    if action not in {"l", "s"}:
        return None
    try:
        telegram_user_id = int(parts[2])
        package_id = int(parts[3]) if len(parts) == 4 else None
    except ValueError:
        return None
    if telegram_user_id <= 0 or (package_id is not None and package_id <= 0):
        return None
    if (action == "s") != (package_id is not None):
        return None
    return action, telegram_user_id, package_id


async def _show_packages(message: Message, telegram_user_id: int) -> None:
    try:
        packages = await payment_service.get_active_coin_packages()
    except PaymentStorageError as error:
        logger.error(
            "Coin paketlarini olishda xato: error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            "Coin paketlarini hozir olib bo‘lmadi. Keyinroq urinib ko‘ring."
        )
        return

    await message.answer(
        texts.coin_packages_text(
            [(package.name, package.coins, package.price_stars) for package in packages]
        ),
        reply_markup=coin_package_keyboard(
            [
                (package.id, package.name, package.coins, package.price_stars)
                for package in packages
            ],
            telegram_user_id,
        ),
        parse_mode="HTML",
    )


async def _send_package_invoice(
    callback: CallbackQuery,
    telegram_user_id: int,
    package_id: int,
) -> None:
    key = (telegram_user_id, package_id)
    lock = _invoice_click_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        last_click = _recent_invoice_clicks.get(key)
        if (
            last_click is not None
            and now - last_click < INVOICE_CLICK_COOLDOWN_SECONDS
        ):
            await callback.answer(
                "Invoice yaqinda yuborildi. Yangi invoice so‘rashdan oldin biroz kuting.",
                show_alert=True,
            )
            return
        await _send_package_invoice_locked(
            callback,
            telegram_user_id,
            package_id,
            key,
        )


async def _send_package_invoice_locked(
    callback: CallbackQuery,
    telegram_user_id: int,
    package_id: int,
    click_key: tuple[int, int],
) -> None:
    try:
        invoice = await payment_service.create_payment_order(
            telegram_user_id,
            package_id,
        )
    except PaymentRejected as error:
        await callback.answer(str(error), show_alert=True)
        return
    except PaymentStorageError as error:
        logger.error(
            "Coin to‘lov buyurtmasi yaratilmadi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "To‘lov buyurtmasini hozir yaratib bo‘lmadi.",
            show_alert=True,
        )
        return

    description = texts.payment_invoice_description(
        invoice.package_name,
        invoice.coins,
    )
    try:
        await callback.bot.send_invoice(
            chat_id=telegram_user_id,
            title=invoice.package_name[:32],
            description=description[:255],
            payload=invoice.invoice_payload,
            provider_token="",
            currency=payment_service.STARS_CURRENCY,
            prices=[
                LabeledPrice(
                    label=invoice.package_name[:32],
                    amount=invoice.price_stars,
                )
            ],
        )
    except TelegramAPIError as error:
        logger.info(
            "Telegram Stars invoice yuborilmadi: error_type=%s",
            type(error).__name__,
        )
        await payment_service.mark_invoice_failed(
            telegram_user_id,
            invoice.invoice_payload,
        )
        await callback.answer(
            texts.payment_start_private_chat_text(),
            show_alert=True,
        )
        return

    _recent_invoice_clicks[click_key] = time.monotonic()
    await callback.answer(texts.payment_invoice_sent_text())


@router.message(Command("buycoins"))
async def buy_coins(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Foydalanuvchini aniqlab bo‘lmadi.")
        return
    await _show_packages(message, message.from_user.id)


@router.callback_query(F.data == "menu:buycoins")
async def open_coin_packages_from_menu(callback: CallbackQuery) -> None:
    await _show_packages_from_callback(callback, callback.from_user.id)


@router.message(Command("paysupport"))
async def payment_support(message: Message) -> None:
    username = os.getenv("PAYMENT_SUPPORT_USERNAME", "").strip().lstrip("@")
    support_url = (
        f"https://t.me/{username}"
        if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username)
        else None
    )
    if support_url is None:
        try:
            admin_ids = get_admin_ids()
        except ValueError as error:
            logger.error(
                "Payment support admin sozlamasi noto‘g‘ri: error_type=%s",
                type(error).__name__,
            )
            admin_ids = frozenset()
        if admin_ids:
            support_url = f"tg://user?id={min(admin_ids)}"

    await message.answer(
        texts.payment_support_text(),
        reply_markup=payment_support_keyboard(support_url),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pay:"))
async def handle_payment_callback(callback: CallbackQuery) -> None:
    parsed = _parse_callback(callback.data)
    if parsed is None:
        await callback.answer("Paket tugmasi eskirgan yoki noto‘g‘ri.", show_alert=True)
        return
    action, owner_id, package_id = parsed
    if callback.from_user.id != owner_id:
        await callback.answer(
            "Bu paket tugmasi boshqa foydalanuvchi uchun.",
            show_alert=True,
        )
        return
    if action == "l":
        await _show_packages_from_callback(callback, owner_id)
        return
    if package_id is None:
        await callback.answer("Paket tanlovi noto‘g‘ri.", show_alert=True)
        return
    await _send_package_invoice(callback, owner_id, package_id)


async def _show_packages_from_callback(
    callback: CallbackQuery,
    telegram_user_id: int,
) -> None:
    try:
        packages = await payment_service.get_active_coin_packages()
    except PaymentStorageError as error:
        logger.error(
            "Coin paketlarini callback orqali olishda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Coin paketlarini hozir olib bo‘lmadi.", show_alert=True)
        return

    if not isinstance(callback.message, Message):
        await callback.answer("Paketlar xabarini topib bo‘lmadi.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            texts.coin_packages_text(
                [
                    (package.name, package.coins, package.price_stars)
                    for package in packages
                ]
            ),
            reply_markup=coin_package_keyboard(
                [
                    (package.id, package.name, package.coins, package.price_stars)
                    for package in packages
                ],
                telegram_user_id,
            ),
            parse_mode="HTML",
        )
    except TelegramAPIError as error:
        logger.info(
            "Coin paketlari xabarini tahrirlashda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer("Paketlar xabarini yangilab bo‘lmadi.", show_alert=True)
        return
    await callback.answer()


@router.pre_checkout_query()
async def validate_pre_checkout(query: PreCheckoutQuery) -> None:
    try:
        validation = await payment_service.validate_pre_checkout(
            query.from_user.id,
            query.invoice_payload,
            query.currency,
            query.total_amount,
        )
    except PaymentStorageError as error:
        logger.error(
            "Pre-checkout tekshiruvida DB xatosi: error_type=%s",
            type(error).__name__,
        )
        await query.answer(
            ok=False,
            error_message="To‘lovni hozir tekshirib bo‘lmadi. Keyinroq urinib ko‘ring.",
        )
        return
    await query.answer(
        ok=validation.ok,
        error_message=validation.error_message,
    )


@router.message(F.successful_payment)
async def process_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return

    for attempt in range(PAYMENT_PROCESSING_ATTEMPTS):
        try:
            settlement = await payment_service.settle_successful_payment(
                message.from_user.id,
                payment.invoice_payload,
                payment.currency,
                payment.total_amount,
                payment.telegram_payment_charge_id,
            )
        except PaymentRejected as error:
            logger.warning(
                "Telegram Stars payment verification rejected: error_type=%s",
                type(error).__name__,
            )
            await message.answer(
                texts.payment_rejected_text(),
                parse_mode="HTML",
            )
            return
        except PaymentStorageError as error:
            logger.error(
                "Stars payment settlement failed: attempt=%s, error_type=%s",
                attempt + 1,
                type(error).__name__,
            )
            if attempt + 1 < PAYMENT_PROCESSING_ATTEMPTS:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            await message.answer(texts.payment_processing_text())
            return

        if settlement.replayed:
            await message.answer(texts.payment_replayed_text())
        else:
            await message.answer(
                texts.payment_success_text(
                    settlement.coins,
                    settlement.balance_coins,
                ),
                parse_mode="HTML",
            )
        return
