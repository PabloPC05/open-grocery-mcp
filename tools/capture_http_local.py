#!/usr/bin/env python3
"""Interactive, value-free HTTP capture on the account owner's machine.

The user signs in directly in a visible browser and manually performs each
labelled action. Credentials, cookies and token values are never written to the
capture. The final order probe records the request shape but aborts it before it
leaves Chromium.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

try:
    from http_capture.common import (
        DANGEROUS,
        RELEVANT,
        STORES,
        safe_headers,
        safe_message,
        safe_url,
        shape,
    )
    from http_capture.manifest import add_manifest
except ModuleNotFoundError:  # Imported as tools.capture_http_local in tests.
    from tools.http_capture.common import (
        DANGEROUS,
        RELEVANT,
        STORES,
        safe_headers,
        safe_message,
        safe_url,
        shape,
    )
    from tools.http_capture.manifest import add_manifest

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
NOISE = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "clarity.ms",
    "facebook.com",
    "hotjar",
    "sentry",
    "newrelic",
)
PHASES = (
    ("login", "1 · Inicio de sesión"),
    ("cart_read", "2 · Leer cesta"),
    ("cart_add", "3 · Añadir producto de prueba"),
    ("quantity_2", "4 · Cambiar cantidad a 2"),
    ("quantity_1", "5 · Volver cantidad a 1"),
    ("cart_remove", "6 · Eliminar producto"),
    ("addresses", "7 · Consultar direcciones"),
    ("delivery_slots", "8 · Consultar franjas"),
    ("checkout_open", "9 · Abrir checkout"),
    ("delivery_select", "10 · Seleccionar entrega"),
    ("order_submit_probe", "11 · SONDA BLOQUEADA del pedido final"),
)

OVERLAY = r"""
(() => {
  const phases = __OPEN_GROCERY_PHASES__;
  const hostId = '__open_grocery_capture';

  const important = (element, property, value) => {
    element.style.setProperty(property, value, 'important');
  };

  const install = () => {
    if (document.getElementById(hostId)) return;

    // Use a top-anchored shadow-DOM host. Some retailer login pages have a
    // transformed/clipped root and can hide bottom-fixed elements outside the
    // physical browser window, especially with Windows display scaling.
    const host = document.createElement('div');
    host.id = hostId;
    important(host, 'all', 'initial');
    important(host, 'position', 'fixed');
    important(host, 'top', '12px');
    important(host, 'right', '12px');
    important(host, 'bottom', 'auto');
    important(host, 'left', 'auto');
    important(host, 'z-index', '2147483647');
    important(host, 'width', 'min(380px, calc(100vw - 24px))');
    important(host, 'max-height', 'calc(100vh - 24px)');
    important(host, 'overflow', 'auto');
    important(host, 'box-sizing', 'border-box');
    important(host, 'isolation', 'isolate');
    important(host, 'pointer-events', 'auto');
    important(host, 'transform', 'none');

    const shadow = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `
      *, *::before, *::after { box-sizing: border-box; }
      .panel {
        width: 100%; padding: 14px; background: #111; color: #fff;
        border-radius: 12px; box-shadow: 0 8px 32px #0008;
        font: 13px/1.35 system-ui, sans-serif;
      }
      .header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
      .title { flex: 1; font-weight: 700; }
      .collapse {
        border: 0; border-radius: 7px; padding: 4px 8px; cursor: pointer;
        background: #333; color: #fff; font: inherit;
      }
      .note { margin-bottom: 10px; color: #ddd; }
      select {
        display: block; width: 100%; margin: 0 0 8px; padding: 9px;
        border: 1px solid #777; border-radius: 8px; background: #fff;
        color: #111; font: inherit;
      }
      .actions { display: flex; gap: 8px; }
      .actions button {
        flex: 1; padding: 9px; border: 0; border-radius: 8px;
        cursor: pointer; font: inherit;
      }
      .mark { background: #f2f2f2; color: #111; }
      .finish { background: #2f80ed; color: #fff; }
      .warning { margin-top: 9px; color: #ffcc80; font-size: 12px; }
      .hidden { display: none; }
    `;

    const panel = document.createElement('section');
    panel.className = 'panel';

    const header = document.createElement('div');
    header.className = 'header';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = 'Open Grocery · captura HTTP local';
    const collapse = document.createElement('button');
    collapse.className = 'collapse';
    collapse.type = 'button';
    collapse.textContent = '−';
    collapse.title = 'Contraer o desplegar';
    header.append(title, collapse);

    const body = document.createElement('div');
    const note = document.createElement('div');
    note.className = 'note';
    note.textContent =
      'Usa una cuenta de prueba. Marca la fase ANTES de cada acción. ' +
      'La sonda final bloquea toda escritura.';

    const select = document.createElement('select');
    for (const [value, label] of phases) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }

    const actions = document.createElement('div');
    actions.className = 'actions';
    const mark = document.createElement('button');
    mark.className = 'mark';
    mark.type = 'button';
    mark.textContent = 'Marcar fase';
    mark.addEventListener('click', async () => {
      await window.__openGrocerySetCapturePhase(select.value);
      panel.style.outline =
        select.value === 'order_submit_probe' ? '3px solid #ff4d4f' : 'none';
      mark.textContent = 'Fase marcada ✓';
      setTimeout(() => { mark.textContent = 'Marcar fase'; }, 900);
    });

    const finish = document.createElement('button');
    finish.className = 'finish';
    finish.type = 'button';
    finish.textContent = 'Finalizar';
    finish.addEventListener('click', async () => {
      finish.disabled = true;
      finish.textContent = 'Guardando…';
      await window.__openGroceryFinishCapture();
      finish.textContent = 'Captura finalizada';
    });
    actions.append(mark, finish);

    const warning = document.createElement('div');
    warning.className = 'warning';
    warning.textContent =
      'No pegues las credenciales en un chat: escríbelas solo en esta ventana.';

    body.append(note, select, actions, warning);
    collapse.addEventListener('click', () => {
      const collapsed = body.classList.toggle('hidden');
      collapse.textContent = collapsed ? '+' : '−';
    });

    panel.append(header, body);
    shadow.append(style, panel);
    (document.body || document.documentElement).appendChild(host);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  } else {
    install();
  }
  new MutationObserver(install).observe(document.documentElement, {
    childList: true, subtree: true
  });
})();
"""


def _protect(path: Path) -> None:
    try:
        mode = (
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
            if path.is_dir()
            else stat.S_IRUSR | stat.S_IWUSR
        )
        os.chmod(path, mode)
    except OSError:
        pass


def _state_path(store: str) -> Path:
    configured = os.getenv(f"OPEN_GROCERY_{store.upper()}_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(os.getenv("OPEN_GROCERY_STATE_DIR", "~/.open-grocery-mcp")).expanduser()
    return root / store / "storage_state.json"


def _launch_kwargs(store: str) -> dict[str, Any]:
    prefix = f"OPEN_GROCERY_{store.upper()}_"
    executable = (
        os.getenv(prefix + "CAPTURE_BROWSER_EXECUTABLE")
        or os.getenv(prefix + "BROWSER_EXECUTABLE")
        or os.getenv("OPEN_GROCERY_CAPTURE_BROWSER_EXECUTABLE")
        or os.getenv("OPEN_GROCERY_BROWSER_EXECUTABLE")
    )
    channel = (
        os.getenv(prefix + "CAPTURE_BROWSER_CHANNEL")
        or os.getenv(prefix + "BROWSER_CHANNEL")
        or os.getenv("OPEN_GROCERY_CAPTURE_BROWSER_CHANNEL")
        or os.getenv("OPEN_GROCERY_BROWSER_CHANNEL")
    )
    result: dict[str, Any] = {
        "headless": False,
        "args": ["--start-maximized"],
    }
    if executable:
        result["executable_path"] = executable
    elif channel:
        result["channel"] = channel
    return result


def request_body_shape(request: Any) -> Any:
    post_data = request.post_data
    if not post_data:
        return None
    content_type = request.headers.get("content-type", "").casefold()
    if "json" in content_type:
        try:
            return shape(json.loads(post_data))
        except (json.JSONDecodeError, TypeError):
            return "<invalid-json-body>"
    if "application/x-www-form-urlencoded" in content_type:
        return {
            key: shape(value, key)
            for key, value in parse_qsl(post_data, keep_blank_values=True)
        }
    if "multipart/form-data" in content_type:
        names = sorted(set(re.findall(r'name="([^"]+)"', post_data)))
        return {"field_names": names, "content": "<redacted-multipart>"}
    return "<non-json-body>"


def should_record_request(request: Any) -> bool:
    url = request.url.casefold()
    if any(host in url for host in NOISE):
        return False
    return (
        request.method.upper() not in SAFE_METHODS
        or request.resource_type
        in {"xhr", "fetch", "document", "websocket", "eventsource"}
        or bool(RELEVANT.search(request.url))
    )


def should_block_request(
    phase: str, method: str, url: str, body: str = ""
) -> bool:
    method = method.upper()
    if DANGEROUS.search(url) or DANGEROUS.search(body):
        return True
    return phase == "order_submit_probe" and method not in SAFE_METHODS


class LocalCapture:
    def __init__(
        self,
        store: str,
        output: Path,
        *,
        timeout_seconds: int,
        fresh_session: bool,
    ) -> None:
        self.store = store
        self.spec = STORES[store]
        self.output = output
        self.timeout_seconds = timeout_seconds
        self.fresh_session = fresh_session
        self.state_path = _state_path(store)
        self.phase = "login"
        self.events: list[dict[str, Any]] = []
        self.blocked: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []
        self.phase_marks: list[dict[str, Any]] = []
        self.finished = threading.Event()
        self._lock = threading.RLock()

    def set_phase(self, phase: str) -> None:
        if phase not in {value for value, _ in PHASES}:
            return
        with self._lock:
            self.phase = phase
            self.phase_marks.append(
                {
                    "phase": phase,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "event_index": len(self.events),
                }
            )

    def finish(self) -> None:
        self.finished.set()

    def record_error(self, phase: str, exc: BaseException) -> None:
        self.errors.append(
            {
                "phase": phase,
                "type": type(exc).__name__,
                "message": safe_message(str(exc)),
            }
        )

    def on_request(self, request: Any) -> None:
        if not should_record_request(request):
            return
        try:
            row = {
                "kind": "request",
                "phase": self.phase,
                "method": request.method.upper(),
                "url": safe_url(request.url),
                "resource_type": request.resource_type,
                "headers": safe_headers(request.headers),
                "body": request_body_shape(request),
            }
            with self._lock:
                self.events.append(row)
        except Exception as exc:
            self.record_error("request_capture", exc)

    def on_response(self, response: Any) -> None:
        request = response.request
        if not should_record_request(request):
            return
        body = None
        content_type = response.headers.get("content-type", "").casefold()
        if "json" in content_type:
            try:
                body = shape(response.json())
            except Exception:
                body = "<invalid-json-response>"
        row = {
            "kind": "response",
            "phase": self.phase,
            "method": request.method.upper(),
            "url": safe_url(response.url),
            "status": response.status,
            "headers": safe_headers(response.headers),
            "body": body,
        }
        with self._lock:
            self.events.append(row)

    def route(self, route: Any) -> None:
        request = route.request
        body = request.post_data or ""
        blocked = should_block_request(
            self.phase, request.method, request.url, body
        )
        if blocked:
            with self._lock:
                self.blocked.append(
                    {
                        "phase": self.phase,
                        "method": request.method.upper(),
                        "url": safe_url(request.url),
                        "body": request_body_shape(request),
                        "reason": (
                            "dangerous order/payment route"
                            if DANGEROUS.search(request.url) or DANGEROUS.search(body)
                            else "all writes are blocked during order_submit_probe"
                        ),
                    }
                )
            route.abort("blockedbyclient")
            return
        route.continue_()

    def _setup_page(self, page: Any) -> None:
        page.set_default_timeout(30_000)
        try:
            page.expose_function("__openGrocerySetCapturePhase", self.set_phase)
        except Exception:
            pass
        try:
            page.expose_function("__openGroceryFinishCapture", self.finish)
        except Exception:
            pass
        page.on("request", self.on_request)
        page.on("response", self.on_response)

    def run(self) -> int:
        if not 60 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout must be between 60 and 3600 seconds")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                'install the browser extra and Chromium: '
                '`python -m pip install -e ".[browser]" && playwright install chromium`'
            ) from exc

        self.output.parent.mkdir(parents=True, exist_ok=True)
        _protect(self.output.parent)
        started = datetime.now(UTC)
        timed_out = False

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**_launch_kwargs(self.store))
            try:
                context_args: dict[str, Any] = {
                    "locale": "es-ES",
                    "no_viewport": True,
                }
                if not self.fresh_session and self.state_path.exists():
                    context_args["storage_state"] = str(self.state_path)
                context = browser.new_context(**context_args)
                context.add_init_script(
                    OVERLAY.replace(
                        "__OPEN_GROCERY_PHASES__",
                        json.dumps(PHASES, ensure_ascii=False),
                    )
                )
                context.route("**/*", self.route)
                page = context.new_page()
                self._setup_page(page)
                context.on("page", self._setup_page)
                try:
                    page.goto(self.spec.base_url, wait_until="domcontentloaded")
                except Exception as exc:
                    self.record_error("bootstrap", exc)

                deadline = time.monotonic() + self.timeout_seconds
                while not self.finished.is_set() and time.monotonic() < deadline:
                    page.wait_for_timeout(250)
                timed_out = not self.finished.is_set()

                try:
                    self.state_path.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(self.state_path))
                    _protect(self.state_path)
                except Exception as exc:
                    self.record_error("save_session", exc)
                context.close()
            finally:
                browser.close()

        if not self.events:
            self.errors.append(
                {
                    "phase": self.phase,
                    "type": "EmptyCapture",
                    "message": (
                        "no HTTP traffic was captured; the storefront may be "
                        "unreachable or not issuing API calls"
                    ),
                }
            )

        payload = {
            "schema_version": 2,
            "captured_at": datetime.now(UTC).isoformat(),
            "started_at": started.isoformat(),
            "store": self.store,
            "mode": "interactive-local",
            "completed": not timed_out,
            "timed_out": timed_out,
            "phase_marks": self.phase_marks,
            "events": self.events,
            "blocked": self.blocked,
            "errors": self.errors,
            "safety": {
                "raw_credentials_persisted": False,
                "raw_cookies_persisted": False,
                "raw_tokens_persisted": False,
                "order_request_reached_retailer": False,
                "order_probe_writes_blocked": True,
                "storage_state_in_capture": False,
                "values_sanitized": True,
            },
        }
        self.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _protect(self.output)
        add_manifest(self.output)
        print(
            json.dumps(
                {
                    "output": str(self.output),
                    "store": self.store,
                    "events": len(self.events),
                    "blocked": len(self.blocked),
                    "errors": len(self.errors),
                    "timed_out": timed_out,
                    "state_path": str(self.state_path),
                    "state_path_is_not_in_capture": True,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if self.events else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a sanitized Gadis/Froiz HTTP contract in a visible local browser. "
            "Credentials are typed only into the retailer page."
        )
    )
    parser.add_argument("--store", required=True, choices=sorted(STORES))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--fresh-session", action="store_true")
    args = parser.parse_args()
    return LocalCapture(
        args.store,
        args.output,
        timeout_seconds=args.timeout,
        fresh_session=args.fresh_session,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
