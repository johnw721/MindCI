"""
Tests for the cost telemetry middleware in pipeline/_client.py.
"""

import json
from pathlib import Path

from pipeline import _client


def _clear_usage():
    p = _client._usage_path()
    if p.exists():
        p.unlink()


def test_record_usage_creates_and_appends_to_today():
    _clear_usage()
    _client._record_usage(100, 200)
    _client._record_usage(50, 25)

    data = json.loads(_client._usage_path().read_text(encoding="utf-8"))
    rows = list(data.values())
    assert len(rows) == 1
    assert rows[0]["input_tokens"]  == 150
    assert rows[0]["output_tokens"] == 225
    assert rows[0]["calls"]         == 2
    _clear_usage()


def test_get_usage_summary_blank_when_no_file():
    _clear_usage()
    s = _client.get_usage_summary(days=7)
    assert s["today"]["calls"]      == 0
    assert s["today"]["cost_usd"]   == 0.0
    assert s["window"]["cost_usd"]  == 0.0


def test_get_usage_summary_computes_cost_from_pricing():
    """At default Sonnet 4.5 pricing ($3/MTok in, $15/MTok out):
    1,000,000 input + 1,000,000 output = $3 + $15 = $18 exactly."""
    _clear_usage()
    _client._record_usage(1_000_000, 1_000_000)

    s = _client.get_usage_summary(days=7)
    assert s["today"]["cost_usd"]  == 18.0
    assert s["window"]["cost_usd"] == 18.0
    assert s["today"]["calls"]     == 1
    _clear_usage()


def test_call_with_retry_records_usage_when_present(monkeypatch):
    """If response.usage exists, call_with_retry records it. If not, no crash."""
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    _clear_usage()

    class _Usage:
        def __init__(self, i, o): self.input_tokens, self.output_tokens = i, o

    class _Resp:
        def __init__(self, text, usage=None):
            block = type("B", (), {"text": text})()
            self.content = [block]
            self.usage = usage

    class _Msgs:
        def __init__(self, resps): self.resps = list(resps)
        def create(self, **_): return self.resps.pop(0)

    class _Client:
        def __init__(self, resps): self.messages = _Msgs(resps)

    fake = _Client([_Resp("first",  _Usage(40, 60)),
                    _Resp("second", None)])  # second call has no usage attr
    monkeypatch.setattr(_client, "get_client", lambda: fake)

    _client.call_with_retry("p1", max_tokens=64)
    _client.call_with_retry("p2", max_tokens=64)

    data = json.loads(_client._usage_path().read_text(encoding="utf-8"))
    row = next(iter(data.values()))
    # Only the first call had usage; totals should be 40/60/1 (calls counts only recorded ones).
    assert row["input_tokens"]  == 40
    assert row["output_tokens"] == 60
    assert row["calls"]         == 1
    _clear_usage()
