"""Safe orchestration for the opt-in live Gadis cart mutation test."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Mapping

from open_grocery_mcp.confirmations import ConfirmationStore
from open_grocery_mcp.drafts import DraftCartStore
from open_grocery_mcp.errors import ConfirmationRequired
from open_grocery_mcp.models import money
from open_grocery_mcp.registry import ProviderRegistry
from open_grocery_mcp.workflows import RetailerWorkflowService

from .common import (
    MAX_ADDED_VALUE,
    ORDER_OPT_INS,
    basket_for_product,
    cart_fingerprint,
    decimal_value,
    enabled,
    product_ids,
    quantity,
    require_http_cart,
    select_safe_product,
)

ProductSelector = Callable[[set[str], Decimal, str | None], dict[str, Any]]


def _commit_quantity(
    workflows: RetailerWorkflowService,
    drafts: DraftCartStore,
    product: Mapping[str, Any],
    *,
    quantity_value: int,
    current_version: int,
    max_total: Decimal,
) -> tuple[str, str]:
    draft = drafts.create(basket_for_product(product, quantity_value))
    prepared = workflows.prepare_cart_update(
        store="gadis",
        draft_id=draft["draft_id"],
        max_total=float(max_total),
        expected_cart_version=current_version,
        mode="merge",
    )
    confirmation_id = str(prepared["confirmation_id"])
    phrase = str(prepared["confirmation_phrase"])
    workflows.commit_cart_update(confirmation_id, phrase)
    return confirmation_id, phrase


def _remove_if_present(
    workflows: RetailerWorkflowService,
    provider: Any,
    product: Mapping[str, Any],
    *,
    original_total: Decimal,
) -> tuple[dict[str, Any], bool]:
    """Read first and issue a removal only when the test line still exists."""
    current = require_http_cart(workflows)
    product_id = str(product["product_id"])
    if quantity(current, product_id) <= 0:
        return current, False
    cleanup_cap = max(original_total, decimal_value(current.get("total")), Decimal("0.01"))
    plan = provider.preview_cart_update(
        [
            {
                "product_id": product_id,
                "name": str(product["name"]),
                "quantity": 0,
                "unit_price": float(decimal_value(product.get("price"))),
            }
        ],
        mode="merge",
        expected_version=int(current.get("version") or 0),
        max_total=cleanup_cap,
    )
    provider.commit_cart_update(plan)
    return require_http_cart(workflows), True


def verify(
    *,
    allow_reversible_cart_write: bool,
    max_added_value: Decimal = MAX_ADDED_VALUE,
    registry: Any | None = None,
    product_selector: ProductSelector = select_safe_product,
) -> tuple[int, dict[str, Any]]:
    report: dict[str, Any] = {
        "ok": False,
        "store": "gadis",
        "backend": "gadis_http",
        "retailer_write_performed": False,
        "order_or_payment_attempted": False,
        "secrets_exposed": False,
        "steps": {
            "add_verified": False,
            "quantity_two_verified": False,
            "quantity_one_verified": False,
            "remove_verified": False,
        },
    }
    if not allow_reversible_cart_write:
        return 2, {**report, "reason": "explicit --allow-reversible-cart-write is required"}
    if not enabled("OPEN_GROCERY_ENABLE_RETAILER_WRITES"):
        return 2, {**report, "reason": "OPEN_GROCERY_ENABLE_RETAILER_WRITES=1 is required"}
    if any(enabled(name) for name in ORDER_OPT_INS):
        return 2, {**report, "reason": "order-submission opt-ins must be disabled"}
    if not (Decimal("0") < max_added_value <= MAX_ADDED_VALUE):
        return 2, {**report, "reason": "max_added_value must be in (0, 5.00] EUR"}

    owned_registry = registry is None
    active_registry = registry or ProviderRegistry()
    drafts = DraftCartStore()
    workflows = RetailerWorkflowService(
        active_registry,
        drafts,
        ConfirmationStore(ttl_seconds=300),
    )
    provider = active_registry.get("gadis")
    baseline: dict[str, Any] | None = None
    product: dict[str, Any] | None = None
    failure_stage: str | None = "preflight"
    failure_type: str | None = None
    write_attempts = 0

    try:
        status = workflows.account_status("gadis")
        if not status.get("authenticated") or status.get("account_backend") != "gadis_http":
            raise RuntimeError("the saved Gadis HTTP session is not authenticated")
        baseline = require_http_cart(workflows)
        original_total = decimal_value(baseline.get("total"))
        store_id = str(baseline.get("store_id") or "").strip() or None
        candidate = product_selector(product_ids(baseline), max_added_value, store_id)
        if str(candidate.get("product_id", "")) in product_ids(baseline):
            raise RuntimeError("the selected test product already exists in the starting cart")
        if decimal_value(candidate.get("price")) * 2 > max_added_value:
            raise RuntimeError("the selected test product exceeds the quantity-two cap")
        product = dict(candidate)
        hard_total_cap = original_total + max_added_value

        failure_stage = "add"
        before = require_http_cart(workflows)
        write_attempts += 1
        confirmation_id, phrase = _commit_quantity(
            workflows,
            drafts,
            product,
            quantity_value=1,
            current_version=int(before["version"]),
            max_total=hard_total_cap,
        )
        after = require_http_cart(workflows)
        if quantity(after, str(product["product_id"])) != 1:
            raise RuntimeError("quantity one was not observed after add")
        if int(after["version"]) == int(before["version"]):
            raise RuntimeError("cart version did not change after add")
        report["steps"]["add_verified"] = True

        failure_stage = "confirmation_single_use"
        try:
            workflows.commit_cart_update(confirmation_id, phrase)
        except ConfirmationRequired:
            report["confirmation_single_use"] = True
        else:
            raise RuntimeError("a consumed confirmation was accepted twice")

        for value, key in ((2, "quantity_two_verified"), (1, "quantity_one_verified")):
            failure_stage = f"quantity_{value}"
            before = require_http_cart(workflows)
            write_attempts += 1
            _commit_quantity(
                workflows,
                drafts,
                product,
                quantity_value=value,
                current_version=int(before["version"]),
                max_total=hard_total_cap,
            )
            after = require_http_cart(workflows)
            if quantity(after, str(product["product_id"])) != value:
                raise RuntimeError("reviewed quantity was not observed")
            if int(after["version"]) == int(before["version"]):
                raise RuntimeError("cart version did not change after quantity update")
            if decimal_value(after.get("total")) > hard_total_cap:
                raise RuntimeError("cart total exceeded the reversible-test cap")
            report["steps"][key] = True

        failure_stage = "remove"
        before = require_http_cart(workflows)
        if quantity(before, str(product["product_id"])) > 0:
            write_attempts += 1
        final, removal_written = _remove_if_present(
            workflows,
            provider,
            product,
            original_total=original_total,
        )
        if quantity(final, str(product["product_id"])) != 0:
            raise RuntimeError("test product remained after removal")
        if removal_written and int(final["version"]) == int(before["version"]):
            raise RuntimeError("cart version did not change after removal")
        report["steps"]["remove_verified"] = True
        failure_stage = None
    except Exception as exc:
        failure_type = type(exc).__name__
    finally:
        cleanup_error: str | None = None
        final_cart: dict[str, Any] | None = None
        if baseline is not None and product is not None:
            try:
                final_cart, cleanup_written = _remove_if_present(
                    workflows,
                    provider,
                    product,
                    original_total=decimal_value(baseline.get("total")),
                )
                if cleanup_written:
                    write_attempts += 1
            except Exception as exc:
                cleanup_error = type(exc).__name__
        if baseline is not None and final_cart is not None:
            start = cart_fingerprint(baseline)
            end = cart_fingerprint(final_cart)
            report["initial_and_final_signature_match"] = start[0] == end[0]
            report["initial_and_final_total_match"] = start[1] == end[1]
            report["initial_and_final_count_match"] = start[2] == end[2]
            report["cart_restored"] = start == end
        elif baseline is not None:
            report["cart_restored"] = False
        report["write_attempts"] = write_attempts
        report["retailer_write_performed"] = write_attempts > 0
        report["reads_after_each_write"] = failure_stage is None
        report["max_added_value_text"] = money(max_added_value)
        if failure_stage:
            report["failure_stage"] = failure_stage
        if failure_type:
            report["failure_type"] = failure_type
        if cleanup_error:
            report["cleanup_failure_type"] = cleanup_error
        if owned_registry:
            active_registry.close()

    report["ok"] = bool(
        failure_stage is None
        and all(report["steps"].values())
        and report.get("confirmation_single_use") is True
        and report.get("cart_restored") is True
    )
    return (0 if report["ok"] else 1), report
