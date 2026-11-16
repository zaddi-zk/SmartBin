"""Telegram WebApp ``initData`` verification (section 18).

The mini app authenticates with the signed payload Telegram injects into
``WebApp.initData``. The signature is HMAC-SHA256 over the sorted key/value
pairs, keyed with ``HMAC("WebAppData", bot_token)``. A header or query value is
never trusted without that check.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl


def verify_init_data(
    init_data: str, bot_token: str, max_age_s: int = 86_400
) -> dict[str, Any] | None:
    """Return the Telegram user dict, or ``None`` when the payload is invalid."""
    if not init_data or not bot_token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        return None

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0") or 0)
    except ValueError:
        return None
    if max_age_s > 0 and abs(time.time() - auth_date) > max_age_s:
        return None

    try:
        user = json.loads(pairs.get("user", ""))
    except ValueError:
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None
    return user


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a valid payload. Test helper and a way to mint local fixtures."""
    pairs = dict(fields)
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return "&".join(f"{key}={value}" for key, value in pairs.items()) + f"&hash={digest}"
