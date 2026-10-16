import unittest

from ui.keyboards import (
    coin_package_keyboard,
    inventory_keyboard,
    shop_insufficient_funds_keyboard,
    shop_keyboard,
    shop_main_keyboard,
)
from ui.texts import (
    coin_packages_text,
    shop_home_text,
    shop_insufficient_funds_text,
)


class ShopUiTests(unittest.TestCase):
    @staticmethod
    def _rows(keyboard):
        return [[button for button in row] for row in keyboard.inline_keyboard]

    def test_shop_home_omits_unavailable_section_and_keeps_two_columns(self):
        keyboard = shop_main_keyboard(
            101,
            has_survival_items=False,
            webapp_url="https://gifts.example.org/app",
        )
        rows = self._rows(keyboard)
        texts = [[button.text for button in row] for row in rows]

        self.assertEqual(
            texts,
            [
                ["🎁 Para sovg‘alari", "🎒 Inventar"],
                ["🪙 Coin sotib olish", "⬅️ Asosiy menyu"],
            ],
        )
        self.assertEqual(rows[0][0].web_app.url, "https://gifts.example.org/app")
        self.assertEqual(rows[0][1].callback_data, "sh:i:101")
        self.assertEqual(rows[1][0].callback_data, "pay:l:101")
        self.assertEqual(rows[1][1].callback_data, "menu:home")

    def test_shop_home_includes_existing_survival_section(self):
        keyboard = shop_main_keyboard(
            202,
            has_survival_items=True,
            webapp_url=None,
        )
        labels = [button.text for row in keyboard.inline_keyboard for button in row]

        self.assertIn("🆘 Survival buyumlari", labels)
        self.assertNotIn("🏅 Kolleksiya va medallar", labels)
        para_button = next(button for row in keyboard.inline_keyboard for button in row
                           if button.text == "🎁 Para sovg‘alari")
        self.assertEqual(para_button.callback_data, "menu:gifts_unconfigured")

    def test_product_and_package_buttons_show_database_values_compactly(self):
        products = shop_keyboard([(7, "Med Kit", 1250)], 101)
        product_rows = self._rows(products)
        self.assertIn("1 250", product_rows[0][0].text)
        self.assertEqual(product_rows[0][0].callback_data, "sh:d:101:7")
        self.assertEqual(product_rows[-1][-1].callback_data, "sh:m:101")

        packages = coin_package_keyboard(
            [(1, "Small", 100, 10), (2, "Medium", 500, 45), (3, "Large", 1000, 80)],
            101,
        )
        package_rows = self._rows(packages)
        self.assertEqual(len(package_rows), 2)
        self.assertIn("🪙100", package_rows[0][0].text)
        self.assertIn("⭐10", package_rows[0][0].text)
        self.assertEqual(package_rows[0][0].callback_data, "pay:s:101:1")
        self.assertEqual(package_rows[-1][-1].callback_data, "sh:m:101")

    def test_inventory_back_and_insufficient_funds_navigation(self):
        self.assertEqual(
            inventory_keyboard(101).inline_keyboard[0][0].callback_data,
            "sh:m:101",
        )
        keyboard = shop_insufficient_funds_keyboard(101, 7)
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "pay:l:101")
        self.assertEqual(keyboard.inline_keyboard[0][1].callback_data, "sh:d:101:7")
        self.assertIn("1 000", shop_insufficient_funds_text(100, 1000))
        self.assertIn("🪙 Coin sotib olish", shop_insufficient_funds_text(100, 1000))

    def test_shop_copy_uses_provided_values_and_escapes_package_name(self):
        rendered = coin_packages_text([("<Small>", 1234, 56)])
        self.assertIn("&lt;Small&gt;", rendered)
        self.assertIn("1 234 coin", rendered)
        self.assertIn("56 Stars", rendered)
        self.assertIn("Balansingiz: <b>2 500</b>", shop_home_text(2500, 3))


if __name__ == "__main__":
    unittest.main()
