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

import json
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds; doubles each attempt


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

    Args:
        prompt: the user message content.
        max_tokens: hard cap on response tokens. Pick from config.MAX_TOKENS_*.
        model: override the default model. Defaults to config.MODEL.

    Raises:
        The last exception encountered if all retries fail.
    """
    from config import MODEL  # local import keeps this module env-free at import

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = get_client().messages.create(
                model=model or MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
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
            return response.content[0].text
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


def _record_usage(input_tokens: int, output_tokens: int) -> None:
    """Add a single API call's token counts to today's row in usage.json."""
    path = _usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    row = data.setdefault(today, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    row["input_tokens"]  += int(input_tokens)
    row["output_tokens"] += int(output_tokens)
    row["calls"]         += 1
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    from config import MODEL_INPUT_PRICE_PER_MTOK, MODEL_OUTPUT_PRICE_PER_MTOK
    return (
        (input_tokens  / 1_000_000) * MODEL_INPUT_PRICE_PER_MTOK +
        (output_tokens / 1_000_000) * MODEL_OUTPUT_PRICE_PER_MTOK
    )


def get_usage_summary(days: int = 7) -> dict:
    """Return today + last-N-days totals, with USD cost. Safe to call on a missing file."""
    blank = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    path = _usage_path()
    if not path.exists():
        return {
            "today":  {**blank, "cost_usd": 0.0},
            "window": {"days": days, **blank, "cost_usd": 0.0},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    today_key = datetime.now().strftime("%Y-%m-%d")
    today_row = data.get(today_key, blank)

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    window_rows = [v for k, v in data.items() if k >= cutoff]
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
    }
