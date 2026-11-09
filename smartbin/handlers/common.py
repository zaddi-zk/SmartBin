from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from config import Config


def parse_premium_until(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_premium(user: Any) -> bool:
    until = parse_premium_until(user["premium_until"] if user is not None else "")
    return until is not None and until > datetime.now(timezone.utc)


def is_admin(user_id: int, config: Config) -> bool:
    return bool(config.admin_id) and int(user_id) == int(config.admin_id)


def _user_id(user: Any) -> int:
    try:
        return int(user["user_id"])
    except (KeyError, TypeError, ValueError):
        return 0


def quota_decision(user: Any, kind: str, config: Config) -> dict[str, Any]:
    """Returns allowed + what to consume. Paywall fires only after the free allowance."""
    limit = config.free_bins_per_day if kind == "bin" else config.free_checks_per_day
    if is_admin(_user_id(user), config) or is_premium(user):
        return {"allowed": True, "consume": "premium", "limit": limit}
    used = int(user[f"free_{kind}"])
    credits = int(user["credits"])
    if used < limit:
        return {"allowed": True, "consume": "free", "limit": limit, "used": used}
    if credits > 0:
        return {"allowed": True, "consume": "credit", "limit": limit, "used": used}
    return {"allowed": False, "consume": "paywall", "limit": limit, "used": used}


async def consume_quota(db: Any, user_id: int, kind: str, consume: str) -> None:
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
