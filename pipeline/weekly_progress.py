"""
Per-week task completion tracking for archived weekly plans.

The plan markdown emits actionable items as `- [ ] Task description` lines
(see pipeline/weekly.py). This module provides the parser, the persistence
layer, and a tiny stats helper for the dashboard.

Schema (data/weekly_progress.json):

    {
      "2026-W19": { "0": true, "1": false, "2": true },
      "2026-W20": { ... }
    }

Keys are stringified line indices (Streamlit checkboxes need a stable key
and JSON object keys are strings). The line index is the position of the
checkbox among extracted task lines, so re-parsing the same plan gives the
same key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from config import DATA_DIR, OUTPUT_DIR

PROGRESS_PATH = Path(DATA_DIR) / "weekly_progress.json"

_TASK_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$")


def parse_checklist(plan_text: str) -> list[tuple[int, str, bool]]:
    """Extract `- [ ]` / `- [x]` lines from a plan. Returns [(idx, text, done), ...]."""
    out: list[tuple[int, str, bool]] = []
    for line in plan_text.splitlines():
        m = _TASK_RE.match(line)
        if not m:
            continue
        done = m.group(1).lower() == "x"
        text = m.group(2)
        out.append((len(out), text, done))
    return out


def load_progress() -> dict:
    if not PROGRESS_PATH.exists():
        return {}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_progress(week_key: str, idx: int, done: bool) -> None:
    data = load_progress()
    week = data.setdefault(week_key, {})
    week[str(idx)] = bool(done)
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def archived_weeks() -> list[str]:
    """Return week keys (`weekly_plan_YYYY-WNN.md` → `YYYY-WNN`) sorted desc."""
    out: list[str] = []
    for p in Path(OUTPUT_DIR).glob("weekly_plan_*.md"):
        stem = p.stem.replace("weekly_plan_", "")
        out.append(stem)
    return sorted(out, reverse=True)


def week_completion(week_key: str, plan_text: str) -> tuple[int, int]:
    """Return (done_count, total_count) for a given week."""
    items = parse_checklist(plan_text)
    if not items:
        return (0, 0)
    saved = load_progress().get(week_key, {})
    done = sum(1 for idx, _, baseline in items
               if saved.get(str(idx), baseline))
    return (done, len(items))
