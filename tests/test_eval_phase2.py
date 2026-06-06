"""Offline tests for Phase 2 of the eval harness: grounding/hallucination,
consistency/flake rate, and the LLM-as-judge (plus its self-validation).

All API calls are stubbed; nothing touches the network.
"""

from __future__ import annotations

import json

from evals import consistency, hallucination, judge


# ---------------------------------------------------------------------------
# hallucination / grounding
# ---------------------------------------------------------------------------

JD = "Cloud Engineer. Required: Terraform, EKS, Python."


def test_is_grounded_present_and_absent():
    assert hallucination.is_grounded("Amazon EKS", JD)      # 'eks' is in the JD
    assert hallucination.is_grounded("Terraform", JD)
    assert not hallucination.is_grounded("Kafka", JD)        # never mentioned


def test_is_grounded_vendor_word_alone_does_not_ground():
    # "AWS" is a vendor word -> no significant token -> cannot be grounded.
    assert not hallucination.is_grounded("AWS", JD)


def test_check_grounding_flags_invented_skills():
    parsed = {
        "priority_gaps": [{"domain": "EKS"}, {"domain": "Kafka"}],
        "matched_skills": [{"domain": "Terraform"}, {"domain": "Redis"}],
    }
    r = hallucination.check_grounding(parsed, JD)
    assert r["asserted"] == 4
    assert r["hallucinated"] == 2
    assert r["rate"] == 0.5
    assert r["hallucinated_gaps"] == ["Kafka"]
    assert r["hallucinated_matches"] == ["Redis"]


def test_check_grounding_clean_output_has_zero_rate():
    parsed = {"priority_gaps": [{"domain": "EKS"}], "matched_skills": [{"domain": "Python"}]}
    assert hallucination.check_grounding(parsed, JD)["rate"] == 0.0


# ---------------------------------------------------------------------------
# consistency
# ---------------------------------------------------------------------------

def test_canonical_key_folds_vendor_prefix_variants():
    # Vendor words are dropped, so "EKS" and "Amazon EKS" fold to one key...
    assert consistency.canonical_key("EKS") == consistency.canonical_key("Amazon EKS")
    # ...but a genuinely different phrasing (extra content word) stays distinct.
    assert consistency.canonical_key("CI/CD") != consistency.canonical_key("CI/CD pipelines")


def test_mean_pairwise_jaccard_extremes():
    assert consistency._mean_pairwise_jaccard([{1}, {1}, {1}]) == 1.0
    assert consistency._mean_pairwise_jaccard([{1}, {2}]) == 0.0
    assert consistency._mean_pairwise_jaccard([set(), set()]) == 1.0  # empty == empty
    assert consistency._mean_pairwise_jaccard([{1}]) == 1.0           # single run


def test_run_consistency_perfectly_stable():
    # Deterministic caller -> identical output every run -> stability 1.0, stdev 0.
    def caller(prompt, *, max_tokens, model=None):
        return json.dumps({"readiness_score": 60,
                           "priority_gaps": [{"domain": "Helm"}, {"domain": "ArgoCD"}],
                           "matched_skills": []})
    res = consistency.run_consistency(
        n=3, caller=caller, repair_fn=lambda r: "{}",
        case_ids=["platform-k8s-heavy"], disable_cache=False,
    )
    assert res["mean_gap_stability"] == 1.0
    assert res["perfectly_stable_cases"] == 1
    assert res["cases"][0]["score_stdev"] == 0.0
    assert res["cases"][0]["runs"] == 3


def test_run_consistency_detects_flake():
    # Alternate the gap set between calls -> stability < 1.0.
    state = {"i": 0}

    def caller(prompt, *, max_tokens, model=None):
        state["i"] += 1
        gaps = [{"domain": "Helm"}] if state["i"] % 2 else [{"domain": "ArgoCD"}]
        return json.dumps({"readiness_score": 50, "priority_gaps": gaps, "matched_skills": []})

    res = consistency.run_consistency(
        n=4, caller=caller, repair_fn=lambda r: "{}",
        case_ids=["platform-k8s-heavy"], disable_cache=False,
    )
    assert res["mean_gap_stability"] < 1.0
    assert res["perfectly_stable_cases"] == 0


# ---------------------------------------------------------------------------
# LLM-as-judge
# ---------------------------------------------------------------------------

def _scoring_judge(prompt, *, max_tokens, model=None):
    """Stub judge: low scores when the explanation contains lazy phrasing."""
    if "Learn it." in prompt or "study it" in prompt.lower():
        return '{"specificity":1,"actionability":1,"grounding":2,"justification":"vague"}'
    return '{"specificity":5,"actionability":4,"grounding":5,"justification":"concrete"}'


def test_judge_explanation_returns_overall():
    analysis = {"summary": "x", "priority_gaps": [{"domain": "Helm", "action": "Build a chart"}]}
    r = judge.judge_explanation("JD: Helm", analysis, _scoring_judge, repair_fn=lambda r: "{}")
    assert r["parse_status"] == "first_try"
    assert r["overall"] == 4.67
    assert r["specificity"] == 5.0


def test_judge_clamps_out_of_range_scores():
    def caller(prompt, *, max_tokens, model=None):
        return '{"specificity":9,"actionability":0,"grounding":3,"justification":"x"}'
    r = judge.judge_explanation("JD", {"summary": "", "priority_gaps": []},
                                caller, repair_fn=lambda r: "{}")
    assert r["specificity"] == 5.0   # clamped from 9
    assert r["actionability"] == 1.0  # clamped from 0


def test_judge_handles_unparseable_response():
    r = judge.judge_explanation("JD", {"summary": "", "priority_gaps": []},
                                lambda p, *, max_tokens, model=None: "not json",
                                repair_fn=lambda r: "still not json")
    assert r["parse_status"] == "failed"
    assert r["overall"] is None


def test_validate_judge_passes_when_ranking_is_correct():
    v = judge.validate_judge(_scoring_judge, repair_fn=lambda r: "{}")
    assert v["passed"] is True
    assert v["good_overall"] > v["bad_overall"]


def test_validate_judge_fails_when_judge_is_blind():
    # A judge that gives everything the same score cannot tell good from bad.
    flat = lambda p, *, max_tokens, model=None: '{"specificity":3,"actionability":3,"grounding":3}'
    v = judge.validate_judge(flat, repair_fn=lambda r: "{}")
    assert v["passed"] is False
