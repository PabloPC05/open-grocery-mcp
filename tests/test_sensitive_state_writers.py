import json
from pathlib import Path

import pytest

from open_grocery_mcp.providers.browser_account import BrowserAccountClient
from open_grocery_mcp.providers.browser_config import BrowserStoreConfig
from open_grocery_mcp.providers.browser_driver import PlaywrightBrowserDriver
from open_grocery_mcp.providers.froiz_http import FroizHTTPClient
from open_grocery_mcp.providers.mercadona_account import MercadonaAccountClient
from open_grocery_mcp.providers.mercadona_browser import MercadonaBrowserMixin
from open_grocery_mcp.providers.mercadona_state import MercadonaStateClient


DEMO = BrowserStoreConfig(
    key="demo",
    label="Demo",
    base_url="https://demo.test",
    cart_paths=("/cart",),
)


def _assert_no_temp_files(directory: Path) -> None:
    assert not list(directory.glob("*.tmp"))
    assert not list(directory.glob(".*.tmp"))


def test_browser_account_state_cleans_temp_when_replace_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {"cookies": [{"domain": "demo.test", "name": "session", "value": "x"}], "origins": []}
        ),
        encoding="utf-8",
    )
    client = BrowserAccountClient(DEMO, state_root=tmp_path / "state")

    def fail_replace(_self, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        client.import_storage_state(str(source))
    _assert_no_temp_files(client.root)
    assert not client.state_path.exists()


def test_browser_checkout_records_are_mode_protected_and_cleaned(tmp_path, monkeypatch):
    client = BrowserAccountClient(DEMO, state_root=tmp_path)
    client._write_checkout_records({"checkout": {"store": "demo"}})
    assert client.checkout_path.exists()
    assert not list(client.root.glob("*.tmp"))

    def fail_replace(_self, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        client._write_checkout_records({"checkout": {"store": "demo", "changed": True}})
    _assert_no_temp_files(client.root)


def test_browser_driver_state_writer_cleans_playwright_temp(tmp_path, monkeypatch):
    driver = PlaywrightBrowserDriver(
        DEMO,
        state_path=tmp_path / "state.json",
        checkout_store=tmp_path / "checkouts.json",
    )

    class Context:
        def storage_state(self, *, path):
            Path(path).write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    def fail_replace(_self, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        driver._save_storage_state(Context())
    _assert_no_temp_files(tmp_path)


def test_froiz_token_cache_cleans_temp_when_replace_fails(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    cache = tmp_path / "http_token.json"
    client = FroizHTTPClient(state_path=state, token_cache_path=cache)
    monkeypatch.setattr(
        "open_grocery_mcp.providers.froiz_http.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )
    try:
        client._store_token("placeholder")
    finally:
        client.close()
    _assert_no_temp_files(tmp_path)
    assert not cache.exists()


def test_froiz_browser_state_cleans_temp_when_replace_fails(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    client = FroizHTTPClient(state_path=state, token_cache_path=tmp_path / "cache.json")

    class Context:
        def storage_state(self, *, path):
            Path(path).write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    monkeypatch.setattr(
        "open_grocery_mcp.providers.froiz_http.os.replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )
    with pytest.raises(OSError, match="simulated"):
        client._save_browser_state(Context())
    client.close()
    _assert_no_temp_files(tmp_path)
    assert json.loads(state.read_text(encoding="utf-8")) == {}


def test_mercadona_attempt_marker_cleans_temp_when_replace_fails(tmp_path, monkeypatch):
    client = MercadonaAccountClient(state_path=tmp_path / "state.json")
    monkeypatch.setattr(Path, "replace", lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")))
    with pytest.raises(OSError, match="simulated"):
        client._mark_order_attempt("checkout-placeholder")
    _assert_no_temp_files(tmp_path)
    client.close()


def test_mercadona_session_writer_preserves_old_state_on_replace_failure(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    original = {
        "cookies": [],
        "origins": [
            {
                "origin": "https://tienda.mercadona.es",
                "localStorage": [
                    {"name": "MO-user", "value": json.dumps({"token": "old", "uuid": "customer"})}
                ],
            }
        ],
    }
    state_path.write_text(json.dumps(original), encoding="utf-8")
    client = MercadonaStateClient(state_path=state_path)
    monkeypatch.setattr(Path, "replace", lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")))
    with pytest.raises(OSError, match="simulated"):
        client._write_user_tokens(access_token="new", refresh_token=None, customer_id="customer")
    assert json.loads(state_path.read_text(encoding="utf-8")) == original
    _assert_no_temp_files(tmp_path)
    client.close()


def test_mercadona_browser_state_preserves_old_state_on_replace_failure(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "state.json"
    state_path.write_text("old-state", encoding="utf-8")

    class Client(MercadonaBrowserMixin):
        def __init__(self):
            self.state_path = state_path

    class Context:
        def storage_state(self, *, path):
            Path(path).write_text("new-state", encoding="utf-8")

    monkeypatch.setattr(
        Path,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )

    with pytest.raises(OSError, match="simulated"):
        Client()._save_browser_storage_state(Context())

    assert state_path.read_text(encoding="utf-8") == "old-state"
    _assert_no_temp_files(tmp_path)
