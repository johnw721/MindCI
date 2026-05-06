"""
Tests for pipeline/calibration.py — the adaptive confidence loop.
"""

import json
from pathlib import Path

from pipeline import calibration
from pipeline.calibration import (
    HYSTERESIS_BUFFER,
    MIN_SAMPLE_SIZE,
    TIER_HIGH_MIN,
    TIER_MEDIUM_MIN,
    effective_confidence,
    entry_matches_topic,
    next_tier,
    recalibrate_kb,
    recent_scores,
)


# ── effective_confidence ──────────────────────────────────────────────────────
def test_effective_confidence_prefers_auto_then_manual():
    assert effective_confidence({"confidence": "Low", "auto_confidence": "High"}) == "High"
    assert effective_confidence({"confidence": "Medium"}) == "Medium"
    assert effective_confidence({}) == "Low"
    # Invalid auto value falls back to manual
    assert effective_confidence({"confidence": "Medium", "auto_confidence": "garbage"}) == "Medium"


# ── entry_matches_topic ───────────────────────────────────────────────────────
def test_entry_matches_topic_across_label_variants():
    e = {"type": "project", "error": "Lambda cold start failed", "concept": "Python imports", "tool": ""}
    # Topic from history can be the error, concept, or even category — all should match.
    assert entry_matches_topic(e, "Lambda cold start failed")
    assert entry_matches_topic(e, "Python imports")
    assert not entry_matches_topic(e, "")
    assert not entry_matches_topic(e, "Kubernetes")
    # Empty/missing labels don't false-positive
    assert not entry_matches_topic({"tool": ""}, "")


# ── next_tier hysteresis ──────────────────────────────────────────────────────
def test_next_tier_climb_requires_threshold_plus_buffer():
    """From Low, you need avg ≥ 8.5 to reach High and ≥ 6.5 to reach Medium."""
    assert next_tier(8.5, "Low") == "High"
    assert next_tier(8.4, "Low") == "Medium"   # high enough for medium-buffer (6.5) but not high+buffer (8.5)
    assert next_tier(6.5, "Low") == "Medium"
    assert next_tier(6.4, "Low") == "Low"


def test_next_tier_drop_requires_threshold_minus_buffer():
    """From High, you stay High unless avg < 7.5; only fall to Low if avg < 5.5."""
    assert next_tier(7.5, "High") == "High"     # equal to threshold − buffer is still High
    assert next_tier(7.49, "High") == "Medium"
    assert next_tier(5.5, "High") == "Medium"
    assert next_tier(5.49, "High") == "Low"


def test_next_tier_medium_sticks_in_the_band():
    """From Medium, neither 7.4 nor 8.4 promotes; only 8.5+ does."""
    assert next_tier(7.4, "Medium") == "Medium"
    assert next_tier(8.4, "Medium") == "Medium"
    assert next_tier(8.5, "Medium") == "High"
    assert next_tier(5.5, "Medium") == "Medium"
    assert next_tier(5.49, "Medium") == "Low"


# ── recent_scores ─────────────────────────────────────────────────────────────
def test_recent_scores_pulls_only_last_n_for_matching_topic():
    history = [
        {"questions": [
            {"topic": "Kubernetes", "score": 3},
            {"topic": "Other",      "score": 9},
        ]},
        {"questions": [
            {"topic": "Kubernetes", "score": 5},
            {"topic": "Kubernetes", "score": 6},
        ]},
        {"questions": [
            {"topic": "Kubernetes", "score": 7},
            {"topic": "Kubernetes", "score": 8},
            {"topic": "Kubernetes", "score": 9},
        ]},
    ]
    entry = {"topic": "Kubernetes", "type": "exploration"}
    out = recent_scores(entry, history, last_n=5)
    # Total Kubernetes scores: [3, 5, 6, 7, 8, 9] → last 5 = [5, 6, 7, 8, 9]
    assert out == [5, 6, 7, 8, 9]


# ── recalibrate_kb (end-to-end with monkeypatched paths) ──────────────────────
def _seed_files(tmp_path: Path, kb, history):
    kb_path      = tmp_path / "structured.json"
    history_path = tmp_path / "interview_history.json"
    kb_path.write_text(json.dumps(kb), encoding="utf-8")
    history_path.write_text(json.dumps(history), encoding="utf-8")
    calibration.KB_PATH      = kb_path
    calibration.HISTORY_PATH = history_path
    return kb_path


def test_recalibrate_skips_below_min_samples(tmp_path, monkeypatch):
    kb = [{"type": "exploration", "tool": "Karpenter", "description": "x", "confidence": "Low"}]
    history = [{"questions": [{"topic": "Karpenter", "score": 9}, {"topic": "Karpenter", "score": 9}]}]
    _seed_files(tmp_path, kb, history)

    changes = recalibrate_kb()

    assert changes == []  # 2 attempts < MIN_SAMPLE_SIZE (3)
    written = json.loads((tmp_path / "structured.json").read_text())
    assert "auto_confidence" not in written[0]


def test_recalibrate_writes_auto_confidence_and_changelog(tmp_path, monkeypatch):
    kb = [
        {"type": "exploration", "tool": "Karpenter",  "description": "x", "confidence": "Low"},
        {"type": "project",     "error": "EKS broke", "root_cause": "y", "fix": "z", "confidence": "High"},
    ]
    history = [{"questions": [
        {"topic": "Karpenter",  "score": 9},  # avg 9 → climbs Low → High
        {"topic": "Karpenter",  "score": 9},
        {"topic": "Karpenter",  "score": 9},
        {"topic": "EKS broke",  "score": 4},  # avg 4 → drops High → Low
        {"topic": "EKS broke",  "score": 5},
        {"topic": "EKS broke",  "score": 4},
    ]}]
    _seed_files(tmp_path, kb, history)

    changes = recalibrate_kb()

    assert len(changes) == 2
    by_label = {c["label"]: c for c in changes}
    assert by_label["Karpenter"]["new"] == "High"
    assert by_label["Karpenter"]["old"] == "Low"
    assert by_label["EKS broke"]["new"] == "Low"
    assert by_label["EKS broke"]["old"] == "High"

    written = json.loads((tmp_path / "structured.json").read_text())
    by_label_w = {(e.get("tool") or e.get("error")): e for e in written}
    assert by_label_w["Karpenter"]["auto_confidence"] == "High"
    assert by_label_w["Karpenter"]["confidence"]      == "Low"        # manual seed preserved
    assert by_label_w["EKS broke"]["auto_confidence"] == "Low"
    assert by_label_w["EKS broke"]["confidence"]      == "High"
    assert by_label_w["Karpenter"]["confidence_updated_at"]
