"""
Tests for the response cache layered into pipeline._client.call_with_retry.
"""

import json
import time

import pytest

from pipeline import _client


# ── Fakes ─────────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, text, in_t=0, out_t=0):
        block = type("B", (), {"text": text})()
        self.content = [block]
        self.usage = type("U", (), {"input_tokens": in_t, "output_tokens": out_t})()


class _CountingClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0
        self.messages = self

    def create(self, **_):
        self.calls += 1
        return self._response


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _isolate_cache_files():
    """Clear cache + usage files before and after each test."""
    for path in (_client._cache_path(), _client._usage_path()):
        if path.exists():
            path.unlink()
    yield
    for path in (_client._cache_path(), _client._usage_path()):
        if path.exists():
            path.unlink()


# ── Tests ─────────────────────────────────────────────────────────────────────
def test_cache_miss_then_hit_skips_second_api_call(monkeypatch):
    fake = _CountingClient(_Resp("hello", in_t=10, out_t=20))
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    out1 = _client.call_with_retry("identical prompt", max_tokens=128)
    out2 = _client.call_with_retry("identical prompt", max_tokens=128)

    assert out1 == out2 == "hello"
    assert fake.calls == 1  # second call was a cache hit

    usage = json.loads(_client._usage_path().read_text(encoding="utf-8"))
    assert usage["_cache"] == {"hits": 1, "misses": 1}


def test_different_prompts_do_not_collide(monkeypatch):
    # Two different prompts should both miss and produce two real API calls.
    responses = [_Resp("first"), _Resp("second")]
    class _Twins:
        def __init__(self):
            self.calls = 0
            self.messages = self

        def create(self, **_):
            r = responses[self.calls]
            self.calls += 1
            return r
    fake = _Twins()
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    a = _client.call_with_retry("prompt A", max_tokens=64)
    b = _client.call_with_retry("prompt B", max_tokens=64)

    assert a == "first" and b == "second"
    assert fake.calls == 2


def test_different_max_tokens_do_not_collide(monkeypatch):
    """Cache key includes max_tokens — same prompt at different caps must miss."""
    responses = [_Resp("short"), _Resp("long")]

    class _CapTwins:
        def __init__(self):
            self.calls = 0
            self.messages = self

        def create(self, **_):
            r = responses[self.calls]
            self.calls += 1
            return r
    fake = _CapTwins()
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    _client.call_with_retry("same prompt", max_tokens=128)
    _client.call_with_retry("same prompt", max_tokens=512)

    assert fake.calls == 2


def test_disable_env_bypasses_cache(monkeypatch):
    fake = _CountingClient(_Resp("uncached"))
    monkeypatch.setattr(_client, "get_client", lambda: fake)
    monkeypatch.setenv("MINDCI_CACHE_DISABLE", "1")

    _client.call_with_retry("prompt", max_tokens=64)
    _client.call_with_retry("prompt", max_tokens=64)

    assert fake.calls == 2  # disable env forces every call through


def test_eviction_drops_oldest_at_cap(monkeypatch):
    """When cache exceeds CACHE_MAX_ENTRIES, the oldest (insertion-ordered) is dropped."""
    monkeypatch.setattr(_client, "CACHE_MAX_ENTRIES", 3)
    responses = [_Resp(f"r{i}") for i in range(5)]

    class _Seq:
        def __init__(self):
            self.calls = 0
            self.messages = self

        def create(self, **_):
            r = responses[self.calls]
            self.calls += 1
            return r
    fake = _Seq()
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    for i in range(5):
        _client.call_with_retry(f"prompt-{i}", max_tokens=64)

    cache = json.loads(_client._cache_path().read_text(encoding="utf-8"))
    assert len(cache) == 3
    # The two oldest (prompt-0, prompt-1) should be evicted; the three newest remain.
    cached_texts = {entry["text"] for entry in cache.values()}
    assert cached_texts == {"r2", "r3", "r4"}
