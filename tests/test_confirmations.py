import pytest

from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.errors import ConfirmationRequired


def test_confirmation_is_one_use_and_phrase_bound() -> None:
    store = ConfirmationStore(ttl_seconds=60)
    created = store.create(
        action="cart",
        phrase="CONFIRMAR CARRITO 12.00 EUR",
        payload={"x": 1},
        summary={"total": 12},
    )
    with pytest.raises(ConfirmationRequired):
        store.consume(created["confirmation_id"], "wrong", action="cart")
    assert store.consume(
        created["confirmation_id"],
        "CONFIRMAR CARRITO 12.00 EUR",
        action="cart",
    ) == {"x": 1}
    with pytest.raises(ConfirmationRequired):
        store.consume(
            created["confirmation_id"],
            "CONFIRMAR CARRITO 12.00 EUR",
            action="cart",
        )
