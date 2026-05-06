"""
Tests for the confidence_history append + cap behavior in pipeline.calibration.
"""

import json
from pathlib import Path

from pipeline import calibration
from pipeline.calibration import HISTORY_CAP, recalibrate_kb


def _seed(tmp_path: Path, kb, history):
    kb_path      = tmp_path / "structured.json"
    history_path = tmp_path / "interview_history.json"
    kb_path.write_text(json.dumps(kb), encoding="utf-8")
    history_path.write_text(json.dumps(history), encoding="utf-8")
    calibration.KB_PATH      = kb_path
    calibration.HISTORY_PATH = history_path
    return kb_path


def test_confidence_history_appends_on_tier_change(tmp_path):
    kb = [{"type": "exploration", "tool": "Karpenter", "description": "x", "confidence": "Low"}]
    history = [{"questions": [
        {"topic": "Karpenter", "score": 9},
        {"topic": "Karpenter", "score": 9},
        {"topic": "Karpenter", "score": 9},
    ]}]
    kb_path = _seed(tmp_path, kb, history)

    recalibrate_kb()

    written = json.loads(kb_path.read_text())
    hist = written[0]["confidence_history"]
    assert hist is not None
    assert len(hist) == 1
    ts, tier = hist[-1]
    assert tier == "High"
    assert "T" in ts  # ISO timestamp


def test_confidence_history_caps_at_history_cap(tmp_path):
    # Pre-seed an entry whose history is already at the cap, then trigger a
    # change. Oldest entry should be dropped.
    pre_history = [["2026-01-01T00:00:00", "Low"]] * HISTORY_CAP
    kb = [{
        "type": "exploration", "tool": "Karpenter", "description": "x",
        "confidence": "Low", "auto_confidence": "Low",
        "confidence_history": list(pre_history),
    }]
    history = [{"questions": [
        {"topic": "Karpenter", "score": 9},
        {"topic": "Karpenter", "score": 9},
        {"topic": "Karpenter", "score": 9},
    ]}]
    kb_path = _seed(tmp_path, kb, history)

    recalibrate_kb()

    written = json.loads(kb_path.read_text())
    hist = written[0]["confidence_history"]
    assert len(hist) == HISTORY_CAP
    assert hist[-1][1] == "High"   # newest
    # First entry rotated out; the original first was Low at 2026-01-01,
    # the new first should still be Low (rotated by one) but the LAST is High.
    assert hist[-2][1] == "Low"
