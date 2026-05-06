"""
Adaptive confidence calibration based on mock-interview history.

After each interview ends, this module recomputes each KB entry's
`auto_confidence` from the rolling average of the entry's last N attempt
scores. Hysteresis prevents tier-flipping at threshold edges; a minimum
sample size prevents noisy single-attempt swings.

Tier mapping (climbing requires threshold + buffer; dropping requires
falling below threshold − buffer; otherwise the tier sticks):

    avg ≥ 8.5  → High      (from any tier)
    avg ≥ 6.5  → Medium    (from Low, climbing)
    avg < 7.5  → Medium    (from High, dropping)
    avg < 5.5  → Low       (from any tier, dropping)

Manual `confidence` is always preserved as the user's seed; `auto_confidence`
is the derived value all downstream consumers actually use via
`effective_confidence(entry)`.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, OUTPUT_DIR

TIER_HIGH_MIN     = 8.0   # avg ≥ this is High territory
TIER_MEDIUM_MIN   = 6.0   # avg ≥ this is Medium territory
HYSTERESIS_BUFFER = 0.5

LAST_N_ATTEMPTS = 5
MIN_SAMPLE_SIZE = 3

KB_PATH      = Path(DATA_DIR)   / "structured.json"
HISTORY_PATH = Path(OUTPUT_DIR) / "interview_history.json"

VALID_TIERS = {"High", "Medium", "Low"}


def effective_confidence(entry: dict) -> str:
    """Confidence read by all downstream consumers — auto if set and valid, else manual."""
    auto = entry.get("auto_confidence")
    if auto in VALID_TIERS:
        return auto
    manual = entry.get("confidence")
    return manual if manual in VALID_TIERS else "Low"


def entry_label(entry: dict) -> str:
    """Canonical primary label for an entry."""
    return (entry.get("topic") or entry.get("concept") or
            entry.get("tool")  or entry.get("error")   or "unknown")


def entry_matches_topic(entry: dict, topic: str) -> bool:
    """Best-effort match of a history `topic` string against an entry's labels.

    History records the topic field from the interview pool, which can be:
      * scenario topic (entry's primary label)
      * flashcard category (entry.category or entry.tool or 'general')
    We try every plausible field so old data still contributes.
    """
    if not topic:
        return False
    candidates = {
        entry.get("topic"), entry.get("concept"),
        entry.get("tool"),  entry.get("error"),
        entry.get("category"),
    }
    candidates.discard(None)
    candidates.discard("")
    return topic in candidates


def next_tier(avg: float, current_tier: str) -> str:
    """Hysteresis-aware tier mapping. Climbing requires +buffer; dropping requires −buffer."""
    if current_tier == "High":
        if avg < TIER_MEDIUM_MIN - HYSTERESIS_BUFFER:
            return "Low"
        if avg < TIER_HIGH_MIN - HYSTERESIS_BUFFER:
            return "Medium"
        return "High"
    if current_tier == "Medium":
        if avg >= TIER_HIGH_MIN + HYSTERESIS_BUFFER:
            return "High"
        if avg < TIER_MEDIUM_MIN - HYSTERESIS_BUFFER:
            return "Low"
        return "Medium"
    # Low or unknown
    if avg >= TIER_HIGH_MIN + HYSTERESIS_BUFFER:
        return "High"
    if avg >= TIER_MEDIUM_MIN + HYSTERESIS_BUFFER:
        return "Medium"
    return "Low"


def recent_scores(entry: dict, history: list, last_n: int = LAST_N_ATTEMPTS) -> list[int]:
    """Last N attempt scores for this entry, oldest → newest. Skipped answers count as 0."""
    matches: list[int] = []
    for session in history:
        for q in session.get("questions", []):
            if entry_matches_topic(entry, q.get("topic", "")):
                matches.append(int(q.get("score", 0)))
    return matches[-last_n:]


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def recalibrate_kb() -> list[dict]:
    """Recompute auto_confidence for every entry. Returns a list of changes:
    [{"label", "old", "new", "samples", "avg"}, ...]"""
    history = _load_json(HISTORY_PATH, [])
    kb      = _load_json(KB_PATH, [])
    if not history or not kb:
        return []

    changes: list[dict] = []
    now = datetime.now().isoformat(timespec="seconds")

    for entry in kb:
        scores = recent_scores(entry, history)
        if len(scores) < MIN_SAMPLE_SIZE:
            continue
        avg     = sum(scores) / len(scores)
        current = effective_confidence(entry)
        new     = next_tier(avg, current)
        if new == current:
            continue
        entry["auto_confidence"]       = new
        entry["confidence_updated_at"] = now
        changes.append({
            "label":   entry_label(entry),
            "old":     current,
            "new":     new,
            "samples": len(scores),
            "avg":     round(avg, 2),
        })

    if changes:
        KB_PATH.parent.mkdir(parents=True, exist_ok=True)
        KB_PATH.write_text(json.dumps(kb, indent=2, ensure_ascii=False), encoding="utf-8")

    return changes
