"""Transactional Bunker Coin wallet operations."""

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_sessionmaker
from database.models import PlayerWallet, WalletTransaction

logger = logging.getLogger(__name__)

WalletReason = Literal[
    "daily_bonus",
    "game_reward",
    "shop_purchase",
    "admin_adjustment",
    "payment",
]
WalletDirection = Literal["credit", "debit"]

_VALID_REASONS = {
    "daily_bonus",
    "game_reward",
    "shop_purchase",
    "admin_adjustment",
    "payment",
}


class WalletError(Exception):
    """Base class for expected wallet operation errors."""


class InvalidWalletOperation(WalletError, ValueError):
    """Raised when wallet operation arguments are invalid."""


class InsufficientFunds(WalletError):
    """Raised when a wallet cannot cover a debit."""

    def __init__(self, balance_coins: int, requested_coins: int) -> None:
        self.balance_coins = balance_coins
        self.requested_coins = requested_coins
        super().__init__(
            f"Insufficient Bunker Coins: balance={balance_coins}, "
            f"requested={requested_coins}"
        )


class IdempotencyConflict(WalletError):
    """Raised when an idempotency key is reused for a different operation."""


class WalletStorageError(WalletError):
    """Raised when a wallet operation cannot be persisted."""


@dataclass(frozen=True)
class WalletOperationResult:
    transaction_id: int
    balance_coins: int
    replayed: bool


async def _get_or_create_locked_wallet(
    session: AsyncSession,
    telegram_user_id: int,
) -> PlayerWallet:
    await session.execute(
        postgresql_insert(PlayerWallet)
        .values(telegram_user_id=telegram_user_id)
        .on_conflict_do_nothing(
            constraint="uq_player_wallets_telegram_user_id"
        )
    )
    wallet = await session.scalar(
        select(PlayerWallet)
        .where(PlayerWallet.telegram_user_id == telegram_user_id)
        .with_for_update()
    )
    if wallet is None:
        raise WalletStorageError("Hamyonni olish imkonsiz bo‘ldi.")
    return wallet


def _validate_user_id(telegram_user_id: int) -> None:
    if type(telegram_user_id) is not int or telegram_user_id <= 0:
        raise InvalidWalletOperation("Telegram foydalanuvchi ID’si musbat bo‘lishi kerak.")


def _validate_operation(
    telegram_user_id: int,
    amount: int,
    reason: str,
    idempotency_key: str,
    note: str | None,
) -> None:
    _validate_user_id(telegram_user_id)
    if type(amount) is not int or amount <= 0:
        raise InvalidWalletOperation("Coin miqdori musbat butun son bo‘lishi kerak.")
    if reason not in _VALID_REASONS:
        raise InvalidWalletOperation("Coin operatsiyasi manbasi noto‘g‘ri.")
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 128
    ):
        raise InvalidWalletOperation("Idempotency kaliti noto‘g‘ri.")
    if note is not None and (
        not isinstance(note, str) or len(note.strip()) > 255
    ):
        raise InvalidWalletOperation("Coin operatsiyasi izohi noto‘g‘ri.")


def _log_storage_error(error: SQLAlchemyError) -> None:
    logger.error(
        "Wallet database operation failed: error_type=%s",
        type(error).__name__,
    )


def _ensure_same_operation(
    transaction: WalletTransaction,
    telegram_user_id: int,
    direction: WalletDirection,
    amount: int,
    reason: str,
    note: str | None,
) -> None:
    if (
        transaction.telegram_user_id != telegram_user_id
        or transaction.direction != direction
        or transaction.amount_coins != amount
        or transaction.reason != reason
        or transaction.note != (note.strip() if note else None)
    ):
        raise IdempotencyConflict(
            "Idempotency kaliti boshqa coin operatsiyasida ishlatilgan."
        )


async def _find_replayed_operation(
    telegram_user_id: int,
    direction: WalletDirection,
    amount: int,
    reason: str,
    idempotency_key: str,
    note: str | None,
) -> WalletOperationResult | None:
    async with get_sessionmaker()() as session:
        transaction = await session.scalar(
            select(WalletTransaction).where(
                WalletTransaction.idempotency_key == idempotency_key
            )
        )
        if transaction is None:
            return None

        _ensure_same_operation(
            transaction,
            telegram_user_id,
            direction,
            amount,
            reason,
            note,
        )
        balance = await session.scalar(
            select(PlayerWallet.balance_coins).where(
                PlayerWallet.telegram_user_id == telegram_user_id
            )
        )
        if balance is None:
            return None
        return WalletOperationResult(
            transaction_id=transaction.id,
            balance_coins=balance,
            replayed=True,
        )


async def get_or_create_wallet(telegram_user_id: int) -> PlayerWallet:
    """Return a player's wallet, creating it safely if it does not exist."""
    _validate_user_id(telegram_user_id)
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                return await _get_or_create_locked_wallet(
                    session,
                    telegram_user_id,
                )
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise WalletStorageError("Hamyonni hozir olib bo‘lmadi.") from None


async def get_balance(telegram_user_id: int) -> int:
    """Return the current balance, creating an empty wallet when needed."""
    wallet = await get_or_create_wallet(telegram_user_id)
    return wallet.balance_coins


async def _change_balance(
    telegram_user_id: int,
    amount: int,
    *,
    direction: WalletDirection,
    reason: WalletReason,
    idempotency_key: str,
    note: str | None,
) -> WalletOperationResult:
    _validate_operation(telegram_user_id, amount, reason, idempotency_key, note)

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                wallet = await _get_or_create_locked_wallet(
                    session,
                    telegram_user_id,
                )
                existing = await session.scalar(
                    select(WalletTransaction).where(
                        WalletTransaction.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    _ensure_same_operation(
                        existing,
                        telegram_user_id,
                        direction,
                        amount,
                        reason,
                        note,
                    )
                    return WalletOperationResult(
                        transaction_id=existing.id,
                        balance_coins=wallet.balance_coins,
                        replayed=True,
                    )

                if direction == "debit" and wallet.balance_coins < amount:
                    raise InsufficientFunds(wallet.balance_coins, amount)

                if direction == "credit":
                    wallet.balance_coins += amount
                else:
                    wallet.balance_coins -= amount

                transaction = WalletTransaction(
                    telegram_user_id=telegram_user_id,
                    direction=direction,
                    amount_coins=amount,
                    reason=reason,
                    note=note.strip() if note else None,
                    idempotency_key=idempotency_key,
                )
                session.add(transaction)
                await session.flush()
                return WalletOperationResult(
                    transaction_id=transaction.id,
                    balance_coins=wallet.balance_coins,
                    replayed=False,
                )
    except InsufficientFunds:
        raise
    except IdempotencyConflict:
        raise
    except IntegrityError as error:
        try:
            replayed = await _find_replayed_operation(
                telegram_user_id,
                direction,
                amount,
                reason,
                idempotency_key,
                note,
            )
        except IdempotencyConflict:
            raise
        except SQLAlchemyError as lookup_error:
            _log_storage_error(lookup_error)
            raise WalletStorageError(
                "Coin operatsiyasini tekshirib bo‘lmadi."
            ) from None
        if replayed is not None:
            return replayed
        _log_storage_error(error)
        raise WalletStorageError("Coin operatsiyasini saqlab bo‘lmadi.") from None
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise WalletStorageError("Coin operatsiyasini saqlab bo‘lmadi.") from None


async def add_coins(
    telegram_user_id: int,
    amount: int,
    *,
    reason: WalletReason,
    idempotency_key: str,
    note: str | None = None,
) -> WalletOperationResult:
    """Credit coins and append the matching idempotent ledger entry."""
    return await _change_balance(
        telegram_user_id,
        amount,
        direction="credit",
        reason=reason,
        idempotency_key=idempotency_key,
        note=note,
    )


async def spend_coins(
    telegram_user_id: int,
    amount: int,
    *,
    reason: WalletReason,
    idempotency_key: str,
    note: str | None = None,
) -> WalletOperationResult:
    """Debit coins if affordable and append the matching ledger entry."""
    return await _change_balance(
        telegram_user_id,
        amount,
        direction="debit",
        reason=reason,
        idempotency_key=idempotency_key,
        note=note,
    )


async def get_transaction_history(
    telegram_user_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[WalletTransaction]:
    """Return newest wallet transactions first, with bounded pagination."""
    _validate_user_id(telegram_user_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidWalletOperation("Tarix limit 1 dan 100 gacha bo‘lishi kerak.")
    if type(offset) is not int or offset < 0:
        raise InvalidWalletOperation("Tarix offset manfiy bo‘lmasligi kerak.")

    try:
        async with get_sessionmaker()() as session:
            result = await session.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.telegram_user_id == telegram_user_id)
                .order_by(
                    WalletTransaction.created_at.desc(),
                    WalletTransaction.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            return list(result.all())
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise WalletStorageError("Coin tarixini hozir olib bo‘lmadi.") from None