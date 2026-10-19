"""Group command for transferring Bunker Coins between registered players."""

import logging
import re
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import LinkPreviewOptions, Message, User

from database.wallet import (
    COIN_TRANSFER_FEE,
    MAX_COIN_TRANSFER,
    DuplicateWalletTransfer,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidWalletOperation,
    WalletAccountNotFound,
    WalletStorageError,
    transfer_coins,
)

router = Router()
logger = logging.getLogger(__name__)
_AMOUNT_PATTERN = re.compile(r"[0-9]+\Z")
_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_]{5,32}\Z")


def parse_transfer_amount(args: str | None) -> int | None:
    if not args:
        return None
    parts = args.split()
    if len(parts) != 1 or _AMOUNT_PATTERN.fullmatch(parts[0]) is None:
        return None
    amount = int(parts[0])
    if not 1 <= amount <= MAX_COIN_TRANSFER:
        return None
    return amount


def _format_coins(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def _user_link(user: User) -> str:
    name = escape(user.full_name)
    username = (user.username or "").lstrip("@")
    if username and _USERNAME_PATTERN.fullmatch(username):
        href = f"https://t.me/{username}"
    else:
        href = f"tg://user?id={user.id}"
    return f'<a href="{escape(href, quote=True)}">{name}</a>'


def coin_transfer_text(sender: User, recipient: User, amount: int) -> str:
    return (
        f"💸 {_user_link(sender)} → {_user_link(recipient)} ga "
        f"<b>{_format_coins(amount)} coin</b> yubordi!\n"
        f"🧾 Komissiya: <b>{COIN_TRANSFER_FEE} coin</b>"
    ).replace(",", " ")


@router.message(Command("coin"))
async def transfer_coin_command(message: Message, command: CommandObject) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("/coin buyrug‘i faqat guruhda ishlaydi.")
        return
    sender = message.from_user
    if sender is None or sender.is_bot:
        await message.answer("Coin yuboruvchi aniqlanmadi.")
        return

    amount = parse_transfer_amount(command.args)
    if amount is None:
        await message.answer(
            f"Miqdorni 1 dan {_format_coins(MAX_COIN_TRANSFER)} gacha butun son qilib kiriting.\n"
            "Masalan: boshqa foydalanuvchi xabariga reply qilib <code>/coin 100</code> yuboring.",
            parse_mode="HTML",
        )
        return

    replied = message.reply_to_message
    recipient = replied.from_user if replied is not None else None
    if recipient is None or recipient.is_bot or getattr(replied, "sender_chat", None):
        await message.answer(
            "Coin yuborish uchun guruhdagi haqiqiy foydalanuvchi xabariga reply qiling."
        )
        return
    if recipient.id == sender.id:
        await message.answer("O‘zingizga coin yubora olmaysiz.")
        return

    operation_key = f"coin-transfer:{message.chat.id}:{message.message_id}"
    try:
        await transfer_coins(
            sender.id,
            recipient.id,
            amount,
            idempotency_key=operation_key,
        )
    except InsufficientFunds as error:
        await message.answer(
            f"Balans yetarli emas. O‘tkazma: {_format_coins(amount)} coin, "
            f"komissiya: {COIN_TRANSFER_FEE} coin, jami kerak: "
            f"{_format_coins(error.requested_coins)} coin. Hozirgi balans: "
            f"{_format_coins(error.balance_coins)} coin."
        )
        return
    except WalletAccountNotFound:
        await message.answer(
            "Yuboruvchi yoki qabul qiluvchining botdagi coin akkaunti topilmadi."
        )
        return
    except DuplicateWalletTransfer:
        await message.answer("Bu coin o‘tkazma buyrug‘i avval bajarilgan.")
        return
    except (InvalidWalletOperation, IdempotencyConflict):
        await message.answer("Coin o‘tkazmasi tekshiruvdan o‘tmadi. Yangi buyruq yuboring.")
        return
    except WalletStorageError as error:
        logger.error("Coin transfer failed: error_type=%s", type(error).__name__)
        await message.answer("Coin o‘tkazmasini hozir bajarib bo‘lmadi. Keyinroq urinib ko‘ring.")
        return

    try:
        await message.answer(
            coin_transfer_text(sender, recipient, amount),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramAPIError as error:
        # The DB transaction has committed; do not repeat it if this notice fails.
        logger.info("Coin transfer notice failed: error_type=%s", type(error).__name__)
