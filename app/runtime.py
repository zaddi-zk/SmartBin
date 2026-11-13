"""Process-wide wiring: adapters, key manager, proxy pool, executors, BIN."""

from __future__ import annotations

from functools import lru_cache

from app.bin.resolver import BinResolver
from app.config import Settings, load_settings
from app.exec.check import CheckExecutor
from app.exec.kill import KillExecutor
from app.keys.manager import KeyManager
from app.providers.base import AdapterRegistry
from app.providers.stripe import StripeAdapter
from app.proxy.pool import ProxyPool


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.adapters = AdapterRegistry()
        if settings.stripe_ready:
            self.adapters.register(StripeAdapter(settings))
        self.keys = KeyManager(settings, self.adapters)
        self.proxies = ProxyPool(settings)
        self.executor = CheckExecutor(settings, self.adapters, self.keys, self.proxies)
        self.kill = KillExecutor(self.executor)
        self.bins = BinResolver(settings)

    @property
    def adapters_live(self) -> tuple[str, ...]:
        return self.adapters.names


@lru_cache(maxsize=1)
def runtime() -> Runtime:
    return Runtime(load_settings())
