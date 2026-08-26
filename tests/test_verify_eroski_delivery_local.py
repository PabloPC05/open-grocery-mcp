from __future__ import annotations

import json

from tools import verify_eroski_delivery_local as verifier


def test_read_only_route_policy_allows_gets_and_blocks_all_non_gets() -> None:
    assert (
        verifier.classify_request(
            "GET",
            "https://supermercado.eroski.es/es/bookingdelivery/",
            allow_delivery_read_post=False,
        )
        == "allow_read"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/addresslistselector:update_map",
            allow_delivery_read_post=False,
        )
        == "block_other_non_get"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/orders/create",
            allow_delivery_read_post=True,
        )
        == "block_order_or_payment"
    )
    assert (
        verifier.classify_request(
            "GET",
            "https://supermercado.eroski.es/es/payment/status",
            allow_delivery_read_post=False,
        )
        == "block_order_or_payment"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/addresslistselector:update_map",
            allow_delivery_read_post=True,
            body="action=submitOrder&confirm=true",
        )
        == "block_order_or_payment"
    )


def test_optional_delivery_post_policy_never_allows_final_slot_form() -> None:
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/addresslistselector:update_map",
            allow_delivery_read_post=True,
            body="ref=opaque&selected=true&mobile=false&confirm=true",
        )
        == "allow_delivery_read_post"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/bookingdelivery.selectdelivery."
            "addressselector.homeaddressselector.selectdeliveryaddress:change",
            allow_delivery_read_post=True,
            body="t%3Aselectvalue=opaque&t%3Azoneid=zone",
        )
        == "allow_delivery_read_post"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/pickupaddressselector.slotform",
            allow_delivery_read_post=True,
        )
        == "block_final_slot_form"
    )
    slot_url = (
        "https://supermercado.eroski.es"
        "/es/bookingdelivery.selectdelivery.addressselector."
        "pickupaddressselector.slotform"
    )
    safe_slot_body = (
        "checkoutBasketType_0=ALI&radiogroup=selected&"
        "selectedAddressRef=opaque&selectedSlotRef_0=opaque&"
        "selectedSlotTime_0=opaque&t%3Aformdata=opaque&t%3Azoneid=zone"
    )
    assert (
        verifier.classify_request(
            "POST",
            slot_url,
            allow_delivery_read_post=False,
            allow_slot_summary_post=True,
            body=safe_slot_body,
        )
        == "allow_slot_summary_post"
    )
    assert (
        verifier.classify_request(
            "POST",
            slot_url,
            allow_delivery_read_post=False,
            allow_slot_summary_post=True,
            body=safe_slot_body + "&submitOrder=true",
        )
        == "block_order_or_payment"
    )
    assert (
        verifier.classify_request(
            "GET",
            "https://supermercado.eroski.es/es/pickupaddressselector.slotform",
            allow_delivery_read_post=True,
        )
        == "block_final_slot_form"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/addresslistselector:update_map",
            allow_delivery_read_post=True,
            body="ref=opaque&selected=true&mobile=false&confirm=true",
        )
        == "allow_delivery_read_post"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/addresslistselector:update_map",
            allow_delivery_read_post=True,
            body="slotForm=1",
        )
        == "block_final_slot_form"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://supermercado.eroski.es/es/payment/addresslistselector:update_map",
            allow_delivery_read_post=True,
        )
        == "block_order_or_payment"
    )


def test_external_analytics_purchase_events_are_not_misclassified_as_orders() -> None:
    assert (
        verifier.classify_request(
            "GET",
            "https://supermercado.eroski.es/assets/meta/fixed/imgs/base/quick-purchase-menu-icon-white.svg",
            allow_delivery_read_post=False,
        )
        == "allow_read"
    )
    assert (
        verifier.classify_request(
            "GET",
            "https://www.googleadservices.com/pagead/conversion/1/?event=purchase",
            allow_delivery_read_post=False,
        )
        == "allow_read"
    )
    assert (
        verifier.classify_request(
            "POST",
            "https://www.googleadservices.com/pagead/1p-conversion/1/",
            allow_delivery_read_post=False,
            body="event=purchase",
        )
        == "block_other_non_get"
    )
    assert (
        verifier.classify_request(
            "GET",
            "https://sis.redsys.es/payment/start",
            allow_delivery_read_post=False,
        )
        == "block_order_or_payment"
    )


def test_default_verifier_is_read_only_and_requires_saved_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPEN_GROCERY_EROSKI_STATE_PATH", str(tmp_path / "missing.json"))
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False)

    code, report = verifier.verify()

    assert code == 1
    assert report["ok"] is False
    assert report["read_only"] is True
    assert report["retailer_write_performed"] is False
    assert report["order_or_payment_attempted"] is False
    assert report["storage_state_written"] is False


def test_delivery_post_opt_in_requires_explicit_write_gate(monkeypatch, tmp_path) -> None:
    state = tmp_path / "storage_state.json"
    state.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    monkeypatch.setenv("OPEN_GROCERY_EROSKI_STATE_PATH", str(state))
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_RETAILER_WRITES", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", raising=False)
    monkeypatch.delenv("OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION", raising=False)

    code, report = verifier.verify(allow_delivery_read_post=True)

    assert code == 2
    assert report["ok"] is False
    assert report["read_only"] is False
    assert report["retailer_write_performed"] is False
    assert "OPEN_GROCERY_ENABLE_RETAILER_WRITES" in report["reason"]


def test_order_opt_in_is_rejected_even_for_read_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPEN_GROCERY_EROSKI_STATE_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("OPEN_GROCERY_ENABLE_ORDER_SUBMISSION", "1")

    code, report = verifier.verify()

    assert code == 2
    assert report["order_or_payment_attempted"] is False
    assert report["retailer_write_performed"] is False
