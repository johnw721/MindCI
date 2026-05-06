"""
Lazy Anthropic client + universal retry.

Importing a `pipeline.*` module no longer constructs an Anthropic client.
The client is built on first call to `get_client()` and cached for the rest
of the process. This means:

* Tests can import any pipeline module without ANTHROPIC_API_KEY set, and
  without httpx trying to wire up a network client (which fails on
  sandboxed runners that lack `socksio` for SOCKS proxy support).
* Tools that only use the deterministic helpers in a module (parsers,
  scorers) never pay for client construction.
* Stubbing for tests is a one-line monkeypatch on this module.

`call_with_retry` is the standard way to call the API across the codebase.
It wraps `messages.create` with exponential backoff, pulls the model name
and token caps from `config`, and returns the response text directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds; doubles each attempt

CACHE_MAX_ENTRIES = 1000  # LRU-style cap on cached responses


@lru_cache(maxsize=1)
def get_client():
    """Return the shared Anthropic client, constructing it on first call."""
    from anthropic import Anthropic
    return Anthropic()


def reset_client() -> None:
    """Clear the cached client. Useful in tests after monkeypatching."""
    get_client.cache_clear()


def call_with_retry(prompt: str, *, max_tokens: int, model: str | None = None) -> str:
    """
    Call Anthropic with exponential backoff and return the response text.
    Records token usage to <DATA_DIR>/usage.json on every successful call.

    Response cache: identical (model, max_tokens, prompt) calls hit the
    on-disk cache and skip the API entirely. Disable with
    MINDCI_CACHE_DISABLE=1.

    Args:
        prompt: the user message content.
        max_tokens: hard cap on response tokens. Pick from config.MAX_TOKENS_*.
        model: override the default model. Defaults to config.MODEL.

    Raises:
        The last exception encountered if all retries fail.
    """
    from config import MODEL  # local import keeps this module env-free at import

    resolved_model = model or MODEL

    # ── Response cache lookup ─────────────────────────────────────────────────
    if not os.environ.get("MINDCI_CACHE_DISABLE"):
        key = _cache_key(resolved_model, max_tokens, prompt)
        hit = _cache_get(key)
        if hit is not None:
            _record_cache_event("hit")
            return hit

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = get_client().messages.create(
                model=resolved_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            # Best-effort cost telemetry — never let logging fail a real call.
            try:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    _record_usage(
                        getattr(usage, "input_tokens", 0) or 0,
                        getattr(usage, "output_tokens", 0) or 0,
                    )
            except Exception:
                pass
            # Cache the successful response (skipped if disabled).
            if not os.environ.get("MINDCI_CACHE_DISABLE"):
                _record_cache_event("miss")
                try:
                    _cache_put(_cache_key(resolved_model, max_tokens, prompt), text)
                except Exception:
                    pass
            return text
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE ** attempt)
    assert last_error is not None  # for type-checkers
    raise last_error


# ── Cost telemetry ────────────────────────────────────────────────────────────
def _usage_path() -> Path:
    """Resolve the usage log path lazily so DATA_DIR env overrides take effect."""
    from config import DATA_DIR
    return Path(DATA_DIR) / "usage.json"


def _load_usage() -> dict:
    path = _usage_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_usage(data: dict) -> None:
    path = _usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _record_usage(input_tokens: int, output_tokens: int) -> None:
    """Add a single API call's token counts to today's row in usage.json."""
    data = _load_usage()
    today = datetime.now().strftime("%Y-%m-%d")
    row = data.setdefault(today, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    row["input_tokens"]  += int(input_tokens)
    row["output_tokens"] += int(output_tokens)
    row["calls"]         += 1
    _save_usage(data)


def _record_cache_event(kind: str) -> None:
    """Increment cumulative cache hit/miss counters under the special `_cache` key."""
    data = _load_usage()
    cache_row = data.setdefault("_cache", {"hits": 0, "misses": 0})
    if kind == "hit":
        cache_row["hits"] += 1
    elif kind == "miss":
        cache_row["misses"] += 1
    _save_usage(data)


# ── Response cache ────────────────────────────────────────────────────────────
def _cache_path() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "response_cache.json"


def _cache_key(model: str, max_tokens: int, prompt: str) -> str:
    blob = f"{model}|{max_tokens}|{prompt}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _cache_load() -> dict:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_save(data: dict) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cache_get(key: str) -> str | None:
    data = _cache_load()
    entry = data.get(key)
    if entry is None:
        return None
    # Touch: move to end so LRU eviction keeps it fresh.
    data.pop(key)
    data[key] = entry
    _cache_save(data)
    return entry.get("text")


def _cache_put(key: str, text: str) -> None:
    data = _cache_load()
    data[key] = {"text": text, "ts": datetime.now().isoformat(timespec="seconds")}
    # LRU eviction: drop oldest entries when over cap.
    while len(data) > CACHE_MAX_ENTRIES:
        oldest = next(iter(data))
        data.pop(oldest)
    _cache_save(data)


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    from config import MODEL_INPUT_PRICE_PER_MTOK, MODEL_OUTPUT_PRICE_PER_MTOK
    return (
        (input_tokens  / 1_000_000) * MODEL_INPUT_PRICE_PER_MTOK +
        (output_tokens / 1_000_000) * MODEL_OUTPUT_PRICE_PER_MTOK
    )


def get_usage_summary(days: int = 7) -> dict:
    """Return today + last-N-days totals plus cumulative cache stats.
    Safe to call on a missing file."""
    blank = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    blank_cache = {"hits": 0, "misses": 0}
    path = _usage_path()
    if not path.exists():
        return {
            "today":  {**blank, "cost_usd": 0.0},
            "window": {"days": days, **blank, "cost_usd": 0.0},
            "cache":  blank_cache,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    today_key = datetime.now().strftime("%Y-%m-%d")
    today_row = data.get(today_key, blank)

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    # Aggregate only date-keyed rows (skip the special `_cache` row).
    window_rows = [
        v for k, v in data.items()
        if not k.startswith("_") and k >= cutoff and isinstance(v, dict) and "calls" in v
    ]
    win_in    = sum(r["input_tokens"]  for r in window_rows)
    win_out   = sum(r["output_tokens"] for r in window_rows)
    win_calls = sum(r["calls"]         for r in window_rows)

    return {
        "today": {
            **today_row,
            "cost_usd": round(_cost_usd(today_row["input_tokens"], today_row["output_tokens"]), 4),
        },
        "window": {
            "days": days,
            "input_tokens":  win_in,
            "output_tokens": win_out,
            "calls":         win_calls,
            "cost_usd":      round(_cost_usd(win_in, win_out), 4),
        },
        "cache": data.get("_cache", blank_cache),
    }
