"""Read-only delivery data for the server-rendered Eroski storefront.

The authenticated delivery capture establishes two safe HTML reads:

* ``GET /es/bookingdelivery/shopdelivery/`` renders the pickup points (and,
  when the account has them, the saved home-address options).
* ``GET /es/bookingdeliverysummary/`` renders the currently selected delivery
  context and its slot grid.

Changing the delivery mode, store, address or slot is intentionally not part
of this module.  Eroski implements those transitions as Tapestry form POSTs;
the observed slot transition submits ``selectedAddressRef``,
``selectedSlotRef_0`` and ``selectedSlotTime_0``.  A read method must never
silently perform that transition, so ``delivery_slots`` only reports slots
already rendered by the current server-side context.

Saved-address labels are always redacted.  Postal codes and opaque option IDs
are retained because they are needed to distinguish an address and request a
future slot listing; street, name, phone and email values never leave this
module.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from open_grocery_mcp.errors import ProviderError
from open_grocery_mcp.providers.eroski_http import EroskiHTTPClient

_DELIVERY_PAGE = "/es/bookingdelivery/shopdelivery/"
_SUMMARY_PAGE = "/es/bookingdeliverysummary/"
_POSTAL_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")
_TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2})\s*h?\s*(?:a|[-–])\s*"
    r"(?P<end>\d{1,2}:\d{2})\s*h?",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"(?<!\d)(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:€|&euro;)", re.I)
# Keep the actual euro sign as well as the legacy mojibake seen in a few
# saved captures.  ``data-price`` is preferred for the live table cells.
_PRICE_UNICODE_RE = re.compile(
    r"(?<!\d)(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:\u20ac|&euro;)",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SENSITIVE_TEXT_RE = re.compile(
    r"\b(?:tel(?:éfono)?|m(?:ó|o)vil|email|correo|nombre|apellidos?)\b\s*:?.*",
    re.I,
)


class _HTMLGetter(Protocol):
    def _get_html(self, path: str, **params: str) -> str: ...


@dataclass(frozen=True, slots=True)
class EroskiDeliveryAddress:
    """A saved delivery address with private fields removed."""

    id: str
    label: str
    postal_code: str
    default: bool = False
    street_redacted: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "postal_code": self.postal_code,
            "street_redacted": True,
            "default": self.default,
        }


@dataclass(frozen=True, slots=True)
class EroskiPickupStore:
    """A public Eroski pickup point rendered by the delivery page."""

    id: str
    label: str
    postal_code: str = ""
    address: str = ""
    distance: str = ""
    mode: str = "pickup"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "postal_code": self.postal_code,
            "mode": self.mode,
        }
        if self.address:
            result["address"] = self.address
        if self.distance:
            result["distance"] = self.distance
        return result


@dataclass(frozen=True, slots=True)
class EroskiDeliverySlot:
    """A slot already rendered in the current delivery context."""

    id: str
    date: str
    start: str
    end: str
    available: bool
    price: float = 0.0
    price_text: str = "0.00"
    label: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "start": self.start,
            "end": self.end,
            "available": self.available,
            "open": self.available,
            "price": self.price,
            "price_text": self.price_text,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class EroskiDeliveryPage:
    addresses: list[dict[str, Any]] = field(default_factory=list)
    stores: list[dict[str, Any]] = field(default_factory=list)


def _attrs(tag: str) -> dict[str, str]:
    """Parse attributes without introducing a second HTML dependency."""

    result: dict[str, str] = {}
    for match in re.finditer(
        r"([:\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        tag,
        re.S,
    ):
        result[match.group(1).casefold()] = html_lib.unescape(
            match.group(2) or match.group(3) or match.group(4) or ""
        )
    # HTML boolean attributes have no value (``checked``, ``disabled``).
    # Preserve their presence for safe availability/default parsing.
    for name in ("checked", "disabled", "selected", "readonly"):
        if re.search(rf"\s{name}(?:\s|/?>)", tag, re.I):
            result.setdefault(name, "")
    return result


def _clean_text(value: Any) -> str:
    text = html_lib.unescape(_TAG_RE.sub(" ", str(value or "")))
    return _SPACE_RE.sub(" ", text).strip()


def _input_records(source: str) -> list[tuple[dict[str, str], str]]:
    """Return controls with a bounded, tag-stripped following context."""

    records: list[tuple[dict[str, str], str]] = []
    input_re = re.compile(r"<input\b[^>]*>", re.I | re.S)
    for match in input_re.finditer(source):
        attrs = _attrs(match.group(0))
        if attrs.get("type", "radio").casefold() not in {"radio", "checkbox", "hidden"}:
            continue
        control_id = attrs.get("id", "")
        label = ""
        if control_id:
            label_match = re.search(
                rf"<label\b[^>]*\bfor\s*=\s*['\"]{re.escape(control_id)}['\"][^>]*>"
                rf"(.*?)</label>",
                source,
                re.I | re.S,
            )
            if label_match:
                label = _clean_text(label_match.group(1))
        context = _clean_text(source[match.end() : match.end() + 900])
        records.append((attrs, _clean_text(f"{label} {context}")))
    return records


def _select_address_records(
    source: str,
) -> list[tuple[dict[str, str], dict[str, str], str]]:
    """Return options from the saved-address select without exposing labels."""
    records: list[tuple[dict[str, str], dict[str, str], str]] = []
    select_re = re.compile(
        r"<select\b(?P<attrs>[^>]*)>(?P<body>.*?)</select\s*>",
        re.I | re.S,
    )
    option_re = re.compile(
        r"<option\b(?P<attrs>[^>]*)>(?P<body>.*?)</option\s*>",
        re.I | re.S,
    )
    for select_match in select_re.finditer(source):
        select_attrs = _attrs(f"<select{select_match.group('attrs')}>")
        if select_attrs.get("name", "").casefold() != "selectdeliveryaddress":
            continue
        for option_match in option_re.finditer(select_match.group("body")):
            option_attrs = _attrs(
                f"<option{option_match.group('attrs')}>"
            )
            context = _clean_text(option_match.group("body"))
            records.append((select_attrs, option_attrs, context))
    return records


def _is_private_address(attrs: Mapping[str, str], context: str) -> bool:
    haystack = " ".join(
        [attrs.get("name", ""), attrs.get("id", ""), attrs.get("class", ""), context]
    ).casefold()
    if any(token in haystack for token in ("pickup", "pick-up", "collect", "click&collect", "tienda")):
        return False
    return any(token in haystack for token in ("address", "direcci", "domicilio", "home"))


def _public_store_control(attrs: Mapping[str, str], context: str) -> bool:
    identity = " ".join(
        [attrs.get("name", ""), attrs.get("id", ""), attrs.get("class", "")]
    ).casefold()
    # A saved home address often contains only ``address`` in its control
    # name.  Do not let a later pickup label in the bounded context reclassify
    # that private option as a public store.
    if any(token in identity for token in ("address", "direcci", "domicilio", "home")) and not any(
        token in identity for token in ("pickup", "pick-up", "collect", "store", "tienda")
    ):
        return False
    haystack = " ".join(
        [attrs.get("name", ""), attrs.get("id", ""), attrs.get("class", ""), context]
    ).casefold()
    return any(
        token in haystack
        for token in (
            "pickup",
            "pick-up",
            "collect",
            "click&collect",
            "click&drive",
            "taquilla",
            "store",
            "tienda",
            "eroski/center",
        )
    )


def _control_id(attrs: Mapping[str, str]) -> str:
    # IDs are intentionally returned only for controls, never arbitrary text.
    return (attrs.get("value") or attrs.get("data-id") or attrs.get("id") or "").strip()


def parse_delivery_addresses(source: str) -> list[dict[str, Any]]:
    """Parse saved home-address controls while redacting their descriptions."""

    result: list[EroskiDeliveryAddress] = []
    seen: set[str] = set()
    for attrs, context in _input_records(source):
        if attrs.get("type", "").casefold() != "radio" or not _is_private_address(attrs, context):
            continue
        option_id = _control_id(attrs)
        if not option_id or option_id in seen:
            continue
        postal = (_POSTAL_RE.search(context) or [""])[0]
        result.append(
            EroskiDeliveryAddress(
                id=option_id,
                label=f"{postal} · Dirección guardada" if postal else "Dirección guardada",
                postal_code=postal,
                default=("checked" in attrs or attrs.get("aria-checked", "").casefold() == "true"),
            ).as_dict()
        )
        seen.add(option_id)
    for _, attrs, context in _select_address_records(source):
        option_id = _control_id(attrs)
        postal = (_POSTAL_RE.search(context) or [""])[0]
        if not option_id or not postal or option_id in seen:
            continue
        result.append(
            EroskiDeliveryAddress(
                id=option_id,
                label=f"{postal} · Dirección guardada",
                postal_code=postal,
                default=(
                    "selected" in attrs
                    or attrs.get("aria-selected", "").casefold() == "true"
                ),
            ).as_dict()
        )
        seen.add(option_id)
    return result


def parse_pickup_stores(source: str) -> list[dict[str, Any]]:
    """Parse public pickup stores; no account address text is returned."""

    result: list[EroskiPickupStore] = []
    seen: set[str] = set()
    for attrs, context in _input_records(source):
        if attrs.get("type", "").casefold() != "radio" or not _public_store_control(attrs, context):
            continue
        option_id = _control_id(attrs)
        if not option_id or option_id in seen:
            continue
        postal = (_POSTAL_RE.search(context) or [""])[0]
        distance_match = re.search(r"\b(?:\d+(?:[.,]\d+)?)\s*(?:km|m)\b", context, re.I)
        # A store label is public and useful; remove phone/email-like tails if a
        # page ever includes them next to the control.
        label = _SENSITIVE_TEXT_RE.sub("", context).strip(" -·")
        result.append(
            EroskiPickupStore(
                id=option_id,
                label=label[:160] or "Punto de recogida",
                postal_code=postal,
                distance=distance_match.group(0) if distance_match else "",
            ).as_dict()
        )
        seen.add(option_id)
    return result


def _slot_records(source: str) -> list[tuple[dict[str, str], str]]:
    records: list[tuple[dict[str, str], str]] = []
    # The live delivery grid uses ``td.delivery-table`` cells.  Older pages
    # used links or buttons, so retain those forms for compatibility.  Keep
    # the context bounded so malformed HTML cannot make one slot swallow the
    # grid.
    pattern = re.compile(r"<(?:a|button|input|td)\b[^>]*>", re.I | re.S)
    for match in pattern.finditer(source):
        attrs = _attrs(match.group(0))
        haystack = " ".join(attrs.get(key, "") for key in ("id", "name", "class", "data-slot", "data-slot-id"))
        if not re.search(r"slot|franja|selectedslot|delivery-table|deliverytable", haystack, re.I):
            continue
        tag_name = re.match(r"<\s*(a|button|input|td)\b", match.group(0), re.I)
        closing = (
            re.search(rf"</{tag_name.group(1)}\s*>", source[match.end() :], re.I)
            if tag_name
            else None
        )
        end = match.end() + closing.end() if closing else match.end()
        # Keep only the current control's contents.  Including a previous
        # slot would make the first time/price match belong to that previous
        # slot.  A nearby heading is enough to recover a date when it is not
        # present in a data attribute.
        prefix = source[max(0, match.start() - 1200) : match.start()]
        headings = re.findall(
            r"<(?:h[1-6]|th)\b[^>]*>(.*?)</(?:h[1-6]|th)>",
            prefix,
            re.I | re.S,
        )
        heading = headings[-1] if headings else ""
        context = _clean_text(f"{heading} {source[match.end() : end]}")
        records.append((attrs, context))
    return records


def _slot_id(attrs: Mapping[str, str], context: str) -> str:
    # ``.data('ref')`` in the storefront is backed by ``data-ref`` in the
    # rendered cell.  It is the stable opaque slot reference and must win
    # over a CSS id or a synthetic fallback.
    for key in (
        "data-ref",
        "data-slot-id",
        "data-slot",
        "data-value",
        "value",
        "id",
    ):
        value = attrs.get(key, "").strip()
        if value and value.casefold() not in {"slot", "selectedslot"}:
            return value
    # Do not expose a timestamp or a private form token.  A deterministic
    # synthetic key is sufficient when the UI omitted an opaque slot ID.
    digest = hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]
    return f"slot-{digest}"


def parse_delivery_slots(source: str) -> list[dict[str, Any]]:
    """Parse rendered slot controls without selecting any slot."""

    result: list[EroskiDeliverySlot] = []
    seen: set[str] = set()
    for attrs, context in _slot_records(source):
        time_match = _TIME_RE.search(context)
        if not time_match:
            continue
        slot_id = _slot_id(attrs, context)
        if slot_id in seen:
            continue
        date = attrs.get("data-date") or attrs.get("data-day") or attrs.get("date") or ""
        if not date:
            date_match = re.search(
                r"\b(?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)?\s*"
                r"\d{1,2}\s*(?:de\s+)?[a-záéíóú]+\b",
                context,
                re.I,
            )
            date = date_match.group(0).strip() if date_match else ""
        classes = attrs.get("class", "")
        unavailable = bool(
            attrs.get("disabled") is not None
            or attrs.get("aria-disabled", "").casefold() == "true"
            or re.search(
                r"\b(?:disabled|unavailable|no disponible|agotad[oa]|not[-_ ]?available)\b",
                f"{classes} {context}",
                re.I,
            )
        )
        # jQuery's ``.data('price')`` reads the data-price attribute.  It is
        # often a bare decimal, without a currency symbol, so inspect it
        # before the human-readable text.
        price_value = attrs.get("data-price", "").strip()
        price_match = _PRICE_RE.search(context) or _PRICE_UNICODE_RE.search(context)
        if price_value:
            numeric = re.search(r"\d+(?:[.,]\d{1,2})?", price_value)
            price = float(numeric.group(0).replace(",", ".")) if numeric else 0.0
        else:
            price = float((price_match.group(1).replace(",", ".") if price_match else "0"))
        price_text = f"{price:.2f}"
        label = f"{date} {time_match.group('start')}–{time_match.group('end')}".strip()
        result.append(
            EroskiDeliverySlot(
                id=slot_id,
                date=date,
                start=time_match.group("start"),
                end=time_match.group("end"),
                available=not unavailable,
                price=price,
                price_text=price_text,
                label=label,
            ).as_dict()
        )
        seen.add(slot_id)
    return result


def _selected_delivery_address_id(source: str) -> str | None:
    """Return the selected home-address ref visible in a rendered page.

    Eroski's slot summary is tied to server-side context.  This deliberately
    reads only an already-selected radio/input; it never submits the address
    form to change that context.
    """

    for attrs, _ in _input_records(source):
        identity = " ".join(
            [attrs.get("name", ""), attrs.get("id", ""), attrs.get("class", "")]
        ).casefold()
        if not any(token in identity for token in ("selectedaddressref", "addressref")):
            continue
        # ``selectedAddressRef`` is also emitted as a hidden context field in
        # some summary responses; unlike a generic address radio, its name
        # already asserts that it is the active context.
        explicit_context = "selectedaddressref" in identity
        if (
            not explicit_context
            and "checked" not in attrs
            and attrs.get("aria-checked", "").casefold() != "true"
        ):
            continue
        option_id = _control_id(attrs)
        if option_id:
            return option_id
    for _, attrs, _ in _select_address_records(source):
        if (
            "selected" not in attrs
            and attrs.get("aria-selected", "").casefold() != "true"
        ):
            continue
        option_id = _control_id(attrs)
        if option_id:
            return option_id
    return None


class EroskiDeliveryClient:
    """Authenticated, GET-only Eroski delivery reader."""

    def __init__(
        self,
        *,
        http: _HTMLGetter | None = None,
        state_path: str | None = None,
        zip_code: str = "48001",
    ) -> None:
        self._http = http or EroskiHTTPClient(state_path=state_path, zip_code=zip_code)

    def _persist_authenticated_cookies(self) -> None:
        """Best-effort persistence for clients that support cookie rotation.

        The delivery reader deliberately accepts the small ``_HTMLGetter``
        protocol, so tests and alternate GET-only clients do not need to
        implement session-state persistence.  The concrete HTTP client owns
        the atomic writer and its failure policy.
        """

        persist = getattr(self._http, "_persist_authenticated_cookies", None)
        if callable(persist):
            persist()

    def read_delivery_page(self) -> EroskiDeliveryPage:
        html = self._http._get_html(_DELIVERY_PAGE)
        page = EroskiDeliveryPage(
            addresses=parse_delivery_addresses(html),
            stores=parse_pickup_stores(html),
        )
        # Pickup points are public and do not prove an account-only response.
        # A saved private address does; only then may a rotated session cookie
        # be persisted by the concrete authenticated HTTP client.
        if page.addresses:
            self._persist_authenticated_cookies()
        return page

    def delivery_addresses(self) -> list[dict[str, Any]]:
        return self.read_delivery_page().addresses

    def pickup_stores(self) -> list[dict[str, Any]]:
        return self.read_delivery_page().stores

    def current_delivery_slots(self) -> list[dict[str, Any]]:
        html = self._http._get_html(_SUMMARY_PAGE)
        slots = parse_delivery_slots(html)
        if not slots:
            raise ProviderError(
                "Eroski delivery summary exposed no readable current slots; "
                "the delivery context may not be selected"
            )
        selected = _selected_delivery_address_id(html)
        if selected is None:
            raise ProviderError(
                "Eroski delivery summary does not expose a selected address; "
                "refusing to persist an ambiguous delivery context"
            )
        self._persist_authenticated_cookies()
        return slots

    def delivery_slots(self, address_id: str | int) -> list[dict[str, Any]]:
        """Read slots only when the requested address is visibly selected.

        This method intentionally does not select ``address_id``.  Returning
        slots from another server-side context would falsely claim they belong
        to the requested address, so an unobservable or mismatched context is
        rejected.
        """

        if not str(address_id).strip():
            raise ProviderError("Eroski delivery slots require a non-empty address id")
        html = self._http._get_html(_SUMMARY_PAGE)
        selected = _selected_delivery_address_id(html)
        requested = str(address_id).strip()
        if selected is None:
            raise ProviderError(
                "Eroski delivery summary does not expose a selected address; "
                "refusing to attribute current slots to the requested address"
            )
        if selected != requested:
            raise ProviderError(
                "Eroski delivery summary is for a different selected address; "
                "address selection is not performed by this read-only client"
            )
        slots = parse_delivery_slots(html)
        if not slots:
            raise ProviderError(
                "Eroski delivery summary exposed no readable current slots; "
                "the delivery context may not be selected"
            )
        self._persist_authenticated_cookies()
        return slots

    def close(self) -> None:
        close = getattr(self._http, "close", None)
        if callable(close):
            close()


__all__ = [
    "EroskiDeliveryAddress",
    "EroskiDeliveryClient",
    "EroskiDeliveryPage",
    "EroskiDeliverySlot",
    "EroskiPickupStore",
    "parse_delivery_addresses",
    "parse_delivery_slots",
    "parse_pickup_stores",
]
