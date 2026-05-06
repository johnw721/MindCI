"""
Tests for pipeline._client.call_with_retry — the universal API wrapper.

Verifies that:
* a successful first call returns the response text
* transient failures retry up to MAX_RETRIES with exponential backoff
* the model + max_tokens are forwarded to the underlying client
"""

import time

import pytest

from pipeline import _client


class _FakeMessages:
    def __init__(self, behavior):
        """behavior is a list of (kind, value) tuples; consumed in order.
        kind ∈ {'ok', 'fail'}.  'ok' → returns object whose .content[0].text == value.
                                'fail' → raises RuntimeError(value)."""
        self.behavior = list(behavior)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        kind, value = self.behavior.pop(0)
        if kind == "fail":
            raise RuntimeError(value)
        block = type("Block", (), {"text": value})()
        return type("Resp", (), {"content": [block]})()


class _FakeClient:
    def __init__(self, behavior):
        self.messages = _FakeMessages(behavior)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip the exponential backoff sleeps so tests stay fast."""
    monkeypatch.setattr(time, "sleep", lambda *_: None)


def test_first_attempt_success_returns_text(monkeypatch):
    fake = _FakeClient([("ok", "hello world")])
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    out = _client.call_with_retry("prompt", max_tokens=128)

    assert out == "hello world"
    assert len(fake.messages.calls) == 1
    assert fake.messages.calls[0]["max_tokens"] == 128
    assert fake.messages.calls[0]["model"]  # whatever config.MODEL is — present


def test_retries_until_success(monkeypatch):
    fake = _FakeClient([("fail", "rate limit"), ("fail", "server error"), ("ok", "third try lucky")])
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    out = _client.call_with_retry("prompt", max_tokens=64)

    assert out == "third try lucky"
    assert len(fake.messages.calls) == 3


def test_raises_after_exhausting_retries(monkeypatch):
    fake = _FakeClient([("fail", "boom-1"), ("fail", "boom-2"), ("fail", "boom-3")])
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    with pytest.raises(RuntimeError, match="boom-3"):
        _client.call_with_retry("prompt", max_tokens=32)

    assert len(fake.messages.calls) == _client.MAX_RETRIES
