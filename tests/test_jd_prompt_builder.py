"""Lock in the build_gap_analysis_prompt extraction.

run_gap_analysis was refactored to delegate prompt construction to
build_gap_analysis_prompt so the eval harness can reuse the exact production
prompt. These tests guard against drift between the two.
"""

from __future__ import annotations

from pipeline import jd_analyzer

KB = [
    {"type": "certification", "topic": "Terraform Associate", "confidence": "Medium"},
    {"type": "exploration", "tool": "AWS Lambda", "confidence": "High"},
]
JD = "Cloud Engineer. Need Terraform, EKS, Python."
RESUME = {"skills": ["Terraform"], "projects": ["MindCI"], "companies": ["Acme"]}


def _capture_prompt(monkeypatch):
    captured = {}

    def fake(prompt, *args, **kwargs):
        captured["prompt"] = prompt
        return "{}"

    monkeypatch.setattr(jd_analyzer, "call_with_retry", fake)
    return captured


def test_run_gap_analysis_sends_builder_output_plain(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    jd_analyzer.run_gap_analysis(JD, KB)
    assert captured["prompt"] == jd_analyzer.build_gap_analysis_prompt(JD, KB)


def test_run_gap_analysis_sends_builder_output_with_resume(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    jd_analyzer.run_gap_analysis(JD, KB, RESUME)
    assert captured["prompt"] == jd_analyzer.build_gap_analysis_prompt(JD, KB, RESUME)


def test_builder_includes_schema_and_no_api_call():
    prompt = jd_analyzer.build_gap_analysis_prompt(JD, KB)
    for key in ("role_title", "priority_gaps", "matched_skills", "readiness_score"):
        assert key in prompt
    # plain (no resume) prompt must NOT contain the resume-only buckets
    assert "strengths_to_lead_with" not in prompt


def test_builder_adds_resume_buckets_when_claims_present():
    prompt = jd_analyzer.build_gap_analysis_prompt(JD, KB, RESUME)
    assert "strengths_to_lead_with" in prompt
    assert "hidden_assets" in prompt
    assert "RESUME CLAIMS" in prompt
