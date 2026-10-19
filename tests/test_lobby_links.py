import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import DeleteMessage, PinChatMessage
from aiogram.types import CallbackQuery, Chat, Message, User
from database.models import Game, GamePlayer
from handlers.lobby import (
    Lobby,
    create_lobby,
    is_current_group_admin,
    join_lobby_by_game_id,
    lobbies,
    parse_join_game_payload,
    process_join_deep_link,
    start_lobby,
)


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _JoinSession:
    def __init__(self, game, player_ids):
        self.game = game
        self.player_ids = set(player_ids)
        self.pending = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Transaction()

    async def scalar(self, statement):
        if statement.column_descriptions[0]["entity"] is Game:
            return self.game if self.game.status == "lobby" else None
        raise AssertionError("Unexpected scalar query")

    async def scalars(self, statement):
        if statement.column_descriptions[0]["entity"] is not GamePlayer:
            raise AssertionError("Unexpected scalars query")
        return _Rows(self.player_ids)

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        self.player_ids.update(row.telegram_user_id for row in self.pending)


class _CreateSession:
    def __init__(self, previous_message_id=None):
        self.previous_message_id = previous_message_id
        self.scalar_calls = 0
        self.game = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Transaction()

    async def scalar(self, _statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return None  # No currently open game in this group.
        return self.previous_message_id

    def add(self, row):
        if isinstance(row, Game):
            row.id = 702
            self.game = row

    async def flush(self):
        return None

    async def execute(self, _statement):
        return SimpleNamespace(rowcount=1)


class _SequentialCreateState:
    def __init__(self):
        self.game = None
        self.players = []
        self.previous_message_id = 55
        self.next_game_id = 702


class _SequentialCreateSession:
    def __init__(self, state):
        self.state = state
        self.pending_players = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Transaction()

    async def scalar(self, statement):
        expression = statement.column_descriptions[0]["expr"]
        if expression is Game:
            if self.state.game and self.state.game.status in {"lobby", "active"}:
                return self.state.game
            return None
        if expression is Game.lobby_message_id:
            return self.state.previous_message_id
        raise AssertionError(f"Unexpected scalar expression {expression}")

    async def scalars(self, statement):
        if statement.column_descriptions[0]["entity"] is not GamePlayer:
            raise AssertionError("Unexpected scalars query")
        return _Rows(self.state.players)

    def add(self, row):
        if isinstance(row, Game):
            row.id = self.state.next_game_id
            self.state.next_game_id += 1
            self.state.game = row
        elif isinstance(row, GamePlayer):
            self.pending_players.append(row)

    async def flush(self):
        for player in self.pending_players:
            player.game_id = player.game.id
            self.state.players.append(player)
        self.pending_players.clear()

    async def execute(self, statement):
        if self.state.game is None or self.state.game.status != "lobby":
            return SimpleNamespace(rowcount=0)
        self.state.game.lobby_message_id = statement.compile().params[
            "lobby_message_id"
        ]
        return SimpleNamespace(rowcount=1)


class LobbyLinkTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        lobbies.clear()

    def test_start_payload_accepts_only_positive_game_ids_within_telegram_limit(self):
        self.assertEqual(parse_join_game_payload("join_702"), 702)
        self.assertEqual(parse_join_game_payload(f"join_{2**63 - 1}"), 2**63 - 1)
        self.assertIsNone(parse_join_game_payload(None))
        self.assertIsNone(parse_join_game_payload("join_0"))
        self.assertIsNone(parse_join_game_payload("join_-2"))
        self.assertIsNone(parse_join_game_payload("join_7.extra"))
        self.assertIsNone(parse_join_game_payload("join_" + "7" * 65))
        self.assertIsNone(parse_join_game_payload(f"join_{2**63}"))

    def test_lobby_join_button_is_deep_link_and_start_callback_is_preserved(self):
        lobby = Lobby(chat_id=-10077, host_id=12, game_id=702, bot_username="BunkerTestBot")
        from handlers.lobby import lobby_keyboard

        markup = lobby_keyboard(lobby)
        join_button, start_button = [row[0] for row in markup.inline_keyboard]
        self.assertEqual(
            join_button.url,
            "https://t.me/BunkerTestBot?start=join_702",
        )
        self.assertIsNone(join_button.callback_data)
        self.assertEqual(start_button.callback_data, "bunker:start:-10077")

    async def test_deep_link_join_uses_game_id_and_updates_existing_lobby(self):
        game = SimpleNamespace(id=702, chat_id=-10077, status="lobby")
        session = _JoinSession(game, {12})
        lobby = Lobby(
            chat_id=-10077,
            host_id=12,
            game_id=702,
            bot_username="BunkerTestBot",
            players={12: "Host"},
            usernames={12: None},
            message_id=40,
        )
        lobbies[lobby.chat_id] = lobby
        user = SimpleNamespace(id=44, full_name="Guest", username="guest")
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=user,
            bot=SimpleNamespace(edit_message_text=AsyncMock()),
            answer=AsyncMock(),
        )

        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: session),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
        ):
            await process_join_deep_link(message, "join_702")

        self.assertIn(44, lobby.players)
        self.assertEqual(lobby.players[44], "Guest")
        message.bot.edit_message_text.assert_awaited_once()
        self.assertIn("qo‘shildingiz", message.answer.await_args.args[0])

    async def test_repeat_invalid_ended_and_full_join_states_are_reported(self):
        invalid_message = SimpleNamespace(answer=AsyncMock(), chat=SimpleNamespace(type="private"))
        await process_join_deep_link(invalid_message, "join_bad")
        self.assertIn("noto‘g‘ri", invalid_message.answer.await_args.args[0])

        ended = Lobby(chat_id=-1, host_id=1, game_id=2, started=True)
        lobbies[-1] = ended
        ended_session = _JoinSession(SimpleNamespace(id=2, chat_id=-1, status="active"), {1})
        with patch("handlers.lobby.get_sessionmaker", return_value=lambda: ended_session):
            status, _ = await join_lobby_by_game_id(2, SimpleNamespace(id=3))
        self.assertEqual(status, "unavailable")

        lobby = Lobby(chat_id=-2, host_id=1, game_id=3, players={1: "Host"})
        lobbies[-2] = lobby
        user = SimpleNamespace(id=1, full_name="Host", username=None)
        duplicate_message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=user,
            bot=SimpleNamespace(edit_message_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: _JoinSession(
                SimpleNamespace(id=3, chat_id=-2, status="lobby"), {1}
            )),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
        ):
            await process_join_deep_link(duplicate_message, "join_3")
        self.assertIn("allaqachon", duplicate_message.answer.await_args.args[0])

        lobby.players = {index: str(index) for index in range(16)}
        full_session = _JoinSession(SimpleNamespace(id=3, chat_id=-2, status="lobby"), set(lobby.players))
        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: full_session),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
        ):
            status, _ = await join_lobby_by_game_id(3, SimpleNamespace(id=99, full_name="Full", username=None))
        self.assertEqual(status, "full")

    async def test_invalid_game_id_and_non_lobby_game_are_rejected(self):
        user = SimpleNamespace(id=99, full_name="Guest", username=None)
        with patch("handlers.lobby.get_sessionmaker") as get_sessionmaker:
            status, lobby = await join_lobby_by_game_id(9999, user)
        self.assertEqual(status, "unavailable")
        self.assertIsNone(lobby)
        get_sessionmaker.assert_not_called()

        lobby = Lobby(chat_id=-3, host_id=1, game_id=8)
        lobbies[-3] = lobby
        session = _JoinSession(SimpleNamespace(id=8, chat_id=-3, status="completed"), {1})
        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: session),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
        ):
            status, _ = await join_lobby_by_game_id(8, user)
        self.assertEqual(status, "unavailable")

    async def test_current_group_owner_and_admin_are_allowed_but_member_is_not(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        for role, expected in (("creator", True), ("administrator", True), ("member", False)):
            bot.get_chat_member.return_value = SimpleNamespace(status=role)
            self.assertEqual(await is_current_group_admin(bot, -10077, 44), expected)
        bot.get_chat_member.assert_awaited_with(chat_id=-10077, user_id=44)

    async def test_regular_member_start_callback_is_rejected_before_database_access(self):
        lobby = Lobby(chat_id=-10077, host_id=12, game_id=702, players={12: "Host"})
        lobbies[-10077] = lobby
        bot = Bot(token="123456:TEST_TOKEN")
        message = Message(
            message_id=99,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-10077, type="supergroup"),
            from_user=User(id=44, is_bot=False, first_name="Member"),
            text="Lobby",
        )
        callback = CallbackQuery(
            id="callback-id",
            from_user=User(id=44, is_bot=False, first_name="Member"),
            chat_instance="chat-instance",
            message=message,
            data="bunker:start:-10077",
        ).as_(bot)
        with (
            patch.object(Bot, "get_chat_member", new=AsyncMock(return_value=SimpleNamespace(status="member"))) as get_member,
            patch.object(CallbackQuery, "answer", new=AsyncMock()) as answer,
            patch("handlers.lobby.get_sessionmaker") as get_sessionmaker,
        ):
            await start_lobby(callback)
        get_member.assert_awaited_once_with(chat_id=-10077, user_id=44)
        answer.assert_awaited_once()
        self.assertIn("faqat guruh egasi", answer.await_args.args[0])
        get_sessionmaker.assert_not_called()
        await bot.session.close()

    async def test_pin_persist_then_delete_old_registration_message(self):
        events = []
        sent = SimpleNamespace(message_id=99, pin=AsyncMock(), delete=AsyncMock())
        sent.pin.side_effect = lambda **_: events.append("pin")
        session = _CreateSession(previous_message_id=55)
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="BunkerTestBot")),
            delete_message=AsyncMock(side_effect=lambda **_: events.append("delete_old")),
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10077, type="supergroup"),
            from_user=SimpleNamespace(id=12, full_name="Host", username=None),
            bot=bot,
            answer=AsyncMock(return_value=sent),
        )
        original_execute = session.execute

        async def record_execute(statement):
            events.append("persist_message_id")
            return await original_execute(statement)

        session.execute = record_execute
        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: session),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
        ):
            await create_lobby(message)

        self.assertEqual(events, ["pin", "persist_message_id", "delete_old"])
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].url, "https://t.me/BunkerTestBot?start=join_702")
        sent.pin.assert_awaited_once_with(disable_notification=True)
        bot.delete_message.assert_awaited_once_with(chat_id=-10077, message_id=55)

    async def test_sequential_game_commands_replace_only_the_previous_bot_post(self):
        events = []
        state = _SequentialCreateState()
        sent_messages = []
        for message_id in (99, 100):
            sent = SimpleNamespace(message_id=message_id, pin=AsyncMock())
            sent.pin.side_effect = lambda current_id=message_id, **_: events.append(
                f"pin:{current_id}"
            )
            sent_messages.append(sent)
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="BunkerTestBot")),
            delete_message=AsyncMock(
                side_effect=lambda *, chat_id, message_id: events.append(
                    f"delete:{message_id}"
                )
            ),
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10077, type="group"),
            from_user=SimpleNamespace(id=12, full_name="Host", username=None),
            bot=bot,
            answer=AsyncMock(side_effect=sent_messages),
        )
        session_factory = lambda: _SequentialCreateSession(state)

        with (
            patch("handlers.lobby.get_sessionmaker", return_value=session_factory),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
        ):
            await create_lobby(message)
            await create_lobby(message)

        self.assertEqual(state.game.id, 702)
        self.assertEqual(state.game.lobby_message_id, 100)
        self.assertEqual(events, ["pin:99", "delete:55", "pin:100", "delete:99"])
        self.assertEqual(bot.delete_message.await_count, 2)
        self.assertEqual(message.answer.await_count, 2)
        self.assertEqual(len(lobbies[-10077].players), 1)

    async def test_pin_failure_while_refreshing_keeps_existing_post_and_database_id(self):
        state = _SequentialCreateState()
        state.game = SimpleNamespace(
            id=702,
            chat_id=-10077,
            host_telegram_id=12,
            status="lobby",
            lobby_message_id=55,
        )
        state.players = [SimpleNamespace(
            telegram_user_id=12,
            name="Host",
            username=None,
            id=1,
        )]
        lobby = Lobby(
            chat_id=-10077,
            host_id=12,
            game_id=702,
            bot_username="BunkerTestBot",
            players={12: "Host"},
            message_id=55,
        )
        lobbies[-10077] = lobby
        sent = SimpleNamespace(message_id=99, pin=AsyncMock(), delete=AsyncMock())
        sent.pin.side_effect = TelegramAPIError(
            method=PinChatMessage(chat_id=-10077, message_id=99),
            message="not enough rights",
        )
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="BunkerTestBot")),
            delete_message=AsyncMock(),
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10077, type="group"),
            from_user=SimpleNamespace(id=12, full_name="Host", username=None),
            bot=bot,
            answer=AsyncMock(return_value=sent),
        )

        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: _SequentialCreateSession(state)),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
            patch("handlers.lobby.cancel_open_game", new=AsyncMock()) as cancel,
        ):
            await create_lobby(message)

        cancel.assert_not_awaited()
        bot.delete_message.assert_not_awaited()
        sent.delete.assert_awaited_once()
        self.assertEqual(state.game.lobby_message_id, 55)
        self.assertEqual(lobbies[-10077].message_id, 55)
        self.assertIn("pin", message.answer.await_args_list[-1].args[0])

    async def test_missing_old_registration_message_is_handled_as_already_deleted(self):
        sent = SimpleNamespace(message_id=99, pin=AsyncMock(), delete=AsyncMock())
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="BunkerTestBot")),
            delete_message=AsyncMock(side_effect=TelegramBadRequest(
                method=DeleteMessage(chat_id=-10077, message_id=55),
                message="Bad Request: message to delete not found",
            )),
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10077, type="group"),
            from_user=SimpleNamespace(id=12, full_name="Host", username=None),
            bot=bot,
            answer=AsyncMock(return_value=sent),
        )
        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: _CreateSession(previous_message_id=55)),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
        ):
            await create_lobby(message)

        bot.delete_message.assert_awaited_once_with(chat_id=-10077, message_id=55)
        self.assertEqual(message.answer.await_count, 1)
        self.assertEqual(lobbies[-10077].message_id, 99)

    async def test_pin_failure_cancels_new_lobby_and_does_not_keep_ghost_message(self):
        sent = SimpleNamespace(message_id=100, delete=AsyncMock(), pin=AsyncMock())
        sent.pin.side_effect = TelegramAPIError(
            method=PinChatMessage(chat_id=-10077, message_id=100),
            message="permission denied",
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10077, type="group"),
            from_user=SimpleNamespace(id=12, full_name="Host", username=None),
            bot=SimpleNamespace(
                get_me=AsyncMock(return_value=SimpleNamespace(username="BunkerTestBot")),
            ),
            answer=AsyncMock(return_value=sent),
        )
        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: _CreateSession()),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
            patch("handlers.lobby.cancel_open_game", new=AsyncMock(return_value=("cancelled", -10077))) as cancel,
        ):
            await create_lobby(message)

        cancel.assert_awaited_once_with(game_id=702)
        sent.delete.assert_awaited_once()
        self.assertNotIn(-10077, lobbies)
        self.assertIn("pin", message.answer.await_args_list[-1].args[0])

    async def test_previous_message_delete_failure_keeps_new_pinned_lobby(self):
        sent = SimpleNamespace(message_id=99, pin=AsyncMock(), delete=AsyncMock())
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="BunkerTestBot")),
            delete_message=AsyncMock(side_effect=TelegramAPIError(
                method=DeleteMessage(chat_id=-10077, message_id=55),
                message="permission denied",
            )),
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10077, type="group"),
            from_user=SimpleNamespace(id=12, full_name="Host", username=None),
            bot=bot,
            answer=AsyncMock(return_value=sent),
        )
        with (
            patch("handlers.lobby.get_sessionmaker", return_value=lambda: _CreateSession(previous_message_id=55)),
            patch("handlers.lobby.lock_user_for_block_check", new=AsyncMock()),
            patch("handlers.lobby.is_user_blocked", new=AsyncMock(return_value=False)),
            patch("handlers.lobby.cancel_open_game", new=AsyncMock()) as cancel,
        ):
            await create_lobby(message)

        cancel.assert_not_awaited()
        self.assertIn(-10077, lobbies)
        self.assertTrue(lobbies[-10077].message_id == 99)
        self.assertIn("o‘chirish", message.answer.await_args_list[-1].args[0])


if __name__ == "__main__":
    unittest.main()
