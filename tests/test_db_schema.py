"""Postgres schema isolation (section 12).

The legacy stack owns `users` and `checks` in public. The platform must never
create or read its tables there, so a Postgres URL without a dedicated schema
is a hard boot error, not a silent collision.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.db.session import engine_kwargs, is_sqlite, schema_for_url

PG = "postgresql+asyncpg://u:p@db.example.com:5432/postgres"


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, live_only=False, tg_token="", **overrides)


def test_sqlite_has_no_schema() -> None:
    url = "sqlite+aiosqlite:///./venice.db"
    assert is_sqlite(url) is True
    assert schema_for_url(url, "smartbin") == ""
    assert engine_kwargs(url, "smartbin") == {}


def test_postgres_requires_an_isolated_schema() -> None:
    with pytest.raises(RuntimeError, match="DB_SCHEMA"):
        schema_for_url(PG, "")


def test_postgres_pins_search_path_to_the_isolated_schema() -> None:
    kwargs = engine_kwargs(PG, "smartbin")
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["connect_args"] == {
        "server_settings": {"search_path": "smartbin,public"}
    }


def test_schema_name_is_validated() -> None:
    assert settings(db_schema=" SmartBin ").db_schema == "smartbin"
    assert settings(db_schema="").db_schema == ""
    for bad in ("public; drop table users", "smart bin", "1smartbin", "a-b"):
        with pytest.raises(ValueError, match="DB_SCHEMA"):
            settings(db_schema=bad)


def test_postgres_url_keeps_ssl_translation() -> None:
    built = settings(db_url="postgres://u:p@h/db?sslmode=require")
    assert built.db_url == "postgresql+asyncpg://u:p@h/db?ssl=require"
