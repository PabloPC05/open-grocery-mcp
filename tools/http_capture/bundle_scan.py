"""Extract value-free API candidates from retailer JavaScript bundles."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from .common import RELEVANT, safe_url

ENDPOINT_LITERAL = re.compile(
    r"""(?ix)
    (?:
        https?://[a-z0-9.-]+(?::\d+)?/
        [a-z0-9_./?&=%{}:@,+~*-]{1,240}
      |
        /(?:api|graphql|auth|login|session|customer|users?|profile|cart|basket|
        cesta|carrito|checkout|addresses?|delivery|slots?|orders?|stores?|catalog|
        products?|categories?)
        [a-z0-9_./?&=%{}:@,+~*-]{0,220}
    )
    """
)
STATIC_LITERAL = re.compile(
    r"(?i)\.(?:css|js|mjs|map|svg|png|jpe?g|gif|webp|woff2?|ttf|ico)(?:\?|$)"
)


def endpoint_literals(text: str, source_url: str, limit: int = 250) -> list[str]:
    """Extract API/cart/checkout URL literals without persisting bundle code."""
    decoded = text.replace("\\/", "/")
    found: set[str] = set()
    for match in ENDPOINT_LITERAL.finditer(decoded[:5_000_000]):
        literal = match.group(0).rstrip(");,]}")
        if STATIC_LITERAL.search(literal):
            continue
        absolute = urljoin(source_url, literal)
        if not RELEVANT.search(absolute):
            continue
        found.add(safe_url(absolute))
        if len(found) >= limit:
            break
    return sorted(found)
