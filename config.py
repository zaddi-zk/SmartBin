from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DISPLAY_VERSION = "7.1.0"

_ENV_KEYS = (
    "BOT_TOKEN ADMIN_ID DB_PATH DATABASE_URL MINIAPP_PORT MINIAPP_BASE STRIPE_API_KEY "
    "STRIPE_TEST_MODE CANARY_NONVBV CANARY_VBV CANARY_DEAD "
    "FREE_BINS_PER_DAY FREE_CHECKS_PER_DAY STARS_DAY STARS_MONTH "
    "STARS_CREDITS_500 CORRECTION_REWARD LRU_SIZE BIN_CACHE_DAYS "
    "BULK_FREE_LINES BULK_PREMIUM_LINES USDT_TRC20 PREMIUM_EMOJI_ID LOG_LEVEL"
).split()


def load_env(path: str = ".env") -> dict[str, str]:
    """Read .env without any third-party dependency. Real env wins."""
    data: dict[str, str] = {}
    candidates = [path]
    if not Path(path).exists():
        # bot.py lives at the repo root while the bot package keeps its .env.
        candidates += ["smartbin/.env", str(Path(__file__).with_name(".env"))]
    for candidate in candidates:
        p = Path(candidate)
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            data[key] = value
        if data:
            break
    for key in _ENV_KEYS:
        if key in os.environ:
            data[key] = os.environ[key]
    return data


def _int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    bot_token: str = ""
    admin_id: int = 0
    db_path: str = "smartbin.db"
    database_url: str = ""
    miniapp_port: int = 8877
    miniapp_base: str = "https://smartbin-eurr.onrender.com"

    stripe_api_key: str = ""
    stripe_test_mode: bool = True

    canary_nonvbv: str = "4242424242424242|12|2030|123"
    canary_vbv: str = "4000000000003220|12|2030|123"
    canary_dead: str = "4000000000000002|12|2030|123"

    free_bins_per_day: int = 3
    free_checks_per_day: int = 2
    stars_day: int = 300
    stars_month: int = 1200
    stars_credits_500: int = 500
    correction_reward: int = 50
    lru_size: int = 5000
    bin_cache_days: int = 30
    bulk_free_lines: int = 10
    bulk_premium_lines: int = 100
    usdt_trc20: str = ""
    premium_emoji_id: str = ""
    log_level: str = "INFO"

    display_version: str = DISPLAY_VERSION
    build_id: str = ""

    # Fixed mechanism numbers. Not operator-tunable.
    canary_idle_seconds: int = 900
    canary_checks_per_window: int = 20
    canary_http_timeout: float = 12.0
    source_timeout: float = 6.0
    eea_enrollment_min_minor: int = 3100

    @classmethod
    def load(cls, path: str = ".env") -> "Config":
        env = load_env(path)
        return cls(
            bot_token=env.get("BOT_TOKEN", ""),
            admin_id=_int(env, "ADMIN_ID", 0),
            db_path=env.get("DB_PATH", "smartbin.db"),
            database_url=env.get("DATABASE_URL", "").strip(),
            miniapp_port=_int(env, "MINIAPP_PORT", 8877),
            miniapp_base=env.get("MINIAPP_BASE", "https://smartbin-eurr.onrender.com"),
            stripe_api_key=env.get("STRIPE_API_KEY", ""),
            stripe_test_mode=_bool(env, "STRIPE_TEST_MODE", True),
            canary_nonvbv=env.get("CANARY_NONVBV") or cls.canary_nonvbv,
            canary_vbv=env.get("CANARY_VBV") or cls.canary_vbv,
            canary_dead=env.get("CANARY_DEAD") or cls.canary_dead,
            free_bins_per_day=_int(env, "FREE_BINS_PER_DAY", 3),
            free_checks_per_day=_int(env, "FREE_CHECKS_PER_DAY", 2),
            stars_day=_int(env, "STARS_DAY", 300),
            stars_month=_int(env, "STARS_MONTH", 1200),
            stars_credits_500=_int(env, "STARS_CREDITS_500", 500),
            correction_reward=_int(env, "CORRECTION_REWARD", 50),
            lru_size=_int(env, "LRU_SIZE", 5000),
            bin_cache_days=_int(env, "BIN_CACHE_DAYS", 30),
            bulk_free_lines=_int(env, "BULK_FREE_LINES", 10),
            bulk_premium_lines=_int(env, "BULK_PREMIUM_LINES", 100),
            usdt_trc20=env.get("USDT_TRC20", ""),
            premium_emoji_id=env.get("PREMIUM_EMOJI_ID", ""),
            log_level=env.get("LOG_LEVEL", "INFO"),
            display_version=DISPLAY_VERSION,
            build_id=env.get("BUILD_ID", ""),
        )

    @property
    def canary_cards(self) -> dict[str, str]:
        return {
            "nonvbv": self.canary_nonvbv,
            "vbv": self.canary_vbv,
            "dead": self.canary_dead,
        }
