from __future__ import annotations

from decimal import Decimal

from open_grocery_mcp.errors import AuthenticationRequired
from open_grocery_mcp.models import Product
from open_grocery_mcp.providers.froiz_full import FroizFullProvider


class FakeCatalogue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, query: str, **kwargs: object) -> list[Product]:
        self.calls.append({"query": query, **kwargs})
        return [
            Product(
                store="froiz",
                id="public",
                name="Producto público",
                price=Decimal("2.00"),
                metadata={"location_aware": False},
            )
        ]

    def close(self) -> None:
        pass


class FakeAccount:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def search_products(self, query: str, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append({"query": query, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]

    def close(self) -> None:
        pass


def test_full_provider_prefers_authenticated_location_aware_search() -> None:
    catalogue = FakeCatalogue()
    account = FakeAccount(
        [{"id": "auth-1", "name": "Leche", "order_price": 1.65, "enabled": True}]
    )
    provider = FroizFullProvider(catalogue=catalogue, account=account)

    products = provider.search("leche", limit=4, postal_code="28050")

    assert [product.id for product in products] == ["auth-1"]
    assert products[0].metadata["location_aware"] is True
    assert products[0].metadata["catalogue_backend"] == "froiz_authenticated"
    assert account.calls == [{"query": "leche", "limit": 4, "postal_code": "28050"}]
    assert catalogue.calls == []


def test_full_provider_exposes_authenticated_direct_discount_metadata() -> None:
    provider = FroizFullProvider(
        catalogue=FakeCatalogue(),
        account=FakeAccount(
            [
                {
                    "id": "auth-offer",
                    "name": "Leche",
                    "order_price": 1.50,
                    "base_price": 2.00,
                    "offer": "Oferta semana",
                    "enabled": True,
                }
            ]
        ),
    )

    product = provider.search("leche")[0]

    assert product.price == Decimal("1.50")
    assert product.metadata["promotion_type"] == "direct_discount"
    assert product.metadata["discount_percent"] == 25.0


def test_full_provider_falls_back_to_public_search_on_auth_failure() -> None:
    catalogue = FakeCatalogue()
    account = FakeAccount(AuthenticationRequired("expired"))
    provider = FroizFullProvider(catalogue=catalogue, account=account)

    products = provider.search("leche", limit=2, postal_code="28050", eco=True)

    assert [product.id for product in products] == ["public"]
    assert catalogue.calls == [
        {"query": "leche", "limit": 2, "postal_code": "28050", "eco": True}
    ]


def test_full_provider_does_not_repeat_failed_auth_on_each_search() -> None:
    catalogue = FakeCatalogue()
    account = FakeAccount(AuthenticationRequired("expired"))
    provider = FroizFullProvider(catalogue=catalogue, account=account)

    provider.search("leche", postal_code="28050")
    provider.search("arroz", postal_code="28050")

    assert len(account.calls) == 1
    assert [call["query"] for call in catalogue.calls] == ["leche", "arroz"]


def test_full_provider_falls_back_when_authenticated_rows_are_unusable() -> None:
    catalogue = FakeCatalogue()
    account = FakeAccount([{"id": "missing-price", "name": "Leche"}])
    provider = FroizFullProvider(catalogue=catalogue, account=account)

    products = provider.search("leche")

    assert [product.id for product in products] == ["public"]
