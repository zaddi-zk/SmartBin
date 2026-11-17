"""Settings, with the LIVE_ONLY guard from section 5.

Every key that can reach a provider lives here. ``load_settings`` refuses to
start when ``LIVE_ONLY`` is on and a test key was supplied: a test key approves
anything Luhn-valid, which teaches the user the wrong thing.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_TEST_KEY_RE = re.compile(r"sk_test_", re.IGNORECASE)


def psycopg_url(url: str) -> str:
    """Normalise any Postgres URL to the scheme psycopg accepts.

    Settings store the platform URL as ``postgresql+asyncpg://`` (SQLAlchemy's
    driver prefix). The legacy import talks psycopg directly, so strip the
    driver suffix and keep ``sslmode`` intact.
    """
    value = url.strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if value.startswith(prefix):
            value = "postgresql://" + value[len(prefix) :]
            break
    return value


def postgres_target(url: str) -> tuple[str, int, str] | None:
    """``(host, port, database)`` for a Postgres URL, ignoring driver/params."""
    parsed = urlparse(psycopg_url(url))
    if parsed.scheme != "postgresql" or not parsed.hostname:
        return None
    return (parsed.hostname, parsed.port or 5432, parsed.path or "/")


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
    # Postgres/Supabase isolation. The legacy stack already owns `users` and
    # `checks` in the public schema, so the platform must live in its own
    # schema. Empty on SQLite; required before a Postgres cutover.
    db_schema: str = Field(
        default="",
        validation_alias=AliasChoices("DB_SCHEMA", "SMARTBIN_DB_SCHEMA"),
    )
    # Read-only source for the one-time legacy import. The legacy bot owns
    # ``public.users`` in Supabase; the platform owns its own tables. When the
    # platform already runs on that same Postgres URL there is nothing to
    # import from, so ``legacy_source_url`` returns "".
    legacy_db_url: str = Field(
        default="",
        validation_alias=AliasChoices("LEGACY_DATABASE_URL", "DATABASE_URL"),
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

    # --- polling (one bot token, one poller) --------------------------------
    # Render keeps the old instance alive until the new one is healthy, so two
    # pollers can briefly hold the same token and Telegram answers 409
    # ("terminated by other getUpdates request"). Waiting a few seconds after
    # boot lets the old SIGTERM shutdown release the session first.
    poll_start_delay_s: float = Field(
        default=5.0,
        validation_alias=AliasChoices("POLL_START_DELAY_S", "BOT_START_DELAY_S"),
    )
    # Attempts to start polling before the process exits. 0 = no retry.
    poll_retries: int = 5

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
    proxy_seed_file: str = ""
    proxy_seed_url: str = ""
    proxy_refresh_hours: int = 24
    proxy_max_latency_ms: int = 4000

    # --- access -------------------------------------------------------------
    bot_username: str = "SmartBlN_Bot"
    support_username: str = "SmartBin_info"
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
    # Multi-key pool from the legacy deployment env (comma/newline separated).
    # Seeded into the ``keys`` table as PENDING at boot; only a livemode read
    # promotes them to READY (section 10).
    stripe_keys_raw: str = Field(default="", validation_alias="STRIPE_API_KEYS")
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
    adyen_currency: str = "USD"
    adyen_capture_delay_hours: int = 7

    authnet_login_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("AUTHNET_API_LOGIN_ID", "GATE_AUTHNET_API_LOGIN_ID"),
    )
    authnet_transaction_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("AUTHNET_TRANSACTION_KEY", "GATE_AUTHNET_TRANSACTION_KEY"),
    )
    authnet_api_base: str = "https://api.authorize.net/xml/v1/request.api"

    checkout_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("CHECKOUT_SECRET_KEY", "GATE_CHECKOUT_SECRET_KEY"),
    )
    checkout_api_base: str = "https://api.checkout.com"
    checkout_currency: str = "USD"

    cybersource_merchant_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("CYBERSOURCE_MERCHANT_ID", "GATE_CYBERSOURCE_MERCHANT_ID"),
    )
    cybersource_key_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("CYBERSOURCE_KEY_ID", "GATE_CYBERSOURCE_KEY_ID"),
    )
    cybersource_shared_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "CYBERSOURCE_SHARED_SECRET", "GATE_CYBERSOURCE_SHARED_SECRET"
        ),
    )
    cybersource_api_base: str = "https://api.cybersource.com"
    cybersource_currency: str = "USD"

    braintree_merchant_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("BRAINTREE_MERCHANT_ID", "GATE_BRAINTREE_MERCHANT_ID"),
    )
    braintree_public_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("BRAINTREE_PUBLIC_KEY", "GATE_BRAINTREE_PUBLIC_KEY"),
    )
    braintree_private_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("BRAINTREE_PRIVATE_KEY", "GATE_BRAINTREE_PRIVATE_KEY"),
    )
    braintree_api_base: str = "https://api.braintreegateway.com"
    braintree_currency: str = "USD"

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

    @field_validator("db_schema", mode="after")
    @classmethod
    def _validate_db_schema(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
            raise ValueError("DB_SCHEMA must be a plain lowercase identifier")
        return value

    @field_validator("poll_start_delay_s", mode="after")
    @classmethod
    def _clamp_start_delay(cls, value: float) -> float:
        """A negative or huge delay would either skip the stagger or stall boot."""
        return min(60.0, max(0.0, value))

    @field_validator("poll_retries", mode="after")
    @classmethod
    def _clamp_poll_retries(cls, value: int) -> int:
        return min(20, max(0, value))

    @field_validator("db_url", mode="after")
    @classmethod
    def _normalise_db_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://") :]
        # Supabase/Render URLs ship `?sslmode=require`; asyncpg's connect()
        # takes `ssl` and rejects `sslmode`.
        if value.startswith("postgresql+asyncpg"):
            value = value.replace("sslmode=", "ssl=")
        return value

    @property
    def legacy_source_url(self) -> str:
        """The distinct Postgres URL to import legacy users from, or ``""``.

        ``DATABASE_URL`` doubles as the platform URL when ``DB_URL`` is unset.
        Importing ``public.users`` into ``public.users`` would be a no-op with
        write risk, so the same database is skipped unless the platform lives
        in its own schema (where ``public`` is still the legacy source).
        """
        raw = self.legacy_db_url.strip()
        if not raw:
            return ""
        candidate = psycopg_url(raw)
        if not candidate.startswith("postgresql://"):
            return ""
        same_database = postgres_target(candidate) == postgres_target(self.db_url)
        if same_database and self.db_schema.strip().lower() in ("", "public"):
            return ""
        return candidate

    def secrets(self) -> list[SecretStr]:
        values = [
            self.tg_token,
            self.stripe_secret_key,
            self.adyen_api_key,
            self.authnet_login_id,
            self.authnet_transaction_key,
            self.checkout_secret_key,
            self.cybersource_merchant_id,
            self.cybersource_key_id,
            self.cybersource_shared_secret,
            self.braintree_merchant_id,
            self.braintree_public_key,
            self.braintree_private_key,
        ]
        # The single Stripe key is already in the list via stripe_secret_key;
        # extend with the multi-key pool only, so the live-only assert sees
        # every loaded key exactly once.
        values.extend(
            SecretStr(part)
            for part in re.split(r"[,\s]+", self.stripe_keys_raw.strip())
            if part
        )
        return values

    def stripe_key_list(self) -> list[str]:
        """The whole key pool: STRIPE_API_KEYS plus the single-key env."""
        raw = " ".join(
            (self.stripe_keys_raw, self.stripe_secret_key.get_secret_value())
        )
        return [part for part in re.split(r"[,\s]+", raw.strip()) if part]

    def adyen_key_list(self) -> list[str]:
        value = self.adyen_api_key.get_secret_value().strip()
        return [value] if value else []

    def authnet_key_list(self) -> list[str]:
        login = self.authnet_login_id.get_secret_value().strip()
        key = self.authnet_transaction_key.get_secret_value().strip()
        return [f"{login}|{key}"] if login and key else []

    def checkout_key_list(self) -> list[str]:
        value = self.checkout_secret_key.get_secret_value().strip()
        return [value] if value else []

    def cybersource_key_list(self) -> list[str]:
        merchant = self.cybersource_merchant_id.get_secret_value().strip()
        key_id = self.cybersource_key_id.get_secret_value().strip()
        shared = self.cybersource_shared_secret.get_secret_value().strip()
        return [f"{merchant}|{key_id}|{shared}"] if merchant and key_id and shared else []

    def braintree_key_list(self) -> list[str]:
        merchant = self.braintree_merchant_id.get_secret_value().strip()
        public = self.braintree_public_key.get_secret_value().strip()
        private = self.braintree_private_key.get_secret_value().strip()
        return [f"{merchant}|{public}|{private}"] if merchant and public and private else []

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

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
        # The adapter takes its key per request from the key pool, so the pool
        # env (STRIPE_API_KEYS) is enough to register it. Requiring the single
        # legacy key here left Stripe unregistered while keys sat PENDING.
        return bool(self.stripe_key_list())

    @property
    def adyen_ready(self) -> bool:
        return bool(self.adyen_api_key.get_secret_value() and self.adyen_merchant_account)

    @property
    def authnet_ready(self) -> bool:
        return bool(
            self.authnet_login_id.get_secret_value()
            and self.authnet_transaction_key.get_secret_value()
        )

    @property
    def checkout_ready(self) -> bool:
        return bool(self.checkout_secret_key.get_secret_value())

    @property
    def cybersource_ready(self) -> bool:
        return bool(
            self.cybersource_merchant_id.get_secret_value()
            and self.cybersource_key_id.get_secret_value()
            and self.cybersource_shared_secret.get_secret_value()
        )

    @property
    def braintree_ready(self) -> bool:
        return bool(
            self.braintree_merchant_id.get_secret_value()
            and self.braintree_public_key.get_secret_value()
            and self.braintree_private_key.get_secret_value()
        )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    settings = Settings()
    settings.assert_live_keys()
    return settings
