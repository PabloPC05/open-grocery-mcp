import pytest

from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import InvalidRequest


def test_draft_never_claims_to_modify_or_order() -> None:
    store = DraftCartStore()
    draft = store.create({"store": "gadis", "total": 12.34})
    assert draft["retailer_cart_modified"] is False
    assert draft["order_placed"] is False
    assert draft["human_confirmation_required"] is True
    assert store.get(draft["draft_id"])["basket"]["store"] == "gadis"


def test_delete_draft() -> None:
    store = DraftCartStore()
    draft = store.create({"store": "gadis"})
    assert store.delete(draft["draft_id"])["deleted"] is True
    with pytest.raises(InvalidRequest):
        store.get(draft["draft_id"])
