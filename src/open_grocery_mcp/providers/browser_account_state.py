"""Local browser-session state shared by browser-backed retailers."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest
from open_grocery_mcp.providers.browser_config import BrowserStoreConfig
from open_grocery_mcp.providers.browser_driver import PlaywrightBrowserDriver

DriverFactory = Callable[..., PlaywrightBrowserDriver]


def default_state_root() -> Path:
    configured = os.getenv("OPEN_GROCERY_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    # Every session owner (capture tools, GadisSessionClient and the browser
    # driver) reads and writes the same directory, so the HTTP and browser
    # backends must not disagree about where a session lives.
    return Path.home() / ".open-grocery-mcp"


class BrowserAccountStateMixin:
    def __init__(
        self,
        config: BrowserStoreConfig,
        *,
        state_root: Path | None = None,
        driver_factory: DriverFactory = PlaywrightBrowserDriver,
    ) -> None:
        self.config = config
        self.root = (state_root or default_state_root()) / config.key
        self.state_path = self.root / "storage_state.json"
        self.checkout_path = self.root / "checkouts.json"
        self._driver_factory = driver_factory
        self._lock = threading.RLock()
        self._active_checkout_id: str | None = None

    def _driver(self) -> PlaywrightBrowserDriver:
        return self._driver_factory(
            self.config,
            state_path=self.state_path,
            checkout_store=self.checkout_path,
        )

    @staticmethod
    def _protect(path: Path) -> None:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _protect_root(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            os.chmod(self.root, stat.S_IRWXU)
        except OSError:
            pass

    def _state_matches_retailer(self, state: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        host = (urlsplit(self.config.base_url).hostname or "").casefold()
        parent = ".".join(host.split(".")[-2:])
        cookies = [item for item in state.get("cookies", []) if isinstance(item, Mapping)]
        matching_cookies = []
        for item in cookies:
            domain = str(item.get("domain", "")).lstrip(".").casefold()
            if domain and (host == domain or host.endswith("." + domain) or domain.endswith("." + parent)):
                matching_cookies.append(item)
        origins = [item for item in state.get("origins", []) if isinstance(item, Mapping)]
        matching_origins = []
        for item in origins:
            origin_host = (urlsplit(str(item.get("origin", ""))).hostname or "").casefold()
            if origin_host and (origin_host == host or origin_host.endswith("." + parent)):
                matching_origins.append(item)
        return matching_cookies, matching_origins

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            raise AuthenticationRequired(
                f"no local {self.config.label} browser session; run login_with_browser"
            )
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AuthenticationRequired(
                f"stored {self.config.label} browser session is unreadable"
            ) from exc
        if not isinstance(payload, dict):
            raise AuthenticationRequired("browser storage state is not a JSON object")
        return payload

    def status(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "store": self.config.key,
                "authenticated_session": False,
                "state_path": str(self.state_path),
                "validation": "run login_with_browser or import_browser_session",
            }
        try:
            state = self._read_state()
        except AuthenticationRequired as exc:
            return {
                "store": self.config.key,
                "authenticated_session": False,
                "state_path": str(self.state_path),
                "error": str(exc),
            }
        matching_cookies, matching_origins = self._state_matches_retailer(state)
        return {
            "store": self.config.key,
            "authenticated_session": bool(matching_cookies or matching_origins),
            "state_path": str(self.state_path),
            "cookie_count": len(matching_cookies),
            "origin_count": len(matching_origins),
            "validated_live": False,
            "validation": "the next authenticated read verifies the retailer session",
        }

    def import_storage_state(self, storage_state_path: str) -> dict[str, Any]:
        source = Path(storage_state_path).expanduser()
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            raise InvalidRequest(f"storage_state_path does not exist: {storage_state_path}") from exc
        if not source.is_file() or source.stat().st_size > 5 * 1024 * 1024:
            raise InvalidRequest("storage_state_path must be a JSON file smaller than 5 MiB")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InvalidRequest("storage_state_path is not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("cookies", []), list) or not isinstance(payload.get("origins", []), list):
            raise InvalidRequest("storage state must contain Playwright cookies/origins arrays")
        matching_cookies, matching_origins = self._state_matches_retailer(payload)
        if not matching_cookies and not matching_origins:
            raise InvalidRequest(
                f"storage state contains no {self.config.label} cookies or local storage"
            )
        self._protect_root()
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.root, delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False)
            self._protect(temporary)
            temporary.replace(self.state_path)
            self._protect(self.state_path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return self.status()

    def login_with_browser(self, *, timeout_seconds: int = 300) -> dict[str, Any]:
        self._protect_root()
        result = self._driver().login(timeout_seconds=timeout_seconds)
        if self.config.key.casefold() in {"froiz", "eroski"}:
            # Keep the driver's live, read-only validation marker.  A static
            # storage-file inspection must not overwrite this stronger result.
            return {**self.status(), **result}
        return {**result, **self.status()}

    def cart(self) -> dict[str, Any]:
        return self._driver().read_cart()

