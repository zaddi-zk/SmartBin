"""Cutover launcher: every deploy boots the Venice Check platform.

Render applies blueprint (render.yaml) startCommand changes only on a manual
sync, but code changes deploy automatically. This file used to run the legacy
v7.1 bot; it now execs ``python -m app`` so the platform is what serves users
no matter which startCommand Render has stored.
"""

from __future__ import annotations

import os
import sys

# The dashboard DATABASE_URL points at the legacy Supabase schema; the platform
# must start on its own store. render.yaml's DB_URL key only lands on a manual
# blueprint sync, so the launcher sets the same decision here — code deploys
# always apply. Postgres cutover later: set DB_URL in the dashboard and this
# default steps aside.
if not os.environ.get("DB_URL"):
    base = "/home/render" if os.path.isdir("/home/render") else os.getcwd()
    os.environ["DB_URL"] = f"sqlite+aiosqlite:///{base}/venice.db"

os.execvp(sys.executable, [sys.executable, "-m", "app"])  # noqa: S606 - exec the platform entrypoint
