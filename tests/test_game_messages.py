import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.lobby import Lobby, lobbies, send_card_announcement, start_round

from ui.texts import (
    card_announcement_text,
    confirmation_text,
    player_name_html,
    round_started_text,
    winners_text,
)


class GamePlayerMessageTests(unittest.TestCase):
    def test_valid_username_links_to_telegram_profile(self):
        self.assertEqual(
            player_name_html("Player", "@player_123"),
            '<a href="https://t.me/player_123"><b>Player</b></a>',
        )

    def test_missing_or_invalid_username_is_safe_plain_bold_text(self):
        self.assertEqual(player_name_html("Player <One>"), "<b>Player &lt;One&gt;</b>")
        self.assertNotIn("tg://user", player_name_html("Player", "bad/name"))

    def test_living_roster_has_no_private_user_entity_without_username(self):
        text = round_started_text(2, [(101, "Alice & Bob", None)])
        self.assertIn("<b>Alice &amp; Bob</b>", text)
        self.assertNotIn("tg://user?id=101", text)
        self.assertNotIn("<a href=", text)

    def test_living_roster_links_only_valid_public_usernames(self):
        text = round_started_text(
            2,
            [
                (101, "<Alice>", "alice_123"),
                (102, "Bob & Carol", None),
            ],
        )
        self.assertIn(
            '<a href="https://t.me/alice_123"><b>&lt;Alice&gt;</b></a>',
            text,
        )
        self.assertIn("<b>Bob &amp; Carol</b>", text)
        self.assertNotIn("tg://user?id=", text)

    def test_card_announcement_uses_same_safe_profile_format(self):
        roster = round_started_text(1, [(101, "<Alice>", "alice_123")])
        announcement = card_announcement_text(
            "<Alice>", "kasb", "Tadqiqotchi", username="alice_123"
        )
        profile_link = '<a href="https://t.me/alice_123"><b>&lt;Alice&gt;</b></a>'
        self.assertIn(profile_link, roster)
        self.assertIn(profile_link, announcement)
        self.assertNotIn("tg://user", roster + announcement)

    def test_other_game_player_messages_do_not_use_private_user_links(self):
        winners = winners_text(
            [(101, "<Alice>", "alice_123"), (102, "Bob & Carol", None)]
        )
        confirmation = confirmation_text("<Alice>", "alice_123")
        plain_confirmation = confirmation_text("Bob & Carol", None)

        self.assertIn('href="https://t.me/alice_123"', winners)
        self.assertIn("<b>Bob &amp; Carol</b>", winners)
        self.assertIn('href="https://t.me/alice_123"', confirmation)
        self.assertIn("Bob &amp; Carol", plain_confirmation)
        self.assertNotIn("tg://user?id=", winners + confirmation + plain_confirmation)


class GamePlayerMessageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_announcement_disables_link_previews(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        await send_card_announcement(
            bot,
            -10077,
            "<Alice>",
            "kasb",
            "Tadqiqotchi",
            username="alice_123",
        )

        kwargs = bot.send_message.await_args.kwargs
        self.assertTrue(kwargs["link_preview_options"].is_disabled)
        self.assertIn('href="https://t.me/alice_123"', kwargs["text"])
        self.assertIn("&lt;Alice&gt;", kwargs["text"])

    async def test_round_roster_disables_link_previews(self):
        lobby = Lobby(chat_id=-10078, host_id=1, started=True, phase="lobby")
        lobby.players = {1: "Alice"}
        lobby.usernames = {1: "alice_123"}
        lobbies[lobby.chat_id] = lobby
        bot = SimpleNamespace(send_message=AsyncMock())
        with (
            patch("handlers.lobby._persist_round_start", new=AsyncMock(return_value=True)),
            patch("handlers.lobby.start_discussion", new=AsyncMock()),
        ):
            await start_round(lobby.chat_id, bot)

        kwargs = bot.send_message.await_args.kwargs
        self.assertTrue(kwargs["link_preview_options"].is_disabled)
        self.assertIn('href="https://t.me/alice_123"', bot.send_message.await_args.args[1])

    async def asyncTearDown(self):
        lobbies.clear()
