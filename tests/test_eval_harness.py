"""Tests for the gap-analysis evaluation harness (evals/).

These are offline: scoring math, parse classification, golden-set loading, and a
full harness run are exercised with injected stubs so no API call happens. The
conftest already sandboxes env + paths and stubs the Anthropic client.
"""

from __future__ import annotations

import json

import pytest

from evals import metrics
from evals.cases import load_golden_cases, load_kb_snapshot
from evals.harness import _extract_matched_domains, run


# ---------------------------------------------------------------------------
# domain_match -- the core of honest scoring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("EKS", "Amazon EKS"),
    ("CI/CD", "CI/CD pipelines"),
    ("S3", "S3 remote state"),
    ("AWS Lambda", "Lambda"),
    ("Prometheus", "Prometheus/Grafana monitoring"),
    ("Terraform", "terraform"),
])
def test_domain_match_positive(a, b):
    assert metrics.domain_match(a, b)


@pytest.mark.parametrize("a,b", [
    # The exact false positives the loose app matcher produces:
    ("RDS", "Quality Guards"),          # 'rds' is inside 'guards' as a substring
    ("Service Mesh", "service worker"),  # shares only the generic 'service'
    ("Incident response", "AI Response Parsing"),
    ("Helm", "Kubernetes"),
    ("ArgoCD", "Terraform"),
    ("AWS", "AWS Lambda"),               # vendor word alone must not anchor a match
])
def test_domain_match_negative(a, b):
    assert not metrics.domain_match(a, b)


# ---------------------------------------------------------------------------
# score_set -- precision/recall with empty-set conventions
# ---------------------------------------------------------------------------

def test_score_set_perfect():
    s = metrics.score_set(["Helm", "ArgoCD"], ["ArgoCD", "Helm"])
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["f1"] == 1.0
    assert s["fp"] == 0 and s["fn"] == 0


def test_score_set_missed_and_invented():
    # predicted Helm (real) + Jenkins (invented); expected Helm + ArgoCD
    s = metrics.score_set(["Helm", "Jenkins"], ["Helm", "ArgoCD"])
    assert s["precision"] == 0.5   # 1 of 2 predicted is right
    assert s["recall"] == 0.5      # 1 of 2 expected found
    assert s["false_positives"] == ["Jenkins"]
    assert s["false_negatives"] == ["ArgoCD"]


def test_score_set_empty_expected_and_predicted():
    s = metrics.score_set([], [])
    assert s["precision"] == 1.0 and s["recall"] == 1.0


def test_score_set_invented_gap_when_none_expected():
    # Model invents a gap though the JD has none -> precision 0, recall defined 1.
    s = metrics.score_set(["EKS"], [])
    assert s["precision"] == 0.0
    assert s["recall"] == 1.0
    assert s["false_positives"] == ["EKS"]


def test_score_set_missed_gap_when_none_predicted():
    s = metrics.score_set([], ["EKS"])
    assert s["precision"] == 1.0   # nothing predicted, nothing wrong
    assert s["recall"] == 0.0
    assert s["false_negatives"] == ["EKS"]


def test_one_prediction_can_satisfy_two_labels():
    s = metrics.score_set(["Prometheus/Grafana"], ["Prometheus", "Grafana"])
    assert s["recall"] == 1.0
    assert s["precision"] == 1.0


def test_micro_average_pools_counts():
    a = metrics.score_set(["Helm"], ["Helm", "ArgoCD"])       # tp1 fn1
    b = metrics.score_set(["Vault"], ["Vault"])                # tp1
    micro = metrics.micro_average([a, b])
    assert micro["total_predicted"] == 2
    assert micro["total_expected"] == 3
    assert micro["recall"] == round(2 / 3, 4)
    assert micro["precision"] == 1.0


# ---------------------------------------------------------------------------
# classify_parse -- first_try / repaired / failed
# ---------------------------------------------------------------------------

def test_classify_first_try_plain():
    status, obj = metrics.classify_parse('{"a": 1}')
    assert status == metrics.PARSE_FIRST_TRY and obj == {"a": 1}


def test_classify_first_try_with_code_fence():
    status, obj = metrics.classify_parse('```json\n{"a": 1}\n```')
    assert status == metrics.PARSE_FIRST_TRY and obj == {"a": 1}


def test_classify_repaired_uses_injected_repair():
    status, obj = metrics.classify_parse(
        "{ not json", repair_fn=lambda raw: '{"fixed": true}'
    )
    assert status == metrics.PARSE_REPAIRED and obj == {"fixed": True}


def test_classify_failed_without_repair():
    status, obj = metrics.classify_parse("{ not json", repair_fn=None)
    assert status == metrics.PARSE_FAILED and obj is None


def test_classify_failed_when_repair_also_fails():
    status, obj = metrics.classify_parse(
        "{ not json", repair_fn=lambda raw: "still not json"
    )
    assert status == metrics.PARSE_FAILED and obj is None


# ---------------------------------------------------------------------------
# domain extraction
# ---------------------------------------------------------------------------

def test_extract_gaps_from_dicts_and_strings():
    obj = {"priority_gaps": [{"domain": "Helm"}, "ArgoCD", {"skill": "Vault"}]}
    assert metrics.extract_domains(obj, "priority_gaps") == ["Helm", "ArgoCD", "Vault"]


def test_extract_matched_respects_status():
    obj = {"matched_skills": [
        {"domain": "Terraform", "status": "covered"},
        {"domain": "EKS", "status": "gap"},        # not a real match -> excluded
        {"domain": "Docker", "status": "partial"},
    ]}
    assert _extract_matched_domains(obj) == ["Terraform", "Docker"]


# ---------------------------------------------------------------------------
# golden set + KB snapshot load
# ---------------------------------------------------------------------------

def test_golden_set_loads_and_is_consistent():
    cases = load_golden_cases()
    assert len(cases) >= 10
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))               # unique ids
    for c in cases:
        assert c.jd_text
        # a skill can't be both a gap and a match in the same case
        assert not (set(c.expected_gaps) & set(c.expected_matches))


def test_kb_snapshot_loads():
    kb = load_kb_snapshot()
    assert isinstance(kb, list) and len(kb) > 0
    assert "type" in kb[0]


# ---------------------------------------------------------------------------
# full harness run, fully offline
# ---------------------------------------------------------------------------

def test_harness_run_offline_end_to_end():
    """Drive run() with a stub caller so the whole pipeline executes without an
    API call, and assert one case scores exactly as designed."""

    def caller(prompt, *, max_tokens, model):
        if "Helm charts" in prompt:  # the platform-k8s-heavy case
            return json.dumps({
                "role_title": "Platform Engineer",
                "readiness_score": 55,
                "overall_readiness": "Partial",
                "matched_skills": [
                    {"domain": "Kubernetes", "status": "covered"},
                    {"domain": "Terraform", "status": "covered"},
                    {"domain": "Docker", "status": "partial"},
                    {"domain": "CI/CD pipelines", "status": "covered"},
                ],
                "priority_gaps": [
                    {"domain": "Helm"}, {"domain": "ArgoCD"},
                    {"domain": "Prometheus"}, {"domain": "Grafana"},
                ],
                "strengths": [], "summary": "",
            })
        return "{ deliberately malformed"

    results = run(
        caller=caller,
        repair_fn=lambda raw: json.dumps({
            "priority_gaps": [], "matched_skills": [],
            "role_title": "x", "readiness_score": 0, "summary": "",
        }),
        case_ids=["platform-k8s-heavy"],
        disable_cache=False,
    )

    assert results["n_cases"] == 1
    case = results["cases"][0]
    assert case["parse_status"] == metrics.PARSE_FIRST_TRY
    assert case["gaps"]["precision"] == 1.0
    assert case["gaps"]["recall"] == 1.0
    assert case["matches"]["recall"] == 1.0
    assert results["parse_counts"]["first_try"] == 1


def test_harness_counts_repaired_responses():
    results = run(
        caller=lambda prompt, *, max_tokens, model: "{ broken",
        repair_fn=lambda raw: json.dumps({"priority_gaps": [], "matched_skills": []}),
        case_ids=["all-match-sanity"],
        disable_cache=False,
    )
    assert results["parse_counts"]["repaired"] == 1


def test_transport_error_not_counted_as_parse_failure():
    def boom(prompt, *, max_tokens, model):
        raise RuntimeError("Unauthorized")

    results = run(
        caller=boom,
        repair_fn=lambda raw: "{}",
        case_ids=["platform-k8s-heavy"],
        disable_cache=False,
    )
    assert results["transport_errors"] == 1
    assert results["n_scored"] == 0
    # A transport failure must NOT inflate the parse "failed" count.
    assert results["parse_counts"]["failed"] == 0
    assert results["cases"][0]["parse_status"] == "transport_error"
    assert results["cases"][0]["error"] is not None
