from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Flow(StatesGroup):
    """Guided button flows. One state per input step."""

    bin = State()
    check = State()
    check_confirm = State()
    correct_bin = State()
    correct_pick = State()
    correct_value = State()
    bulk_input = State()
    bulk_confirm = State()
    kill = State()
    kill_confirm = State()
