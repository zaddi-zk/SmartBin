from __future__ import annotations

import re
from typing import Any

from config import Config
from quota import (
    claim_usage,
    daily_limit,
    is_admin,
    is_banned,
    is_premium,
    is_unlimited,
    parse_premium_until,
    quota_snapshot,
    refund_usage,
    remaining,
    should_refund,
    user_id,
)


def _user_id(user: Any) -> int:
    return user_id(user)


def _bonus(user: Any) -> int:
    try:
        return max(0, int(user["bonus_check"] or 0))
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


def quota_decision(user: Any, kind: str, config: Config) -> dict[str, Any]:
    """Pure preview of the daily allowance (no DB write).

    Kept for callers/tests that only need to know whether a *next* use is
    possible. Real consumption goes through `claim_usage`, which is atomic.
    """
    limit = daily_limit(kind, config)
    bonus = _bonus(user) if kind == "check" else 0
    allowance = limit + bonus
    if is_admin(_user_id(user), config) or is_premium(user):
        return {"allowed": True, "consume": "premium", "limit": allowance}
    if is_banned(user):
        return {"allowed": False, "consume": "banned", "limit": allowance, "used": 0}
    used = int(user[f"free_{kind}"])
    credits = int(user["credits"])
    if used < limit:
        return {"allowed": True, "consume": "free", "limit": allowance, "used": used}
    if bonus > 0:
        return {"allowed": True, "consume": "bonus", "limit": allowance, "used": used}
    if credits > 0:
        return {"allowed": True, "consume": "credit", "limit": allowance, "used": used}
    return {"allowed": False, "consume": "paywall", "limit": allowance, "used": used}


async def consume_quota(db: Any, user_id: int, kind: str, consume: str) -> None:
    """Deprecated non-atomic helper, kept for older scripts.

    New code must use `claim_usage`, which cannot over-grant under concurrency.
    """
    if consume == "free":
        await db.execute(
            f"UPDATE users SET free_{kind}=free_{kind}+1 WHERE user_id=?", (user_id,)
        )
    elif consume == "credit":
        await db.execute(
            "UPDATE users SET credits=CASE WHEN credits>0 THEN credits-1 ELSE 0 END WHERE user_id=?",
            (user_id,),
        )


def parse_bin_arg(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) in (6, 8):
        return digits
    return None


def bin_key(bin_arg: str) -> str:
    return bin_arg[:8] if len(bin_arg) >= 8 else bin_arg


def free_watermark(user: Any, config: Config | None = None) -> bool:
    if config is not None and is_admin(_user_id(user), config):
        return False
    return not is_premium(user)


async def mark_first(db: Any, key: str) -> bool:
    if await db.stats_get(key, 0) == 0:
        await db.stats_bump(key)
        return True
    return False
