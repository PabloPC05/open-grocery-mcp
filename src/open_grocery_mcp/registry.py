"""Provider registry."""

from __future__ import annotations

import threading
from collections.abc import Callable

from open_grocery_mcp.errors import StoreNotFound
from open_grocery_mcp.models import StoreInfo
from open_grocery_mcp.providers import (
    FroizProvider,
    GadisProvider,
    GroceryProvider,
    MercadonaFullProvider,
)

ProviderFactory = Callable[[], GroceryProvider]


class ProviderRegistry:
    """Lazily constructs adapters and exposes only their common contract."""

    def __init__(self, factories: dict[str, ProviderFactory] | None = None) -> None:
        self._factories = factories if factories is not None else {
            "froiz": FroizProvider,
            "gadis": GadisProvider,
            "mercadona": MercadonaFullProvider,
        }
        self._instances: dict[str, GroceryProvider] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> GroceryProvider:
        normalized = key.casefold().strip()
        factory = self._factories.get(normalized)
        if factory is None:
            valid = ", ".join(sorted(self._factories))
            raise StoreNotFound(f"unknown store {key!r}; available stores: {valid}")
        with self._lock:
            provider = self._instances.get(normalized)
            if provider is None:
                provider = factory()
                self._instances[normalized] = provider
        return provider

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def list(self, *, country: str | None = None) -> list[dict[str, object]]:
        wanted = country.upper().strip() if country else None
        infos: list[StoreInfo] = []
        for key in self.keys():
            provider = self.get(key)
            if wanted is None or provider.info.country == wanted:
                infos.append(provider.info)
        return [info.to_dict() for info in infos]

    def close(self) -> None:
        for provider in tuple(self._instances.values()):
            provider.close()


_default_registry: ProviderRegistry | None = None
_default_lock = threading.Lock()


def default_registry() -> ProviderRegistry:
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = ProviderRegistry()
    return _default_registry
