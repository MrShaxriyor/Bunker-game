
from dataclasses import dataclass, field
import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from html import escape
from functools import wraps
from typing import Literal
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatPermissions, LinkPreviewOptions, Message
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from database.blocks import is_user_blocked, lock_user_for_block_check
from database.engine import get_sessionmaker
from database.models import (
    Game, GamePlayer, GameRound, PlayerCard, PlayerWallet, WalletTransaction,
)
from data.cards import deal_cards, format_private_cards
from ui import texts
from ui.keyboards import (
    card_reveal_keyboard,
    confirmation_keyboard,
    lobby_keyboard as build_lobby_keyboard,
    voting_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)
_lobby_creation_locks: dict[int, asyncio.Lock] = {}


def _serialize_lobby_creation(handler):
    @wraps(handler)
    async def wrapped(message: Message):
        if message.chat.type not in ("group", "supergroup"):
            return await handler(message)
        lock = _lobby_creation_locks.setdefault(
            message.chat.id,
            asyncio.Lock(),
        )
        async with lock:
            return await handler(message)

    return wrapped

MIN_PLAYERS = 4
MAX_PLAYERS = 16
INITIAL_ROUND_DELAY_SECONDS = 15
REVEAL_DURATION_SECONDS = 30
DISCUSSION_DURATION_SECONDS = 180
VOTING_DURATION_SECONDS = 60
CONFIRMATION_DURATION_SECONDS = 30
ROUND_TRANSITION_DELAY_SECONDS = 5


def _integrity_constraint_name(error: IntegrityError) -> str | None:
    original_error: BaseException | None = error.orig
    visited: set[int] = set()

    while original_error is not None and id(original_error) not in visited:
        visited.add(id(original_error))
        constraint_name = getattr(original_error, "constraint_name", None)
        if isinstance(constraint_name, str):
            return constraint_name
        original_error = original_error.__cause__

    return None


async def _persist_card_reveal(
    lobby: "Lobby",
    user_id: int,
    category: str,
    round_number: int,
    method: Literal["manual", "automatic"],
) -> tuple[
    Literal["updated", "already_revealed", "unavailable", "error"],
    str | None,
    Literal["manual", "automatic"] | None,
]:
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                game = await session.scalar(
                    select(Game)
                    .where(
                        Game.chat_id == lobby.chat_id,
                        Game.status == "active",
                        Game.phase == "reveal",
                        Game.current_round == round_number,
                    )
                    .with_for_update()
                )
                if game is None:
                    return ("unavailable", None, None)

                game_round = await session.scalar(
                    select(GameRound)
                    .where(
                        GameRound.game_id == game.id,
                        GameRound.round_number == round_number,
                    )
                    .with_for_update()
                )
                if game_round is None or game_round.is_settled:
                    return ("unavailable", None, None)

                player_reveal = (
                    await session.execute(
                        select(
                            PlayerCard.category,
                            PlayerCard.reveal_method,
                        )
                        .where(
                            PlayerCard.game_id == game.id,
                            PlayerCard.telegram_user_id == user_id,
                            PlayerCard.revealed_round == round_number,
                        )
                        .limit(1)
                    )
                ).one_or_none()
                if player_reveal is not None:
                    if player_reveal.reveal_method not in ("manual", "automatic"):
                        return ("unavailable", None, None)
                    return (
                        "already_revealed",
                        player_reveal.category,
                        player_reveal.reveal_method,
                    )

                result = await session.execute(
                    update(PlayerCard)
                    .where(
                        PlayerCard.game_id == game.id,
                        PlayerCard.telegram_user_id == user_id,
                        PlayerCard.category == category,
                        PlayerCard.revealed_at.is_(None),
                        PlayerCard.revealed_round.is_(None),
                        PlayerCard.reveal_method.is_(None),
                    )
                    .values(
                        revealed_at=datetime.now(timezone.utc),
                        revealed_round=round_number,
                        reveal_method=method,
                    )
                )
                if result.rowcount != 1:
                    return ("unavailable", None, None)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Karta ochilgan holatini DB’da saqlashda xato: "
            "chat_id=%s, user_id=%s, round=%s, method=%s, error_type=%s",
            lobby.chat_id,
            user_id,
            round_number,
            method,
            type(error).__name__,
        )
        return ("error", None, None)

    return ("updated", category, method)


def _record_reveal_in_lobby(
    lobby: "Lobby",
    user_id: int,
    category: str,
    round_number: int,
    method: Literal["manual", "automatic"],
) -> None:
    lobby.revealed.setdefault(user_id, set()).add(category)
    lobby.round_reveals.setdefault(round_number, set()).add(user_id)
    if method == "manual":
        lobby.round_manual_reveals.setdefault(round_number, set()).add(user_id)


async def _persist_active_phase(
    lobby: "Lobby",
    phase: str,
    current_round: int,
    duration_seconds: int | None,
) -> bool:
    now = datetime.now(timezone.utc)
    deadline = (
        now + timedelta(seconds=duration_seconds)
        if duration_seconds is not None
        else None
    )

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                result = await session.execute(
                    update(Game)
                    .where(
                        Game.chat_id == lobby.chat_id,
                        Game.status == "active",
                    )
                    .values(
                        phase=phase,
                        current_round=current_round,
                        phase_started_at=now,
                        phase_deadline_at=deadline,
                        version=Game.version + 1,
                    )
                )
                if result.rowcount != 1:
                    logger.error(
                        "O‘yin fazasini DB’da yangilab bo‘lmadi: "
                        "chat_id=%s, phase=%s, affected_rows=%s",
                        lobby.chat_id,
                        phase,
                        result.rowcount,
                    )
                    return False
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "O‘yin fazasini DB’da saqlashda xato: "
            "chat_id=%s, phase=%s, error_type=%s",
            lobby.chat_id,
            phase,
            type(error).__name__,
        )
        return False

    return True


async def _persist_round_start(lobby: "Lobby", round_number: int) -> bool:
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=REVEAL_DURATION_SECONDS)

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                game = await session.scalar(
                    select(Game)
                    .where(
                        Game.chat_id == lobby.chat_id,
                        Game.status == "active",
                    )
                    .with_for_update()
                )
                if game is None:
                    logger.error(
                        "Raund boshlash uchun faol DB o‘yini topilmadi: "
                        "chat_id=%s, round=%s",
                        lobby.chat_id,
                        round_number,
                    )
                    return False
                if round_number < game.current_round:
                    logger.warning(
                        "Eskirgan raundni boshlash rad etildi: "
                        "chat_id=%s, requested_round=%s, db_round=%s",
                        lobby.chat_id,
                        round_number,
                        game.current_round,
                    )
                    return False

                game_round = await session.scalar(
                    select(GameRound)
                    .where(
                        GameRound.game_id == game.id,
                        GameRound.round_number == round_number,
                    )
                    .with_for_update()
                )
                if game_round is not None:
                    if game_round.is_settled or game_round.status == "completed":
                        logger.warning(
                            "Yakunlangan raundni qayta boshlash rad etildi: "
                            "chat_id=%s, round=%s",
                            lobby.chat_id,
                            round_number,
                        )
                        return False
                    if game_round.status == "active":
                        return False
                    game_round.status = "active"
                    game_round.started_at = now
                    game_round.deadline_at = deadline
                else:
                    session.add(
                        GameRound(
                            game_id=game.id,
                            round_number=round_number,
                            status="active",
                            started_at=now,
                            deadline_at=deadline,
                        )
                    )

                game.phase = "reveal"
                game.current_round = round_number
                game.phase_started_at = now
                game.phase_deadline_at = deadline
                game.version += 1
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Raund boshlanishini DB’da saqlashda xato: "
            "chat_id=%s, round=%s, error_type=%s",
            lobby.chat_id,
            round_number,
            type(error).__name__,
        )
        return False

    return True


async def _persist_reveal_round_settlement(
    lobby: "Lobby",
    round_number: int,
) -> tuple[bool, list[tuple[int, str, int, str]]] | None:
    now = datetime.now(timezone.utc)

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                game = await session.scalar(
                    select(Game)
                    .where(
                        Game.chat_id == lobby.chat_id,
                        Game.status == "active",
                        Game.current_round == round_number,
                    )
                    .with_for_update()
                )
                if game is None:
                    logger.error(
                        "Raund yakunlash uchun mos faol o‘yin topilmadi: "
                        "chat_id=%s, round=%s",
                        lobby.chat_id,
                        round_number,
                    )
                    return None

                game_round = await session.scalar(
                    select(GameRound)
                    .where(
                        GameRound.game_id == game.id,
                        GameRound.round_number == round_number,
                    )
                    .with_for_update()
                )
                if game_round is None:
                    logger.error(
                        "DB’da yakunlanadigan raund topilmadi: "
                        "chat_id=%s, round=%s",
                        lobby.chat_id,
                        round_number,
                    )
                    return None

                newly_settled = not game_round.is_settled
                if newly_settled:
                    if game_round.status != "active":
                        logger.error(
                            "Raund holati yakunlashga mos emas: "
                            "chat_id=%s, round=%s, status=%s",
                            lobby.chat_id,
                            round_number,
                            game_round.status,
                        )
                        return None

                    active_players = list(
                        (
                            await session.scalars(
                                select(GamePlayer)
                                .where(
                                    GamePlayer.game_id == game.id,
                                    GamePlayer.status == "active",
                                )
                                .with_for_update()
                            )
                        ).all()
                    )
                    manually_revealed_ids = set(
                        (
                            await session.scalars(
                                select(PlayerCard.telegram_user_id)
                                .where(
                                    PlayerCard.game_id == game.id,
                                    PlayerCard.revealed_round == round_number,
                                    PlayerCard.reveal_method == "manual",
                                )
                                .distinct()
                            )
                        ).all()
                    )

                    for player in active_players:
                        if player.telegram_user_id in manually_revealed_ids:
                            player.missed_reveal_rounds = 0
                            continue

                        player.missed_reveal_rounds += 1
                        if player.missed_reveal_rounds >= 2:
                            player.status = "eliminated"

                    game_round.status = "completed"
                    game_round.is_settled = True
                    game_round.finished_at = now
                    game_round.settled_at = now
                    game.version += 1
                elif game_round.status != "completed":
                    logger.error(
                        "DB raundi settled bo‘lsa-da completed holatida emas: "
                        "chat_id=%s, round=%s, status=%s",
                        lobby.chat_id,
                        round_number,
                        game_round.status,
                    )
                    return None

                players = list(
                    (
                        await session.scalars(
                            select(GamePlayer)
                            .where(GamePlayer.game_id == game.id)
                            .order_by(GamePlayer.telegram_user_id)
                        )
                    ).all()
                )
                snapshot = [
                    (
                        player.telegram_user_id,
                        player.name,
                        player.missed_reveal_rounds,
                        player.status,
                    )
                    for player in players
                ]
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Raund natijalarini DB’da saqlashda xato: "
            "chat_id=%s, round=%s, error_type=%s",
            lobby.chat_id,
            round_number,
            type(error).__name__,
        )
        return None

    return newly_settled, snapshot


async def _persist_game_completion(
    lobby: "Lobby",
    first_winner_id: int | None,
    second_winner_id: int | None,
) -> dict[int, int] | None:
    finished_at = datetime.now(timezone.utc)
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                game = await session.scalar(
                    select(Game)
                    .where(Game.chat_id == lobby.chat_id, Game.status == "active")
                    .with_for_update()
                )
                if game is None:
                    logger.error(
                        "O‘yinni DB’da yakunlab bo‘lmadi: chat_id=%s, active_game_missing",
                        lobby.chat_id,
                    )
                    return None
                players = list((await session.scalars(
                    select(GamePlayer).where(GamePlayer.game_id == game.id)
                    .order_by(GamePlayer.telegram_user_id)
                )).all())
                try:
                    reward_amounts = calculate_game_reward_amounts(
                        {player.telegram_user_id for player in players},
                        first_winner_id,
                        second_winner_id,
                    )
                except ValueError:
                    logger.error("Game reward participants invalid: game_id=%s", game.id)
                    return None

                for player in players:
                    amount = reward_amounts[player.telegram_user_id]
                    await session.execute(
                        pg_insert(PlayerWallet)
                        .values(telegram_user_id=player.telegram_user_id)
                        .on_conflict_do_nothing(
                            constraint="uq_player_wallets_telegram_user_id"
                        )
                    )
                    wallet = await session.scalar(
                        select(PlayerWallet)
                        .where(PlayerWallet.telegram_user_id == player.telegram_user_id)
                        .with_for_update()
                    )
                    key = f"game-reward:{game.id}:{player.telegram_user_id}"
                    existing = await session.scalar(
                        select(WalletTransaction).where(
                            WalletTransaction.idempotency_key == key
                        )
                    )
                    if existing is None:
                        wallet.balance_coins += amount
                        session.add(WalletTransaction(
                            telegram_user_id=player.telegram_user_id,
                            direction="credit",
                            amount_coins=amount,
                            reason="game_reward",
                            note=f"Game {game.id} yakuniy mukofoti",
                            idempotency_key=key,
                        ))
                    elif (
                        existing.telegram_user_id != player.telegram_user_id
                        or existing.direction != "credit"
                        or existing.amount_coins != amount
                        or existing.reason != "game_reward"
                    ):
                        raise RuntimeError("Game reward idempotency conflict")
                game.status = "completed"
                game.phase = "finished"
                game.phase_started_at = finished_at
                game.phase_deadline_at = None
                game.finished_at = finished_at
                game.first_winner_id = first_winner_id
                game.second_winner_id = second_winner_id
                game.version += 1
                await session.flush()
                return reward_amounts
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "O‘yinni DB’da yakunlashda xato: chat_id=%s, error_type=%s",
            lobby.chat_id,
            type(error).__name__,
        )
        return None


@dataclass
class Lobby:
    chat_id: int
    host_id: int
    game_id: int | None = None
    bot_username: str | None = None

    players: dict[int, str] = field(default_factory=dict)
    usernames: dict[int, str | None] = field(default_factory=dict)
    message_id: int | None = None
    started: bool = False

    # O'yinchilarning barcha kartalari
    cards: dict[int, dict[str, str]] = field(default_factory=dict)

    # O'yinchilarning ochilgan kartalari
    revealed: dict[int, set[str]] = field(default_factory=dict)

    # Raund boshqaruvi
    current_round: int = 1
    phase: str = "lobby"

    # Raund raqami -> karta ochgan o'yinchilar ID'lari
    round_reveals: dict[int, set[int]] = field(default_factory=dict)
    round_manual_reveals: dict[int, set[int]] = field(default_factory=dict)
    missed_reveal_rounds: dict[int, int] = field(default_factory=dict)
    processed_reveal_rounds: set[int] = field(default_factory=set)
    votes: dict[int, int] = field(default_factory=dict)
    confirmation_votes: dict[int, str] = field(default_factory=dict)
    confirmation_eligible: set[int] = field(default_factory=set)
    confirmation_target: int | None = None
    confirmation_message_id: int | None = None
    finished: bool = False
    first_winner: int | None = None
    second_winner: int | None = None


# Hozircha vaqtinchalik xotira
lobbies: dict[int, Lobby] = {}


def parse_join_game_payload(payload: str | None) -> int | None:
    """Parse Telegram's short start payload without accepting arbitrary data."""
    if not payload or len(payload) > 64:
        return None
    match = re.fullmatch(r"join_([1-9][0-9]*)", payload)
    if match is None:
        return None
    game_id = int(match.group(1))
    return game_id if game_id <= 2**63 - 1 else None


def calculate_game_reward_amounts(
    participant_ids: set[int],
    first_winner_id: int | None,
    second_winner_id: int | None,
) -> dict[int, int]:
    winners = {
        user_id
        for user_id in (first_winner_id, second_winner_id)
        if user_id is not None
    }
    if not participant_ids or not winners or not winners.issubset(participant_ids):
        raise ValueError("Game reward participant set is invalid")
    return {
        user_id: (30 if user_id in winners else 15)
        for user_id in participant_ids
    }



def lobby_keyboard(lobby: Lobby):
    if lobby.game_id is None or not lobby.bot_username:
        raise ValueError("Lobby deep-link data is missing")
    return build_lobby_keyboard(lobby.bot_username, lobby.game_id, lobby.chat_id)


def lobby_text(lobby: Lobby) -> str:
    return texts.lobby_text(
        lobby.players.values(),
        len(lobby.players),
        MIN_PLAYERS,
        MAX_PLAYERS,
        lobby.players.get(lobby.host_id, "Noma’lum"),
    )


@router.message(Command("bunker", "game"))
@_serialize_lobby_creation
async def create_lobby(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            texts.error_text("Bu buyruqni Telegram guruhida ishlating."),
            parse_mode="HTML",
        )
        return

    chat_id = message.chat.id

    host = message.from_user
    if host is None:
        await message.answer(
            texts.error_text("Xonani yaratib bo‘lmadi."),
            parse_mode="HTML",
        )
        return

    try:
        bot_info = await message.bot.get_me()
    except TelegramAPIError as error:
        logger.warning(
            "Lobby yaratish uchun bot username olinmadi: error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            texts.error_text("O‘yin havolasini tayyorlab bo‘lmadi. Keyinroq urinib ko‘ring."),
            parse_mode="HTML",
        )
        return
    if not bot_info.username:
        await message.answer(
            texts.error_text("Bot username sozlanmagan, o‘yin havolasini yaratib bo‘lmadi."),
            parse_mode="HTML",
        )
        return

    game_id: int | None = None
    previous_message_id: int | None = None
    expected_message_id: int | None = None
    blocked = False
    game_started = False
    is_new_game = False
    lobby: Lobby | None = None
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                await lock_user_for_block_check(session, host.id)
                blocked = await is_user_blocked(session, host.id)
                if not blocked:
                    game = await session.scalar(
                        select(Game)
                        .where(
                            Game.chat_id == chat_id,
                            Game.status.in_(("lobby", "active")),
                        )
                        .with_for_update()
                        .limit(1)
                    )
                    if game is not None and game.status == "active":
                        game_started = True
                    elif game is not None:
                        game_id = game.id
                        previous_message_id = game.lobby_message_id
                        expected_message_id = game.lobby_message_id
                        player_rows = list(
                            (
                                await session.scalars(
                                    select(GamePlayer)
                                    .where(GamePlayer.game_id == game.id)
                                    .order_by(GamePlayer.id)
                                )
                            ).all()
                        )
                        current_lobby = lobbies.get(chat_id)
                        if (
                            current_lobby is not None
                            and current_lobby.game_id == game.id
                            and not current_lobby.started
                            and not current_lobby.finished
                        ):
                            lobby = current_lobby
                            lobby.message_id = game.lobby_message_id
                            lobby.players = {
                                player.telegram_user_id: player.name
                                for player in player_rows
                            }
                            lobby.usernames = {
                                player.telegram_user_id: player.username
                                for player in player_rows
                            }
                        else:
                            lobby = Lobby(
                                chat_id=chat_id,
                                host_id=game.host_telegram_id,
                                game_id=game.id,
                                bot_username=bot_info.username,
                                players={
                                    player.telegram_user_id: player.name
                                    for player in player_rows
                                },
                                usernames={
                                    player.telegram_user_id: player.username
                                    for player in player_rows
                                },
                                message_id=game.lobby_message_id,
                            )
                    else:
                        previous_message_id = await session.scalar(
                            select(Game.lobby_message_id)
                            .where(
                                Game.chat_id == chat_id,
                                Game.lobby_message_id.is_not(None),
                            )
                            .order_by(Game.id.desc())
                            .limit(1)
                        )
                        game = Game(
                            chat_id=chat_id,
                            host_telegram_id=host.id,
                            status="lobby",
                            phase="lobby",
                        )
                        session.add(game)
                        session.add(
                            GamePlayer(
                                game=game,
                                telegram_user_id=host.id,
                                name=host.full_name,
                                username=host.username,
                                status="active",
                            )
                        )
                        await session.flush()
                        game_id = game.id
                        is_new_game = True
                        expected_message_id = None
                        lobby = Lobby(
                            chat_id=chat_id,
                            host_id=host.id,
                            game_id=game.id,
                            bot_username=bot_info.username,
                            players={host.id: host.full_name},
                            usernames={host.id: host.username},
                        )
    except IntegrityError as error:
        if (
            _integrity_constraint_name(error)
            == "uq_games_one_open_game_per_chat"
        ):
            await message.answer(
                texts.error_text("Bu guruhda allaqachon faol o‘yin mavjud."),
                parse_mode="HTML",
            )
            return

        logger.error(
            "Lobby yaratishda DB integrity xatosi (%s)",
            type(error.orig).__name__,
        )
        await message.answer(
            texts.error_text("Xonani bazada saqlab bo‘lmadi. Qayta urinib ko‘ring."),
            parse_mode="HTML",
        )
        return
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Lobby yaratishda DB xatosi (%s)",
            type(error).__name__,
        )
        await message.answer(
            texts.error_text("Xonani bazada saqlab bo‘lmadi. Qayta urinib ko‘ring."),
            parse_mode="HTML",
        )
        return

    if blocked:
        await message.answer(
            texts.error_text("Sizga o‘yin yaratish cheklangan."),
            parse_mode="HTML",
        )
        return

    if game_started:
        await message.answer(
            texts.error_text("Bu guruhdagi o‘yin allaqachon boshlangan; yangi ro‘yxatdan o‘tish posti yuborilmadi."),
            parse_mode="HTML",
        )
        return

    if game_id is None:
        await message.answer(
            texts.error_text("Bu guruhda allaqachon faol o‘yin mavjud."),
            parse_mode="HTML",
        )
        return

    if lobby is None:
        await message.answer(
            texts.error_text("Lobby ma’lumotlarini tiklab bo‘lmadi. Admin bilan bog‘laning."),
            parse_mode="HTML",
        )
        return

    # Keep a recovered active lobby usable if publishing its replacement fails.
    if not is_new_game:
        lobbies[chat_id] = lobby

    sent = None
    try:
        sent = await message.answer(
            lobby_text(lobby),
            reply_markup=lobby_keyboard(lobby),
            parse_mode="HTML",
        )
        await sent.pin(disable_notification=True)
        async with get_sessionmaker()() as session:
            async with session.begin():
                result = await session.execute(
                    update(Game)
                    .where(
                        Game.id == game_id,
                        Game.status == "lobby",
                        (
                            Game.lobby_message_id.is_(None)
                            if expected_message_id is None
                            else Game.lobby_message_id == expected_message_id
                        ),
                    )
                    .values(lobby_message_id=sent.message_id)
                )
                if result.rowcount != 1:
                    raise RuntimeError("Lobby message changed during publication")
    except (TelegramAPIError, SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Lobby xabarini yuborish/pin qilishda xato: game_id=%s, error_type=%s",
            game_id,
            type(error).__name__,
        )
        cancel_status = "not_new_game"
        if is_new_game:
            cancel_status, _ = await cancel_open_game(game_id=game_id)
            if lobbies.get(chat_id) is lobby:
                lobbies.pop(chat_id, None)
        if sent is not None:
            try:
                if isinstance(error, (SQLAlchemyError, RuntimeError)):
                    await sent.unpin()
                await sent.delete()
            except TelegramAPIError as cleanup_error:
                logger.info(
                    "Yangi lobby xabarini tozalab bo‘lmadi: error_type=%s",
                    type(cleanup_error).__name__,
                )
        if cancel_status == "error":
            detail = (
                "O‘yin xabarini yuborib/pin qilib bo‘lmadi va bazadagi lobby "
                "holatini bekor qilish ham muvaffaqiyatsiz tugadi. Admin bilan bog‘laning."
            )
        elif isinstance(error, TelegramAPIError) and sent is not None:
            detail = (
                "O‘yin lobby xabarini pin qilib bo‘lmadi. Botga guruhda "
                "xabarlarni pin qilish huquqini bering va /game ni qayta yuboring."
            )
        elif isinstance(error, TelegramAPIError):
            detail = (
                "Bot guruhga lobby xabarini yubora olmadi. Botning guruhga xabar "
                "yuborish huquqini tekshirib, /game ni qayta yuboring."
            )
        else:
            detail = (
                "Yangi ro‘yxatdan o‘tish xabarini saqlab bo‘lmadi. Avvalgi lobby posti "
                "saqlandi; birozdan keyin /game ni qayta yuboring."
            )
        await message.answer(
            texts.error_text(detail),
            parse_mode="HTML",
        )
        return

    lobby.message_id = sent.message_id
    lobbies[chat_id] = lobby
    if previous_message_id is not None and previous_message_id != sent.message_id:
        try:
            await message.bot.delete_message(
                chat_id=chat_id,
                message_id=previous_message_id,
            )
        except TelegramBadRequest as error:
            description = getattr(error, "message", "").casefold()
            if "message to delete not found" in description:
                logger.info(
                    "Oldingi lobby xabari avvaldan o‘chirilgan: chat_id=%s",
                    chat_id,
                )
                return
            logger.info(
                "Oldingi lobby xabarini o‘chirib bo‘lmadi: chat_id=%s, error_type=%s",
                chat_id,
                type(error).__name__,
            )
            await message.answer(
                "✅ Yangi lobby xabari pin qilindi va saqlandi. Eski xabarni o‘chirish uchun "
                "botga guruhda xabarlarni o‘chirish huquqini bering.",
            )
        except TelegramAPIError as error:
            logger.info(
                "Oldingi lobby xabari o‘chmadi: chat_id=%s, error_type=%s",
                chat_id,
                type(error).__name__,
            )
            await message.answer(
                "✅ Yangi lobby xabari pin qilindi. Eski bot xabarini o‘chirish uchun "
                "botga guruhda xabarlarni o‘chirish huquqini bering.",
            )


async def join_lobby_by_game_id(game_id: int, user) -> tuple[str, Lobby | None]:
    """Join through the same locked, validated lobby path for buttons and deep links."""
    lobby = next(
        (candidate for candidate in lobbies.values() if candidate.game_id == game_id),
        None,
    )
    if lobby is None or lobby.started or lobby.finished:
        return "unavailable", lobby

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                await lock_user_for_block_check(session, user.id)
                if await is_user_blocked(session, user.id):
                    return "blocked", lobby

                game = await session.scalar(
                    select(Game)
                    .where(Game.id == game_id, Game.status == "lobby")
                    .with_for_update()
                )
                if game is None or game.chat_id != lobby.chat_id:
                    return "unavailable", lobby

                player_ids = set(
                    (
                        await session.scalars(
                            select(GamePlayer.telegram_user_id).where(
                                GamePlayer.game_id == game_id
                            )
                        )
                    ).all()
                )
                if user.id in player_ids:
                    return "already_joined", lobby
                if len(player_ids) >= MAX_PLAYERS:
                    return "full", lobby

                session.add(
                    GamePlayer(
                        game_id=game_id,
                        telegram_user_id=user.id,
                        name=user.full_name,
                        username=user.username,
                        status="active",
                    )
                )
                await session.flush()
    except IntegrityError as error:
        if _integrity_constraint_name(error) == "uq_game_players_game_user":
            return "already_joined", lobby
        logger.error(
            "Lobbyga qo‘shishda DB integrity xatosi: error_type=%s",
            type(error.orig).__name__,
        )
        return "error", lobby
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Lobbyga qo‘shishda DB xatosi: error_type=%s",
            type(error).__name__,
        )
        return "error", lobby

    lobby.players[user.id] = user.full_name
    lobby.usernames[user.id] = user.username
    return "joined", lobby


async def refresh_lobby_message(bot, lobby: Lobby) -> bool:
    if lobby.message_id is None:
        return False
    try:
        await bot.edit_message_text(
            chat_id=lobby.chat_id,
            message_id=lobby.message_id,
            text=lobby_text(lobby),
            reply_markup=lobby_keyboard(lobby),
            parse_mode="HTML",
        )
        return True
    except TelegramAPIError as error:
        logger.info(
            "Lobby xabarini yangilab bo‘lmadi: game_id=%s, error_type=%s",
            lobby.game_id,
            type(error).__name__,
        )
        return False


async def process_join_deep_link(message: Message, payload: str) -> None:
    game_id = parse_join_game_payload(payload)
    if game_id is None:
        await message.answer(
            "⚠️ O‘yin havolasi noto‘g‘ri yoki eskirgan. Guruhdagi yangi havoladan foydalaning."
        )
        return
    if message.chat.type != "private":
        await message.answer("O‘yinga qo‘shilish uchun bot bilan shaxsiy chatni oching.")
        return
    if message.from_user is None:
        await message.answer("Foydalanuvchi aniqlanmadi. Botga shaxsiy chatdan /start yuboring.")
        return

    status, lobby = await join_lobby_by_game_id(game_id, message.from_user)
    if status == "joined" and lobby is not None:
        await refresh_lobby_message(message.bot, lobby)
        await message.answer("✅ Guruh o‘yiniga qo‘shildingiz! Lobby tafsilotlari guruhda.")
    elif status == "already_joined":
        await message.answer("ℹ️ Siz bu o‘yinga allaqachon qo‘shilgansiz.")
    elif status == "full":
        await message.answer("🚫 Bu lobby to‘lib bo‘lgan.")
    elif status == "blocked":
        await message.answer("🚫 Sizga o‘yinga qo‘shilish cheklangan.")
    elif status == "unavailable":
        await message.answer("⚠️ Bu o‘yin topilmadi yoki ro‘yxatdan o‘tish yopilgan.")
    else:
        await message.answer("⚠️ O‘yinga hozir qo‘shib bo‘lmadi. Birozdan keyin urinib ko‘ring.")


@router.callback_query(F.data.startswith("bunker:join:"))
async def join_lobby(callback: CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    lobby = lobbies.get(chat_id)
    if lobby is None or lobby.game_id is None:
        await callback.answer("⚠️ Bu xona faol emas.", show_alert=True)
        return

    status, lobby = await join_lobby_by_game_id(lobby.game_id, callback.from_user)
    if status == "joined" and lobby is not None:
        await refresh_lobby_message(callback.bot, lobby)
        await callback.answer("✅ Xonaga qo‘shildingiz!")
    elif status == "already_joined":
        await callback.answer("ℹ️ Siz allaqachon xonadasiz.")
    elif status == "full":
        await callback.answer("🚫 Xona to‘lib bo‘lgan.", show_alert=True)
    elif status == "blocked":
        await callback.answer("🚫 Sizga o‘yinga qo‘shilish cheklangan.", show_alert=True)
    elif status == "unavailable":
        await callback.answer("⚠️ Bu xona faol emas yoki ro‘yxatdan o‘tish yopilgan.", show_alert=True)
    else:
        await callback.answer("⚠️ Bazaga qo‘shilmadi. Birozdan keyin qayta urinib ko‘ring.", show_alert=True)


async def cancel_open_game(
    *,
    game_id: int | None = None,
    chat_id: int | None = None,
) -> tuple[str, int | None]:
    if (game_id is None) == (chat_id is None):
        raise ValueError("Aynan game_id yoki chat_id berilishi kerak.")

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                statement = select(Game).where(
                    Game.status.in_(("lobby", "active"))
                )
                if game_id is not None:
                    statement = statement.where(Game.id == game_id)
                else:
                    statement = statement.where(Game.chat_id == chat_id)
                game = await session.scalar(statement.with_for_update())
                if game is None:
                    exists_statement = select(Game.id).where(
                        Game.status.in_(("lobby", "active"))
                    )
                    if game_id is not None:
                        exists_statement = exists_statement.where(
                            Game.id == game_id
                        )
                    else:
                        exists_statement = exists_statement.where(
                            Game.chat_id == chat_id
                        )
                    open_game_id = await session.scalar(exists_statement)
                    if open_game_id is None:
                        return "not_open", None
                    logger.error(
                        "O‘yinni bekor qilish uchun ochiq DB yozuvi topilmadi: "
                        "game_id=%s, chat_id=%s",
                        game_id,
                        chat_id,
                    )
                    return "not_open", None

                now = datetime.now(timezone.utc)
                game.status = "cancelled"
                game.phase = "cancelled"
                game.phase_started_at = now
                game.phase_deadline_at = None
                game.finished_at = now
                game.version += 1
                cancelled_chat_id = game.chat_id
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "O‘yinni DB’da bekor qilishda xato: game_id=%s, "
            "chat_id=%s, error_type=%s",
            game_id,
            chat_id,
            type(error).__name__,
        )
        return "error", None

    lobby = lobbies.get(cancelled_chat_id)
    if lobby is not None:
        lobby.finished = True
        lobby.phase = "cancelled"

    return "cancelled", cancelled_chat_id


@router.message(Command("bunkercancel"))
async def cancel_lobby(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            texts.error_text("Bu buyruqni Telegram guruhida ishlating."),
            parse_mode="HTML",
        )
        return

    lobby = lobbies.get(message.chat.id)
    if lobby is None or lobby.finished:
        await message.answer(
            texts.error_text("Bu guruhda bekor qilinadigan o‘yin yo‘q."),
            parse_mode="HTML",
        )
        return

    if message.from_user is None or message.from_user.id != lobby.host_id:
        await message.answer(
            texts.error_text("O‘yinni faqat tashkilotchi bekor qilishi mumkin."),
            parse_mode="HTML",
        )
        return

    status, _ = await cancel_open_game(chat_id=lobby.chat_id)
    if status == "cancelled":
        await message.answer("🚫 O‘yin tashkilotchi tomonidan bekor qilindi.")
    elif status == "not_open":
        await message.answer(
            texts.error_text(
                "O‘yin allaqachon yakunlangan yoki bekor qilingan."
            ),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            texts.error_text(
                "O‘yinni bazada bekor qilib bo‘lmadi. Qayta urinib ko‘ring."
            ),
            parse_mode="HTML",
        )


async def is_current_group_admin(bot, chat_id: int, user_id: int) -> bool | None:
    """Return None if Telegram could not verify, otherwise current admin status."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramAPIError as error:
        logger.warning(
            "Guruh adminligini tekshirib bo‘lmadi: chat_id=%s, error_type=%s",
            chat_id,
            type(error).__name__,
        )
        return None
    return member.status in {"creator", "administrator"}


@router.callback_query(F.data.startswith("bunker:start:"))
async def start_lobby(callback: CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    lobby = lobbies.get(chat_id)

    if lobby is None or lobby.started or lobby.finished:
        await callback.answer("⚠️ Bu xona faol emas.", show_alert=True)
        return

    if not isinstance(callback.message, Message) or callback.message.chat.id != chat_id:
        await callback.answer("⚠️ Bu tugma tegishli guruh xabari emas.", show_alert=True)
        return

    is_admin = await is_current_group_admin(
        callback.bot,
        chat_id,
        callback.from_user.id,
    )
    if is_admin is None:
        await callback.answer(
            "⚠️ Guruhdagi admin huquqini tekshirib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
        return
    if not is_admin:
        await callback.answer(
            "⛔ O‘yinni faqat guruh egasi yoki administratori boshlashi mumkin.",
            show_alert=True,
        )
        return

    if len(lobby.players) < MIN_PLAYERS:
        await callback.answer(
            f"⚠️ O‘yinni boshlash uchun kamida {MIN_PLAYERS} o‘yinchi kerak.",
            show_alert=True,
        )
        return

    phase_started_at = datetime.now(timezone.utc)
    dealt_cards: dict[int, dict[str, str]] = {}
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                await lock_user_for_block_check(session, callback.from_user.id)
                if await is_user_blocked(session, callback.from_user.id):
                    await callback.answer(
                        "🚫 Siz bloklangansiz va o‘yinni boshlay olmaysiz.",
                        show_alert=True,
                    )
                    return

                game = await session.scalar(
                    select(Game)
                    .where(
                        Game.chat_id == chat_id,
                        Game.status == "lobby",
                    )
                    .with_for_update()
                )
                if game is None:
                    await callback.answer(
                        "⚠️ O‘yin bazada faol lobby sifatida topilmadi. "
                        "Yangi xona yarating.",
                        show_alert=True,
                    )
                    return

                player_ids = set(
                    (
                        await session.scalars(
                            select(GamePlayer.telegram_user_id)
                            .where(GamePlayer.game_id == game.id)
                            .with_for_update()
                        )
                    ).all()
                )
                if player_ids != set(lobby.players):
                    await callback.answer(
                        "⚠️ O‘yinchilar bazadagi lobby bilan mos kelmadi. "
                        "Xonani tekshirib, qayta urinib ko‘ring.",
                        show_alert=True,
                    )
                    return

                existing_card_id = await session.scalar(
                    select(PlayerCard.id)
                    .where(PlayerCard.game_id == game.id)
                    .limit(1)
                )
                if existing_card_id is not None:
                    await callback.answer(
                        "⚠️ Bu o‘yin uchun kartalar allaqachon yaratilgan.",
                        show_alert=True,
                    )
                    return

                card_rows = []
                for user_id in lobby.players:
                    cards = deal_cards()
                    dealt_cards[user_id] = cards
                    card_rows.extend(
                        PlayerCard(
                            game_id=game.id,
                            telegram_user_id=user_id,
                            category=category,
                            value=value,
                        )
                        for category, value in cards.items()
                    )

                game.status = "active"
                game.phase = lobby.phase
                game.current_round = lobby.current_round
                game.phase_started_at = phase_started_at
                game.phase_deadline_at = (
                    phase_started_at
                    + timedelta(seconds=INITIAL_ROUND_DELAY_SECONDS)
                )
                game.version += 1
                session.add_all(card_rows)
                await session.flush()
    except IntegrityError as error:
        logger.error(
            "O‘yinni boshlash/kartalarni saqlashda DB integrity xatosi: "
            "chat_id=%s, error_type=%s",
            chat_id,
            type(error.orig).__name__,
        )
        await callback.answer(
            "⚠️ O‘yinni boshlash yoki kartalarni bazada saqlash imkoni "
            "bo‘lmadi. Qayta urinib ko‘ring.",
            show_alert=True,
        )
        return
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "O‘yinni boshlash/kartalarni saqlashda DB xatosi: "
            "chat_id=%s, error_type=%s",
            chat_id,
            type(error).__name__,
        )
        await callback.answer(
            "⚠️ O‘yinni boshlash yoki kartalarni bazada saqlash imkoni "
            "bo‘lmadi. Qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    lobby.started = True

    await callback.message.edit_text(
        texts.game_started_text(len(lobby.players)),
        parse_mode="HTML",
    )

    sent_count = 0
    failed_count = 0


    for user_id in lobby.players:
        cards = dealt_cards[user_id]
        lobby.cards[user_id] = cards
        lobby.revealed[user_id] = set()

        try:
            await callback.bot.send_message(
                chat_id=user_id,
                text=format_private_cards(cards),
                parse_mode="HTML",
            )
            sent_count += 1
        except Exception:
            failed_count += 1



    await callback.message.answer(
        texts.cards_dealt_text(sent_count, failed_count),
        parse_mode="HTML",
    )

    await callback.answer("O‘yin boshlandi!")

    # 15 soniyadan keyin birinchi raund boshlanadi
    asyncio.create_task(
        start_first_round(lobby.chat_id, callback.bot)
    )



@router.message(Command("mycards"))
async def show_my_cards(message: Message):
    user_id = message.from_user.id if message.from_user else None

    if user_id is None:
        return

    lobby = next(
        (
            item for item in lobbies.values()
            if item.started and user_id in item.players
        ),
        None,
    )

    if lobby is None:
        await message.answer(
            texts.error_text("Faol o‘yiningiz topilmadi."),
            parse_mode="HTML",
        )
        return

    cards = lobby.cards.get(user_id, {})
    revealed = lobby.revealed.get(user_id, set())
    keyboard = card_reveal_keyboard(lobby.chat_id, cards, revealed)

    if keyboard is None:
        await message.answer(texts.all_cards_revealed_text(), parse_mode="HTML")
        return

    await message.answer(
        texts.unrevealed_cards_text(),
        reply_markup=keyboard,
        parse_mode="HTML",
    )




async def send_card_announcement(
    bot,
    chat_id: int,
    player_name: str,
    category: str,
    card_value: str,
    *,
    username: str | None = None,
    automatic: bool = False,
) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=texts.card_announcement_text(
            player_name,
            category,
            card_value,
            automatic=automatic,
            username=username,
        ),
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.callback_query(F.data.startswith("bunker:reveal:"))
async def reveal_card(callback: CallbackQuery):
    parts = callback.data.split(":", 3)
    chat_id = int(parts[2])
    category = parts[3]

    lobby = lobbies.get(chat_id)
    user_id = callback.from_user.id

    if lobby is None or not lobby.started:
        await callback.answer("⚠️ O‘yin faol emas.", show_alert=True)
        return

    if user_id not in lobby.players:
        await callback.answer(
            "⛔ Siz bu o‘yinning ishtirokchisi emassiz.",
            show_alert=True,
        )
        return

    # Faqat karta ochish bosqichida ruxsat beriladi
    if getattr(lobby, "phase", "reveal") != "reveal":
        await callback.answer(
            "⏳ Hozir karta ochish vaqti emas.",
            show_alert=True,
        )
        return

    cards = lobby.cards.get(user_id, {})
    revealed = lobby.revealed.get(user_id, set())

    if category not in cards:
        await callback.answer("⚠️ Karta topilmadi.", show_alert=True)
        return

    if category in revealed:
        await callback.answer(
            "ℹ️ Bu karta allaqachon ochilgan.",
            show_alert=True,
        )
        return

    round_number = lobby.current_round
    opened_players = lobby.round_reveals.get(round_number, set())

    if user_id in opened_players:
        await callback.answer(
            "ℹ️ Bu raundda allaqachon karta ochgansiz.",
            show_alert=True,
        )
        return

    active_players = {
        player_id
        for player_id in lobby.players
        if get_unrevealed_keyboard(lobby, player_id) is not None
    }

    if not active_players:
        await callback.answer(
            "ℹ️ Bu raundda ochiladigan kartalar qolmadi.",
            show_alert=True,
        )
        return

    reveal_status, persisted_category, persisted_method = await _persist_card_reveal(
        lobby,
        user_id,
        category,
        round_number,
        "manual",
    )
    if reveal_status == "error":
        await callback.answer(
            "⚠️ Kartani bazada saqlab bo‘lmadi. Qayta urinib ko‘ring.",
            show_alert=True,
        )
        return
    if reveal_status == "unavailable":
        await callback.answer(
            "ℹ️ Bu karta yoki raund endi ochish uchun mavjud emas.",
            show_alert=True,
        )
        return
    if reveal_status == "already_revealed":
        if persisted_category is not None and persisted_method is not None:
            _record_reveal_in_lobby(
                lobby,
                user_id,
                persisted_category,
                round_number,
                persisted_method,
            )
        await callback.answer(
            "ℹ️ Bu raundda allaqachon karta ochgansiz.",
            show_alert=True,
        )
        return

    if persisted_category is None or persisted_method is None:
        logger.error(
            "DB’dan karta ochish natijasi to‘liq qaytmadi: "
            "chat_id=%s, user_id=%s, round=%s",
            chat_id,
            user_id,
            round_number,
        )
        await callback.answer(
            "⚠️ Karta holatini tekshirib bo‘lmadi. Qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    _record_reveal_in_lobby(
        lobby,
        user_id,
        persisted_category,
        round_number,
        persisted_method,
    )

    player_name = lobby.players[user_id]
    card_value = cards[category]

    await callback.message.edit_text(
        texts.card_revealed_text(round_number, category, card_value),
        parse_mode="HTML",
    )

    await send_card_announcement(
        callback.bot,
        chat_id,
        player_name,
        category,
        card_value,
        username=lobby.usernames.get(user_id),
    )

    await callback.answer("✅ Karta ochildi!")

    if active_players.issubset(opened_players):
        await start_discussion(
            chat_id,
            callback.bot,
            round_number,
        )

    # if active_players.issubset(opened_players):
    #     lobby.phase = "discussion"

    #     await callback.bot.send_message(
    #         chat_id=chat_id,
    #         text=(
    #             f"🗣️ {round_number}-RAUND: MUHOKAMA BOSHLANDI!\n\n"
    #             "Sizda 3 daqiqa bor. "
    #             "Kim bunkerda qolishga loyiq ekanini muhokama qiling."
    #         ),
    #     )

    #     asyncio.create_task(
    #         discussion_timer(chat_id, callback.bot)
    #     )





async def notify_first_card(chat_id: int, bot):
    await asyncio.sleep(INITIAL_ROUND_DELAY_SECONDS)

    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        return

    for user_id in lobby.players:
        if user_id not in lobby.cards:
            continue

        if lobby.revealed.get(user_id):
            continue

        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "⏰ <b>15 SONIYA TUGADI</b>\n\n"
                    "🎴 Endi birinchi kartangizni oching.\n"
                    "Kartalarni ko‘rish va ochish uchun <code>/mycards</code> "
                    "buyrug‘ini yuboring."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


def get_unrevealed_keyboard(lobby: Lobby, user_id: int):
    cards = lobby.cards.get(user_id, {})
    revealed = lobby.revealed.get(user_id, set())
    return card_reveal_keyboard(lobby.chat_id, cards, revealed)



async def prompt_players_to_reveal(lobby: Lobby, bot, round_number: int):
    for user_id in list(lobby.players):
        if user_id not in lobby.cards:
            continue

        if not lobby.players or user_id not in lobby.players:
            continue

        keyboard = get_unrevealed_keyboard(lobby, user_id)

        if keyboard is None:
            await bot.send_message(
                chat_id=user_id,
                text=texts.all_cards_revealed_text(),
                parse_mode="HTML",
            )
            continue

        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"{texts.card_prompt_text(round_number)}\n"
                    "Tanlangan karta guruhga e’lon qilinadi."
                ),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            pass


async def start_next_round(chat_id: int, bot):
    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        return

    lobby.current_round += 1

    for user_id in lobby.players:
        if user_id not in lobby.cards:
            continue

        if len(lobby.revealed.get(user_id, set())) == len(
            lobby.cards[user_id]
        ):
            continue

        keyboard = get_unrevealed_keyboard(lobby, user_id)

        if keyboard is None:
            continue

        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    texts.card_prompt_text(lobby.current_round)
                ),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            pass


async def start_round(chat_id: int, bot):
    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        return

    if lobby.phase in {"reveal", "discussion", "voting", "confirm_elimination"}:
        return

    if not await _persist_round_start(lobby, lobby.current_round):
        return

    lobby.phase = "reveal"
    lobby.round_reveals.setdefault(lobby.current_round, set())

    await bot.send_message(
        chat_id,
        texts.round_started_text(
            lobby.current_round,
            (
                (user_id, player_name, lobby.usernames.get(user_id))
                for user_id, player_name in lobby.players.items()
            ),
        ),
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )

    eligible_players = {
        user_id
        for user_id in list(lobby.players)
        if user_id in lobby.cards and get_unrevealed_keyboard(lobby, user_id) is not None
    }

    if not eligible_players:
        await start_discussion(chat_id, bot, lobby.current_round)
        return

    for user_id in list(lobby.players):
        if user_id not in eligible_players:
            continue

        keyboard = get_unrevealed_keyboard(lobby, user_id)
        if keyboard is None:
            continue

        try:
            await bot.send_message(
                user_id,
                texts.card_prompt_text(lobby.current_round),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            pass

    asyncio.create_task(reveal_timer(chat_id, bot, lobby.current_round))




async def start_first_round(chat_id: int, bot):
    await asyncio.sleep(INITIAL_ROUND_DELAY_SECONDS)

    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        return

    await start_round(chat_id, bot)


@router.callback_query(F.data.startswith("bunker:vote:"))
async def vote_for_player(callback: CallbackQuery):
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    voter_id = int(parts[3])
    target_id = int(parts[4])

    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished or lobby.phase != "voting":
        await callback.answer("⏰ Ovoz berish tugagan.", show_alert=True)
        return

    if callback.from_user.id != voter_id:
        await callback.answer(
            "⛔ Bu tugma boshqa o‘yinchi uchun.",
            show_alert=True,
        )
        return

    if voter_id not in lobby.players or target_id not in lobby.players:
        await callback.answer("⚠️ O‘yinchi topilmadi.", show_alert=True)
        return

    if voter_id == target_id:
        await callback.answer("⛔ O‘zingizga ovoz bera olmaysiz.", show_alert=True)
        return

    if voter_id in lobby.votes:
        await callback.answer("ℹ️ Siz allaqachon ovoz bergansiz.", show_alert=True)
        return

    lobby.votes[voter_id] = target_id

    voter_name = lobby.players[voter_id]
    target_name = lobby.players[target_id]

    await callback.answer("✅ Ovozingiz qabul qilindi!")

    await callback.bot.send_message(
        chat_id=chat_id,
        text=texts.vote_announcement_text(voter_name, target_name),
        parse_mode="HTML",
    )

    try:
        await callback.message.edit_text(
            f"✅ <b>Ovozingiz qabul qilindi.</b>\n"
            f"{escape(target_name)} ni tanladingiz.",
            parse_mode="HTML",
        )
    except Exception:
        pass



async def voting_timer(chat_id: int, bot):
    await asyncio.sleep(VOTING_DURATION_SECONDS)

    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished or lobby.phase != "voting":
        return

    if not lobby.votes:
        if not await _persist_active_phase(
            lobby,
            "round_end",
            lobby.current_round + 1,
            ROUND_TRANSITION_DELAY_SECONDS,
        ):
            return
        lobby.current_round += 1
        lobby.phase = "round_end"
        await bot.send_message(
            chat_id=chat_id,
            text=texts.no_votes_text(),
            parse_mode="HTML",
        )
        asyncio.get_running_loop().call_later(
            ROUND_TRANSITION_DELAY_SECONDS,
            lambda: asyncio.create_task(start_round(chat_id, bot)),
        )
        return

    counts = {}

    for target_id in lobby.votes.values():
        counts[target_id] = counts.get(target_id, 0) + 1

    max_votes = max(counts.values())
    winners = [
        target_id
        for target_id, count in counts.items()
        if count == max_votes
    ]

    await bot.send_message(
        chat_id=chat_id,
        text=texts.voting_finished_text(),
        parse_mode="HTML",
    )

    if len(winners) != 1:
        if not await _persist_active_phase(
            lobby,
            "round_end",
            lobby.current_round + 1,
            ROUND_TRANSITION_DELAY_SECONDS,
        ):
            return
        lobby.current_round += 1
        lobby.phase = "round_end"
        await bot.send_message(
            chat_id=chat_id,
            text=texts.tied_vote_text(),
            parse_mode="HTML",
        )
        asyncio.get_running_loop().call_later(
            ROUND_TRANSITION_DELAY_SECONDS,
            lambda: asyncio.create_task(start_round(chat_id, bot)),
        )
        return

    target_id = winners[0]

    await bot.send_message(
        chat_id=chat_id,
        text=texts.vote_leader_text(
            lobby.players.get(target_id, str(target_id)),
            max_votes,
        ),
        parse_mode="HTML",
    )

    await start_elimination_confirmation(
        chat_id,
        bot,
        target_id,
    )


async def start_voting(chat_id: int, bot):
    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        return

    # Faqat faol o'yinchilar ovoz bera oladi
    players = dict(lobby.players)

    if len(players) == 2:
        if not await finish_game_with_two_winners(lobby, bot):
            lobby.phase = "completion_error"
        return

    if lobby.phase == "voting":
        print("[VOTE] Ovoz berish allaqachon boshlangan", flush=True)
        return

    if len(players) < 2:
        await bot.send_message(
            chat_id=chat_id,
            text=texts.error_text("Ovoz berish uchun o‘yinchilar yetarli emas."),
            parse_mode="HTML",
        )
        return

    if not await _persist_active_phase(
        lobby,
        "voting",
        lobby.current_round,
        VOTING_DURATION_SECONDS,
    ):
        return

    lobby.phase = "voting"
    lobby.votes = {}

    await bot.send_message(
        chat_id=chat_id,
        text=texts.voting_started_text(len(players)),
        parse_mode="HTML",
    )

    for user_id, player_name in players.items():
        keyboard = voting_keyboard(chat_id, user_id, players)

        try:
            await bot.send_message(
                chat_id=user_id,
                text=texts.vote_prompt_text(),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[VOTE] {player_name} ga xabar yuborilmadi: {e}")

    asyncio.create_task(voting_timer(chat_id, bot))



async def discussion_timer(chat_id: int, bot):
    print(f"[TIMER] Ishga tushdi: {chat_id}", flush=True)

    await asyncio.sleep(DISCUSSION_DURATION_SECONDS)

    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        print("[TIMER] O'yin faol emas yoki xona topilmadi", flush=True)
        return

    if lobby.phase != "discussion":
        print(f"[TIMER] Phase mos emas: {lobby.phase}", flush=True)
        return

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=texts.discussion_finished_text(),
            parse_mode="HTML",
        )

        await start_voting(chat_id, bot)

    except Exception:
        raise


async def finish_game_with_two_winners(
    lobby: Lobby,
    bot,
    result_text: str | None = None,
) -> bool:
    if lobby.finished or len(lobby.players) != 2:
        return False

    winner_ids = list(lobby.players)
    rewards = await _persist_game_completion(
        lobby,
        winner_ids[0],
        winner_ids[1],
    )
    if rewards is None:
        lobby.phase = "completion_error"
        return False

    lobby.first_winner, lobby.second_winner = winner_ids
    lobby.finished = True
    lobby.phase = "finished"

    announcement = texts.winners_text(
        (
            player_id,
            lobby.players[player_id],
            lobby.usernames.get(player_id),
        )
        for player_id in winner_ids
    )
    if result_text:
        announcement = f"{result_text}\n\n{announcement}"
    announcement += "\n\n🪙 <b>O‘yin mukofotlari</b>\n" + "\n".join(
        f"{escape(lobby.players.get(user_id, str(user_id)))} — <b>+{amount} coin</b>"
        for user_id, amount in rewards.items()
    )

    await bot.send_message(
        lobby.chat_id,
        announcement,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    return True


async def settle_reveal_round(
    lobby: Lobby,
    bot,
    round_number: int,
) -> bool | None:
    if round_number in lobby.processed_reveal_rounds:
        return lobby.finished

    settlement = await _persist_reveal_round_settlement(lobby, round_number)
    if settlement is None:
        return None

    newly_settled, player_snapshot = settlement
    lobby.processed_reveal_rounds.add(round_number)
    eliminated_names = []
    for user_id, player_name, missed_rounds, status in player_snapshot:
        if status == "eliminated":
            if newly_settled:
                eliminated_names.append(lobby.players.get(user_id, player_name))
            lobby.players.pop(user_id, None)
            lobby.usernames.pop(user_id, None)
            lobby.cards.pop(user_id, None)
            lobby.revealed.pop(user_id, None)
            lobby.missed_reveal_rounds.pop(user_id, None)

            lobby.votes = {
                voter_id: target_id
                for voter_id, target_id in lobby.votes.items()
                if voter_id != user_id and target_id != user_id
            }
            lobby.confirmation_votes.pop(user_id, None)
            lobby.confirmation_eligible.discard(user_id)
            for players_who_revealed in lobby.round_reveals.values():
                players_who_revealed.discard(user_id)
            for players_who_revealed in lobby.round_manual_reveals.values():
                players_who_revealed.discard(user_id)
            continue

        lobby.missed_reveal_rounds[user_id] = missed_rounds

    if newly_settled and eliminated_names:
        await bot.send_message(
            lobby.chat_id,
            texts.inactive_players_removed_text(eliminated_names),
            parse_mode="HTML",
        )

    if newly_settled and eliminated_names and len(lobby.players) <= 2:
        if len(lobby.players) < 2:
            return False
        if not await finish_game_with_two_winners(lobby, bot):
            lobby.phase = "completion_error"
            return True

    return lobby.finished or lobby.phase == "completion_error"


async def start_discussion(chat_id: int, bot, round_number: int):
    print("[DEBUG] discussion_timer funksiyasiga kirdi!", flush=True)
    lobby = lobbies.get(chat_id)

    if lobby is None or lobby.finished:
        return

    # Faqat karta ochish bosqichidan muhokamaga o'tish mumkin
    if lobby.phase != "reveal":
        return

    if lobby.current_round != round_number:
        return

    settlement_result = await settle_reveal_round(lobby, bot, round_number)
    if settlement_result is None or settlement_result:
        return

    if not await _persist_active_phase(
        lobby,
        "discussion",
        round_number,
        DISCUSSION_DURATION_SECONDS,
    ):
        return

    lobby.phase = "discussion"

    await bot.send_message(
        chat_id=chat_id,
        text=texts.discussion_started_text(round_number),
        parse_mode="HTML",
    )
    print("[DEBUG] Muhokama taymeri ishga tushirilmoqda!", flush=True)
    asyncio.create_task(discussion_timer(chat_id, bot))


async def reveal_timer(chat_id: int, bot, round_number: int):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + REVEAL_DURATION_SECONDS
    await asyncio.sleep(REVEAL_DURATION_SECONDS - 1)

    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        return

    if lobby.phase != "reveal":
        return

    if lobby.current_round != round_number:
        return

    opened_players = lobby.round_reveals.get(round_number, set())
    for user_id, player_name in list(lobby.players.items()):
        if lobby.phase != "reveal" or lobby.current_round != round_number:
            return

        if user_id in opened_players:
            continue

        cards = lobby.cards.get(user_id, {})
        revealed = lobby.revealed.get(user_id, set())
        unrevealed_categories = [
            category
            for category in cards
            if category not in revealed
        ]
        if not unrevealed_categories:
            continue

        category = random.choice(unrevealed_categories)
        card_value = cards[category]
        reveal_status, persisted_category, persisted_method = await _persist_card_reveal(
            lobby,
            user_id,
            category,
            round_number,
            "automatic",
        )
        if reveal_status == "already_revealed":
            if persisted_category is not None and persisted_method is not None:
                _record_reveal_in_lobby(
                    lobby,
                    user_id,
                    persisted_category,
                    round_number,
                    persisted_method,
                )
            continue
        if reveal_status != "updated":
            if reveal_status == "error":
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            "⚠️ Avtomatik kartani bazada saqlab bo‘lmadi. "
                            "Bu karta ochilmadi."
                        ),
                    )
                except Exception:
                    logger.warning(
                        "Avtomatik karta saqlanmadi va foydalanuvchiga "
                        "bildirishnoma yuborilmadi: chat_id=%s, user_id=%s",
                        chat_id,
                        user_id,
                    )
            continue

        _record_reveal_in_lobby(
            lobby,
            user_id,
            category,
            round_number,
            "automatic",
        )

        try:
            await bot.send_message(
                chat_id=user_id,
                text=texts.automatic_card_text(category, card_value),
                parse_mode="HTML",
            )
        except Exception as error:
            print(f"[REVEAL] {player_name} ga avtomatik karta yuborilmadi: {error}")

        await send_card_announcement(
            bot,
            chat_id,
            player_name,
            category,
            card_value,
            automatic=True,
            username=lobby.usernames.get(user_id),
        )

    await asyncio.sleep(max(0, deadline - loop.time()))

    lobby = lobbies.get(chat_id)
    if lobby is None or not lobby.started or lobby.finished:
        return

    if lobby.phase != "reveal" or lobby.current_round != round_number:
        return

    await start_discussion(chat_id, bot, round_number)



@router.message(F.chat.type.in_({"group", "supergroup"}))
async def block_chat_after_discussion(message: Message):
    # Bot xabarlarini tekshirmaymiz
    if message.from_user is None or message.from_user.is_bot:
        raise SkipHandler

    # Faqat o'yin ovoz berish bosqichiga o'tganda
    lobby = lobbies.get(message.chat.id)

    if lobby is None or not lobby.started or lobby.phase != "voting":
        raise SkipHandler

    user_id = message.from_user.id
    text = message.text or message.caption or ""

    # Foydalanuvchi admin yoki guruh egasimi?
    try:
        member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
        )
    except Exception as e:
        print(f"Adminni tekshirishda xato: {e}")
        raise SkipHandler

    is_admin = member.status in {"administrator", "creator"}

    # Faqat admin va guruh egasi nuqta bilan yozsa,
    # xabarni o'chirmaymiz
    if is_admin and text.startswith("."):
        return

    # Boshqa barcha xabarlar o'chiriladi
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    # Admin va guruh egasiga mute berilmaydi
    if is_admin:
        return

    # Oddiy o'yinchini 1 daqiqaga mute qilamiz
    try:
        await message.bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=False,
            ),
            until_date=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
    except TelegramBadRequest:
        pass


@router.message(F.migrate_to_chat_id)
async def handle_group_migration(message: Message):
    old_chat_id = message.chat.id
    new_chat_id = message.migrate_to_chat_id

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                result = await session.execute(
                    update(Game)
                    .where(Game.chat_id == old_chat_id)
                    .values(
                        chat_id=new_chat_id,
                        version=Game.version + 1,
                    )
                )
                if result.rowcount == 0:
                    logger.warning(
                        "Guruh migratsiyasi uchun DB yozuvi topilmadi: "
                        "old_chat_id=%s, new_chat_id=%s",
                        old_chat_id,
                        new_chat_id,
                    )
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Guruh chat_id qiymatini DB’da yangilashda xato: "
            "old_chat_id=%s, new_chat_id=%s, error_type=%s",
            old_chat_id,
            new_chat_id,
            type(error).__name__,
        )

    # Eski guruh uchun faol xona bormi?
    lobby = lobbies.pop(old_chat_id, None)

    if lobby is None:
        print(
            f"Guruh ID o'zgardi: {old_chat_id} -> {new_chat_id}. "
            "Faol xona topilmadi."
        )
        return

    # Xona ID sini yangilaymiz
    lobby.chat_id = new_chat_id
    lobbies[new_chat_id] = lobby

    print(
        f"Lobby ID yangilandi: {old_chat_id} -> {new_chat_id}"
    )



async def start_elimination_confirmation(
    chat_id: int,
    bot,
    target_id: int,
):
    lobby = lobbies.get(chat_id)

    if lobby is None or not lobby.started or lobby.finished:
        return

    if lobby.phase == "confirm_elimination":
        return

    eligible = set(lobby.votes.keys()) - {target_id}

    if not eligible:
        if not await _persist_active_phase(
            lobby,
            "round_end",
            lobby.current_round + 1,
            ROUND_TRANSITION_DELAY_SECONDS,
        ):
            return
        lobby.current_round += 1
        lobby.phase = "round_end"
        await bot.send_message(
            chat_id,
            texts.error_text(
                "Tasdiqlash uchun boshqa ovoz bergan o‘yinchi yo‘q. "
                "O‘yinchi chiqarilmadi."
            ),
            parse_mode="HTML",
        )
        if not lobby.finished:
            asyncio.get_running_loop().call_later(
                ROUND_TRANSITION_DELAY_SECONDS,
                lambda: asyncio.create_task(start_round(chat_id, bot)),
            )
        return

    if not await _persist_active_phase(
        lobby,
        "confirm_elimination",
        lobby.current_round,
        CONFIRMATION_DURATION_SECONDS,
    ):
        return

    lobby.confirmation_target = target_id
    lobby.confirmation_eligible = eligible
    lobby.confirmation_votes = {}
    lobby.phase = "confirm_elimination"

    target_name = lobby.players.get(target_id, str(target_id))
    target_username = lobby.usernames.get(target_id)

    yes_count = 0
    no_count = 0

    keyboard = confirmation_keyboard(
        chat_id,
        target_id,
        yes_count,
        no_count,
    )

    confirmation_message = await bot.send_message(
        chat_id=chat_id,
        text=texts.confirmation_text(
            target_name,
            target_username,
            yes_count,
            no_count,
        ),
        reply_markup=keyboard,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    lobby.confirmation_message_id = confirmation_message.message_id

    asyncio.create_task(
        confirmation_timer(chat_id, bot, target_id)
    )


async def confirmation_timer(
    chat_id: int,
    bot,
    target_id: int,
):
    await asyncio.sleep(CONFIRMATION_DURATION_SECONDS)
    lobby = lobbies.get(chat_id)
    if lobby is not None and lobby.finished:
        return
    await finish_elimination_confirmation(
        chat_id,
        bot,
        target_id,
    )


async def finish_elimination_confirmation(
    chat_id: int,
    bot,
    target_id: int,
):
    lobby = lobbies.get(chat_id)

    if lobby is None:
        return

    # Taymer va oxirgi tugma bir vaqtda ishlasa,
    # natija faqat bir marta chiqariladi.
    if (
        lobby.phase != "confirm_elimination"
        or lobby.confirmation_target != target_id
    ):
        return

    yes_count = sum(
        vote == "yes"
        for vote in lobby.confirmation_votes.values()
    )
    no_count = sum(
        vote == "no"
        for vote in lobby.confirmation_votes.values()
    )

    target_name = lobby.players.get(target_id, str(target_id))

    # Ovozlar teng bo'lsa, chiqarilmaydi.
    cards = lobby.cards.get(target_id, {})
    eliminated = yes_count > no_count
    result_text = texts.confirmation_result_text(
        target_name,
        yes_count,
        no_count,
        eliminated,
        cards,
    )

    if eliminated:
        lobby.players.pop(target_id, None)
        lobby.usernames.pop(target_id, None)
        lobby.cards.pop(target_id, None)
        lobby.revealed.pop(target_id, None)

    lobby.phase = "round_end"
    lobby.confirmation_target = None
    lobby.confirmation_eligible = set()

    remaining_players = list(lobby.players)

    if yes_count > no_count and len(remaining_players) == 2:
        if not await finish_game_with_two_winners(
            lobby,
            bot,
            result_text,
        ):
            lobby.phase = "completion_error"
        return

    if len(remaining_players) == 1:
        final_survivor_id = remaining_players[0]

        first_winner_id = (
            lobby.first_winner
            if lobby.first_winner is not None
            else final_survivor_id
        )
        rewards = await _persist_game_completion(
            lobby,
            first_winner_id,
            final_survivor_id,
        )
        if rewards is None:
            lobby.phase = "completion_error"
            return

        lobby.first_winner = first_winner_id
        lobby.second_winner = final_survivor_id
        lobby.finished = True
        lobby.phase = "finished"

        winners_text = texts.winners_text(
            (
                player_id,
                lobby.players.get(player_id, str(player_id)),
                lobby.usernames.get(player_id),
            )
            for player_id in (
                lobby.first_winner,
                lobby.second_winner,
            )
        )

        reward_text = "\n\n🪙 <b>O‘yin mukofotlari</b>\n" + "\n".join(
            f"{escape(lobby.players.get(user_id, str(user_id)))} — <b>+{amount} coin</b>"
            for user_id, amount in rewards.items()
        )
        await bot.send_message(
            chat_id,
            f"{result_text}\n\n{winners_text}{reward_text}",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    if len(remaining_players) == 2 and lobby.first_winner is None:
        lobby.first_winner = remaining_players[0]
        first_name = lobby.players.get(lobby.first_winner, str(lobby.first_winner))
        await bot.send_message(
            chat_id,
            f"{result_text}\n\n"
            "🟢 <b>Bunkerda 2 ta joy qoldi.</b>\n"
            f"🥇 <b>Birinchi g‘olib:</b> {escape(first_name)}\n"
            "Yakuniy raundlar davom etmoqda.",
            parse_mode="HTML",
        )
    else:
        await bot.send_message(
            chat_id,
            result_text,
            parse_mode="HTML",
        )

    if not lobby.finished:
        if not await _persist_active_phase(
            lobby,
            "round_end",
            lobby.current_round + 1,
            ROUND_TRANSITION_DELAY_SECONDS,
        ):
            return
        lobby.current_round += 1
        asyncio.get_running_loop().call_later(
            ROUND_TRANSITION_DELAY_SECONDS,
            lambda: asyncio.create_task(start_round(chat_id, bot)),
        )



@router.callback_query(F.data.startswith("bunker:confirm:"))
async def confirmation_vote(callback: CallbackQuery):
    parts = callback.data.split(":")

    chat_id = int(parts[2])
    target_id = int(parts[3])
    choice = parts[4]

    lobby = lobbies.get(chat_id)

    if (
        lobby is None
        or lobby.phase != "confirm_elimination"
        or lobby.confirmation_target != target_id
    ):
        await callback.answer(
            "⏰ Tasdiqlash ovoz berishi yakunlangan.",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    if user_id not in lobby.confirmation_eligible:
        await callback.answer(
            "⛔ Siz dastlabki ovoz berishda qatnashmagansiz.",
            show_alert=True,
        )
        return

    if user_id == target_id:
        await callback.answer(
            "⛔ O‘zingiz haqingizda ovoz bera olmaysiz.",
            show_alert=True,
        )
        return

    if user_id in lobby.confirmation_votes:
        await callback.answer(
            "ℹ️ Siz allaqachon ovoz bergansiz.",
            show_alert=True,
        )
        return

    if choice not in {"yes", "no"}:
        await callback.answer(
            "⚠️ Noto‘g‘ri ovoz.",
            show_alert=True,
        )
        return

    # Ovozini saqlaymiz
    lobby.confirmation_votes[user_id] = choice

    # Hozirgacha yig'ilgan ovozlarni hisoblaymiz
    yes_count = sum(
        vote == "yes"
        for vote in lobby.confirmation_votes.values()
    )

    no_count = sum(
        vote == "no"
        for vote in lobby.confirmation_votes.values()
    )

    # Tugmalardagi sonlarni yangilaymiz
    keyboard = confirmation_keyboard(
        chat_id,
        target_id,
        yes_count,
        no_count,
    )

    try:
        await callback.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=lobby.confirmation_message_id,
            reply_markup=keyboard,
        )
    except Exception as e:
        print(f"[CONFIRM] Tugmalarni yangilashda xato: {e}")

    await callback.answer(
        "✅ Ovozingiz qabul qilindi."
        if choice == "yes"
        else "✅ Ovozingiz qabul qilindi."
    )
