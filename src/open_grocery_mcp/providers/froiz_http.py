"""Authenticated Froiz HTTP client built from the verified Nuxt contract.

The Froiz storefront is a Nuxt SPA whose REST API lives on
``servicios.froiz.com``. Session uses an OAuth bearer token that the SPA
rotates at every boot, so this client bootstraps a fresh token by opening the
saved browser session headlessly whenever the stored one is rejected, and
caches it locally until it expires.

Verified live contract (value-free):

- ``POST /api/cart`` with ``{"items": [...]}`` creates a cart (201);
- ``PUT /api/cart/{id}`` replaces the full reviewed cart object;
- ``GET /api/cart/raw/{id}`` reads the current cart;
- ``GET /api/cart/{id}`` independently reads the processed cart with product
  prices, subtotal and delivery-inclusive total;
- ``DELETE /api/cart/{id}`` disposes of a whole cart;
- the user's active cart id comes from ``/api/me``
  (``userChannelOptions[channelName == "shop"].cartId``).

Order submission (`/api/orders`) and payment (`/api/payment/*`) are never
called by this client and stay behind separate guarded workflows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

import httpx

from open_grocery_mcp.errors import AuthenticationRequired, ProviderError
from open_grocery_mcp.providers.froiz_pricing import normalize_pricing

_API_BASE = "https://servicios.froiz.com"
_SITE_BASE = "https://supermercado.froiz.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_TOKEN_MAX_AGE_SECONDS = 8 * 3600


def _default_state_path() -> Path:
    configured = os.getenv("OPEN_GROCERY_FROIZ_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(
        os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
    ).expanduser()
    return root / "froiz" / "storage_state.json"


def _default_token_cache_path() -> Path:
    root = Path(
        os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")
    ).expanduser()
    return root / "froiz" / "http_token.json"


class FroizHTTPClient:
    """Read-write authenticated Froiz cart client over the Nuxt REST API."""

    def __init__(
        self,
        *,
        state_path: str | os.PathLike[str] | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        token_cache_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else _default_state_path()
        self._token_cache_path = (
            Path(token_cache_path).expanduser()
            if token_cache_path
            else _default_token_cache_path()
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
                "Origin": _SITE_BASE,
                "Referer": _SITE_BASE + "/",
            },
        )
        self._access_token: str | None = None
        self._bootstrap_lock = threading.Lock()

    # ------------------------------------------------------------------ auth

    def _state_fingerprint(self) -> str | None:
        try:
            return hashlib.sha256(self.state_path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _stored_token(self) -> str | None:
        try:
            payload = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        token = str(payload.get("token") or "").strip()
        try:
            fetched_at = float(payload.get("fetched_at") or 0)
        except (TypeError, ValueError):
            return None
        now = time.time()
        state_fingerprint = self._state_fingerprint()
        if (
            not token
            or fetched_at > now + 300
            or now - fetched_at > _TOKEN_MAX_AGE_SECONDS
            or not state_fingerprint
            or payload.get("state_fingerprint") != state_fingerprint
        ):
            return None
        return token

    def _store_token(self, token: str) -> None:
        state_fingerprint = self._state_fingerprint()
        if not state_fingerprint:
            return
        temporary: Path | None = None
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._token_cache_path.parent,
                prefix=f".{self._token_cache_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    {
                        "token": token,
                        "fetched_at": time.time(),
                        "state_fingerprint": state_fingerprint,
                    },
                    handle,
                )
            # Protect before publishing the cache.  NamedTemporaryFile is
            # already mode 0600 on supported platforms; chmod also covers a
            # permissive process umask.
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary, self._token_cache_path)
            os.chmod(self._token_cache_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _save_browser_state(self, context: Any) -> None:
        """Publish refreshed Playwright state only after complete serialization."""

        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
            context.storage_state(path=str(temporary))
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary, path)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _cookie_token(self) -> str | None:
        """Return the rotated-out cookie token; kept as a last resort."""
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(state, Mapping):
            return None
        now = time.time()
        for row in state.get("cookies", []) or []:
            if not isinstance(row, Mapping):
                continue
            domain = str(row.get("domain") or "").strip().lower().lstrip(".")
            path = str(row.get("path") or "/").strip() or "/"
            try:
                expires = float(row.get("expires", -1))
            except (TypeError, ValueError):
                continue
            if (
                str(row.get("name")) == "auth._token.froiz"
                and domain in {"froiz.com", "supermercado.froiz.com"}
                and path == "/"
                and (expires <= 0 or expires > now)
            ):
                value = unquote(str(row.get("value", ""))).strip()
                if value.lower().startswith("bearer "):
                    value = value[7:].strip()
                return value or None
        return None

    def _bootstrap_token_via_browser(self) -> str | None:
        """Open the saved session headlessly and grab the SPA's fresh bearer.

        The storefront rotates its OAuth access token on every boot and keeps
        the new one in memory/session storage, so the only reliable source for
        a working token is the rendered session itself.
        """
        if not self.state_path.exists():
            return None
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None

        grabbed: dict[str, str | None] = {"token": None}
        request_tokens: dict[int, str] = {}
        token_candidates: list[str] = []
        tokens_seen: set[str] = set()

        def on_request(request: Any) -> None:
            parsed = urlsplit(str(request.url))
            if (
                parsed.scheme == "https"
                and parsed.hostname == "servicios.froiz.com"
                and parsed.path.startswith("/api/")
                and str(request.method).upper() == "GET"
            ):
                auth = request.headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    token = auth[7:].strip()
                    if token:
                        if token not in tokens_seen:
                            tokens_seen.add(token)
                            token_candidates.append(token)
                        if parsed.path == "/api/me":
                            request_tokens[id(request)] = token

        def on_response(response: Any) -> None:
            if grabbed["token"] is not None:
                return
            request = response.request
            token = request_tokens.get(id(request))
            if not token or not 200 <= int(response.status) < 300:
                return
            try:
                payload = response.json()
            except (TypeError, ValueError):
                return
            if not self._valid_authenticated_me(payload):
                return
            grabbed["token"] = token

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        storage_state=str(self.state_path),
                        locale="es-ES",
                        viewport={"width": 1280, "height": 900},
                    )
                    context.on("request", on_request)
                    context.on("response", on_response)
                    page = context.new_page()
                    page.set_default_timeout(45000)
                    try:
                        page.goto(_SITE_BASE + "/", wait_until="commit")
                    except Exception:
                        pass
                    page.wait_for_timeout(6000)
                    for token in self._storage_token_candidates(page):
                        if token not in tokens_seen:
                            tokens_seen.add(token)
                            token_candidates.append(token)
                    if grabbed["token"] is None:
                        for token in token_candidates:
                            if self._token_authenticated_in_page(page, token):
                                grabbed["token"] = token
                                break
                    self._save_browser_state(context)
                finally:
                    browser.close()
        except Exception:
            return None
        return grabbed["token"] or None

    @staticmethod
    def _storage_token_candidates(page: Any) -> tuple[str, ...]:
        """Read exact Nuxt auth keys, never arbitrary browser storage."""

        try:
            origin = str(page.evaluate("() => location.origin"))
            if origin != _SITE_BASE:
                return ()
            values = page.evaluate(
                """
                () => {
                  const keys = ['auth._token.froiz', 'auth._token.local'];
                  const values = [];
                  for (const storage of [localStorage, sessionStorage]) {
                    for (const key of keys) {
                      const value = storage.getItem(key);
                      if (typeof value === 'string' && value.trim()) values.push(value);
                    }
                  }
                  return values;
                }
                """
            )
        except Exception:
            return ()
        if not isinstance(values, list):
            return ()
        candidates: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            token = value.strip().strip('"')
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            if token and not any(char.isspace() for char in token):
                candidates.append(token)
        return tuple(dict.fromkeys(candidates))

    @staticmethod
    def _token_authenticated_in_page(page: Any, token: str) -> bool:
        """Validate an observed candidate without returning profile data to Python."""

        try:
            current = urlsplit(str(page.url))
            configured = urlsplit(_SITE_BASE)
            trusted_page = (
                configured.scheme == "https"
                and current.scheme == "https"
                and current.hostname == configured.hostname
                and (current.port or 443) == (configured.port or 443)
            )
        except Exception:
            trusted_page = False
        if not token or not trusted_page:
            return False
        try:
            return page.evaluate(
                """
                async (token) => {
                  try {
                    const response = await fetch(
                      'https://servicios.froiz.com/api/me',
                      {
                        method: 'GET',
                        credentials: 'omit',
                        cache: 'no-store',
                        headers: { Authorization: `Bearer ${token}` },
                      },
                    );
                    if (response.status < 200 || response.status >= 300) return false;
                    const payload = await response.json();
                    if (!payload || typeof payload !== 'object') return false;
                    if (payload.authenticated === false) return false;
                    const identity = payload.id ?? payload.userId;
                    return identity !== null && identity !== undefined && identity !== ''
                      && typeof identity !== 'boolean'
                      && Array.isArray(payload.userChannelOptions);
                  } catch (_) {
                    return false;
                  }
                }
                """,
                token,
            ) is True
        except Exception:
            return False

    @staticmethod
    def _valid_authenticated_me(payload: Any) -> bool:
        """Recognize the authenticated ``/api/me`` response shape.

        A bearer observed on an arbitrary API request is not evidence that it
        belongs to a signed-in account: the storefront can issue guest traffic
        before authentication settles.  Require the identity and channel
        fields used by the cart contract before accepting a token.
        """
        if not isinstance(payload, Mapping):
            return False
        if payload.get("authenticated") is False:
            return False
        identity = payload.get("id") or payload.get("userId")
        if identity in (None, "") or isinstance(identity, bool):
            return False
        return isinstance(payload.get("userChannelOptions"), list)

    def _bearer(self) -> str:
        if self._access_token:
            return self._access_token
        with self._bootstrap_lock:
            if self._access_token:
                return self._access_token
            token = self._stored_token() or self._cookie_token()
            if token:
                self._access_token = token
            return self._access_token or ""

    def _refresh_token(self) -> str | None:
        with self._bootstrap_lock:
            self._access_token = None
            token = self._bootstrap_token_via_browser()
            if token:
                self._access_token = token
                self._store_token(token)
            return self._access_token

    def invalidate_session(self) -> None:
        self._access_token = None
        try:
            self._token_cache_path.unlink(missing_ok=True)
        except OSError:
            pass

    # --------------------------------------------------------------- requests

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        allow_refresh: bool = True,
    ) -> Any:
        method = method.upper()
        url = f"{_API_BASE}{path}"
        token = self._bearer()
        headers = (
            {"Authorization": f"Bearer {token}"} if token else {}
        )
        try:
            response = self._client.request(
                method, url, json=json_body, params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Froiz request failed: {exc}") from exc
        if response.status_code == 401:
            self._access_token = None
            if allow_refresh and method in {"GET", "HEAD", "OPTIONS"}:
                refreshed = self._refresh_token()
                if refreshed:
                    return self._request(
                        method,
                        path,
                        json_body=json_body,
                        params=params,
                        allow_refresh=False,
                    )
            raise AuthenticationRequired(
                "Froiz session is expired or invalid; the request was not retried"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderError(
                f"Froiz {method} {path} returned HTTP {response.status_code}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Froiz {path} returned invalid JSON") from exc

    # ------------------------------------------------------------------ carts

    @staticmethod
    def stable_version(payload: Mapping[str, Any]) -> int:
        """Content-derived version: the API exposes no mutation counter."""

        def item_key(item: Mapping[str, Any]) -> list[str]:
            product = item.get("product")
            product_id = (
                str(item.get("product_id") or "")
                or str((product or {}).get("id") if isinstance(product, Mapping) else "")
            )
            return [
                product_id,
                str(item.get("qty", "")),
                str(item.get("unit", "")),
                str(item.get("comment", "")),
                str(item.get("enabled", "")),
                repr(item.get("units")) if item.get("units") is not None else "<absent>",
                str(
                    _as_decimal(
                        (
                            (product or {}).get("order_price")
                            or (product or {}).get("base_price")
                            or (product or {}).get("price")
                        )
                        if isinstance(product, Mapping)
                        else None,
                        default="0",
                    ).normalize()
                ),
            ]

        items = sorted(
            item_key(item)
            for item in payload.get("items", [])
            if isinstance(item, Mapping)
        )
        material = json.dumps(
            {
                "items": items,
                "cart_id": str(payload.get("id") or ""),
                "total": str(
                    _as_decimal(payload.get("total"), default="0").normalize()
                ),
                "subtotal": str(
                    _as_decimal(payload.get("subtotal"), default="0").normalize()
                ),
                "count": len(items),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") >> 1

    @staticmethod
    def normalize_cart(payload: Mapping[str, Any]) -> dict[str, Any]:
        lines = []
        for item in payload.get("items", []) or []:
            if not isinstance(item, Mapping):
                continue
            product = item.get("product")
            product_map = product if isinstance(product, Mapping) else {}
            product_id = str(
                item.get("product_id") or product_map.get("id") or ""
            ).strip()
            if not product_id:
                continue
            price = (
                product_map.get("order_price")
                or product_map.get("base_price")
                or product_map.get("price")
            )
            quantity = _as_decimal(item.get("qty"), default="0")
            if quantity <= 0 or item.get("enabled") is False:
                continue
            lines.append(
                {
                    "product_id": product_id,
                    "name": str(product_map.get("name") or ""),
                    "quantity": float(quantity),
                    "unit_price": float(_as_decimal(price)) if price is not None else 0.0,
                    "metadata": normalize_pricing(
                        product_map,
                        price_source="authenticated.cart.order_price",
                    ),
                }
            )
        total = _as_decimal(payload.get("total"))
        subtotal_value = payload.get("subtotal")
        subtotal = (
            _as_decimal(subtotal_value)
            if subtotal_value not in (None, "")
            else total
        )
        return {
            "store": "froiz",
            "cart_id": str(payload.get("id", "")).strip() or None,
            "version": FroizHTTPClient.stable_version(payload),
            "products_count": len(lines),
            "total": float(total),
            "total_text": f"{total:.2f}",
            "subtotal": float(subtotal),
            "subtotal_text": f"{subtotal:.2f}",
            "currency": "EUR",
            "lines": lines,
        }

    def channel_cart_id(self) -> str | None:
        me_payload = self.me()
        options = (
            me_payload.get("userChannelOptions", [])
            if isinstance(me_payload, Mapping)
            else []
        )
        for option in options if isinstance(options, list) else []:
            if (
                isinstance(option, Mapping)
                and option.get("channelName") == "shop"
                and option.get("cartId")
            ):
                return str(option["cartId"])
        return None

    def me(self, *, allow_browser_refresh: bool = True) -> dict[str, Any]:
        payload = self._request(
            "GET",
            "/api/me",
            allow_refresh=allow_browser_refresh,
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz profile returned an invalid response")
        return payload

    def addresses(self) -> list[dict[str, Any]]:
        """Saved addresses from /api/me with personal values redacted."""
        me_payload = self.me()
        rows = me_payload.get("userAddresses", [])
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            result.append(
                {
                    "id": str(row.get("id", "")).strip() or None,
                    "is_default": bool(row.get("isDefault")),
                    "field_names": sorted(str(key) for key in row),
                }
            )
        return result

    def default_postal_code(
        self,
        *,
        allow_browser_refresh: bool = True,
    ) -> str | None:
        for row in self.me(
            allow_browser_refresh=allow_browser_refresh
        ).get("userAddresses", []) or []:
            if isinstance(row, Mapping) and row.get("isDefault"):
                code = str(row.get("postalCode") or "").strip()
                if code:
                    return code
        return None

    def postal_code_for_address(self, address_id: str | int) -> str:
        wanted = str(address_id).strip()
        for row in self.me().get("userAddresses", []) or []:
            if not isinstance(row, Mapping) or str(row.get("id")) != wanted:
                continue
            code = str(row.get("postalCode") or "").strip()
            if not re.fullmatch(r"\d{5}", code):
                raise ProviderError(
                    "selected Froiz address has no usable five-digit postal code"
                )
            return code
        raise ProviderError("selected Froiz address was not found in the live session")

    def store_by_postal_code(
        self,
        postal_code: str,
        *,
        allow_browser_refresh: bool = True,
    ) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/api/stores/postalcode/{str(postal_code).strip()}",
            allow_refresh=allow_browser_refresh,
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz store lookup returned an invalid response")
        return payload

    def delivery_calendar(self, postal_code: str | None = None) -> list[dict[str, Any]]:
        """Delivery slots for the store serving a postal code.

        Live contract: ``GET /api/stores/postalcode/{cp}`` yields the store's
        ``codEnt``/``codSubent`` and ``GET
        /api/deliverymatrix/calendar/{codEnt}_{codSubent}`` returns a week of
        ``deliveryCalendar`` days each holding six two-hour ``slots``.
        """
        effective = str(postal_code or "").strip()
        if not effective:
            effective = str(self.default_postal_code() or "").strip()
        if not effective:
            raise ProviderError("Froiz delivery calendar needs a postal code")
        if not re.fullmatch(r"\d{5}", effective):
            raise ProviderError("Froiz delivery calendar needs a five-digit postal code")
        store = self.store_by_postal_code(effective)
        if store.get("hasDelivery") is not True:
            raise ProviderError("Froiz does not confirm delivery for this postal code")
        code = store.get("codEnt")
        subcode = store.get("codSubent")
        if code in (None, "") or subcode in (None, ""):
            raise ProviderError("Froiz store lookup lacked codEnt/codSubent")
        payload = self._request(
            "GET", f"/api/deliverymatrix/calendar/{code}_{subcode}"
        )
        calendar = (
            payload.get("deliveryCalendar")
            if isinstance(payload, Mapping)
            else None
        )
        if calendar is None and isinstance(payload, Mapping):
            calendar = payload.get("elements")
        if not isinstance(calendar, list):
            raise ProviderError(
                "Froiz delivery calendar returned an invalid response"
            )
        slots: list[dict[str, Any]] = []
        for day in calendar:
            if not isinstance(day, Mapping):
                continue
            date = str(day.get("date", "")).strip()
            day_active = bool(day.get("active"))
            for slot in day.get("slots", []) or []:
                if not isinstance(slot, Mapping):
                    continue
                text = str(slot.get("slotText", "")).strip()
                start, _, end = text.partition(" - ")
                slots.append(
                    {
                        "id": f"{date}#{slot.get('slotNumber')}",
                        "date": date,
                        "start": start.strip() or None,
                        "end": end.strip() or None,
                        "available": bool(slot.get("active")) and day_active,
                        "active": bool(slot.get("active")),
                    }
                )
        return slots

    def raw_cart(self, cart_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/cart/raw/{str(cart_id).strip()}")
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz raw cart returned an invalid response")
        return payload

    def processed_cart(self, cart_id: str) -> dict[str, Any]:
        """Read an independently processed cart, including prices and totals."""

        payload = self._request("GET", f"/api/cart/{str(cart_id).strip()}")
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz processed cart returned an invalid response")
        return payload

    def search_products(
        self,
        term: str,
        *,
        store: str,
        page: int = 1,
        size: int = 20,
        allow_browser_refresh: bool = True,
    ) -> list[dict[str, Any]]:
        """Search the authenticated, store-specific Froiz catalogue.

        The Nuxt storefront sends this GET to ``/api/products`` with the
        selected ``codEnt_codSubent`` store.  This is deliberately separate
        from the public Empathy catalogue: cart probes must select an SKU and
        price from the same location-aware account context.
        """
        clean_term = str(term or "").strip()
        clean_store = str(store or "").strip()
        if not clean_term:
            raise ProviderError("Froiz product search needs a term")
        if not clean_store:
            raise ProviderError("Froiz product search needs a store code")
        if page < 1 or size < 1 or size > 100:
            raise ProviderError("Froiz product search page/size is invalid")
        payload = self._request(
            "GET",
            "/api/products",
            params={
                "term": clean_term,
                "page": page,
                "size": size,
                "store": clean_store,
            },
            allow_refresh=allow_browser_refresh,
        )
        rows = payload.get("products") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise ProviderError("Froiz product search returned an invalid response")
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def create_cart(self, items: list[Mapping[str, Any]]) -> dict[str, Any]:
        payload = self._request("POST", "/api/cart", json_body={"items": list(items)})
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz cart creation returned an invalid response")
        return payload

    def update_cart(
        self, cart_id: str, items: list[Mapping[str, Any]]
    ) -> dict[str, Any]:
        payload = self._request(
            "PUT", f"/api/cart/{str(cart_id).strip()}", json_body={"items": list(items)}
        )
        if not isinstance(payload, Mapping):
            raise ProviderError("Froiz cart update returned an invalid response")
        return payload

    def delete_cart(self, cart_id: str) -> None:
        self._request("DELETE", f"/api/cart/{str(cart_id).strip()}")

    def status(self) -> dict[str, Any]:
        checked = False
        authenticated = False
        try:
            self.me()
            checked = True
            authenticated = True
        except (AuthenticationRequired, ProviderError):
            checked = True
        return {
            "store": "froiz",
            "state_path": str(self.state_path),
            "session_present": self.state_path.is_file(),
            "authenticated": authenticated,
            "http_session_checked": checked,
            "http_backend": "froiz_http",
            "profile_values_exposed": False,
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _as_decimal(value: Any, *, default: str = "0"):
    from decimal import Decimal, InvalidOperation

    if value is None or isinstance(value, bool):
        return Decimal(default)
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


__all__ = ["FroizHTTPClient"]
