from __future__ import annotations

"""Daily free-quota engine.

One rule, enforced everywhere (bot commands, inline mode, Mini App):
a non-premium account gets `FREE_BINS_PER_DAY` BIN lookups and
`FREE_CHECKS_PER_DAY` card checks per UTC day. After that it must spend a
credit per use; with no credits left the request is refused.

The claim itself is a single conditional UPDATE, so two simultaneous taps
can never both take the last free unit.
"""

from datetime import datetime, timezone
from typing import Any

from models import utc_date

KINDS = ("bin", "check")


def parse_premium_until(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def is_premium(user: Any) -> bool:
    if user is None:
        return False
    try:
        raw = user["premium_until"]
    except (KeyError, TypeError, IndexError):
        return False
    until = parse_premium_until(raw)
    return until is not None and until > datetime.now(timezone.utc)


def user_id(user: Any) -> int:
    try:
        return int(user["user_id"])
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


def is_admin(uid: Any, config: Any) -> bool:
    try:
        admin_id = int(getattr(config, "admin_id", 0) or 0)
    except (TypeError, ValueError):
        return False
    try:
        return admin_id != 0 and int(uid) == admin_id
    except (TypeError, ValueError):
        return False


def is_banned(user: Any) -> bool:
    if user is None:
        return False
    try:
        return int(user["is_banned"] or 0) != 0
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def daily_limit(kind: str, config: Any) -> int:
    if kind == "bin":
        return max(0, int(getattr(config, "free_bins_per_day", 3)))
    if kind == "check":
        return max(0, int(getattr(config, "free_checks_per_day", 2)))
    raise ValueError(f"unknown quota kind: {kind}")


def is_unlimited(user: Any, config: Any) -> bool:
    """Admins and active premium subscribers never hit the daily counters."""
    return is_admin(user_id(user), config) or is_premium(user)


def _used(user: Any, kind: str) -> int:
    if user is None:
        return 0
    try:
        return max(0, int(user[f"free_{kind}"] or 0))
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


def _credits(user: Any) -> int:
    if user is None:
        return 0
    try:
        return max(0, int(user["credits"] or 0))
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


def _bonus_checks(user: Any) -> int:
    if user is None:
        return 0
    try:
        return max(0, int(user["bonus_check"] or 0))
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


def _allowance(user: Any, kind: str, config: Any) -> int:
    """Free units available today, including referral bonus checks."""
    limit = daily_limit(kind, config)
    if kind == "check":
        limit += _bonus_checks(user)
    return limit


def remaining(user: Any, kind: str, config: Any) -> int | None:
    """Usable uses left today, or None when the account is unlimited."""
    if is_unlimited(user, config):
        return None
    if is_banned(user):
        return 0
    return max(0, _allowance(user, kind, config) - _used(user, kind)) + _credits(user)


async def claim_usage(db: Any, user: Any, kind: str, config: Any) -> dict[str, Any]:
    """Reserve exactly one use, atomically.

    Returns {"allowed", "consume", "limit", "used", "kind", "limit_reached"}.
    `consume` is one of: premium | free | credit | paywall | banned.
    """
    limit = daily_limit(kind, config)
    uid = user_id(user)
    used = _used(user, kind)
    base = {"kind": kind, "limit": limit, "used": used, "limit_reached": False}

    if is_admin(uid, config):
        return {**base, "allowed": True, "consume": "premium"}
    if is_banned(user):
        return {**base, "allowed": False, "consume": "banned", "limit_reached": True}
    if is_premium(user):
        return {**base, "allowed": True, "consume": "premium"}

    if uid and await db.claim_free(uid, kind, limit):
        return {**base, "allowed": True, "consume": "free", "used": used + 1}
    if kind == "check" and uid and await db.claim_bonus_check(uid):
        # Referral/promo checks are a finite balance, spent before credits.
        return {**base, "allowed": True, "consume": "bonus"}
    if uid and await db.claim_credit(uid):
        return {**base, "allowed": True, "consume": "credit"}
    return {**base, "allowed": False, "consume": "paywall", "limit_reached": True}


async def refund_usage(db: Any, uid: int, kind: str, consume: str) -> None:
    """Give back a use that never produced a result (3DS challenge, crash)."""
    if not uid:
        return
    if consume == "free":
        await db.refund_free(uid, kind)
    elif consume == "bonus":
        await db.refund_bonus_check(uid)
    elif consume == "credit":
        await db.refund_credit(uid)


def quota_snapshot(user: Any, config: Any) -> dict[str, Any]:
    """Everything the UI needs to show the remaining allowance."""
    bins_limit = daily_limit("bin", config)
    checks_limit = _allowance(user, "check", config)
    bins_used = _used(user, "bin")
    checks_used = _used(user, "check")
    unlimited = is_unlimited(user, config)
    premium = is_premium(user)
    return {
        "date": utc_date(),
        "free_bins_limit": bins_limit,
        "free_bins_used": bins_used,
        "free_bins_left": max(0, bins_limit - bins_used) if not unlimited else None,
        "free_checks_limit": checks_limit,
        "free_checks_used": checks_used,
        "free_checks_left": max(0, checks_limit - checks_used) if not unlimited else None,
        "bonus_checks": _bonus_checks(user),
        "credits": _credits(user),
        "premium": premium,
        "premium_until": (user["premium_until"] if user is not None else "") or "",
        "admin": is_admin(user_id(user), config),
        "banned": is_banned(user),
        "unlimited": unlimited,
    }
