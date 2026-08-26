"""Browser lifecycle, login and safe retailer navigation."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Sequence
from urllib.parse import urljoin, urlsplit

from open_grocery_mcp.errors import AuthenticationRequired, InvalidRequest, ProviderError
from open_grocery_mcp.providers.browser_config import BrowserStoreConfig
from open_grocery_mcp.providers.browser_normalize import normalized_text

_CAPTURE_RE = re.compile(
    r"cart|basket|cesta|carrito|checkout|address|direccion|delivery|slot|order|pedido",
    re.I,
)


class BrowserDriverCore:
    def __init__(
        self,
        config: BrowserStoreConfig,
        *,
        state_path: Path,
        checkout_store: Path,
        timeout_seconds: int = 30,
    ) -> None:
        self.config = config
        self.state_path = state_path
        self.checkout_store = checkout_store
        self.timeout_ms = max(5, timeout_seconds) * 1000
        self._mutex = threading.RLock()

    @staticmethod
    def _playwright():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised by installation, not unit tests.
            raise AuthenticationRequired(
                'browser workflows require `pip install "open-grocery-mcp[browser]"` '
                'and `playwright install chromium`'
            ) from exc
        return sync_playwright

    def _headless(self) -> bool:
        value = os.getenv("OPEN_GROCERY_BROWSER_HEADLESS", "1").casefold()
        return value not in {"0", "false", "no", "off"}

    def _session_storage_path(self) -> Path:
        return self.state_path.with_name("session_storage.json")

    def _read_session_storage(self) -> dict[str, dict[str, str]]:
        try:
            payload = json.loads(self._session_storage_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(origin): {
                str(key): str(value)
                for key, value in values.items()
                if isinstance(key, str) and isinstance(value, (str, int, float, bool))
            }
            for origin, values in payload.items()
            if isinstance(origin, str) and isinstance(values, dict)
        }

    def _write_session_storage(self, payload: dict[str, dict[str, str]]) -> None:
        path = self._session_storage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False)
            self._protect(temporary)
            temporary.replace(path)
            self._protect(path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _save_storage_state(self, context: Any) -> None:
        """Persist Playwright state through a private, mode-protected temp file."""

        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            # Playwright owns the serialization, but it accepts a destination
            # path.  Supplying a same-directory temporary keeps the final
            # state file intact if serialization or replace fails.
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
            context.storage_state(path=str(temporary))
            self._protect(temporary)
            temporary.replace(path)
            self._protect(path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _restore_session_storage(self, context: Any) -> None:
        stored = self._read_session_storage()
        if not stored:
            return
        context.add_init_script(
            "(() => { try { const data = "
            + json.dumps(stored, ensure_ascii=False)
            + "; const entries = data[location.origin]; if (!entries) return;"
            " for (const [key, value] of Object.entries(entries)) {"
            " if (!sessionStorage.getItem(key)) sessionStorage.setItem(key, String(value)); }"
            " } catch (_) {} })()"
        )

    def _launch_kwargs(self, *, headless: bool) -> dict[str, Any]:
        prefix = f"OPEN_GROCERY_{self.config.key.upper()}_"
        executable = os.getenv(prefix + "BROWSER_EXECUTABLE") or os.getenv(
            "OPEN_GROCERY_BROWSER_EXECUTABLE"
        )
        channel = os.getenv(prefix + "BROWSER_CHANNEL") or os.getenv(
            "OPEN_GROCERY_BROWSER_CHANNEL"
        )
        kwargs: dict[str, Any] = {"headless": headless}
        if executable:
            kwargs["executable_path"] = executable
        elif channel:
            kwargs["channel"] = channel
        return kwargs

    @contextmanager
    def _page(self, *, headless: bool | None = None, require_state: bool = True):
        if require_state and not self.state_path.exists():
            raise AuthenticationRequired(
                f"no local {self.config.label} session; run login_with_browser first"
            )
        sync_playwright = self._playwright()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        captured: list[Any] = []
        with self._mutex, sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    **self._launch_kwargs(headless=self._headless() if headless is None else headless)
                )
            except Exception as exc:
                raise AuthenticationRequired(
                    "could not start Chromium; install a Playwright browser or configure "
                    f"OPEN_GROCERY_{self.config.key.upper()}_BROWSER_EXECUTABLE"
                ) from exc
            try:
                context_kwargs: dict[str, Any] = {
                    "locale": "es-ES",
                    "viewport": {"width": 1440, "height": 1000},
                }
                if self.state_path.exists():
                    context_kwargs["storage_state"] = str(self.state_path)
                context = browser.new_context(**context_kwargs)
                if self.config.key.casefold() in {"froiz", "eroski"}:
                    self._restore_session_storage(context)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)

                def capture(response: Any) -> None:
                    try:
                        content_type = response.headers.get("content-type", "")
                        if "json" not in content_type.casefold() or not _CAPTURE_RE.search(response.url):
                            return
                        captured.append(response.json())
                        if len(captured) > 100:
                            del captured[:-100]
                    except Exception:
                        return

                page.on("response", capture)
                try:
                    yield page, captured, context
                finally:
                    try:
                        self._save_storage_state(context)
                    except Exception:
                        # Preserve the original retailer/browser exception. A failed
                        # state refresh must never turn a successful write into an
                        # automatic retry.
                        pass
            finally:
                browser.close()

    @staticmethod
    def _protect(path: Path) -> None:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def login(self, *, timeout_seconds: int) -> dict[str, Any]:
        if timeout_seconds < 30 or timeout_seconds > 900:
            raise InvalidRequest("timeout_seconds must be between 30 and 900")
        sync_playwright = self._playwright()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        completed = threading.Event()
        with self._mutex, sync_playwright() as playwright:
            browser = playwright.chromium.launch(**self._launch_kwargs(headless=False))
            try:
                context_kwargs: dict[str, Any] = {"locale": "es-ES"}
                if self.state_path.is_file():
                    try:
                        saved_state = json.loads(
                            self.state_path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError):
                        saved_state = None
                    if (
                        isinstance(saved_state, dict)
                        and isinstance(saved_state.get("cookies", []), list)
                        and isinstance(saved_state.get("origins", []), list)
                    ):
                        context_kwargs["storage_state"] = str(self.state_path)
                context = browser.new_context(**context_kwargs)
                if self.config.key.casefold() in {"froiz", "eroski"}:
                    self._restore_session_storage(context)
                page = context.new_page()
                automatic = self.config.key.casefold() in {"froiz", "eroski", "gadis"}
                pages: list[Any] = []
                authenticated_response = threading.Event()
                froiz_token_candidates: list[str] = []
                froiz_tokens_seen: set[str] = set()

                def observe_request(request: Any) -> None:
                    if self.config.key.casefold() != "froiz":
                        return
                    token = self._froiz_request_token_candidate(request)
                    if token and token not in froiz_tokens_seen:
                        froiz_tokens_seen.add(token)
                        froiz_token_candidates.append(token)

                def observe_response(response: Any) -> None:
                    if (
                        self.config.key.casefold() == "gadis"
                        and self._gadis_response_is_authenticated(response)
                    ):
                        authenticated_response.set()

                def register_page(candidate: Any) -> None:
                    if candidate in pages:
                        return
                    pages.append(candidate)
                    try:
                        candidate.set_default_timeout(self.timeout_ms)
                    except Exception:
                        pass
                    try:
                        candidate.on("popup", register_page)
                    except Exception:
                        pass
                    try:
                        candidate.on("request", observe_request)
                    except Exception:
                        pass
                    try:
                        candidate.on("response", observe_response)
                    except Exception:
                        pass

                register_page(page)
                try:
                    context.on("page", register_page)
                except Exception:
                    pass
                if not automatic:
                    page.expose_function("__openGroceryLoginComplete", lambda: completed.set())
                if not automatic:
                    context.add_init_script(
                    """
                    (() => {
                      const install = () => {
                        if (document.getElementById('__open_grocery_save_session')) return;
                        const button = document.createElement('button');
                        button.id = '__open_grocery_save_session';
                        button.textContent = 'Open Grocery: guardar sesión';
                        Object.assign(button.style, {
                          position: 'fixed', right: '18px', bottom: '18px', zIndex: '2147483647',
                          padding: '14px 18px', border: '0', borderRadius: '10px',
                          background: '#111', color: '#fff', fontSize: '15px',
                          fontFamily: 'system-ui,sans-serif', cursor: 'pointer', boxShadow: '0 4px 20px #0006'
                        });
                        button.addEventListener('click', async () => {
                          button.textContent = 'Sesión guardada';
                          button.disabled = true;
                          await window.__openGroceryLoginComplete();
                        });
                        document.documentElement.appendChild(button);
                      };
                      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
                      else install();
                    })();
                    """
                )
                page.goto(self.config.base_url, wait_until="domcontentloaded")
                if automatic:
                    page.wait_for_timeout(1500)
                    self._dismiss_cookie_banner(page)
                    self._open_login_entry(page)
                deadline = time.monotonic() + timeout_seconds
                authenticated_page: Any | None = None
                while time.monotonic() < deadline:
                    if automatic:
                        if self.config.key.casefold() == "froiz":
                            for candidate in tuple(pages):
                                for token in self._froiz_storage_token_candidates(
                                    candidate
                                ):
                                    if token not in froiz_tokens_seen:
                                        froiz_tokens_seen.add(token)
                                        froiz_token_candidates.append(token)
                        while (
                            self.config.key.casefold() == "froiz"
                            and froiz_token_candidates
                            and not authenticated_response.is_set()
                        ):
                            token = froiz_token_candidates.pop(0)
                            for candidate in tuple(pages):
                                if self._froiz_token_is_authenticated(candidate, token):
                                    authenticated_page = candidate
                                    authenticated_response.set()
                                    break
                        if authenticated_response.is_set() and pages:
                            authenticated_page = authenticated_page or pages[0]
                        else:
                            for candidate in tuple(pages):
                                if self._page_has_authenticated_session(candidate):
                                    authenticated_page = candidate
                                    break
                        if authenticated_page is not None:
                            break
                    elif completed.is_set():
                        break
                    page.wait_for_timeout(250)
                if automatic and authenticated_page is None:
                    raise AuthenticationRequired(
                        "login was not confirmed by a read-only authenticated-page check "
                        "before the timeout"
                    )
                if not automatic and not completed.is_set():
                    raise AuthenticationRequired(
                        "login was not confirmed before the timeout; sign in and click the "
                        "legacy save-session control"
                    )
                self._save_storage_state(context)
                if automatic:
                    self._write_session_storage(self._collect_session_storage(pages))
            finally:
                browser.close()
        result = {"store": self.config.key, "session_saved": True, "state_path": str(self.state_path)}
        if self.config.key.casefold() in {"froiz", "eroski", "gadis"}:
            result.update(
                {
                    "authenticated_session": True,
                    "validated_live": True,
                    "validation": (
                        "froiz_get_api_me_2xx"
                        if self.config.key.casefold() == "froiz"
                        and authenticated_response.is_set()
                        else (
                            "gadis_get_auth_session_2xx"
                            if self.config.key.casefold() == "gadis"
                            and authenticated_response.is_set()
                            else "read_only_authenticated_dom"
                        )
                    ),
                    "session_storage_saved": True,
                }
            )
        return result

    def _page_has_authenticated_session(self, page: Any) -> bool:
        """Require an account-only control; URL or generic welcome copy is insufficient."""

        try:
            password_fields = page.locator("input[type='password']").count()
        except Exception:
            password_fields = 0
        if password_fields:
            return False
        selectors = (
            'a[href*="logout" i]',
            'a[href*="log-out" i]',
            'a[href*="signout" i]',
            'a[href*="sign-out" i]',
            'a[href*="cerrar-sesion" i]',
            'button:has-text("Cerrar sesión")',
            'a:has-text("Cerrar sesión")',
            '[role="button"]:has-text("Cerrar sesión")',
            'button:has-text("Desconectar")',
            'a:has-text("Desconectar")',
        )
        try:
            # The control may live inside a collapsed account menu. Its
            # presence is still account-only; normal guest pages for both
            # retailers contain none of these exact interactive controls.
            return bool(page.locator(",".join(selectors)).count())
        except Exception:
            return False

    def _open_login_entry(self, page: Any) -> bool:
        """Open the retailer login surface without filling or submitting data."""

        selectors = {
            "froiz": (
                'a:has-text("Identif")',
                'a.text-white:has-text("Inicie sesi")',
            ),
            "eroski": (
                'a[href*="/es/login/?l=" i]',
                'a:has-text("MI CUENTA")',
            ),
            "gadis": (
                'a[href*="login" i]',
                'a:has-text("Iniciar sesi")',
                'a:has-text("Mi cuenta")',
            ),
        }.get(self.config.key.casefold(), ())
        for selector in selectors:
            try:
                candidates = page.locator(selector)
                for index in range(min(candidates.count(), 5)):
                    candidate = candidates.nth(index)
                    if candidate.is_visible():
                        candidate.click(timeout=3000)
                        return True
            except Exception:
                continue
        return False

    @staticmethod
    def _dismiss_cookie_banner(page: Any) -> bool:
        """Choose the exact necessary-cookies-only control when Cookiebot blocks login."""

        selectors = (
            "#CybotCookiebotDialogBodyButtonDecline",
            'button:has-text("Solo usar cookies necesarias")',
        )
        for selector in selectors:
            try:
                candidate = page.locator(selector).first
                if candidate.is_visible():
                    candidate.click(timeout=3000)
                    page.wait_for_timeout(250)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _froiz_storage_token_candidates(page: Any) -> tuple[str, ...]:
        """Read only Froiz's exact auth keys from its trusted storefront origin."""

        try:
            origin = str(page.evaluate("() => location.origin"))
            if origin != "https://supermercado.froiz.com":
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
    def _froiz_request_token_candidate(request: Any) -> str | None:
        """Read a bearer only from a trusted, read-only Froiz API request."""

        try:
            parsed = urlsplit(str(request.url))
            if (
                parsed.scheme != "https"
                or parsed.hostname != "servicios.froiz.com"
                or not parsed.path.startswith("/api/")
                or str(request.method).upper() != "GET"
            ):
                return None
            authorization = str(request.headers.get("authorization", ""))
        except Exception:
            return None
        if not authorization.lower().startswith("bearer "):
            return None
        token = authorization[7:].strip()
        return token or None

    def _froiz_page_is_trusted(self, page: Any) -> bool:
        """Only pass bearer candidates to the configured Froiz storefront origin."""

        try:
            configured = urlsplit(self.config.base_url)
            current = urlsplit(str(page.url))
            return (
                configured.scheme == "https"
                and current.scheme == "https"
                and configured.hostname is not None
                and current.hostname == configured.hostname
                and (configured.port or 443) == (current.port or 443)
            )
        except Exception:
            return False

    def _froiz_token_is_authenticated(self, page: Any, token: str) -> bool:
        """Validate a candidate in-page with an exact, read-only ``GET /api/me``."""

        if not token or not self._froiz_page_is_trusted(page):
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
    def _froiz_response_is_authenticated(response: Any) -> bool:
        """Accept only the signed-in GET /api/me response, without exposing values."""

        try:
            request = response.request
            parsed = urlsplit(str(response.url))
            authorization = str(request.headers.get("authorization", ""))
            has_bearer = authorization.lower().startswith("bearer ") and bool(
                authorization[7:].strip()
            )
            if (
                parsed.scheme != "https"
                or parsed.hostname != "servicios.froiz.com"
                or parsed.path != "/api/me"
                or str(request.method).upper() != "GET"
                or not has_bearer
                or not 200 <= int(response.status) < 300
            ):
                return False
            payload = response.json()
        except Exception:
            return False
        if not isinstance(payload, dict) or payload.get("authenticated") is False:
            return False
        identity = payload.get("id") or payload.get("userId")
        return (
            identity not in (None, "")
            and not isinstance(identity, bool)
            and isinstance(payload.get("userChannelOptions"), list)
        )

    @staticmethod
    def _gadis_response_is_authenticated(response: Any) -> bool:
        """Accept only a successful NextAuth session response, value-free."""

        try:
            request = response.request
            parsed = urlsplit(str(response.url))
            if (
                parsed.scheme != "https"
                or parsed.hostname != "www.gadisline.com"
                or parsed.path != "/api/auth/session"
                or str(request.method).upper() != "GET"
                or not 200 <= int(response.status) < 300
            ):
                return False
            payload = response.json()
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        user = payload.get("user")
        token = payload.get("token")
        access_token = token.get("accessToken") if isinstance(token, dict) else None
        return (
            isinstance(user, dict)
            and bool(user)
            and isinstance(access_token, str)
            and bool(access_token.strip())
        )

    def _collect_session_storage(self, pages: Sequence[Any]) -> dict[str, dict[str, str]]:
        host = (urlsplit(self.config.base_url).hostname or "").casefold()
        collected: dict[str, dict[str, str]] = {}
        for page in pages:
            try:
                origin = str(page.evaluate("() => location.origin"))
                page_host = (urlsplit(origin).hostname or "").casefold()
                if not origin or not page_host or not (page_host == host or page_host.endswith("." + host)):
                    continue
                values = page.evaluate(
                    """() => {
                      const result = {};
                      for (let i = 0; i < sessionStorage.length; i += 1) {
                        const key = sessionStorage.key(i);
                        if (key !== null) result[key] = sessionStorage.getItem(key) ?? '';
                      }
                      return result;
                    }"""
                )
                if isinstance(values, dict):
                    collected[origin] = {
                        str(key): str(value)
                        for key, value in values.items()
                        if isinstance(key, str) and isinstance(value, (str, int, float, bool))
                    }
            except Exception:
                continue
        return collected

    @staticmethod
    def _regex(patterns: Sequence[str]) -> re.Pattern[str]:
        return re.compile("(?:" + "|".join(patterns) + ")", re.I)

    def _click_patterns(
        self,
        page: Any,
        patterns: Sequence[str],
        *,
        roles: Sequence[str] = ("button", "link"),
        required: bool = False,
    ) -> bool:
        expression = self._regex(patterns)
        for role in roles:
            try:
                locator = page.get_by_role(role, name=expression).filter(visible=True)
            except TypeError:  # Older Playwright has no visible filter keyword.
                locator = page.get_by_role(role, name=expression)
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click()
                    return True
            except Exception:
                continue
        try:
            locator = page.locator("button,a,[role='button']").filter(has_text=expression)
            if locator.count() and locator.first.is_visible():
                locator.first.click()
                return True
        except Exception:
            pass
        if required:
            raise ProviderError(
                f"could not find a {self.config.label} control matching: {', '.join(patterns)}"
            )
        return False

    def _accept_cookies(self, page: Any) -> None:
        self._click_patterns(
            page,
            (r"aceptar todas", r"aceptar cookies", r"permitir todas", r"accept all"),
            required=False,
        )

    def _page_is_not_found(self, page: Any) -> bool:
        try:
            text = normalized_text(page.locator("body").inner_text(timeout=2500))
        except Exception:
            return False
        return any(token in text for token in ("pagina no encontrada", "página no encontrada", "404 not found"))

    def _goto_paths(self, page: Any, paths: Sequence[str]) -> bool:
        for path in paths:
            try:
                response = page.goto(urljoin(self.config.base_url, path), wait_until="domcontentloaded")
                if response is not None and response.status >= 400:
                    continue
                if not self._page_is_not_found(page):
                    self._accept_cookies(page)
                    return True
            except Exception:
                continue
        return False

    def open_human_handoff(
        self,
        *,
        preferred_url: str | None = None,
        checkout_review: bool = False,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Open a visible, authenticated retailer page without clicking controls.

        The agent performs only direct GET navigation.  The window then belongs
        to the human: this method never selects a payment method, clicks a final
        control or observes/certifies an order result.
        """

        if timeout_seconds < 30 or timeout_seconds > 900:
            raise InvalidRequest("timeout_seconds must be between 30 and 900")
        configured_paths = (
            self.config.checkout_paths
            if checkout_review and self.config.checkout_paths
            else self.config.cart_paths
        )
        candidates = (
            (str(preferred_url),) if str(preferred_url or "").strip() else ()
        ) + tuple(configured_paths)
        if not candidates:
            candidates = (self.config.base_url,)
        targets = tuple(self._retailer_url(candidate) for candidate in candidates)

        opened = False
        final_url = ""
        closed_by_user = False
        blocked_non_get_requests = 0
        with self._page(headless=False) as (page, _, context):
            def allow_get_only(route: Any) -> None:
                nonlocal blocked_non_get_requests
                method = str(route.request.method or "").upper()
                if method != "GET":
                    blocked_non_get_requests += 1
                    route.abort()
                    return
                route.continue_()

            # A review window is observational.  Blocking at the browser
            # routing layer prevents page scripts or an accidental human click
            # from turning the handoff into an order/payment probe.
            context.route("**/*", allow_get_only)
            for target in targets:
                try:
                    response = page.goto(target, wait_until="domcontentloaded")
                    if response is not None and response.status >= 400:
                        continue
                    if self._page_is_not_found(page):
                        continue
                    if checkout_review:
                        expected_path = urlsplit(target).path.rstrip("/")
                        actual_path = urlsplit(str(page.url or "")).path.rstrip("/")
                        if not (
                            actual_path == expected_path
                            or actual_path.startswith(expected_path + "/")
                        ):
                            continue
                    opened = True
                    final_url = str(page.url or target)
                    break
                except Exception:
                    continue
            if not opened:
                raise ProviderError(
                    f"could not open a safe {self.config.label} review page"
                )

            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                try:
                    if page.is_closed():
                        closed_by_user = True
                        break
                    final_url = str(page.url or "")
                    page.wait_for_timeout(250)
                except Exception:
                    closed_by_user = True
                    break

        parsed = urlsplit(final_url)
        expected = (urlsplit(self.config.base_url).hostname or "").casefold()
        actual = (parsed.hostname or "").casefold()
        safe_url = ""
        if actual and (actual == expected or actual.endswith("." + expected)):
            safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return {
            "store": self.config.key,
            "window_opened": True,
            "window_closed_by_user": closed_by_user,
            "review_url": safe_url or None,
            "review_url_sanitized": True,
            "review_path_verified": bool(opened and checkout_review),
            "automated_navigation_method": "GET",
            "automated_clicks": 0,
            "non_get_requests_blocked": blocked_non_get_requests,
            "network_write_guard": "all_non_get_blocked",
            "automated_order_submission": False,
            "order_outcome": "not_observed_by_automation",
        }

    def _goto_cart(self, page: Any) -> None:
        page.goto(self.config.base_url, wait_until="domcontentloaded")
        self._accept_cookies(page)
        if self._click_patterns(page, self.config.cart_patterns):
            page.wait_for_timeout(700)
            return
        for selector in (
            'a[href*="/cart" i]',
            'a[href*="/cesta" i]',
            'a[href*="/carrito" i]',
            'button[aria-label*="cesta" i]',
            'button[aria-label*="carrito" i]',
        ):
            try:
                target = page.locator(selector)
                if target.count() and target.first.is_visible():
                    target.first.click()
                    page.wait_for_timeout(700)
                    return
            except Exception:
                continue
        if self._goto_paths(page, self.config.cart_paths):
            return
        raise ProviderError(f"could not open the {self.config.label} cart")

    def _goto_account(self, page: Any) -> None:
        page.goto(self.config.base_url, wait_until="domcontentloaded")
        self._accept_cookies(page)
        if self._click_patterns(page, self.config.account_patterns):
            page.wait_for_timeout(700)
            return
        if self._goto_paths(page, self.config.account_paths):
            return
        raise ProviderError(
            f"could not open the {self.config.label} account page; create a checkout first "
            "and retry address discovery from that confirmed checkout"
        )

    def _goto_checkout(self, page: Any) -> None:
        self._goto_cart(page)
        # Checkout entry is navigation-only. Human-facing labels such as
        # "hacer pedido" or "ir al pago" may already submit on some versions.
        for selector in (
            'a[href*="checkout" i]',
            'a[href*="proceso-de-compra" i]',
            'a[href*="finalizar-compra" i]',
        ):
            try:
                target = page.locator(selector)
                if target.count() and target.first.is_visible():
                    target.first.click()
                    page.wait_for_timeout(1000)
                    return
            except Exception:
                continue
        if self._goto_paths(page, self.config.checkout_paths):
            return
        raise ProviderError(f"could not open the {self.config.label} checkout")

