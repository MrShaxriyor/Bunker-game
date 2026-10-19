"""Private Telegram admin panel and database-backed statistics."""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.blocks import lock_user_for_block_check
from database.engine import get_sessionmaker
from database.models import BlockedUser, BotUser, Game, GamePlayer, GameRound
from database.community_access import get_join_settings, update_join_settings
from handlers.lobby import cancel_open_game
from ui import texts
from ui.keyboards import (
    admin_broadcast_preview_keyboard,
    admin_broadcast_progress_keyboard,
    admin_cancel_confirmation_keyboard,
    admin_block_confirmation_keyboard,
    admin_block_reason_confirmation_keyboard,
    admin_game_detail_keyboard,
    admin_games_keyboard,
    admin_player_profile_keyboard,
    admin_players_keyboard,
    admin_keyboard,
)

logger = logging.getLogger(__name__)
admin_router = Router(name="admin")
ACTIVE_GAMES_PAGE_SIZE = 5
PLAYER_SEARCH_PAGE_SIZE = 5
RECENT_PLAYER_GAMES_LIMIT = 5
BROADCAST_BATCH_SIZE = 100
BROADCAST_MAX_RETRIES = 2
BROADCAST_SEND_INTERVAL_SECONDS = 0.05
BROADCAST_PROGRESS_INTERVAL = 25


@dataclass(frozen=True)
class BroadcastDraft:
    text: str
    preview_message_id: int


@dataclass
class BroadcastRun:
    admin_id: int
    private_chat_id: int
    progress_message_id: int
    total_recipients: int
    blocked_excluded: int
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    sent: int = 0
    failed: int = 0
    stopped_unprocessed: int = 0
    technical_unprocessed: int = 0
    stop_reason: str | None = None

    @property
    def eligible_recipients(self) -> int:
        return max(self.total_recipients - self.blocked_excluded, 0)


_broadcast_drafts: dict[int, BroadcastDraft] = {}
_broadcast_guard = asyncio.Lock()
_active_broadcast: BroadcastRun | None = None
_active_broadcast_task: asyncio.Task[None] | None = None


class PlayerSearchStates(StatesGroup):
    waiting_for_query = State()


class PlayerBlockStates(StatesGroup):
    waiting_for_reason = State()


class BroadcastStates(StatesGroup):
    waiting_for_message = State()


class MembershipSettingsStates(StatesGroup):
    waiting_for_chat_id = State()
    waiting_for_invite_link = State()


def parse_admin_ids(value: str | None) -> frozenset[int]:
    """Parse a comma-separated list of positive Telegram user IDs."""
    if value is None:
        return frozenset()

    entries = value.split(",")
    if not value.strip() or any(not entry.strip() for entry in entries):
        raise ValueError(
            "ADMIN_IDS bo‘sh bo‘lmasligi kerak; Telegram ID’larini "
            "vergul bilan ajrating."
        )

    admin_ids: set[int] = set()
    for entry in entries:
        candidate = entry.strip()
        if not candidate.isascii() or not candidate.isdecimal():
            raise ValueError(
                "ADMIN_IDS noto‘g‘ri formatda; faqat musbat Telegram "
                "ID’larini vergul bilan ajrating."
            )
        try:
            admin_id = int(candidate)
        except ValueError:
            raise ValueError(
                "ADMIN_IDS noto‘g‘ri formatda; faqat musbat Telegram "
                "ID’larini vergul bilan ajrating."
            ) from None
        if admin_id <= 0:
            raise ValueError(
                "ADMIN_IDS noto‘g‘ri formatda; faqat musbat Telegram "
                "ID’larini vergul bilan ajrating."
            )
        admin_ids.add(admin_id)

    return frozenset(admin_ids)


def get_admin_ids() -> frozenset[int]:
    load_dotenv()
    return parse_admin_ids(os.getenv("ADMIN_IDS"))


class AdminAccessFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        from_user = event.from_user
        if from_user is None or from_user.id not in get_admin_ids():
            if isinstance(event, Message):
                await event.answer("⛔ Bu bo‘limdan foydalanishga ruxsat yo‘q.")
            else:
                await event.answer(
                    "⛔ Bu bo‘limdan foydalanishga ruxsat yo‘q.",
                    show_alert=True,
                )
            return False

        if isinstance(event, CallbackQuery):
            message = event.message
            if not isinstance(message, Message) or message.chat.type != "private":
                await event.answer(
                    "Admin panelidan faqat shaxsiy chatda foydalanish mumkin.",
                    show_alert=True,
                )
                return False

        return True


async def _get_broadcast_recipient_counts() -> tuple[int, int]:
    unique_recipients = (
        select(GamePlayer.telegram_user_id.label("telegram_user_id"))
        .distinct()
        .subquery()
    )
    async with get_sessionmaker()() as session:
        total_recipients = await session.scalar(
            select(func.count()).select_from(unique_recipients)
        )
        blocked_recipients = await session.scalar(
            select(func.count())
            .select_from(unique_recipients)
            .join(
                BlockedUser,
                BlockedUser.telegram_user_id
                == unique_recipients.c.telegram_user_id,
            )
        )
    return total_recipients or 0, blocked_recipients or 0


async def _get_broadcast_recipient_batch(
    after_user_id: int,
) -> list[int]:
    is_blocked = select(BlockedUser.telegram_user_id).where(
        BlockedUser.telegram_user_id == GamePlayer.telegram_user_id
    ).exists()
    async with get_sessionmaker()() as session:
        recipients = await session.scalars(
            select(GamePlayer.telegram_user_id)
            .where(
                GamePlayer.telegram_user_id > after_user_id,
                ~is_blocked,
            )
            .distinct()
            .order_by(GamePlayer.telegram_user_id)
            .limit(BROADCAST_BATCH_SIZE)
        )
        return list(recipients.all())


async def _wait_or_stop(stop_event: asyncio.Event, delay: float) -> bool:
    if stop_event.is_set():
        return True
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(delay, 0))
    except asyncio.TimeoutError:
        return False
    return True


async def _send_broadcast_message(
    bot: Bot,
    recipient_id: int,
    message_text: str,
    stop_event: asyncio.Event,
) -> bool | None:
    retries = 0
    while True:
        if stop_event.is_set():
            return None
        try:
            await bot.send_message(
                chat_id=recipient_id,
                text=message_text,
                parse_mode=None,
            )
            return True
        except TelegramRetryAfter as error:
            if retries >= BROADCAST_MAX_RETRIES:
                return False
            retries += 1
            if await _wait_or_stop(stop_event, error.retry_after):
                return None
        except (TelegramNetworkError, TelegramServerError):
            if retries >= BROADCAST_MAX_RETRIES:
                return False
            delay = min(2**retries, 8)
            retries += 1
            if await _wait_or_stop(stop_event, delay):
                return None
        except TelegramForbiddenError:
            return False
        except TelegramAPIError:
            return False


async def _edit_broadcast_status(
    run: BroadcastRun,
    bot: Bot,
    message_text: str,
    *,
    show_stop_button: bool,
) -> None:
    try:
        await bot.edit_message_text(
            chat_id=run.private_chat_id,
            message_id=run.progress_message_id,
            text=message_text,
            reply_markup=(
                admin_broadcast_progress_keyboard()
                if show_stop_button
                else InlineKeyboardMarkup(inline_keyboard=[])
            ),
        )
    except TelegramAPIError as error:
        logger.error(
            "Admin broadcast holati xabarini yangilab bo‘lmadi: "
            "error_type=%s",
            type(error).__name__,
        )


def _broadcast_progress_text(run: BroadcastRun) -> str:
    processed = run.sent + run.failed
    return (
        "⏳ Yuborilmoqda...\n"
        f"Qabul qilganlar: {run.sent}\n"
        f"Xatolar: {run.failed}\n"
        f"Jarayon: {processed}/{run.eligible_recipients}"
    )


async def _run_broadcast(
    run: BroadcastRun,
    bot: Bot,
    message_text: str,
) -> None:
    global _active_broadcast, _active_broadcast_task

    last_user_id = 0
    database_error = False
    try:
        while not run.stop_event.is_set():
            try:
                recipient_ids = await _get_broadcast_recipient_batch(
                    last_user_id
                )
            except (SQLAlchemyError, RuntimeError) as error:
                logger.error(
                    "Admin broadcast oluvchilarini DB’dan olishda xato: "
                    "error_type=%s",
                    type(error).__name__,
                )
                database_error = True
                break

            if not recipient_ids:
                break

            for recipient_id in recipient_ids:
                if run.stop_event.is_set():
                    break

                delivery_result = await _send_broadcast_message(
                    bot,
                    recipient_id,
                    message_text,
                    run.stop_event,
                )
                if delivery_result is None:
                    break
                if delivery_result:
                    run.sent += 1
                else:
                    run.failed += 1
                last_user_id = recipient_id

                processed = run.sent + run.failed
                if processed and processed % BROADCAST_PROGRESS_INTERVAL == 0:
                    await _edit_broadcast_status(
                        run,
                        bot,
                        _broadcast_progress_text(run),
                        show_stop_button=True,
                    )

                if processed < run.eligible_recipients and await _wait_or_stop(
                    run.stop_event,
                    BROADCAST_SEND_INTERVAL_SECONDS,
                ):
                    break

            if run.stop_event.is_set():
                break

        remaining = max(
            run.eligible_recipients - run.sent - run.failed,
            0,
        )
        if run.stop_reason == "admin":
            run.stopped_unprocessed = remaining
        elif database_error or remaining:
            run.technical_unprocessed = remaining
    finally:
        final_text = texts.admin_broadcast_result_text(
            run.total_recipients,
            run.blocked_excluded,
            run.sent,
            run.failed,
            run.stopped_unprocessed,
            run.technical_unprocessed,
        )
        await _edit_broadcast_status(
            run,
            bot,
            final_text,
            show_stop_button=False,
        )
        async with _broadcast_guard:
            if _active_broadcast is run:
                _active_broadcast = None
                _active_broadcast_task = None


async def stop_broadcast_on_shutdown() -> None:
    run = _active_broadcast
    task = _active_broadcast_task
    if run is None or task is None or task.done():
        return

    run.stop_event.set()
    run.stop_reason = "shutdown"
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Admin broadcast shutdown vaqtida to‘xtatildi")


async def _get_statistics() -> tuple[int, int, int, int, int, int, int, int, int]:
    async with get_sessionmaker()() as session:
        game_counts = (
            await session.execute(
                select(
                    func.count(Game.id),
                    func.count(Game.id).filter(Game.status == "lobby"),
                    func.count(Game.id).filter(Game.status == "active"),
                    func.count(Game.id).filter(Game.status == "completed"),
                    func.count(Game.id).filter(Game.status == "cancelled"),
                )
            )
        ).one()
        unique_players = await session.scalar(
            select(func.count(func.distinct(GamePlayer.telegram_user_id)))
            .join(Game, Game.id == GamePlayer.game_id)
            .where(Game.status.in_(("active", "completed")))
        )
        total_rounds = await session.scalar(select(func.count(GameRound.id)))
        tashkent_day_start = datetime.now(ZoneInfo("Asia/Tashkent")).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)
        registered_users = await session.scalar(select(func.count(BotUser.telegram_user_id)))
        registered_today = await session.scalar(
            select(func.count(BotUser.telegram_user_id)).where(
                BotUser.registered_at >= tashkent_day_start
            )
        )

    return (
        game_counts[0],
        game_counts[1],
        game_counts[2],
        game_counts[3],
        game_counts[4],
        unique_players or 0,
        total_rounds or 0,
        registered_users or 0,
        registered_today or 0,
    )


async def _show_statistics(
    event: Message | CallbackQuery,
    *,
    edit: bool,
) -> None:
    try:
        statistics = await _get_statistics()
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin statistikasi DB’dan olinmadi: error_type=%s",
            type(error).__name__,
        )
        error_message = (
            "⚠️ Statistikani hozir olib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring."
        )
        if isinstance(event, Message):
            await event.answer(error_message)
        else:
            if edit and isinstance(event.message, Message):
                await event.message.edit_text(error_message)
            await event.answer(
                "Statistikani olib bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
                show_alert=True,
            )
        return

    stats_text = texts.admin_stats_text(*statistics)
    if isinstance(event, Message):
        await event.answer(
            stats_text,
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
        return

    if not isinstance(event.message, Message):
        await event.answer(
            "Admin panel xabari endi mavjud emas. /admin buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    if edit:
        if event.message.text == stats_text:
            await event.answer()
            return
        await event.message.edit_text(
            stats_text,
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
    else:
        await event.message.answer(
            stats_text,
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
    await event.answer()


async def _show_active_games(callback: CallbackQuery, page: int) -> None:
    try:
        async with get_sessionmaker()() as session:
            total_games = await session.scalar(
                select(func.count(Game.id)).where(
                    Game.status.in_(("lobby", "active"))
                )
            )
            total_games = total_games or 0
            total_pages = (
                (total_games + ACTIVE_GAMES_PAGE_SIZE - 1)
                // ACTIVE_GAMES_PAGE_SIZE
            )
            if total_pages:
                page = min(page, total_pages - 1)
                games = (
                    await session.scalars(
                        select(Game)
                        .where(Game.status.in_(("lobby", "active")))
                        .order_by(Game.created_at.desc(), Game.id.desc())
                        .limit(ACTIVE_GAMES_PAGE_SIZE)
                        .offset(page * ACTIVE_GAMES_PAGE_SIZE)
                    )
                ).all()
            else:
                page = 0
                games = []
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin faol o‘yinlar ro‘yxatini DB’dan olmadi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "Faol o‘yinlar ro‘yxatini olib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
        return

    labels = [
        (
            game.id,
            texts.admin_game_list_item(
                game.id,
                game.chat_id,
                game.status,
                game.phase,
                game.current_round,
            ),
        )
        for game in games
    ]
    message_text = texts.admin_games_text(page, total_pages, total_games)
    if not isinstance(callback.message, Message):
        await callback.answer(
            "Admin panel xabari endi mavjud emas. /admin buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        message_text,
        reply_markup=admin_games_keyboard(labels, page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()


async def _show_game_details(
    callback: CallbackQuery,
    game_id: int,
    page: int,
) -> None:
    try:
        async with get_sessionmaker()() as session:
            game_data = (
                await session.execute(
                    select(
                        Game.id,
                        Game.chat_id,
                        Game.status,
                        Game.phase,
                        Game.current_round,
                        Game.host_telegram_id,
                        GamePlayer.name,
                        GamePlayer.username,
                    )
                    .outerjoin(
                        GamePlayer,
                        (GamePlayer.game_id == Game.id)
                        & (
                            GamePlayer.telegram_user_id
                            == Game.host_telegram_id
                        ),
                    )
                    .where(
                        Game.id == game_id,
                        Game.status.in_(("lobby", "active")),
                    )
                )
            ).one_or_none()
            if game_data is None:
                player_count = 0
            else:
                player_count = await session.scalar(
                    select(func.count(GamePlayer.id)).where(
                        GamePlayer.game_id == game_id
                    )
                )
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin o‘yin tafsilotlarini DB’dan olmadi: "
            "game_id=%s, error_type=%s",
            game_id,
            type(error).__name__,
        )
        await callback.answer(
            "O‘yin tafsilotlarini olib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
        return

    if game_data is None:
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Bu o‘yin endi lobby yoki faol holatda emas.",
                reply_markup=admin_games_keyboard([], page, 0),
            )
        await callback.answer("O‘yin endi faol emas.", show_alert=True)
        return

    detail_text = texts.admin_game_detail_text(
        game_data.id,
        game_data.chat_id,
        game_data.status,
        game_data.phase,
        game_data.current_round,
        game_data.host_telegram_id,
        game_data.name,
        game_data.username,
        player_count or 0,
    )
    if not isinstance(callback.message, Message):
        await callback.answer(
            "Admin panel xabari endi mavjud emas. /admin buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        detail_text,
        reply_markup=admin_game_detail_keyboard(
            game_id,
            page,
            cancellable=True,
        ),
        parse_mode="HTML",
    )
    await callback.answer()


def _normalize_player_query(query: str) -> str | None:
    normalized = query.strip()
    if normalized.startswith("@"):
        normalized = normalized[1:]

    if normalized.isascii() and normalized.isdecimal():
        return normalized if len(normalized) <= 20 and int(normalized) > 0 else None
    if re.fullmatch(r"[A-Za-z0-9_]{1,32}", normalized):
        return normalized
    return None


def _player_search_condition(query: str):
    if query.isascii() and query.isdecimal():
        return GamePlayer.telegram_user_id == int(query)
    escaped_query = (
        query.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return GamePlayer.username.ilike(f"%{escaped_query}%", escape="\\")


async def _get_player_search_page(
    query: str,
    page: int,
) -> tuple[int, int, list[tuple[int, str, str | None]]]:
    latest_player_ids = (
        select(GamePlayer.telegram_user_id, func.max(GamePlayer.id).label("id"))
        .group_by(GamePlayer.telegram_user_id)
        .subquery()
    )
    condition = _player_search_condition(query)
    async with get_sessionmaker()() as session:
        players_query = (
            select(
                GamePlayer.telegram_user_id,
                GamePlayer.name,
                GamePlayer.username,
            )
            .join(latest_player_ids, GamePlayer.id == latest_player_ids.c.id)
            .where(condition)
        )
        total_players = await session.scalar(
            select(func.count()).select_from(players_query.subquery())
        )
        total_players = total_players or 0
        total_pages = (
            total_players + PLAYER_SEARCH_PAGE_SIZE - 1
        ) // PLAYER_SEARCH_PAGE_SIZE
        if total_pages == 0:
            return 0, 0, []

        page = min(page, total_pages - 1)
        rows = (
            await session.execute(
                players_query
                .order_by(GamePlayer.telegram_user_id)
                .limit(PLAYER_SEARCH_PAGE_SIZE)
                .offset(page * PLAYER_SEARCH_PAGE_SIZE)
            )
        ).all()
    return (
        total_players,
        total_pages,
        [(row.telegram_user_id, row.name, row.username) for row in rows],
    )


def _player_search_button_label(
    telegram_user_id: int,
    name: str,
    username: str | None,
) -> str:
    if username:
        return f"{telegram_user_id} · @{username.lstrip('@')[:20]}"
    return f"{telegram_user_id} · {name[:28]}"


async def _show_player_search(
    event: Message | CallbackQuery,
    query: str,
    page: int,
) -> None:
    try:
        total_players, total_pages, players = await _get_player_search_page(
            query,
            page,
        )
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin o‘yinchi qidiruvi DB’dan olinmadi: error_type=%s",
            type(error).__name__,
        )
        error_text = (
            "O‘yinchilarni qidirib bo‘lmadi. Keyinroq qayta urinib ko‘ring."
        )
        if isinstance(event, Message):
            await event.answer(error_text)
        else:
            await event.answer(error_text, show_alert=True)
        return

    if isinstance(event, Message):
        if total_pages == 0:
            await event.answer(
                texts.admin_player_search_empty_text(query),
                reply_markup=admin_players_keyboard([], 0, 0),
                parse_mode="HTML",
            )
            return

        page = min(page, total_pages - 1)
        await event.answer(
            texts.admin_player_search_text(
                query,
                page,
                total_pages,
                total_players,
            ),
            reply_markup=admin_players_keyboard(
                [
                    (
                        telegram_user_id,
                        _player_search_button_label(
                            telegram_user_id,
                            name,
                            username,
                        ),
                    )
                    for telegram_user_id, name, username in players
                ],
                page,
                total_pages,
            ),
            parse_mode="HTML",
        )
        return

    if not isinstance(event.message, Message):
        await event.answer(
            "Qidiruv natijalarini ko‘rsatib bo‘lmadi. "
            "/admin buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    if total_pages == 0:
        await event.message.edit_text(
            texts.admin_player_search_empty_text(query),
            reply_markup=admin_players_keyboard([], 0, 0),
            parse_mode="HTML",
        )
        await event.answer()
        return

    page = min(page, total_pages - 1)
    event_players = [
        (
            telegram_user_id,
            _player_search_button_label(telegram_user_id, name, username),
        )
        for telegram_user_id, name, username in players
    ]
    await event.message.edit_text(
        texts.admin_player_search_text(
            query,
            page,
            total_pages,
            total_players,
        ),
        reply_markup=admin_players_keyboard(
            event_players,
            page,
            total_pages,
        ),
        parse_mode="HTML",
    )
    await event.answer()


async def _get_player_profile(
    telegram_user_id: int,
) -> tuple[
    str,
    str | None,
    int,
    int,
    list[tuple[int, str, int, str]],
    bool,
    str | None,
] | None:
    async with get_sessionmaker()() as session:
        player = (
            await session.execute(
                select(GamePlayer.name, GamePlayer.username)
                .where(GamePlayer.telegram_user_id == telegram_user_id)
                .order_by(GamePlayer.joined_at.desc(), GamePlayer.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if player is None:
            return None

        total_games = await session.scalar(
            select(func.count(func.distinct(GamePlayer.game_id))).where(
                GamePlayer.telegram_user_id == telegram_user_id
            )
        )
        wins = await session.scalar(
            select(func.count(Game.id)).where(
                Game.status == "completed",
                or_(
                    Game.first_winner_id == telegram_user_id,
                    Game.second_winner_id == telegram_user_id,
                ),
            )
        )
        recent_games = (
            await session.execute(
                select(
                    Game.id.label("game_id"),
                    Game.status.label("game_status"),
                    Game.current_round.label("round_number"),
                    GamePlayer.status.label("player_status"),
                )
                .join(GamePlayer, GamePlayer.game_id == Game.id)
                .where(GamePlayer.telegram_user_id == telegram_user_id)
                .order_by(Game.created_at.desc(), Game.id.desc())
                .limit(RECENT_PLAYER_GAMES_LIMIT)
            )
        ).all()
        blocked_user = await session.get(BlockedUser, telegram_user_id)

    return (
        player.name,
        player.username,
        total_games or 0,
        wins or 0,
        [
            (
                row.game_id,
                row.game_status,
                row.round_number,
                row.player_status,
            )
            for row in recent_games
        ],
        blocked_user is not None,
        blocked_user.reason if blocked_user is not None else None,
    )


async def _block_player(
    telegram_user_id: int,
    admin_id: int,
    reason: str | None,
) -> str:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await lock_user_for_block_check(session, telegram_user_id)
            known_player = await session.scalar(
                select(GamePlayer.id)
                .where(GamePlayer.telegram_user_id == telegram_user_id)
                .limit(1)
            )
            if known_player is None:
                return "not_found"

            result = await session.execute(
                pg_insert(BlockedUser)
                .values(
                    telegram_user_id=telegram_user_id,
                    reason=reason,
                    blocked_by_admin_id=admin_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[BlockedUser.telegram_user_id]
                )
                .returning(BlockedUser.telegram_user_id)
            )
            return (
                "blocked"
                if result.scalar_one_or_none() is not None
                else "already_blocked"
            )


async def _unblock_player(telegram_user_id: int) -> bool:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await lock_user_for_block_check(session, telegram_user_id)
            result = await session.execute(
                delete(BlockedUser).where(
                    BlockedUser.telegram_user_id == telegram_user_id
                )
            )
            return result.rowcount == 1


async def _show_player_profile(
    callback: CallbackQuery,
    telegram_user_id: int,
    page: int,
) -> None:
    try:
        profile = await _get_player_profile(telegram_user_id)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin o‘yinchi profilini DB’dan olmadi: "
            "error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "O‘yinchi profilini olib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
        return

    if profile is None:
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                texts.admin_player_profile_not_found_text(),
                reply_markup=admin_players_keyboard([], page, 0),
            )
        await callback.answer("O‘yinchi topilmadi.", show_alert=True)
        return

    if not isinstance(callback.message, Message):
        await callback.answer(
            "Profilni ko‘rsatib bo‘lmadi. /admin buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    (
        name,
        username,
        total_games,
        wins,
        recent_games,
        blocked,
        block_reason,
    ) = profile
    await callback.message.edit_text(
        texts.admin_player_profile_text(
            telegram_user_id,
            name,
            username,
            total_games,
            wins,
            recent_games,
            blocked,
            block_reason,
        ),
        reply_markup=admin_player_profile_keyboard(
            page,
            telegram_user_id,
            blocked=blocked,
        ),
        parse_mode="HTML",
    )
    await callback.answer()


async def _show_block_confirmation(
    callback: CallbackQuery,
    telegram_user_id: int,
    page: int,
) -> None:
    if callback.from_user.id == telegram_user_id:
        await callback.answer(
            texts.admin_player_self_block_error_text(),
            show_alert=True,
        )
        return

    try:
        profile = await _get_player_profile(telegram_user_id)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Bloklash holatini DB’dan tekshirishda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "O‘yinchi holatini tekshirib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
        return

    if profile is None:
        await callback.answer("O‘yinchi topilmadi.", show_alert=True)
        return
    if profile[5]:
        await callback.answer(
            texts.admin_player_already_blocked_text(telegram_user_id),
            show_alert=True,
        )
        return
    if not isinstance(callback.message, Message):
        await callback.answer(
            "Tasdiqlash xabari topilmadi. O‘yinchi profilini qayta oching.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        texts.admin_player_block_confirmation_text(telegram_user_id),
        reply_markup=admin_block_confirmation_keyboard(
            telegram_user_id,
            page,
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(Command("admin"), AdminAccessFilter())
async def show_admin_panel(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        await message.answer(
            "Admin panelidan faqat shaxsiy chatda foydalanish mumkin."
        )
        return

    await state.clear()
    await message.answer(
        texts.admin_menu_text(),
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin:stats", AdminAccessFilter())
async def show_statistics(callback: CallbackQuery) -> None:
    await _show_statistics(callback, edit=True)


@admin_router.callback_query(F.data == "admin:menu", AdminAccessFilter())
async def show_admin_menu(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            texts.admin_menu_text(),
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


def _membership_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "⏸️ Talabni o‘chirish" if enabled else "▶️ Talabni yoqish"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Chat ID sozlash", callback_data="admin:membership:chat")],
        [InlineKeyboardButton(text="🔗 Join-request havolasi", callback_data="admin:membership:link")],
        [InlineKeyboardButton(text=toggle_text, callback_data="admin:membership:toggle")],
        [InlineKeyboardButton(text="⬅️ Admin menyu", callback_data="admin:menu")],
    ])


async def _show_membership_settings(callback: CallbackQuery) -> None:
    try:
        settings = await get_join_settings()
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error("Join settings read failed: error_type=%s", type(error).__name__)
        await callback.answer("A’zolik sozlamalarini olib bo‘lmadi.", show_alert=True)
        return
    status = "Yoqilgan" if settings.enabled else "O‘chiq"
    chat = str(settings.chat_id) if settings.chat_id is not None else "sozlanmagan"
    link = "sozlangan" if settings.invite_link else "sozlanmagan"
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🔐 <b>Majburiy a’zolik</b>\n\n"
            f"Holati: <b>{status}</b>\nChat ID: <code>{chat}</code>\n"
            f"So‘rov havolasi: <b>{link}</b>\n\n"
            "Botga guruhda invite users huquqini bering va join-request yaratadigan taklif havolasini kiriting.",
            reply_markup=_membership_keyboard(settings.enabled),
            parse_mode="HTML",
        )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:membership", AdminAccessFilter())
async def show_membership_settings(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
        await callback.answer("Bu sozlamani faqat shaxsiy admin chatida oching.", show_alert=True)
        return
    await _show_membership_settings(callback)


@admin_router.callback_query(F.data.in_({"admin:membership:chat", "admin:membership:link"}), AdminAccessFilter())
async def begin_membership_setting(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
        await callback.answer("Sozlamani shaxsiy chatda oching.", show_alert=True)
        return
    await state.clear()
    if callback.data == "admin:membership:chat":
        await state.set_state(MembershipSettingsStates.waiting_for_chat_id)
        prompt = "Target guruh yoki superguruh chat ID sini yuboring (masalan, -100…); /cancel — bekor qilish."
    else:
        await state.set_state(MembershipSettingsStates.waiting_for_invite_link)
        prompt = "Telegram join-request taklif havolasini (https://t.me/...) yuboring; /cancel — bekor qilish."
    await callback.message.edit_text(prompt)
    await callback.answer()


@admin_router.callback_query(F.data == "admin:membership:toggle", AdminAccessFilter())
async def toggle_membership_gate(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
        await callback.answer("Bu sozlamani faqat shaxsiy admin chatida o‘zgartiring.", show_alert=True)
        return
    try:
        current = await get_join_settings()
        if not current.enabled and (current.chat_id is None or not current.invite_link):
            await callback.answer("Avval chat ID va join-request havolasini sozlang.", show_alert=True)
            return
        await update_join_settings(admin_id=callback.from_user.id, enabled=not current.enabled)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error("Join settings update failed: error_type=%s", type(error).__name__)
        await callback.answer("A’zolik talabini yangilab bo‘lmadi.", show_alert=True)
        return
    await _show_membership_settings(callback)


@admin_router.message(StateFilter(MembershipSettingsStates), AdminAccessFilter())
async def receive_membership_setting(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        await message.answer("Sozlamani faqat shaxsiy admin chatida kiriting.")
        return
    value = (message.text or "").strip()
    if value.lower() == "/cancel":
        await state.clear()
        await message.answer("Bekor qilindi. /admin orqali davom eting.")
        return
    current = await state.get_state()
    if current == MembershipSettingsStates.waiting_for_chat_id.state:
        try:
            chat_id = int(value)
        except ValueError:
            await message.answer("Chat ID butun son bo‘lishi kerak.")
            return
        if chat_id >= 0:
            await message.answer("Guruh yoki superguruh chat ID si manfiy son bo‘lishi kerak.")
            return
        update = {"chat_id": chat_id}
    else:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in {"t.me", "telegram.me"} or not parsed.path.strip("/"):
            await message.answer("Telegram join-request havolasi https://t.me/... formatida bo‘lishi kerak.")
            return
        update = {"invite_link": value}
    try:
        await update_join_settings(admin_id=message.from_user.id, **update)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error("Join settings update failed: error_type=%s", type(error).__name__)
        await state.clear()
        await message.answer("Sozlamani saqlab bo‘lmadi.")
        return
    await state.clear()
    settings = await get_join_settings()
    status = "Yoqilgan" if settings.enabled else "O‘chiq"
    await message.answer(
        f"✅ A’zolik sozlamasi saqlandi. Hozirgi holat: <b>{status}</b>.",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


@admin_router.callback_query(
    F.data == "admin:broadcast",
    AdminAccessFilter(),
)
async def begin_admin_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer(
            "Xabar tayyorlash uchun /admin buyrug‘ini yuboring.",
            show_alert=True,
        )
        return

    _broadcast_drafts.pop(callback.from_user.id, None)
    await state.clear()
    await state.set_state(BroadcastStates.waiting_for_message)
    await callback.message.edit_text(texts.admin_broadcast_prompt_text())
    await callback.answer()


@admin_router.message(
    StateFilter(BroadcastStates.waiting_for_message),
    AdminAccessFilter(),
)
async def receive_admin_broadcast_message(
    message: Message,
    state: FSMContext,
) -> None:
    if message.chat.type != "private":
        await message.answer(
            "Ommaviy xabar faqat shaxsiy admin chatida tayyorlanadi."
        )
        return

    message_text = message.text
    if message_text is None or not message_text.strip():
        await message.answer(texts.admin_broadcast_invalid_text())
        return
    if len(message_text.encode("utf-16-le")) // 2 > 4096:
        await message.answer(texts.admin_broadcast_too_long_text())
        return
    if message.from_user is None:
        await message.answer("Admin ma’lumotini aniqlab bo‘lmadi.")
        return

    try:
        preview = await message.answer(
            message_text,
            reply_markup=admin_broadcast_preview_keyboard(),
        )
    except TelegramAPIError as error:
        logger.error(
            "Admin broadcast preview xabarini yuborib bo‘lmadi: "
            "error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            "Xabar preview’ini ko‘rsatib bo‘lmadi. Qayta urinib ko‘ring."
        )
        return

    _broadcast_drafts[message.from_user.id] = BroadcastDraft(
        text=message_text,
        preview_message_id=preview.message_id,
    )
    await state.clear()


@admin_router.callback_query(
    F.data == "admin:broadcast:confirm",
    AdminAccessFilter(),
)
async def confirm_admin_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    global _active_broadcast, _active_broadcast_task

    if not isinstance(callback.message, Message):
        await callback.answer(
            "Tasdiqlash xabari topilmadi. /admin buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    admin_id = callback.from_user.id
    draft = _broadcast_drafts.get(admin_id)
    if (
        draft is None
        or draft.preview_message_id != callback.message.message_id
    ):
        await callback.answer(
            "Bu preview eskirgan. Ommaviy xabarni qayta tayyorlang.",
            show_alert=True,
        )
        return

    try:
        total_recipients, blocked_excluded = (
            await _get_broadcast_recipient_counts()
        )
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin broadcast oluvchilarini hisoblashda DB xatosi: "
            "error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "Oluvchilar ro‘yxatini olib bo‘lmadi. Hech kimga xabar yuborilmadi.",
            show_alert=True,
        )
        return

    async with _broadcast_guard:
        if _broadcast_drafts.get(admin_id) != draft:
            run = None
            draft_was_cancelled = True
        elif _active_broadcast is not None:
            run = None
            draft_was_cancelled = False
        else:
            run = BroadcastRun(
                admin_id=admin_id,
                private_chat_id=callback.message.chat.id,
                progress_message_id=callback.message.message_id,
                total_recipients=total_recipients,
                blocked_excluded=blocked_excluded,
            )
            _active_broadcast = run
            _broadcast_drafts.pop(admin_id, None)
            draft_was_cancelled = False

    if run is None:
        if draft_was_cancelled:
            await callback.answer(
                "Bu preview bekor qilingan yoki yangilangan.",
                show_alert=True,
            )
        else:
            await callback.answer(
                "Boshqa ommaviy xabar yuborish jarayoni davom etmoqda.",
                show_alert=True,
            )
        return

    try:
        await callback.message.edit_text(
            _broadcast_progress_text(run),
            reply_markup=admin_broadcast_progress_keyboard(),
        )
    except TelegramAPIError as error:
        logger.error(
            "Admin broadcast boshlanish holatini ko‘rsatib bo‘lmadi: "
            "error_type=%s",
            type(error).__name__,
        )
        async with _broadcast_guard:
            if _active_broadcast is run:
                _active_broadcast = None
            if admin_id not in _broadcast_drafts:
                _broadcast_drafts[admin_id] = draft
        await callback.answer(
            "Yuborishni xavfsiz boshlash uchun holat xabarini ko‘rsatib "
            "bo‘lmadi. Qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    _active_broadcast_task = asyncio.create_task(
        _run_broadcast(run, callback.bot, draft.text)
    )
    await state.clear()
    await callback.answer("Ommaviy xabar yuborish boshlandi.")


@admin_router.callback_query(
    F.data == "admin:broadcast:cancel",
    AdminAccessFilter(),
)
async def cancel_admin_broadcast_draft(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer("Preview xabari topilmadi.", show_alert=True)
        return

    admin_id = callback.from_user.id
    draft = _broadcast_drafts.get(admin_id)
    if (
        draft is None
        or draft.preview_message_id != callback.message.message_id
    ):
        await callback.answer(
            "Bu preview eskirgan yoki allaqachon bekor qilingan.",
            show_alert=True,
        )
        return

    _broadcast_drafts.pop(admin_id, None)
    await state.clear()
    await callback.message.edit_text("Ommaviy xabar bekor qilindi.")
    await callback.answer("Xabar yuborilmadi.")


@admin_router.callback_query(
    F.data == "admin:broadcast:stop",
    AdminAccessFilter(),
)
async def stop_admin_broadcast(callback: CallbackQuery) -> None:
    run = _active_broadcast
    if (
        run is None
        or not isinstance(callback.message, Message)
        or callback.message.message_id != run.progress_message_id
        or callback.message.chat.id != run.private_chat_id
    ):
        await callback.answer(
            "Faol ommaviy xabar yuborish jarayoni topilmadi.",
            show_alert=True,
        )
        return

    run.stop_event.set()
    run.stop_reason = "admin"
    await callback.answer("Keyingi oluvchilarga yuborish to‘xtatilmoqda.")


@admin_router.callback_query(F.data == "admin:players", AdminAccessFilter())
async def begin_player_search(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer(
            "Qidiruvni boshlash uchun /admin buyrug‘ini yuboring.",
            show_alert=True,
        )
        return
    await state.clear()
    await state.set_state(PlayerSearchStates.waiting_for_query)
    await callback.message.edit_text(
        texts.admin_player_search_prompt_text(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(
    StateFilter(PlayerSearchStates.waiting_for_query),
    AdminAccessFilter(),
)
async def receive_player_search_query(
    message: Message,
    state: FSMContext,
) -> None:
    if message.chat.type != "private":
        await message.answer(
            "O‘yinchilar qidiruvi faqat shaxsiy chatda ishlaydi."
        )
        return

    query = _normalize_player_query(message.text or "")
    await state.clear()
    if query is None:
        await message.answer(texts.admin_player_search_invalid_text())
        return
    await state.update_data(player_search_query=query)
    await _show_player_search(message, query, 0)


@admin_router.callback_query(
    F.data.startswith("admin:players:results:"),
    AdminAccessFilter(),
)
async def show_player_search_page(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        _, _, _, page_value = callback.data.split(":")
        page = int(page_value)
    except ValueError:
        await callback.answer("Qidiruv sahifasi noto‘g‘ri.", show_alert=True)
        return
    query_value = (await state.get_data()).get("player_search_query")
    query = (
        _normalize_player_query(query_value)
        if isinstance(query_value, str)
        else None
    )
    if page < 0 or query is None:
        await callback.answer(
            "Qidiruv muddati tugagan. Yangi qidiruvni boshlang.",
            show_alert=True,
        )
        return
    await _show_player_search(callback, query, page)


@admin_router.callback_query(
    F.data.startswith("admin:player:"),
    AdminAccessFilter(),
)
async def show_player_profile(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        _, _, user_id_value, page_value = callback.data.split(":")
        telegram_user_id = int(user_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    query_value = (await state.get_data()).get("player_search_query")
    query = (
        _normalize_player_query(query_value)
        if isinstance(query_value, str)
        else None
    )
    if telegram_user_id <= 0 or page < 0 or query is None:
        await callback.answer(
            "Qidiruv muddati tugagan. Yangi qidiruvni boshlang.",
            show_alert=True,
        )
        return
    await _show_player_profile(
        callback,
        telegram_user_id,
        page,
    )


@admin_router.callback_query(
    F.data.startswith("admin:block:confirm:"),
    AdminAccessFilter(),
)
async def confirm_player_block(callback: CallbackQuery) -> None:
    try:
        _, _, _, user_id_value, page_value = callback.data.split(":")
        telegram_user_id = int(user_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    if telegram_user_id <= 0 or page < 0:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    await _show_block_confirmation(callback, telegram_user_id, page)


@admin_router.callback_query(
    F.data.startswith("admin:block:reason:"),
    AdminAccessFilter(),
)
async def request_player_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        _, _, _, user_id_value, page_value = callback.data.split(":")
        telegram_user_id = int(user_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    if telegram_user_id <= 0 or page < 0:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    if callback.from_user.id == telegram_user_id:
        await callback.answer(
            texts.admin_player_self_block_error_text(),
            show_alert=True,
        )
        return
    if not isinstance(callback.message, Message):
        await callback.answer(
            "Sabab kiritish uchun profilni qayta oching.",
            show_alert=True,
        )
        return

    await state.update_data(
        pending_block_user_id=telegram_user_id,
        pending_block_page=page,
        pending_block_reason=None,
        pending_block_reason_ready=False,
    )
    await state.set_state(PlayerBlockStates.waiting_for_reason)
    await callback.message.edit_text(
        texts.admin_player_block_reason_prompt_text(telegram_user_id)
    )
    await callback.answer()


@admin_router.message(
    StateFilter(PlayerBlockStates.waiting_for_reason),
    AdminAccessFilter(),
)
async def receive_player_block_reason(
    message: Message,
    state: FSMContext,
) -> None:
    if message.chat.type != "private":
        await message.answer(
            "Blok sababini faqat shaxsiy chatda kiritish mumkin."
        )
        return

    data = await state.get_data()
    telegram_user_id = data.get("pending_block_user_id")
    page = data.get("pending_block_page")
    if not isinstance(telegram_user_id, int) or not isinstance(page, int):
        await state.clear()
        await message.answer(
            "Bloklash so‘rovi eskirgan. O‘yinchi profilidan qayta boshlang."
        )
        return
    if message.from_user is None or message.from_user.id == telegram_user_id:
        await state.clear()
        await message.answer(texts.admin_player_self_block_error_text())
        return

    raw_reason = (message.text or "").strip()
    reason_command = raw_reason.split(maxsplit=1)[0].casefold() if raw_reason else ""
    reason = (
        None
        if reason_command == "/skip" or reason_command.startswith("/skip@")
        else raw_reason
    )
    if len(reason or "") > 500:
        await message.answer(texts.admin_player_block_reason_too_long_text())
        return

    await state.set_state(None)
    await state.update_data(
        pending_block_reason=reason,
        pending_block_reason_ready=True,
    )
    await message.answer(
        texts.admin_player_block_reason_confirmation_text(
            telegram_user_id,
            reason,
        ),
        reply_markup=admin_block_reason_confirmation_keyboard(
            telegram_user_id,
            page,
        ),
        parse_mode="HTML",
    )


@admin_router.callback_query(
    F.data.startswith("admin:block:do:"),
    AdminAccessFilter(),
)
async def block_player(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        _, _, _, user_id_value, page_value = callback.data.split(":")
        telegram_user_id = int(user_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    if telegram_user_id <= 0 or page < 0:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    if callback.from_user.id == telegram_user_id:
        await callback.answer(
            texts.admin_player_self_block_error_text(),
            show_alert=True,
        )
        return

    data = await state.get_data()
    reason = (
        data.get("pending_block_reason")
        if data.get("pending_block_user_id") == telegram_user_id
        and data.get("pending_block_reason_ready") is True
        else None
    )
    try:
        block_result = await _block_player(
            telegram_user_id,
            callback.from_user.id,
            reason,
        )
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin o‘yinchini bloklay olmadi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "O‘yinchini bloklab bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    await state.update_data(
        pending_block_user_id=None,
        pending_block_page=None,
        pending_block_reason=None,
        pending_block_reason_ready=False,
    )
    if isinstance(callback.message, Message):
        if block_result == "not_found":
            response = texts.admin_player_profile_not_found_text()
            keyboard = admin_players_keyboard([], page, 0)
        elif block_result == "already_blocked":
            response = texts.admin_player_already_blocked_text(
                telegram_user_id
            )
            keyboard = admin_player_profile_keyboard(
                page,
                telegram_user_id,
                blocked=True,
            )
        else:
            response = texts.admin_player_blocked_text(telegram_user_id)
            keyboard = admin_player_profile_keyboard(
                page,
                telegram_user_id,
                blocked=True,
            )
        await callback.message.edit_text(
            response,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer(
        "O‘yinchi bloklandi."
        if block_result == "blocked"
        else "O‘yinchi allaqachon bloklangan."
        if block_result == "already_blocked"
        else "O‘yinchi topilmadi."
    )


@admin_router.callback_query(
    F.data.startswith("admin:block:cancel:"),
    AdminAccessFilter(),
)
async def cancel_player_block(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        _, _, _, user_id_value, page_value = callback.data.split(":")
        telegram_user_id = int(user_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    data = await state.get_data()
    if data.get("pending_block_user_id") == telegram_user_id:
        await state.update_data(
            pending_block_user_id=None,
            pending_block_page=None,
            pending_block_reason=None,
            pending_block_reason_ready=False,
        )
    await _show_player_profile(callback, telegram_user_id, page)


@admin_router.callback_query(
    F.data.startswith("admin:unblock:"),
    AdminAccessFilter(),
)
async def unblock_player(callback: CallbackQuery) -> None:
    try:
        _, _, user_id_value, page_value = callback.data.split(":")
        telegram_user_id = int(user_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return
    if telegram_user_id <= 0 or page < 0:
        await callback.answer("O‘yinchi ma’lumoti noto‘g‘ri.", show_alert=True)
        return

    try:
        removed = await _unblock_player(telegram_user_id)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin o‘yinchini blokdan chiqara olmadi: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "O‘yinchini blokdan chiqarib bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    if isinstance(callback.message, Message):
        response = (
            texts.admin_player_unblocked_text(telegram_user_id)
            if removed
            else texts.admin_player_not_blocked_text(telegram_user_id)
        )
        await callback.message.edit_text(
            response,
            reply_markup=admin_player_profile_keyboard(
                page,
                telegram_user_id,
                blocked=False,
            ),
            parse_mode="HTML",
        )
    await callback.answer(
        "Blokdan chiqarildi." if removed else "O‘yinchi bloklanmagan."
    )


@admin_router.callback_query(F.data.startswith("admin:games:"), AdminAccessFilter())
async def show_active_games(callback: CallbackQuery) -> None:
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Noto‘g‘ri sahifa raqami.", show_alert=True)
        return
    if page < 0:
        await callback.answer("Noto‘g‘ri sahifa raqami.", show_alert=True)
        return
    await _show_active_games(callback, page)


@admin_router.callback_query(F.data.startswith("admin:game:"), AdminAccessFilter())
async def show_game_details(callback: CallbackQuery) -> None:
    try:
        _, _, game_id_value, page_value = callback.data.split(":")
        game_id = int(game_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("Noto‘g‘ri o‘yin ma’lumoti.", show_alert=True)
        return
    if game_id <= 0 or page < 0:
        await callback.answer("Noto‘g‘ri o‘yin ma’lumoti.", show_alert=True)
        return
    await _show_game_details(callback, game_id, page)


@admin_router.callback_query(
    F.data.startswith("admin:cancel:confirm:"),
    AdminAccessFilter(),
)
async def confirm_game_cancellation(callback: CallbackQuery) -> None:
    try:
        _, _, _, game_id_value, page_value = callback.data.split(":")
        game_id = int(game_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("Noto‘g‘ri o‘yin ma’lumoti.", show_alert=True)
        return
    if game_id <= 0 or page < 0:
        await callback.answer("Noto‘g‘ri o‘yin ma’lumoti.", show_alert=True)
        return

    try:
        async with get_sessionmaker()() as session:
            is_open = await session.scalar(
                select(Game.id).where(
                    Game.id == game_id,
                    Game.status.in_(("lobby", "active")),
                )
            )
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Admin bekor qilishni tasdiqlashda DB xatosi: "
            "game_id=%s, error_type=%s",
            game_id,
            type(error).__name__,
        )
        await callback.answer(
            "O‘yin holatini tekshirib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
        return

    if is_open is None:
        await callback.answer(
            "O‘yin allaqachon yakunlangan yoki bekor qilingan.",
            show_alert=True,
        )
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            texts.admin_cancel_confirmation_text(game_id),
            reply_markup=admin_cancel_confirmation_keyboard(game_id, page),
            parse_mode="HTML",
        )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("admin:cancel:do:"),
    AdminAccessFilter(),
)
async def cancel_game_from_admin(callback: CallbackQuery) -> None:
    try:
        _, _, _, game_id_value, page_value = callback.data.split(":")
        game_id = int(game_id_value)
        page = int(page_value)
    except ValueError:
        await callback.answer("Noto‘g‘ri o‘yin ma’lumoti.", show_alert=True)
        return
    if game_id <= 0 or page < 0:
        await callback.answer("Noto‘g‘ri o‘yin ma’lumoti.", show_alert=True)
        return

    status, _ = await cancel_open_game(game_id=game_id)
    if status == "error":
        await callback.answer(
            "O‘yinni bekor qilib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
        return
    if status == "not_open":
        await callback.answer(
            "O‘yin allaqachon yakunlangan yoki bekor qilingan.",
            show_alert=True,
        )
        return

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            texts.admin_game_cancelled_text(game_id),
            reply_markup=admin_game_detail_keyboard(
                game_id,
                page,
                cancellable=False,
            ),
            parse_mode="HTML",
        )
    await callback.answer("O‘yin bekor qilindi.")


@admin_router.callback_query(F.data == "admin:refresh", AdminAccessFilter())
async def refresh_admin_panel(callback: CallbackQuery) -> None:
    await _show_statistics(callback, edit=True)


@admin_router.callback_query(F.data == "admin:close", AdminAccessFilter())
async def close_admin_panel(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        closed_text = "Admin paneli yopildi."
        if callback.message.text != closed_text:
            await callback.message.edit_text(closed_text)
    await callback.answer()
