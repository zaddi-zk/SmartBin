"""Menu rendering (section 19/20): the admin button is admin-only."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from app.tg.keyboards import admin_kb, admin_proxy, main_menu


def _callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


def test_admin_button_is_hidden_from_normal_users() -> None:
    assert "adm" not in _callbacks(main_menu())
    assert "adm" not in _callbacks(main_menu(is_admin=False))


def test_admin_button_is_visible_to_admins() -> None:
    assert "adm" in _callbacks(main_menu(is_admin=True))


def test_admin_menu_exposes_the_proxy_pool() -> None:
    assert "adm:px" in _callbacks(admin_kb())


def test_admin_proxy_screen_has_every_operation() -> None:
    callbacks = _callbacks(admin_proxy())
    assert {"adm:px:imp", "adm:px:refresh", "adm:px:purge", "adm:px:auto"} <= set(callbacks)
