"""Telegram WebApp initData authentication."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AUTH_AGE_SECONDS = 24 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 30
MAX_INIT_DATA_LENGTH = 8192


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: int | None = None,
) -> int:
    """Verify Telegram's WebApp HMAC and return its authenticated user ID."""
    if (
        not init_data
        or len(init_data) > MAX_INIT_DATA_LENGTH
        or not bot_token
    ):
        raise ValueError("Invalid Telegram initData.")

    try:
        pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=32,
        )
    except ValueError:
        raise ValueError("Invalid Telegram initData.") from None

    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("Invalid Telegram initData.")
        fields[key] = value

    supplied_hash = fields.pop("hash", None)
    if supplied_hash is None or len(supplied_hash) != 64:
        raise ValueError("Invalid Telegram initData.")

    data_check_string = "\n".join(
        f"{key}={fields[key]}" for key in sorted(fields)
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_hash.lower(), expected_hash):
        raise ValueError("Invalid Telegram initData.")

    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("Invalid Telegram initData.") from None

    current_time = int(time.time()) if now is None else now
    if (
        auth_date <= 0
        or auth_date > current_time + MAX_CLOCK_SKEW_SECONDS
        or current_time - auth_date > MAX_AUTH_AGE_SECONDS
        or not isinstance(user, dict)
        or type(user.get("id")) is not int
        or not 0 < user["id"] <= 2**63 - 1
    ):
        raise ValueError("Invalid Telegram initData.")

    return user["id"]
