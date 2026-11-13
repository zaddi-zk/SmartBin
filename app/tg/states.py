"""FSM states (section 8)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Flow(StatesGroup):
    await_card = State()
    await_file = State()
    await_proxy = State()
    await_bin = State()
    await_grant = State()
    await_report = State()
