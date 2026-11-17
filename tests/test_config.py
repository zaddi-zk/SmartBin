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
        checkout_secret_key="sk_sbox_x",
        cybersource_merchant_id="mid",
        cybersource_key_id="kid",
        cybersource_shared_secret="shared",
        braintree_merchant_id="mid",
        braintree_public_key="pub",
        braintree_private_key="priv",
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
    assert empty.checkout_ready is False
    assert empty.cybersource_ready is False
    assert empty.braintree_ready is False

    ready = build(
        stripe_secret_key="sk_live_x",
        adyen_api_key="adyen",
        adyen_merchant_account="merchant",
        authnet_login_id="login",
        authnet_transaction_key="txn",
        checkout_secret_key="sk_sbox_x",
        cybersource_merchant_id="mid",
        cybersource_key_id="kid",
        cybersource_shared_secret="shared",
        braintree_merchant_id="mid",
        braintree_public_key="pub",
        braintree_private_key="priv",
    )
    assert ready.stripe_ready is True
    assert ready.adyen_ready is True
    assert ready.authnet_ready is True
    assert ready.checkout_ready is True
    assert ready.cybersource_ready is True
    assert ready.braintree_ready is True


def test_stripe_ready_accepts_the_multi_key_pool() -> None:
    # The adapter reads the pool per request, so STRIPE_API_KEYS alone must
    # register it. This is the bug that left the only seeded gates keyless.
    pool = build(stripe_keys_raw="sk_live_one,sk_live_two")
    assert pool.stripe_ready is True
    assert pool.stripe_key_list() == ["sk_live_one", "sk_live_two"]


def test_secrets_returns_secret_str_instances() -> None:
    settings = build(stripe_secret_key="sk_live_x")
    secrets = settings.secrets()
    assert len(secrets) == 12
    assert all(isinstance(item, SecretStr) for item in secrets)


def test_return_url_defaults_to_the_public_service_url() -> None:
    assert build().stripe_return_url == "https://smartbin-eurr.onrender.com/"


def test_return_url_must_be_absolute() -> None:
    # A relative or blank value is not something Stripe accepts on a confirmed
    # intent, and one bad env var must not turn every check into a 400.
    assert build(stripe_return_url="  /back  ").stripe_return_url == (
        "https://smartbin-eurr.onrender.com/"
    )
    assert build(stripe_return_url="https://x.test/ok").stripe_return_url == "https://x.test/ok"
