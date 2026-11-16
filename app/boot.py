"""Boot-time store selection.

The platform keeps its own database. Render applies blueprint (render.yaml)
changes only on a manual sync, so the launcher cannot rely on ``DB_URL`` being
present in the environment, and it cannot trust a path to be writable: the disk
mount may not be provisioned yet, or the project directory may be read-only at
runtime. This module picks the first SQLite location that really accepts a
write and reports the choice on stderr, so the boot log says where the data
lives instead of dying with ``unable to open database file``.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from collections.abc import Sequence

DB_FILENAME = "venice.db"

# Render's build directory is recreated on every deploy. A SQLite file there is
# writable during the run, so nothing fails loudly — the database is simply
# empty after the next deploy. Never trust it when a disk mount is available.
EPHEMERAL_PREFIXES: tuple[str, ...] = ("/opt/render/project",)


def sqlite_url(directory: str) -> str:
    """Absolute SQLite URL for ``venice.db`` inside ``directory``."""
    return "sqlite+aiosqlite:///" + os.path.join(directory, DB_FILENAME)


def sqlite_path(url: str) -> str:
    """File path behind a SQLite URL (``sqlite+aiosqlite:////x/y.db`` -> ``/x/y.db``)."""
    return url.split("///", 1)[-1]


def _dir_writable(directory: str) -> bool:
    """True when a file can really be created in ``directory``.

    ``os.access`` lies on some mounts; creating and removing a probe file is
    the only check that proves the write path.
    """
    try:
        os.makedirs(directory, exist_ok=True)
        handle, probe = tempfile.mkstemp(prefix=".venice-probe-", dir=directory)
    except OSError:
        return False
    os.close(handle)
    with contextlib.suppress(OSError):
        os.remove(probe)
    return True


def _sqlite_usable(url: str) -> bool:
    """Directory writable and, when the file already exists, the file writable."""
    path = sqlite_path(url)
    if path == ":memory:":
        return True
    directory = os.path.dirname(path) or "."
    if not _dir_writable(directory):
        return False
    if not os.path.exists(path):
        return True
    try:
        with open(path, "ab"):
            pass
    except OSError:
        return False
    return True


def resolve_db_url(
    configured: str | None = None,
    *,
    candidates: Sequence[str] | None = None,
    persistent: Sequence[str] | None = None,
    ephemeral_prefixes: Sequence[str] = EPHEMERAL_PREFIXES,
) -> str:
    """Return the DB URL the platform should boot with.

    A non-SQLite URL (Postgres) is the operator's explicit choice and is kept
    as-is. A SQLite URL is kept only when it is genuinely usable and not on an
    ephemeral build path; otherwise the first writable fallback directory wins.
    """
    configured = (configured or "").strip()
    if configured and not configured.startswith("sqlite"):
        return configured
    persistent_dirs = tuple(persistent) if persistent is not None else ("/home/render",)
    if configured and _sqlite_usable(configured):
        path = sqlite_path(configured)
        if any(path.startswith(prefix) for prefix in ephemeral_prefixes):
            for directory in persistent_dirs:
                url = sqlite_url(directory)
                if os.path.dirname(path) != directory and _sqlite_usable(url):
                    print(
                        f"[boot] DB_URL={configured} is on an ephemeral path; "
                        f"using {url}",
                        file=sys.stderr,
                    )
                    return url
        return configured
    if configured:
        print(
            f"[boot] DB_URL={configured} is not writable; probing fallbacks",
            file=sys.stderr,
        )
    search = (
        tuple(candidates)
        if candidates is not None
        else ("/home/render", os.getcwd(), tempfile.gettempdir())
    )
    for directory in search:
        url = sqlite_url(directory)
        if _sqlite_usable(url):
            print(f"[boot] DB_URL={url}", file=sys.stderr)
            return url
    # Even /tmp was rejected; return the canonical temp path so the failure
    # (if any) names the file instead of a silent default.
    return sqlite_url(tempfile.gettempdir())
