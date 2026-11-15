"""Cutover launcher: every deploy boots the Venice Check platform.

Render applies blueprint (render.yaml) startCommand changes only on a manual
sync, but code changes deploy automatically. This file used to run the legacy
v7.1 bot; it now execs ``python -m app`` so the platform is what serves users
no matter which startCommand Render has stored. It also pins the platform's own
store, because a blueprint-managed ``DB_URL``/disk is not guaranteed to exist.
"""

from __future__ import annotations

import os
import sys

from app.boot import resolve_db_url

# The dashboard DATABASE_URL points at the legacy Supabase schema; the platform
# must start on its own store. Only an explicitly configured DB_URL is honoured,
# and only if it is actually usable — otherwise the first writable location
# wins. Postgres cutover later: set DB_URL to a postgres URL and it passes
# through untouched.
os.environ["DB_URL"] = resolve_db_url(os.environ.get("DB_URL"))

os.execvp(sys.executable, [sys.executable, "-m", "app"])  # noqa: S606 - exec the platform entrypoint
