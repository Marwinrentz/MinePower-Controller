"""Client-Adresse hinter einem Reverse-Proxy (Login-Sperre pro echter IP)."""
import pytest
from starlette.requests import Request

from app.core import netutil


def _req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


@pytest.fixture
def proxies(monkeypatch):
    from app.config import get_settings

    def set_(value: str):
        monkeypatch.setenv("TRUSTED_PROXIES", value)
        get_settings.cache_clear()

    yield set_
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    get_settings.cache_clear()


def test_header_ignored_without_trusted_proxies(proxies):
    proxies("")
    assert netutil.client_ip(_req("192.168.1.5", "1.2.3.4")) == "192.168.1.5"


def test_header_ignored_from_untrusted_peer(proxies):
    proxies("172.17.0.0/16")
    assert netutil.client_ip(_req("192.168.1.5", "1.2.3.4")) == "192.168.1.5"


def test_real_client_from_trusted_proxy(proxies):
    proxies("172.17.0.0/16")
    assert netutil.client_ip(_req("172.17.0.3", "203.0.113.9")) == "203.0.113.9"


def test_spoofed_left_entries_are_not_used(proxies):
    proxies("172.17.0.0/16, 10.0.0.1")
    # Angreifer schickt selbst "X-Forwarded-For: 9.9.9.9"; der Proxy hängt die echte Adresse an.
    assert netutil.client_ip(_req("172.17.0.3", "9.9.9.9, 198.51.100.7, 10.0.0.1")) == "198.51.100.7"


def test_garbage_header_falls_back_to_peer(proxies):
    proxies("172.17.0.3")
    assert netutil.client_ip(_req("172.17.0.3", "nicht-eine-ip")) == "172.17.0.3"
