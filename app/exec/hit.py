"""Hitter lane (Whop / Jio / Shopify).

Site-native checkout flows need per-site selectors that can only be derived
from a live session. Rather than invent them, this module refuses loudly and
the corresponding gates ship disabled. Add a driver here, run it against a real
site, and only then enable the gate.
"""

from __future__ import annotations


class HitUnavailable(RuntimeError):
    pass


async def hit(site: str, card: str) -> None:
    raise HitUnavailable(
        f"no live driver for {site!r}: hitters are disabled until a selector set is "
        "captured against the real checkout and mapped into the taxonomy"
    )
