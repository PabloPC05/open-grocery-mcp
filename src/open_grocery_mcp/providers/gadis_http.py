"""Authenticated Gadis HTTP client built from the captured value-free contract.

The Gadisline storefront authenticates with NextAuth + Keycloak. A saved
Playwright session yields a NextAuth cookie; ``/api/auth/session`` then exposes
``token.accessToken``, the Keycloak bearer token used by the retailer's public
microservices (``clients``, ``store``, ``cart``, ``catalog``, ``masters``).

The client reproduces the contract captured and verified value-free: session
status, client profile, saved addresses, delivery calendar, cart read and the
reversible ``/api/config/updateProduct`` cart mutation. Checkout creation and
order submission stay disabled and behind the browser provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from open_grocery_mcp.errors import (
    AuthenticationRequired,
    OrderSubmissionDisabled,
    ProviderError,
)
from open_grocery_mcp.models import as_decimal, money
from open_grocery_mcp.providers.gadis_session import GadisSessionClient

_SITE_BASE = "https://site.gadisline.com/api/v3"
_STORE_BASE = "https://store.gadisline.com/api/v3"
_CART_BASE = "https://cart.gadisline.com/api/v3"
_CLIENTS_BASE = "https://clients.gadisline.com/api/v3"
_WWW_BASE = "https://www.gadisline.com"
_DOMAIN = "www.gadisline.com"
_USER_AGENT = (
    "open-grocery-mcp/0.4 (+https://github.com/PabloPC05/open-grocery-mcp)"
)
_BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
_PRIVATE_PATH_SEGMENT = re.compile(
    r"(?i)(/(?:clients?|addresses?|carts?|checkouts?|orders?|payments?|users?|accounts?))/[^/?#]+"
)


class _ConfigRouteUnavailable(ProviderError):
    """The www wrapper is definitively absent before any retailer write."""


class GadisHTTPClient:
    """Read-only authenticated Gadis microservice client."""

    def __init__(
        self,
        *,
        state_path: str | os.PathLike[str] | None = None,
        site_id: str | None = None,
        store_id: str | None = None,
        language: str = "es",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.language = "gl" if language.lower().strip() == "gl" else "es"
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        )
        self._session = GadisSessionClient(
            state_path=state_path,
            timeout=timeout,
            client=self._client,
        )
        self._configured_site_id = (site_id or "").strip() or None
        self._configured_store_id = (store_id or "").strip() or None
        self._site_id: str | None = None
        self._store_id: str | None = None
        self._access_token: str | None = None
        self._build_id: str | None = None
        self._bootstrap_lock = threading.Lock()
        self._build_id_lock = threading.Lock()

    def _bootstrap(self) -> tuple[str, str]:
        if self._site_id and self._store_id:
            return self._site_id, self._store_id
        with self._bootstrap_lock:
            if self._site_id and self._store_id:
                return self._site_id, self._store_id
            try:
                response = self._client.get(
                    f"{_SITE_BASE}/sites",
                    params={"domain": _DOMAIN},
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                raise ProviderError(f"Gadis site lookup failed: {exc}") from exc
            except ValueError as exc:
                raise ProviderError("Gadis site lookup returned invalid JSON") from exc
            elements = payload.get("elements", []) if isinstance(payload, Mapping) else []
            if not elements:
                raise ProviderError("Gadis site lookup returned no storefront")
            first = elements[0]
            site_id = str(self._configured_site_id or first.get("id", "")).strip()
            store_id = str(
                self._configured_store_id or first.get("default_assortment_store", "")
            ).strip()
            if not site_id or not store_id:
                raise ProviderError("Gadis did not return a usable site/store identifier")
            self._site_id, self._store_id = site_id, store_id
            return site_id, store_id

    def _bearer_token(self) -> str | None:
        if self._access_token:
            return self._access_token
        token, _ = self._session.session_token()
        if token:
            self._access_token = token
        return token

    def _auth_headers(self) -> dict[str, str]:
        site_id, store_id = self._bootstrap()
        headers: dict[str, str] = {
            "accept-language": self.language.upper(),
            "site-id": site_id,
            "store-id": store_id,
        }
        token = self._bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        method = method.upper()
        safe_url = _PRIVATE_PATH_SEGMENT.sub(r"\1/<private>", url)
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Gadis {method} {safe_url} transport failed "
                f"({type(exc).__name__})",
                operation=f"{method} {safe_url}",
            ) from exc
        if response.status_code == 401:
            self._access_token = None
            raise AuthenticationRequired(
                "Gadis session is expired or invalid; run login_with_browser"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderError(
                f"Gadis {method} {safe_url} returned HTTP {response.status_code}",
                status_code=response.status_code,
                operation=f"{method} {safe_url}",
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Gadis {safe_url} returned invalid JSON",
                status_code=response.status_code,
                operation=f"{method} {safe_url}",
            ) from exc

    def status(self) -> dict[str, Any]:
        return self._session.status()

    def _cookie_header(self) -> str | None:
        return self._session._cookie_header()

    def _get_build_id(self) -> str:
        if self._build_id:
            return self._build_id
        with self._build_id_lock:
            if self._build_id:
                return self._build_id
            cookie_header = self._cookie_header()
            headers = {"Cookie": cookie_header} if cookie_header else {}
            try:
                response = self._client.get(_WWW_BASE + "/", headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"Gadis storefront bootstrap failed: {exc}") from exc
            match = _BUILD_ID_RE.search(response.text)
            if not match:
                raise ProviderError("Gadis storefront did not expose a buildId")
            self._build_id = match.group(1)
            return self._build_id

    def _config_headers(self, *, content_type: bool = False) -> dict[str, str]:
        site_id, store_id = self._bootstrap()
        headers: dict[str, str] = {
            "accept-language": self.language.upper(),
            "site-id": site_id,
            "store-id": store_id,
        }
        cookie_header = self._cookie_header()
        if cookie_header:
            headers["Cookie"] = cookie_header
        token = self._bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _config_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any],
    ) -> Any:
        method = method.upper()
        try:
            response = self._client.request(
                method,
                f"{_WWW_BASE}{path}",
                json=json_body,
                headers=self._config_headers(content_type=True),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Gadis {method} {path} transport failed ({type(exc).__name__})",
                operation=f"{method} {path}",
            ) from exc
        if response.status_code == 401:
            self._access_token = None
            raise AuthenticationRequired(
                "Gadis session is expired or invalid; run login_with_browser"
            )
        if response.status_code in {404, 405}:
            raise _ConfigRouteUnavailable(
                f"Gadis {method} {path} is not available",
                status_code=response.status_code,
                operation=f"{method} {path}",
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderError(
                f"Gadis {method} {path} returned HTTP {response.status_code}",
                status_code=response.status_code,
                operation=f"{method} {path}",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Gadis {path} returned invalid JSON",
                status_code=response.status_code,
                operation=f"{method} {path}",
            ) from exc

    def read_cart(self) -> dict[str, Any]:
        build_id = self._get_build_id()
        cookie_header = self._cookie_header()
        headers = {"Cookie": cookie_header} if cookie_header else {}
        url = (
            f"{_WWW_BASE}/_next/data/{build_id}/{self.language}/pag/"
            "proceso-de-compra/carrito.json?slug=proceso-de-compra&slug=carrito"
        )
        try:
            response = self._client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gadis cart read failed: {exc}") from exc
        except ValueError as exc:
            raise ProviderError("Gadis cart read returned invalid JSON") from exc
        page_props = payload.get("pageProps", {}) if isinstance(payload, Mapping) else {}
        cart = page_props.get("cart") if isinstance(page_props, Mapping) else None
        if not isinstance(cart, Mapping):
            raise ProviderError("Gadis cart read returned no cart")
        return dict(cart)

    @staticmethod
    def _stable_version(cart: Mapping[str, Any]) -> int:
        """Derive an optimistic-locking token from cart content only.

        The retailer bumps ``last_modified_date`` on every cart fetch, so the
        raw timestamp cannot guard against concurrent modifications. A content
        fingerprint is stable across reads of an untouched cart and changes
        whenever lines, totals or the product count change.
        """

        lines = sorted(
            [
                str(raw.get("product_id", "")).strip(),
                str(as_decimal(raw.get("amount")).normalize()),
                str(as_decimal(raw.get("line_price")).normalize()),
            ]
            for raw in cart.get("products", [])
            if isinstance(raw, Mapping)
        )
        material = json.dumps(
            {
                "cart_id": str(cart.get("id", "")).strip(),
                "count": int(as_decimal(cart.get("total_products") or 0)),
                "lines": lines,
                "total": str(as_decimal(cart.get("total_cart_price")).normalize()),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") >> 1

    @staticmethod
    def normalize_cart(cart: Mapping[str, Any]) -> dict[str, Any]:
        lines: list[dict[str, Any]] = []
        products = cart.get("products", [])
        for raw in products if isinstance(products, list) else []:
            if not isinstance(raw, Mapping):
                continue
            product_id = str(raw.get("product_id", "")).strip()
            quantity = as_decimal(raw.get("amount"))
            if not product_id or quantity <= 0:
                continue
            lines.append(
                {
                    "product_id": product_id,
                    "name": str(raw.get("product_name", "")).strip(),
                    "quantity": float(quantity),
                    "line_price": float(as_decimal(raw.get("line_price"))),
                    "unit_price": float(as_decimal(raw.get("line_price")) / quantity),
                    "line_total": float(as_decimal(raw.get("line_price"))),
                }
            )
        total = as_decimal(cart.get("total_cart_price"))
        line_total = sum(
            (
                as_decimal(line.get("line_price"))
                for line in lines
            ),
            Decimal("0"),
        )
        product_total = (
            as_decimal(cart.get("total_product_price"))
            if "total_product_price" in cart
            else line_total
        )
        if product_total < 0:
            raise ProviderError("Gadis cart returned an invalid product total")
        if "costs" in cart:
            raw_costs = cart.get("costs")
            if raw_costs is None:
                costs = Decimal("0")
            elif isinstance(raw_costs, (Mapping, list, tuple, bool)):
                raise ProviderError("Gadis cart returned invalid non-product costs")
            else:
                try:
                    costs = Decimal(str(raw_costs).replace(",", ".").strip())
                except (InvalidOperation, ValueError, AttributeError):
                    raise ProviderError(
                        "Gadis cart returned invalid non-product costs"
                    ) from None
                if not costs.is_finite():
                    raise ProviderError("Gadis cart returned invalid non-product costs")
        else:
            costs = total - product_total
        if costs < 0:
            raise ProviderError("Gadis cart returned invalid non-product costs")
        return {
            "store": "gadis",
            "cart_id": str(cart.get("id", "")).strip() or None,
            "version": GadisHTTPClient._stable_version(cart),
            "products_count": int(cart.get("total_products") or 0),
            "total_product_price": float(product_total),
            "total_product_price_text": money(product_total),
            "non_product_costs": float(costs),
            "non_product_costs_text": money(costs),
            "total": float(total),
            "total_text": money(total),
            "currency": "EUR",
            "lines": lines,
        }

    def cart(self) -> dict[str, Any]:
        return self.normalize_cart(self.read_cart())

    def update_product(
        self,
        cart_id: str,
        store_id: str,
        product_id: str,
        amount: int,
        *,
        preparation_mode_id: Any = None,
        product_note: Any = None,
        substitution_type: Any = None,
    ) -> dict[str, Any]:
        payload = self._config_request(
            "PUT",
            "/api/config/updateProduct",
            json_body={
                "store_id": store_id,
                "cartId": cart_id,
                "productId": product_id,
                "amount": amount,
                "preparation_mode_id": preparation_mode_id,
                "product_note": product_note,
                "substitution_type": substitution_type,
                "summaryCheckout": False,
            },
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Gadis updateProduct returned an invalid response")
        normalized = self.normalize_cart(payload)
        normalized["retailer_cart_modified"] = True
        return normalized

    def profile(self) -> dict[str, Any]:
        payload = self._request("GET", f"{_CLIENTS_BASE}/clients/me")
        if not isinstance(payload, Mapping):
            raise ProviderError("Gadis client profile was not an object")
        client_id = str(payload.get("id", "")).strip()
        return {
            "store": "gadis",
            "profile_present": True,
            "name_present": bool(payload.get("name") or payload.get("given_name")),
            "email_present": bool(payload.get("email")),
            "email_verified": bool(payload.get("email_verified")),
            "phone_verified": bool(payload.get("phone_verified")),
            "complete_register": bool(payload.get("complete_register")),
            "postal_code_present": bool(payload.get("postal_code")),
            "client_id_suffix": client_id[-6:] if client_id else None,
            "profile_values_exposed": False,
        }

    def _addresses(
        self, cart_id: str, *, include_delivery_context: bool
    ) -> list[dict[str, Any]]:
        encoded = quote(str(cart_id).strip(), safe="")
        payload = self._request(
            "GET",
            f"{_CART_BASE}/carts/{encoded}/addresses",
        )
        elements = payload.get("elements", []) if isinstance(payload, Mapping) else payload
        if not isinstance(elements, list):
            raise ProviderError("Gadis addresses returned an invalid response")
        result: list[dict[str, Any]] = []
        for row in elements:
            if not isinstance(row, Mapping):
                continue
            # Only identifiers are exposed; street/personal values stay local.
            entry: dict[str, Any] = {
                "id": str(row.get("id", "")).strip() or None,
                "owner": str(row.get("owner", "")).strip() or None,
                "field_names": sorted(str(key) for key in row),
            }
            if include_delivery_context:
                entry["postal_code"] = str(
                    row.get("postal_code") or row.get("zip_code") or ""
                ).strip() or None
            result.append(entry)
        return result

    def addresses(self, cart_id: str) -> list[dict[str, Any]]:
        return self._addresses(cart_id, include_delivery_context=False)

    def address_contexts(self, cart_id: str) -> list[dict[str, Any]]:
        """Local-only address identifiers plus postal routing context."""
        return self._addresses(cart_id, include_delivery_context=True)

    def _client_addresses(
        self, *, include_delivery_context: bool
    ) -> list[dict[str, Any]]:
        """Saved client addresses from the clients microservice (bearer).

        Rows expose only identifiers (`id`, `owner`) and field names; street
        or personal values never leave this method.
        """
        me = self._request("GET", f"{_CLIENTS_BASE}/clients/me")
        client_id = str(me.get("id", "")).strip() if isinstance(me, Mapping) else ""
        if not client_id:
            raise ProviderError("Gadis profile did not expose a client id")
        payload = self._request(
            "GET",
            f"{_CLIENTS_BASE}/clients/{client_id}/addresses",
        )
        elements = (
            payload.get("elements", []) if isinstance(payload, Mapping) else payload
        )
        if not isinstance(elements, list):
            raise ProviderError("Gadis client addresses returned an invalid response")
        result: list[dict[str, Any]] = []
        for row in elements:
            if not isinstance(row, Mapping):
                continue
            entry: dict[str, Any] = {
                "id": str(row.get("id", "")).strip() or None,
                "owner": str(row.get("owner", "")).strip() or None,
                "field_names": sorted(str(key) for key in row),
            }
            if include_delivery_context:
                entry["postal_code"] = str(
                    row.get("postal_code") or row.get("zip_code") or ""
                ).strip() or None
            result.append(entry)
        return result

    def client_addresses(self) -> list[dict[str, Any]]:
        return self._client_addresses(include_delivery_context=False)

    def client_address_contexts(self) -> list[dict[str, Any]]:
        """Local-only saved address identifiers plus postal routing context."""
        return self._client_addresses(include_delivery_context=True)

    def update_schedule(
        self,
        cart_id: str,
        store_id: str,
        *,
        delivery_date: str,
        schedule_range_id: str | int,
        postal_code: str | None = None,
        shipping_address_id: str | int | None = None,
        shipping_address_owner: str | None = None,
    ) -> dict[str, Any]:
        """Attach a delivery slot to the cart (reversible via delete_schedule).

        The storefront sets schedules through the session-wrapped
        ``/api/config/updateCart`` route, sending the full reviewed cart
        context plus the chosen ``delivery_date``/``schedule_range_id``; that
        route authenticates with the same NextAuth cookies proven by the cart
        mutations. The direct microservice endpoint stays as fallback.
        """
        raw = self.read_cart()
        body: dict[str, Any] = {
            "store_id": str(raw.get("store_id") or store_id or "").strip(),
            "postal_code": str(postal_code or raw.get("postal_code") or ""),
            "delivery_type": str(raw.get("delivery_type") or "HOME_DELIVERY"),
            "comments": str(raw.get("comments") or ""),
            "shipping_address_id": str(
                shipping_address_id
                if shipping_address_id is not None
                else raw.get("shipping_address_id") or ""
            ),
            "shipping_address_owner": str(
                shipping_address_owner
                if shipping_address_owner is not None
                else raw.get("shipping_address_owner") or ""
            ),
            "delivery_date": str(delivery_date).strip(),
            "schedule_range_id": schedule_range_id,
        }
        try:
            payload = self._config_request(
                "PUT",
                "/api/config/updateCart",
                json_body=body,
            )
        except _ConfigRouteUnavailable:
            payload = self._request(
                "PUT",
                f"{_CART_BASE}/carts/{quote(str(cart_id).strip(), safe='')}/schedule",
                json_body={
                    "delivery_date": str(delivery_date).strip(),
                    "schedule_range_id": schedule_range_id,
                },
            )
        if not isinstance(payload, Mapping):
            raise ProviderError("Gadis schedule update returned no cart")
        return self.normalize_cart(payload)

    def delete_schedule(self, cart_id: str) -> dict[str, Any] | None:
        """Remove the cart delivery slot; restores the pre-selection state."""
        try:
            payload = self._config_request(
                "DELETE",
                "/api/config/deleteSchedule",
                json_body={"summaryCheckout": False},
            )
        except _ConfigRouteUnavailable:
            payload = self._request(
                "DELETE",
                f"{_CART_BASE}/carts/{quote(str(cart_id).strip(), safe='')}/schedule",
            )
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise ProviderError("Gadis schedule deletion returned invalid JSON")
        if not payload.get("products") and not payload.get("id"):
            return None
        return self.normalize_cart(payload)

    def prepare_checkout_summary(
        self,
        cart_id: str,
        store_id: str,
        *,
        shipping_address_id: str | int,
        shipping_address_owner: str | None = None,
        delivery_date: str,
        schedule_range_id: str | int,
        postal_code: str | None = None,
        delivery_type: str = "HOME_DELIVERY",
    ) -> dict[str, Any]:
        """Prepare the reversible cart context used by the GET checkout page.

        Live browser evidence shows that ``summaryCheckout=true`` is sufficient
        to navigate to the page that renders delivery and card controls.  The
        separate ``/api/config/checkout`` POST contains payment and terms fields
        and therefore stays outside this safe boundary.
        """
        body: dict[str, Any] = {
            "store_id": str(store_id).strip(),
            "postal_code": "",
            "delivery_type": str(delivery_type).strip(),
            "comments": "",
            "shipping_address_id": str(shipping_address_id),
            "shipping_address_owner": str(shipping_address_owner or ""),
            "delivery_date": str(delivery_date).strip(),
            "schedule_range_id": schedule_range_id,
            "save_order_time": True,
            "summaryCheckout": True,
        }
        raw = self.read_cart()
        body["store_id"] = str(raw.get("store_id") or store_id or "").strip()
        body["postal_code"] = str(postal_code or raw.get("postal_code") or "")
        body["delivery_type"] = str(raw.get("delivery_type") or delivery_type)
        body["comments"] = str(raw.get("comments") or "")
        payload = self._config_request(
            "PUT",
            "/api/config/updateCart",
            json_body=body,
        )
        if not isinstance(payload, Mapping):
            raise ProviderError(
                "Gadis checkout summary preparation returned an invalid response",
                operation="checkout_summary_prepare",
            )
        result = self.normalize_cart(payload)
        result["summary_prepared"] = True
        return result

    def restore_cart_context(
        self,
        baseline: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Restore a previously reread delivery context after a safe probe."""

        body: dict[str, Any] = {
            "store_id": str(baseline.get("store_id") or "").strip(),
            "postal_code": str(baseline.get("postal_code") or ""),
            "delivery_type": str(
                baseline.get("delivery_type") or "HOME_DELIVERY"
            ),
            "comments": str(baseline.get("comments") or ""),
            # The storefront treats JSON null as "leave unchanged" here.
            # Empty strings are its explicit unassigned-cart representation.
            "shipping_address_id": str(
                baseline.get("shipping_address_id") or ""
            ),
            "shipping_address_owner": str(
                baseline.get("shipping_address_owner") or ""
            ),
            "save_order_time": False,
            "summaryCheckout": False,
        }
        if str(baseline.get("delivery_date") or "").strip():
            body["delivery_date"] = str(baseline.get("delivery_date"))
        if str(baseline.get("schedule_range_id") or "").strip():
            body["schedule_range_id"] = baseline.get("schedule_range_id")
        payload = self._config_request(
            "PUT",
            "/api/config/updateCart",
            json_body=body,
        )
        if not isinstance(payload, Mapping):
            raise ProviderError(
                "Gadis cart-context restoration returned an invalid response",
                operation="checkout_context_restore",
            )
        return self.normalize_cart(payload)

    def create_checkout(
        self,
        cart_id: str,
        store_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        """Block the payment-bearing retailer POST at the HTTP boundary."""

        del cart_id, store_id
        raise OrderSubmissionDisabled(
            "Gadis /api/config/checkout carries payment and terms fields; "
            "prepare the reversible checkout summary and finish manually"
        )

    def delivery_slots(
        self,
        postal_code: str | None = None,
        *,
        store_id: str | None = None,
        delivery_type: str = "HOME_DELIVERY",
        init_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read the store delivery calendar for the coming week.

        The live contract requires ``delivery_type=HOME_DELIVERY`` plus an
        ``init_date``/``end_date`` range and the session's postal code; the
        postal code falls back to the one stored on the current cart.
        """
        selected_store = store_id or self._bootstrap()[1]
        effective_postal_code = str(postal_code or "").strip()
        if not effective_postal_code:
            try:
                effective_postal_code = str(
                    self.read_cart().get("postal_code") or ""
                ).strip()
            except (AuthenticationRequired, ProviderError):
                effective_postal_code = ""
        params: dict[str, str] = {
            "delivery_type": delivery_type,
            "init_date": init_date or date.today().isoformat(),
            "end_date": end_date or (date.today() + timedelta(days=7)).isoformat(),
        }
        if effective_postal_code:
            params["postal_code"] = effective_postal_code
        payload = self._request(
            "GET",
            f"{_STORE_BASE}/stores/{quote(str(selected_store), safe='')}/calendar",
            params=params,
        )
        elements = payload.get("elements", []) if isinstance(payload, Mapping) else payload
        if not isinstance(elements, list):
            raise ProviderError("Gadis delivery calendar returned an invalid response")
        result: list[dict[str, Any]] = []
        for day in elements:
            if not isinstance(day, Mapping):
                continue
            day_date = str(day.get("date", "")).strip()
            ranges = day.get("schedule_ranges", [])
            for slot in ranges if isinstance(ranges, list) else []:
                if not isinstance(slot, Mapping):
                    continue
                result.append(
                    {
                        "id": str(slot.get("id", "")).strip() or None,
                        "date": day_date or None,
                        "start": str(slot.get("init_time", "")).strip() or None,
                        "end": str(slot.get("end_time", "")).strip() or None,
                        "available": bool(slot.get("available")),
                        "active": bool(slot.get("active")),
                        "max_lines": slot.get("max_lines"),
                    }
                )
        return result

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
