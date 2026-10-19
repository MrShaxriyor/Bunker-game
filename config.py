"""Project-level environment configuration."""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env() -> None:
    """Load project settings from the root .env without replacing other env vars."""
    load_dotenv(dotenv_path=ENV_FILE, override=False)


def get_bot_token(*, prefer_project_env: bool = False) -> str:
    """Return the configured bot token without exposing it in diagnostics.

    Local bot startup prefers the root .env so a stale shell variable cannot
    silently select a different bot. Services without a .env can use the
    deployment environment variable as usual.
    """
    file_token = dotenv_values(ENV_FILE).get("BOT_TOKEN")
    env_token = os.getenv("BOT_TOKEN")
    candidates = (file_token, env_token) if prefer_project_env else (env_token, file_token)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise RuntimeError(
        f"BOT_TOKEN is missing or empty. Set it in {ENV_FILE} or the process environment."
    )
