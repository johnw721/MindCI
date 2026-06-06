"""LLM-as-judge: score the *usefulness of the explanations* in a gap-analysis
result, which precision/recall can't capture.

Precision/recall tell you whether the right gaps were found. They say nothing
about whether the one-line `action` for each gap ("what to study") and the
`summary` are specific, actionable, and tied to the actual JD. Those qualities
are what make the tool worth reading, so we score them with a separate model
call against a fixed rubric.

Rubric (each 1-5, higher is better):
  specificity   -- concrete (names tools/skills/steps) vs vague boilerplate
  actionability -- could the candidate act on it today
  grounding     -- recommendations tie to gaps actually present in THIS JD

VALIDATING THE JUDGE (the hard part of LLM-as-judge):
  A judge you don't validate is just another unverified model. ``validate_judge``
  runs a built-in calibration check -- it judges a deliberately strong
  explanation and a deliberately useless one and asserts the judge ranks the
  good one higher. It's a smoke test, not proof. The fuller plan, documented in
  evals/README.md: hand-score ~15 explanations, measure judge-vs-human rank
  correlation (Spearman) and exact-agreement rate, and use a *different* model
  family for the judge than the generator to limit self-preference bias.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from evals import metrics

_RUBRIC_KEYS = ("specificity", "actionability", "grounding")


def build_judge_prompt(jd_text: str, analysis: object) -> str:
    """Construct the judge prompt. Sends the JD plus the explanation-bearing
    fields of the analysis (summary + each gap's action)."""
    if isinstance(analysis, dict):
        summary = analysis.get("summary", "")
        gaps = analysis.get("priority_gaps", [])
    else:
        summary, gaps = "", []
    gap_lines = []
    if isinstance(gaps, list):
        for g in gaps:
            if isinstance(g, dict):
                gap_lines.append(f"- {g.get('domain', '?')}: {g.get('action', '')}")
            elif isinstance(g, str):
                gap_lines.append(f"- {g}")
    gaps_block = "\n".join(gap_lines) or "(none)"

    return f"""You are grading the USEFULNESS of a career tool's gap-analysis explanations,
not its accuracy. Score only how helpful the wording is to a candidate.

JOB DESCRIPTION:
{jd_text}

TOOL OUTPUT TO GRADE:
summary: {summary}
priority gaps and their recommended actions:
{gaps_block}

Score each criterion from 1 (poor) to 5 (excellent):
- specificity: names concrete tools/skills/steps rather than vague boilerplate
- actionability: the candidate could act on it today
- grounding: the recommendations tie to skills actually in THIS job description

Return ONLY a JSON object, no markdown, no extra text:
{{
  "specificity": <1-5>,
  "actionability": <1-5>,
  "grounding": <1-5>,
  "justification": "one sentence"
}}"""


def _coerce_score(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(1.0, min(5.0, f))


def judge_explanation(
    jd_text: str,
    analysis: object,
    caller: Callable[..., str],
    model: Optional[str] = None,
    repair_fn: Optional[Callable[[str], str]] = None,
    max_tokens: int = 512,
) -> dict:
    """Run one judge call and return rubric scores + overall (mean of the three).

    ``caller`` and ``repair_fn`` are injected so this is testable offline and so
    the judge model can differ from the generator model.
    """
    prompt = build_judge_prompt(jd_text, analysis)
    try:
        raw = caller(prompt, max_tokens=max_tokens, model=model)
    except Exception as e:
        return {"parse_status": "transport_error", "error": f"{type(e).__name__}: {e}",
                "overall": None, **{k: None for k in _RUBRIC_KEYS}}

    status, parsed = metrics.classify_parse(raw, repair_fn=repair_fn)
    if status == metrics.PARSE_FAILED or not isinstance(parsed, dict):
        return {"parse_status": status, "overall": None, **{k: None for k in _RUBRIC_KEYS}}

    scores = {k: _coerce_score(parsed.get(k)) for k in _RUBRIC_KEYS}
    present = [v for v in scores.values() if v is not None]
    overall = round(sum(present) / len(present), 2) if present else None
    return {
        "parse_status": status,
        "overall": overall,
        "justification": parsed.get("justification", ""),
        **scores,
    }


# --- Fixtures for judge self-validation -------------------------------------

_GOOD_JD = ("Platform Engineer. Required: Kubernetes, Helm, ArgoCD, Terraform, "
            "Prometheus for monitoring.")
_GOOD_ANALYSIS = {
    "summary": "Strong on IaC; the gap is the Kubernetes delivery and monitoring stack.",
    "priority_gaps": [
        {"domain": "Helm", "action": "Package a sample app as a Helm chart with values overrides for staging/prod."},
        {"domain": "ArgoCD", "action": "Set up ArgoCD against a Git repo and demo an automated sync + rollback."},
        {"domain": "Prometheus", "action": "Instrument a service with a /metrics endpoint and add two alerting rules."},
    ],
}
_BAD_ANALYSIS = {
    "summary": "You have some gaps. Study more to improve your readiness for this role.",
    "priority_gaps": [
        {"domain": "Helm", "action": "Learn it."},
        {"domain": "ArgoCD", "action": "Get better at this."},
        {"domain": "Prometheus", "action": "Important, study it."},
    ],
}


def validate_judge(
    caller: Callable[..., str],
    model: Optional[str] = None,
    repair_fn: Optional[Callable[[str], str]] = None,
) -> dict:
    """Calibration smoke test: the judge must rank a strong explanation above a
    useless one. Returns both overalls and whether the ordering held."""
    good = judge_explanation(_GOOD_JD, _GOOD_ANALYSIS, caller, model, repair_fn)
    bad = judge_explanation(_GOOD_JD, _BAD_ANALYSIS, caller, model, repair_fn)
    passed = (
        good["overall"] is not None
        and bad["overall"] is not None
        and good["overall"] > bad["overall"]
    )
    return {
        "good_overall": good["overall"],
        "bad_overall": bad["overall"],
        "passed": passed,
    }
