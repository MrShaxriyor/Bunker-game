import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

with patch.dict(os.environ, {"BOT_TOKEN": "123456:TEST_TOKEN"}):
    import main
from ui.start_menu import profile_text


class _Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class _Session:
    def __init__(self, scalars, rows):
        self._scalars = iter(scalars)
        self._rows = iter(rows)
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def scalar(self, statement):
        self.statements.append(statement)
        return next(self._scalars)

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(next(self._rows))


class ProfileDataTests(unittest.IsolatedAsyncioTestCase):
    async def _get_profile_data(self, user_id, scalars, rows):
        session = _Session(scalars, rows)
        session_factory = lambda: session
        with patch.object(main, "get_sessionmaker", return_value=session_factory):
            data = await main._get_profile_data(user_id)
        return data, session.statements

    async def test_new_user_has_zero_stats_and_no_para(self):
        data, statements = await self._get_profile_data(101, [0, 0], [None, None])

        self.assertEqual(data, (0, 0, None, None))
        self.assertEqual(len(statements), 4)

    async def test_participant_and_winner_uses_own_telegram_id(self):
        data, statements = await self._get_profile_data(
            202,
            [2, 1],
            [
                SimpleNamespace(status="active", current_round=3),
                SimpleNamespace(display_name="Snapshot Partner", username="partner"),
            ],
        )

        self.assertEqual(
            data,
            (
                2,
                1,
                ("active", 3),
                ("Snapshot Partner", "partner"),
            ),
        )
        compiled = [
            statement.compile(dialect=postgresql.dialect())
            for statement in statements
        ]
        self.assertIn("count(distinct(game_players.game_id))", str(compiled[0]))
        self.assertIn("games.first_winner_id", str(compiled[1]))
        self.assertIn("games.second_winner_id", str(compiled[1]))
        self.assertIn("para_members.ended_at IS NULL", str(compiled[3]))
        self.assertIn("para_members_1.ended_at IS NULL", str(compiled[3]))
        for query in compiled:
            self.assertIn(202, query.params.values())

    def test_profile_text_uses_database_values_without_demo_stats(self):
        rendered = profile_text(
            "Test User",
            "test_user",
            202,
            total_games=2,
            wins=1,
            latest_game=("active", 3),
            para_partner=("Snapshot Partner", "partner"),
        )

        self.assertIn("O‘yinlar soni: <b>2</b>", rendered)
        self.assertIn("G‘alabalar: <b>1</b>", rendered)
        self.assertIn(
            "<b>Snapshot Partner</b> (@partner)",
            rendered,
        )
        self.assertNotIn("850", rendered)
        self.assertNotIn("demo", rendered.lower())


if __name__ == "__main__":
    unittest.main()
