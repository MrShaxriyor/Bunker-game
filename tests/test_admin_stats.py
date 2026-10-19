import unittest

from ui.texts import admin_stats_text


class AdminStatisticsTextTests(unittest.TestCase):
    def test_stats_include_game_and_registration_counts(self):
        result = admin_stats_text(9, 1, 2, 4, 2, 12, 30, 20, 3)
        self.assertIn("Noyob o‘yinchilar: <b>12</b>", result)
        self.assertIn("Ro‘yxatdan o‘tganlar: <b>20</b>", result)
        self.assertIn("Bugun ro‘yxatdan o‘tganlar (Toshkent): <b>3</b>", result)
