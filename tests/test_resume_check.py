"""
Tests for pipeline.resume_check.

Covers the deterministic coverage math. The LLM parser
(`parse_resume_to_claims`) is exercised by the integration test that stubs
`call_with_retry`; here we keep the focus on matching + bucketing logic.
"""

import json
from pathlib import Path

from pipeline import resume_check
from pipeline.resume_check import (
    _claim_matches_entry,
    _kb_candidates,
    compute_coverage,
    load_resume_claims,
    save_resume_claims,
)


# ── _kb_candidates ────────────────────────────────────────────────────────────
def test_kb_candidates_pulls_all_plausible_fields_lowercased():
    entry = {
        "type": "project", "error": "EKS pod scheduling failure",
        "concept": "Karpenter consolidation",
        "tool": "", "category": None, "description": "Some longer description.",
    }
    out = _kb_candidates(entry)
    assert "eks pod scheduling failure" in out
    assert "karpenter consolidation" in out
    assert "some longer description." in out
    # Empty / None fields are excluded.
    assert "" not in out
    assert None not in out


# ── _claim_matches_entry ──────────────────────────────────────────────────────
def test_claim_matches_substring_in_either_direction():
    blob = {"aws lambda", "kubernetes pod scheduling"}
    assert _claim_matches_entry("Lambda", blob)      # claim ⊂ field
    assert _claim_matches_entry("AWS Lambda", blob)  # exact
    assert _claim_matches_entry("kubernetes", blob)  # claim ⊂ field
    assert not _claim_matches_entry("Karpenter", blob)


def test_claim_matches_handles_empty_claim_gracefully():
    assert not _claim_matches_entry("", {"anything"})
    assert not _claim_matches_entry("   ", {"anything"})


# ── compute_coverage ──────────────────────────────────────────────────────────
def test_coverage_buckets_and_totals():
    claims = {
        "skills":    ["AWS Lambda", "Karpenter", "Terraform"],
        "projects":  ["AD Onboarding System"],
        "companies": ["Stibo Systems"],
    }
    kb = [
        # Lambda is covered (substring of entry's error field).
        {"type": "project", "error": "Lambda cold start", "fix": "lazy import", "root_cause": "circ"},
        # Terraform covered (entry topic).
        {"type": "certification", "topic": "Terraform Associate", "key_points": "state, modules"},
        # AD Onboarding covered (entry concept).
        {"type": "project", "error": "AD provisioning broken", "concept": "AD Onboarding System",
         "fix": "lazy role lookup", "root_cause": "..."},
        # Karpenter, Stibo Systems → no matching entries → missing
    ]

    cov = compute_coverage(claims, kb)

    # Skills
    skills_covered_claims = [r["claim"] for r in cov["skills"]["covered"]]
    skills_missing_claims = [r["claim"] for r in cov["skills"]["missing"]]
    assert "AWS Lambda" in skills_covered_claims
    assert "Terraform" in skills_covered_claims
    assert "Karpenter" in skills_missing_claims

    # Projects
    assert any(r["claim"] == "AD Onboarding System" for r in cov["projects"]["covered"])

    # Companies — no matching entries
    assert any(r["claim"] == "Stibo Systems" for r in cov["companies"]["missing"])

    # Totals: 5 claims, 3 backed = 60%
    assert cov["totals"] == {"claims": 5, "covered": 3, "pct": 60}


def test_coverage_handles_empty_kb_and_empty_claims():
    assert compute_coverage({}, [])["totals"] == {"claims": 0, "covered": 0, "pct": 0}
    cov = compute_coverage({"skills": ["AWS"]}, [])
    assert cov["totals"]["claims"] == 1 and cov["totals"]["covered"] == 0


# ── save / load round-trip ────────────────────────────────────────────────────
def test_save_and_load_resume_claims_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(resume_check, "CLAIMS_PATH", tmp_path / "resume_claims.json")
    payload = {"skills": ["AWS", "K8s"], "projects": ["X"], "companies": ["Co."]}
    save_resume_claims(payload)
    assert load_resume_claims() == payload


def test_load_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(resume_check, "CLAIMS_PATH", tmp_path / "nonexistent.json")
    assert load_resume_claims() is None
