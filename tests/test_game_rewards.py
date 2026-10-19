import unittest

from handlers.lobby import calculate_game_reward_amounts


class GameRewardTests(unittest.TestCase):
    def test_winners_get_30_and_other_actual_participants_get_15(self):
        rewards = calculate_game_reward_amounts({1, 2, 3, 4}, 1, 2)
        self.assertEqual(rewards, {1: 30, 2: 30, 3: 15, 4: 15})

    def test_reward_plan_rejects_winner_outside_game(self):
        with self.assertRaises(ValueError):
            calculate_game_reward_amounts({1, 2}, 1, 3)

    def test_aborted_game_without_winners_has_no_reward_plan(self):
        with self.assertRaises(ValueError):
            calculate_game_reward_amounts({1, 2}, None, None)
