"""Scoring primitives for the eval harness: parse classification + set-based
precision/recall over domain strings.

DESIGN NOTE -- why a custom matcher instead of reusing the app's matcher.
``pipeline.resume_check._claim_matches_entry`` is deliberately loose (raw
substring + short-token matching) because in the app a false match is cheap.
For *scoring* that looseness is a liability: it reports 'RDS' as covered
because the substring "rds" appears inside "gua**rds**", and 'Service Mesh' as
covered because of "**service** worker". Baking those false positives into the
score would make the eval lie. So scoring uses ``domain_match`` below:
word-boundary tokenization (no mid-word substring hits) plus token-subset
matching so surface variants still line up ("EKS" == "Amazon EKS",
"CI/CD" == "CI/CD pipelines"). That the app matcher and the eval matcher
disagree is itself a finding worth reporting.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

# Tokens that carry no discriminating signal for skill matching.
_STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "for", "with", "to", "in", "on",
    "experience", "using", "use", "strong", "deep", "via", "plus", "etc",
    "skills", "knowledge", "hands", "hands-on",
}
# Vendor/platform words too generic to anchor a match on their own.
_VENDOR = {"aws", "amazon", "gcp", "google", "azure", "hashicorp", "cloud"}


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def _tokens(s: str) -> set[str]:
    """Word-boundary tokens, length >= 2, stopwords removed. Word-boundary
    splitting is what prevents the 'rds' in 'guards' class of false match."""
    return {t for t in _normalize(s).split() if len(t) >= 2 and t not in _STOPWORDS}


def _significant(tokens: set[str]) -> set[str]:
    return tokens - _VENDOR


def domain_match(a: str, b: str) -> bool:
    """True if two domain strings refer to the same skill.

    Match when normalized strings are equal, or when the significant tokens of
    one are a (non-empty) subset of the tokens of the other. The subset rule
    absorbs surface variation ("EKS" within "Amazon EKS / Kubernetes") while
    the word-boundary tokenization blocks mid-word substring coincidences.
    """
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = _tokens(a), _tokens(b)
    sa, sb = _significant(ta), _significant(tb)
    if sa and sa <= tb:
        return True
    if sb and sb <= ta:
        return True
    return False


def score_set(predicted: list[str], expected: list[str]) -> dict:
    """Set-based precision/recall for one bucket (gaps or matches).

    A predicted item is a true positive if it matches ANY expected item; an
    expected item is recalled if ANY predicted item matches it. (One predicted
    string like "Prometheus/Grafana" can legitimately satisfy two expected
    labels.) Counts:

      matched_predicted -- predicted items that hit >= 1 expected  (precision num)
      matched_expected  -- expected items hit by >= 1 predicted     (recall num)

    Empty-set conventions: precision = 1.0 when nothing was predicted; recall =
    1.0 when nothing was expected. These make the "no gaps expected, none
    predicted" edge case score perfectly instead of dividing by zero.
    """
    matched_predicted = [p for p in predicted if any(domain_match(p, e) for e in expected)]
    matched_expected = [e for e in expected if any(domain_match(p, e) for p in predicted)]

    n_pred, n_exp = len(predicted), len(expected)
    precision = (len(matched_predicted) / n_pred) if n_pred else 1.0
    recall = (len(matched_expected) / n_exp) if n_exp else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    false_positives = [p for p in predicted if p not in matched_predicted]
    false_negatives = [e for e in expected if e not in matched_expected]

    return {
        "predicted": list(predicted),
        "expected": list(expected),
        "tp_predicted": len(matched_predicted),
        "tp_expected": len(matched_expected),
        "fp": len(false_positives),
        "fn": len(false_negatives),
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def micro_average(per_case_scores: list[dict]) -> dict:
    """Pool tp/fp/fn across cases, then compute precision/recall once.

    Micro-averaging weights every individual gap equally (a case with 5 gaps
    counts 5x a case with 1), which is the honest aggregate for 'how good is
    detection overall'. We report it as the headline number.
    """
    sum_tp_pred = sum(s["tp_predicted"] for s in per_case_scores)
    sum_tp_exp = sum(s["tp_expected"] for s in per_case_scores)
    sum_pred = sum(len(s["predicted"]) for s in per_case_scores)
    sum_exp = sum(len(s["expected"]) for s in per_case_scores)

    precision = (sum_tp_pred / sum_pred) if sum_pred else 1.0
    recall = (sum_tp_exp / sum_exp) if sum_exp else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "total_predicted": sum_pred,
        "total_expected": sum_exp,
    }


# ----------------------------------------------------------------------------
# Parse-reliability classification
# ----------------------------------------------------------------------------

PARSE_FIRST_TRY = "first_try"
PARSE_REPAIRED = "repaired"
PARSE_FAILED = "failed"


def _strip_fences(text: str) -> str:
    """Mirror the fence-stripping in jd_analyzer.run_gap_analysis so the eval's
    first-try test is identical to what the app actually does."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def classify_parse(
    raw_text: str,
    repair_fn: Optional[Callable[[str], str]] = None,
) -> tuple[str, Optional[object]]:
    """Classify a raw model response as first_try / repaired / failed.

    The repair tier is injected (``repair_fn``) rather than imported so the
    classifier is pure and unit-testable offline; the live harness wires in
    ``pipeline.convert._repair_json`` (which costs one extra API call). Models
    the repair-prompt fallback the gap-analysis path does NOT currently have in
    production -- so the 'repaired' count quantifies what adding it would buy.
    """
    try:
        return PARSE_FIRST_TRY, json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        pass

    if repair_fn is None:
        return PARSE_FAILED, None

    try:
        repaired = _strip_fences(repair_fn(raw_text))
        return PARSE_REPAIRED, json.loads(repaired)
    except Exception:
        return PARSE_FAILED, None


def extract_domains(obj: object, key: str) -> list[str]:
    """Pull the 'domain' string from each item under ``key`` in a parsed
    gap-analysis object. Tolerant of items that are bare strings or dicts using
    'domain'/'skill'/'name', and of a missing/non-list key."""
    if not isinstance(obj, dict):
        return []
    items = obj.get(key) or []
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            val = it.get("domain") or it.get("skill") or it.get("name")
            if val:
                out.append(str(val))
    return out
