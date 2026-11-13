from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import ui
from core.canaries import percentile
from handlers.common import claim_usage, parse_bin_arg
from models import utc_date
from render import DISPLAY_VERSION, FOOTER_PREFIX, status_block, status_header
from states import Flow

ALLOWED_FIELDS = {
    "brand", "level", "bank", "phone", "site", "country", "iso2",
    "currency", "prepaid", "commercial", "card_length", "luhn",
    "avs", "dob", "ssn", "mmn",
}

router = Router(name="status")