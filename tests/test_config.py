"""Settings contract: the LIVE_ONLY guard and tolerant admin parsing."""

from __future__ import annotations

import pytest
from app.config import Settings
from pydantic import SecretStr


def build(**kwargs: object) -> Settings:
    """Never read the developer's .env; tests must be hermetic."""
    return Settings(_env_file=None, **kwargs)


def test_live_only_rejects_test_stripe_key() -> None:
    settings = build(live_only=True, stripe_secret_key="sk_test_123")
    with pytest.raises(SystemExit, match="LIVE_ONLY=1"):
        settings.assert_live_keys()


def test_live_only_rejects_test_key_in_any_provider() -> None:
    settings = build(
        live_only=True,
        stripe_secret_key="sk_live_ok",
        adyen_api_key="sk_test_adyen",
    )
    with pytest.raises(SystemExit):
        settings.assert_live_keys()


def test_live_only_accepts_live_keys() -> None:
    settings = build(
        live_only=True,
        stripe_secret_key="sk_live_123",
        authnet_login_id="login",
        authnet_transaction_key="txn",
    )
    settings.assert_live_keys()


def test_test_keys_allowed_when_live_only_is_off() -> None:
    settings = build(live_only=False, stripe_secret_key="sk_test_123")
    settings.assert_live_keys()


def test_admin_ids_accepts_commas_and_spaces() -> None:
    assert build(admin_ids="123, 456 789").admin_ids == [123, 456, 789]


def test_admin_ids_tolerates_placeholder_text() -> None:
    # The shipped .env has ADMIN_ID=your_telegram_user_id; that must not crash.
    assert build(admin_ids="your_telegram_user_id").admin_ids == []


def test_admin_ids_accepts_negative_ids_and_list() -> None:
    assert build(admin_ids="-1001234567890").admin_ids == [-1001234567890]
    assert build(admin_ids=[1, 2]).admin_ids == [1, 2]


def test_db_url_is_upgraded_to_async_driver() -> None:
    assert build(db_url="postgres://u:p@h/db").db_url == "postgresql+asyncpg://u:p@h/db"
    assert build(db_url="postgresql://u:p@h/db").db_url == "postgresql+asyncpg://u:p@h/db"
    assert build(db_url="sqlite+aiosqlite:///./x.db").db_url == "sqlite+aiosqlite:///./x.db"


def test_readiness_properties_track_secrets() -> None:
    empty = build()
    assert empty.stripe_ready is False
    assert empty.adyen_ready is False
    assert empty.authnet_ready is False

    ready = build(
        stripe_secret_key="sk_live_x",
        adyen_api_key="adyen",
        adyen_merchant_account="merchant",
        authnet_login_id="login",
        authnet_transaction_key="txn",
    )
    assert ready.stripe_ready is True
    assert ready.adyen_ready is True
    assert ready.authnet_ready is True


def test_secrets_returns_secret_str_instances() -> None:
    settings = build(stripe_secret_key="sk_live_x")
    secrets = settings.secrets()
    assert len(secrets) == 5
    assert all(isinstance(item, SecretStr) for item in secrets)
