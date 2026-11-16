"""Boot store selection: the launcher must never pick an unusable SQLite path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from app.boot import resolve_db_url, sqlite_path, sqlite_url


def test_explicit_postgres_url_passes_through() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/d?ssl=require"
    assert resolve_db_url(url) == url


def test_writable_configured_sqlite_is_kept(tmp_path: Path) -> None:
    url = sqlite_url(str(tmp_path))
    assert resolve_db_url(url) == url


def test_ephemeral_build_path_moves_to_persistent_disk(tmp_path: Path) -> None:
    build = tmp_path / "opt" / "render" / "project" / "src"
    build.mkdir(parents=True)
    disk = tmp_path / "home" / "render"
    configured = sqlite_url(str(build))

    resolved = resolve_db_url(
        configured,
        persistent=(str(disk),),
        ephemeral_prefixes=(str(build.parent.parent),),
    )

    assert resolved == sqlite_url(str(disk))


def test_ephemeral_path_stays_when_no_persistent_disk(tmp_path: Path) -> None:
    build = tmp_path / "opt" / "render" / "project" / "src"
    build.mkdir(parents=True)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    configured = sqlite_url(str(build))

    resolved = resolve_db_url(
        configured,
        persistent=(str(blocker),),
        ephemeral_prefixes=(str(build.parent.parent),),
    )

    assert resolved == configured


def test_missing_disk_mount_falls_back_to_writable_candidate(tmp_path: Path) -> None:
    # The Render failure: /home/render exists in the URL but the mount is not
    # provisioned, so SQLite cannot create the file there.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    configured = f"sqlite+aiosqlite:///{blocker}/venice.db"
    fallback = tmp_path / "data"

    resolved = resolve_db_url(configured, candidates=(str(fallback),))

    assert resolved == sqlite_url(str(fallback))
    assert sqlite_path(resolved) == str(fallback / "venice.db")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file mode bits")
def test_unwritable_existing_file_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "venice.db"
    path.write_text("")
    path.chmod(0o400)
    try:
        resolved = resolve_db_url(
            sqlite_url(str(tmp_path)), candidates=(str(tmp_path / "data"),)
        )
    finally:
        path.chmod(0o600)

    assert resolved == sqlite_url(str(tmp_path / "data"))


def test_no_configured_url_picks_first_writable_candidate(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    good = tmp_path / "good"

    resolved = resolve_db_url("", candidates=(str(blocker), str(good)))

    assert resolved == sqlite_url(str(good))


def test_sqlite_path_unwraps_absolute_url(tmp_path: Path) -> None:
    assert sqlite_path(sqlite_url(str(tmp_path))) == str(tmp_path / "venice.db")
