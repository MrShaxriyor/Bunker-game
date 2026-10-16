import unittest

from ui.start_menu import main_menu_keyboard


class MainMenuWebAppTests(unittest.TestCase):
    def _buttons(self, keyboard):
        return [button for row in keyboard.inline_keyboard for button in row]

    def test_https_url_creates_webapp_button_and_preserves_existing_buttons(self):
        buttons = self._buttons(
            main_menu_keyboard("bunker_bot", "https://gifts.example.org/app")
        )
        by_text = {button.text: button for button in buttons}

        self.assertEqual(
            set(by_text),
            {
                "🎮 Bunker o‘yini",
                "🛒 Shop",
                "🪙 Coin sotib olish",
                "👤 Profil",
                "🎁 Para Gifts",
                "📖 Qo‘llanma",
            },
        )
        self.assertEqual(
            [[button.text for button in row] for row in main_menu_keyboard(
                "bunker_bot", "https://gifts.example.org/app"
            ).inline_keyboard],
            [
                ["🎮 Bunker o‘yini", "🛒 Shop"],
                ["🪙 Coin sotib olish", "👤 Profil"],
                ["🎁 Para Gifts", "📖 Qo‘llanma"],
            ],
        )
        self.assertEqual(
            by_text["🎁 Para Gifts"].web_app.url,
            "https://gifts.example.org/app",
        )
        self.assertEqual(by_text["📖 Qo‘llanma"].callback_data, "menu:guide")
        self.assertEqual(by_text["👤 Profil"].callback_data, "menu:profile")
        self.assertEqual(by_text["🛒 Shop"].callback_data, "menu:shop")
        self.assertEqual(by_text["🪙 Coin sotib olish"].callback_data, "menu:buycoins")
        self.assertEqual(
            by_text["🎮 Bunker o‘yini"].url,
            "https://t.me/bunker_bot?startgroup=true",
        )

    def test_missing_or_nonproduction_url_never_creates_webapp_button(self):
        for url in (None, "", "http://gifts.example.org", "https://localhost:5173"):
            with self.subTest(url=url):
                buttons = self._buttons(main_menu_keyboard("bunker_bot", url))
                gifts_button = next(button for button in buttons if button.text == "🎁 Para Gifts")
                self.assertIsNone(gifts_button.web_app)
                self.assertIsNone(gifts_button.url)
                self.assertEqual(
                    gifts_button.callback_data,
                    "menu:gifts_unconfigured",
                )
                self.assertIn("📖 Qo‘llanma", [button.text for button in buttons])
                self.assertIn("👤 Profil", [button.text for button in buttons])
                self.assertIn("🛒 Shop", [button.text for button in buttons])
                self.assertIn("🪙 Coin sotib olish", [button.text for button in buttons])
                self.assertEqual(
                    [[button.text for button in row] for row in main_menu_keyboard(
                        "bunker_bot", url
                    ).inline_keyboard],
                    [
                        ["🎮 Bunker o‘yini", "🛒 Shop"],
                        ["🪙 Coin sotib olish", "👤 Profil"],
                        ["🎁 Para Gifts", "📖 Qo‘llanma"],
                    ],
                )


if __name__ == "__main__":
    unittest.main()
