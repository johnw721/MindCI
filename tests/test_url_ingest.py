"""
Tests for pipeline.convert.fetch_url_as_markdown — the Jina Reader bridge.
"""

from io import BytesIO

import pytest

from pipeline import convert


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self._body


def test_fetch_prepends_jina_base_and_returns_body(monkeypatch):
    seen_urls: list[str] = []
    def fake_urlopen(req, timeout):
        seen_urls.append(req.full_url)
        return _FakeResponse(b"# Clean markdown\n\nfrom Jina Reader.")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = convert.fetch_url_as_markdown("https://example.com/blog/foo")
    assert out.startswith("# Clean markdown")
    assert seen_urls == ["https://r.jina.ai/https://example.com/blog/foo"]


def test_fetch_respects_env_override_for_reader_url(monkeypatch):
    seen: list[str] = []
    def fake(req, timeout):
        seen.append(req.full_url)
        return _FakeResponse(b"body")
    monkeypatch.setenv("MINDCI_READER_URL", "https://my-reader.local")
    monkeypatch.setattr("urllib.request.urlopen", fake)

    convert.fetch_url_as_markdown("https://example.com")
    # Trailing slash auto-added.
    assert seen[0] == "https://my-reader.local/https://example.com"


def test_fetch_raises_on_empty_body(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout: _FakeResponse(b"   \n   "))
    with pytest.raises(ValueError, match="empty body"):
        convert.fetch_url_as_markdown("https://example.com")
