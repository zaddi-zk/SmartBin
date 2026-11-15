"""Cutover launcher: every deploy boots the Venice Check platform.

Render applies blueprint (render.yaml) startCommand changes only on a manual
sync, but code changes deploy automatically. This file used to run the legacy
v7.1 bot; it now execs ``python -m app`` so the platform is what serves users
no matter which startCommand Render has stored.
"""

from __future__ import annotations

import os
import sys

os.execvp(sys.executable, [sys.executable, "-m", "app"])  # noqa: S606 - exec the platform entrypoint
