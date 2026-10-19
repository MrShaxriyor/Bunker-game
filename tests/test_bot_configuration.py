import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
import telegram_runtime


class BotConfigurationTests(unittest.TestCase):
    def test_local_bot_prefers_root_env_over_stale_process_value(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text("BOT_TOKEN=project-token\n", encoding="utf-8")
            with (
                patch.object(config, "ENV_FILE", env_file),
                patch.dict(os.environ, {"BOT_TOKEN": "stale-process-token"}),
            ):
                self.assertEqual(
                    config.get_bot_token(prefer_project_env=True), "project-token"
                )

    def test_deployment_environment_token_is_supported(self):
        with (
            patch.object(config, "ENV_FILE", Path("missing-test-env")),
            patch.dict(os.environ, {"BOT_TOKEN": "deployment-token"}),
        ):
            self.assertEqual(config.get_bot_token(), "deployment-token")

    def test_missing_or_empty_token_has_actionable_error_without_value(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text("BOT_TOKEN=   \n", encoding="utf-8")
            with (
                patch.object(config, "ENV_FILE", env_file),
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "BOT_TOKEN is missing or empty"):
                    config.get_bot_token()


class TelegramRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_verification_logs_only_username_and_id(self):
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(
            username="ExampleBot", id=12345
        )))
        with self.assertLogs(telegram_runtime.logger, level=logging.INFO) as captured:
            result = await telegram_runtime.verify_bot_identity(bot)

        bot.get_me.assert_awaited_once_with()
        self.assertEqual(result.id, 12345)
        self.assertIn("@ExampleBot", captured.output[0])
        self.assertIn("12345", captured.output[0])

    async def test_polling_conflict_is_reported_and_not_retried(self):
        class Conflict(Exception):
            pass

        dispatcher = SimpleNamespace(start_polling=AsyncMock(side_effect=Conflict()))
        with patch.object(telegram_runtime, "TelegramConflictError", Conflict):
            with self.assertLogs(telegram_runtime.logger, level=logging.ERROR) as captured:
                with self.assertRaises(SystemExit) as raised:
                    await telegram_runtime.start_polling(dispatcher, object())

        dispatcher.start_polling.assert_awaited_once()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("another process is already calling getUpdates", captured.output[0])
