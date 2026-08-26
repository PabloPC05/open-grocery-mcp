"""Configuration for browser-driven retailer workflows.

The catalogue adapters remain HTTP based. These settings are intentionally
limited to human-facing navigation labels and paths so authenticated workflows
do not depend on undocumented private write endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserStoreConfig:
    key: str
    label: str
    base_url: str
    cart_paths: tuple[str, ...]
    account_paths: tuple[str, ...] = ()
    checkout_paths: tuple[str, ...] = ()
    cart_patterns: tuple[str, ...] = (
        r"cesta",
        r"carrito",
        r"mi compra",
        r"basket",
        r"cart",
    )
    account_patterns: tuple[str, ...] = (
        r"mi cuenta",
        r"cuenta",
        r"perfil",
        r"direcciones",
        r"address",
    )
    add_patterns: tuple[str, ...] = (
        r"añadir",
        r"agregar",
        r"add to cart",
        r"add to basket",
    )
    checkout_patterns: tuple[str, ...] = (
        r"tramitar",
        r"finalizar compra",
        r"continuar con la compra",
        r"hacer pedido",
        r"ir al pago",
        r"checkout",
    )
    # Deliberately excludes a bare "confirmar": at the final checkout step that
    # label can place an order. Only the separately gated submit action may use it.
    continue_patterns: tuple[str, ...] = (
        r"continuar",
        r"siguiente",
        r"guardar dirección",
        r"guardar entrega",
        r"confirmar dirección",
        r"confirmar entrega",
    )
    remove_patterns: tuple[str, ...] = (
        r"eliminar",
        r"quitar",
        r"borrar",
        r"remove",
        r"delete",
    )
    clear_patterns: tuple[str, ...] = (
        r"vaciar",
        r"eliminar todo",
        r"borrar cesta",
        r"clear cart",
    )
    submit_patterns: tuple[str, ...] = (
        r"realizar pedido",
        r"confirmar pedido",
        r"comprar ahora",
        r"pagar",
        r"finalizar pedido",
        r"place order",
    )
    success_patterns: tuple[str, ...] = (
        r"pedido confirmado",
        r"pedido realizado",
        r"gracias por tu compra",
        r"order confirmed",
        r"número de pedido",
    )
    payment_patterns: tuple[str, ...] = (
        r"autorización",
        r"verificación",
        r"3d secure",
        r"confirma en tu banco",
        r"payment",
        r"pago seguro",
    )
    empty_patterns: tuple[str, ...] = (
        r"cesta está vacía",
        r"carrito está vacío",
        r"no hay productos",
        r"basket is empty",
        r"cart is empty",
    )


GADIS_BROWSER_CONFIG = BrowserStoreConfig(
    key="gadis",
    label="Gadis",
    base_url="https://www.gadisline.com",
    cart_paths=("/cart", "/carrito", "/cesta", "/checkout/cart"),
    account_paths=("/account", "/mi-cuenta", "/perfil"),
    checkout_paths=(
        "/pag/proceso-de-compra/compra-segura",
        "/checkout",
        "/finalizar-compra",
    ),
    checkout_patterns=(
        r"tramitar pedido",
        r"finalizar compra",
        r"continuar compra",
        r"hacer pedido",
        r"ir al pago",
    ),
)


FROIZ_BROWSER_CONFIG = BrowserStoreConfig(
    key="froiz",
    label="Froiz",
    base_url="https://supermercado.froiz.com",
    cart_paths=("/cart", "/cesta", "/basket", "/checkout/cart"),
    account_paths=("/account", "/mi-cuenta", "/perfil"),
    checkout_paths=(
        "/es/pag/proceso-de-compra/checkout",
        "/checkout",
        "/finalizar-compra",
    ),
    cart_patterns=(r"tu cesta", r"cesta", r"carrito", r"mi compra"),
    checkout_patterns=(
        r"tramitar pedido",
        r"finalizar compra",
        r"continuar con la compra",
        r"hacer pedido",
        r"ir al pago",
    ),
)


EROSKI_BROWSER_CONFIG = BrowserStoreConfig(
    key="eroski",
    label="Eroski",
    base_url="https://supermercado.eroski.es",
    # The anonymous/user basket lives under the localized login route with a
    # basketType selector (ALI = alimentación).
    cart_paths=(
        "/es/mycart/?basketType=ALI",
        "/es/login/anonymousbasket/?basketType=ALI",
        "/es/basket",
        "/cesta",
    ),
    account_paths=("/es/login/", "/perfil"),
    checkout_paths=("/es/checkout", "/finalizar-compra"),
    cart_patterns=(r"mi cesta", r"cesta", r"carrito", r"mi compra"),
    checkout_patterns=(
        r"tramitar pedido",
        r"finalizar compra",
        r"continuar con la compra",
        r"hacer pedido",
        r"ir al pago",
    ),
)


MERCADONA_BROWSER_CONFIG = BrowserStoreConfig(
    key="mercadona",
    label="Mercadona",
    base_url="https://tienda.mercadona.es",
    cart_paths=("/cart/", "/cart"),
    account_paths=("/profile/", "/account/"),
    checkout_paths=("/checkout/", "/checkout", "/cart/"),
)
