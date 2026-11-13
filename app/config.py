"""Settings, with the LIVE_ONLY guard from section 5.

Every key that can reach a provider lives here. ``load_settings`` refuses to
start when ``LIVE_ONLY`` is on and a test key was supplied: a test key approves
anything Luhn-valid, which teaches the user the wrong thing.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_TEST_KEY_RE = re.compile(r"sk_test_", re.IGNORECASE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- core ---------------------------------------------------------------
    live_only: bool = True
    db_url: str = Field(
        default="sqlite+aiosqlite:///./venice.db",
        validation_alias=AliasChoices("DB_URL", "DATABASE_URL"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("REDIS_URL", "REDIS_URL_APP"),
    )
    tg_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("TG_TOKEN", "BOT_TOKEN"),
    )
    log_level: str = "INFO"

    # --- policy -------------------------------------------------------------
    canary_interval_s: int = 60
    canary_jitter_s: int = 8
    canary_enabled: bool = False
    canary_cards: str = Field(
        default="",
        validation_alias=AliasChoices("CANARY_CARDS", "CANARY_NONVBV"),
    )
    breaker_fault_threshold: int = 3
    breaker_open_s: int = 60
    breaker_backoff_cap_s: int = 900
    credit_refund_on_error: bool = True
    bin_min_confidence: int = 55
    check_timeout_s: float = 12.0
    rate_per_min: int = 30
    rate_concurrent: int = 3
    debounce_ms: int = 800
    dup_window_s: int = 60
    require_proxy: bool = False
    free_proxy_cooldown_s: int = 86_400

    # --- access -------------------------------------------------------------
    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("ADMIN_IDS", "ADMIN_ID"),
    )
    required_channel: str = ""
    required_group: str = ""

    # --- providers ----------------------------------------------------------
    stripe_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("STRIPE_SECRET_KEY", "STRIPE_API_KEY"),
    )
    stripe_api_version: str = "2024-06-20"
    stripe_currency: str = "usd"
    stripe_api_base: str = "https://api.stripe.com"

    adyen_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("ADYEN_API_KEY", "GATE_ADYEN_API_KEY")
    )
    adyen_merchant_account: str = Field(
        default="",
        validation_alias=AliasChoices("ADYEN_MERCHANT_ACCOUNT", "GATE_ADYEN_MERCHANT_ACCOUNT"),
    )
    adyen_api_base: str = "https://checkout-live.adyen.com/v71"

    authnet_login_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("AUTHNET_API_LOGIN_ID", "GATE_AUTHNET_API_LOGIN_ID"),
    )
    authnet_transaction_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("AUTHNET_TRANSACTION_KEY", "GATE_AUTHNET_TRANSACTION_KEY"),
    )
    authnet_api_base: str = "https://api.authorize.net/xml/v1/request.api"

    # --- BIN ----------------------------------------------------------------
    bin_cache_days: int = 7
    bin_timeout_s: float = 6.0

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            # Tolerate placeholders such as ``your_telegram_user_id``: a bad
            # admin id must never stop the bot from starting.
            return [
                int(part)
                for part in re.split(r"[,\s]+", value.strip())
                if part.lstrip("-").isdigit()
            ]
        return value

    @field_validator("db_url", mode="after")
    @classmethod
    def _normalise_db_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    def secrets(self) -> list[SecretStr]:
        return [
            self.tg_token,
            self.stripe_secret_key,
            self.adyen_api_key,
            self.authnet_login_id,
            self.authnet_transaction_key,
        ]

    def assert_live_keys(self) -> None:
        """Section 5: refuse sk_test_ at load when LIVE_ONLY is on."""
        if not self.live_only:
            return
        for secret in self.secrets():
            value = secret.get_secret_value()
            if _TEST_KEY_RE.search(value):
                raise SystemExit(
                    "LIVE_ONLY=1 but a sk_test_ key was loaded. Refusing to start: "
                    "a test key approves anything Luhn-valid."
                )

    @property
    def stripe_ready(self) -> bool:
        return bool(self.stripe_secret_key.get_secret_value())

    @property
    def adyen_ready(self) -> bool:
        return bool(self.adyen_api_key.get_secret_value() and self.adyen_merchant_account)

    @property
    def authnet_ready(self) -> bool:
        return bool(
            self.authnet_login_id.get_secret_value()
            and self.authnet_transaction_key.get_secret_value()
        )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    settings = Settings()
    settings.assert_live_keys()
    return settings
