"""
Tests for config.load_jd_frequencies — the JD frequency source resolver.
The function returns (frequencies, source_label, count) and switches between
baseline / blended / live based on how many reports have been aggregated.
"""

import json
import os
from pathlib import Path

import config


def _write_market_frequencies(payload):
    path = Path(config.MARKET_FREQUENCIES_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _clear_market_frequencies():
    path = Path(config.MARKET_FREQUENCIES_PATH)
    if path.exists():
        path.unlink()


def test_baseline_when_no_aggregated_file_exists():
    _clear_market_frequencies()
    freqs, source, count = config.load_jd_frequencies()

    assert count == 0
    assert source.startswith("baseline")
    # Sanity: a few well-known baseline keys are present.
    assert "Kubernetes" in freqs
    assert "Terraform" in freqs


def test_blended_below_threshold_merges_live_and_baseline():
    _write_market_frequencies({
        "total_reports": 1,  # under MIN_REPORTS_FOR_LIVE_DATA (3)
        "frequencies": {"Karpenter": 0.42},
    })
    try:
        freqs, source, count = config.load_jd_frequencies()
        assert count == 1
        assert source.startswith("blended")
        # Live entry layered on top of baseline:
        assert freqs["Karpenter"] == 0.42
        # Baseline still present:
        assert "Kubernetes" in freqs
    finally:
        _clear_market_frequencies()


def test_live_when_at_or_above_threshold():
    _write_market_frequencies({
        "total_reports": config.MIN_REPORTS_FOR_LIVE_DATA,
        "frequencies": {"Cilium": 0.31, "Istio": 0.27},
    })
    try:
        freqs, source, count = config.load_jd_frequencies()
        assert count == config.MIN_REPORTS_FOR_LIVE_DATA
        assert source.startswith("live")
        # Live data only — baseline keys should NOT leak in.
        assert "Cilium" in freqs
        assert "Kubernetes" not in freqs
    finally:
        _clear_market_frequencies()
