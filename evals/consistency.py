"""Consistency / flake-rate: run each case N times and measure how much the
gap-analysis output wanders between identical inputs.

Why this matters: the gap-analysis call runs at the model's default temperature,
so two runs of the same JD can return different gaps. A tool that tells you to
study Helm on Monday and Istio on Tuesday for the same job is not trustworthy.
This quantifies that wobble so a prompt or temperature change can be judged on
whether it makes the output more stable, not just more accurate on one run.

Metrics per case:
  * gap-set stability  -- mean pairwise Jaccard over the N runs' gap sets
                          (1.0 = identical gaps every run). Surface variants are
                          folded together by canonical key (significant-token
                          set), so "EKS" and "Amazon EKS" count as the same gap.
  * score spread       -- stdev of readiness_score across runs.
  * parse stability    -- are all N parse outcomes the same?

The response cache is disabled so every repeat is a genuine fresh generation.
"""

from __future__ import annotations

import os
import statistics
import sys
from itertools import combinations
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals import metrics
from evals.cases import load_golden_cases, load_kb_snapshot


def canonical_key(domain: str) -> frozenset:
    """Fold surface variants of a skill onto one key: its significant-token set.
    'EKS', 'Amazon EKS', 'EKS / Kubernetes' all share {eks}. Falls back to the
    normalized string when a domain has no significant tokens."""
    sig = metrics._significant(metrics._tokens(domain))
    return frozenset(sig) if sig else frozenset({metrics._normalize(domain)})


def _gap_keyset(parsed: object) -> set:
    return {canonical_key(d) for d in metrics.extract_domains(parsed, "priority_gaps")}


def _mean_pairwise_jaccard(sets: list[set]) -> float:
    """1.0 when every run produced the same gap set. Two empty sets are treated
    as identical (Jaccard 1.0)."""
    if len(sets) < 2:
        return 1.0
    sims = []
    for a, b in combinations(sets, 2):
        if not a and not b:
            sims.append(1.0)
        else:
            sims.append(len(a & b) / len(a | b))
    return sum(sims) / len(sims)


def run_consistency(
    n: int = 3,
    model: Optional[str] = None,
    case_ids: Optional[list[str]] = None,
    limit: Optional[int] = None,
    caller: Optional[Callable[..., str]] = None,
    repair_fn: Optional[Callable[[str], str]] = None,
    disable_cache: bool = True,
) -> dict:
    """Run each selected case ``n`` times and report stability metrics."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    if disable_cache:
        os.environ["MINDCI_CACHE_DISABLE"] = "1"

    from config import MAX_TOKENS_ANALYSIS, MODEL
    from pipeline.jd_analyzer import build_gap_analysis_prompt

    resolved_model = model or MODEL
    if caller is None:
        from pipeline._client import call_with_retry as caller  # type: ignore
    if repair_fn is None:
        from pipeline.convert import _repair_json as repair_fn  # type: ignore

    kb = load_kb_snapshot()
    cases = load_golden_cases()
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]
    if limit:
        cases = cases[:limit]
    if not cases:
        raise ValueError("No cases selected to run.")

    case_results = []
    for case in cases:
        prompt = build_gap_analysis_prompt(case.jd_text, kb)
        gap_sets: list[set] = []
        scores: list[float] = []
        statuses: list[str] = []
        errors = 0
        for _ in range(n):
            try:
                raw = caller(prompt, max_tokens=MAX_TOKENS_ANALYSIS, model=resolved_model)
                status, parsed = metrics.classify_parse(raw, repair_fn=repair_fn)
            except Exception:
                errors += 1
                continue
            statuses.append(status)
            gap_sets.append(_gap_keyset(parsed))
            if isinstance(parsed, dict) and isinstance(parsed.get("readiness_score"), (int, float)):
                scores.append(float(parsed["readiness_score"]))

        stability = _mean_pairwise_jaccard(gap_sets)
        score_stdev = statistics.pstdev(scores) if len(scores) >= 2 else 0.0
        parse_stable = len(set(statuses)) <= 1
        case_results.append({
            "id": case.id,
            "runs": len(gap_sets),
            "errors": errors,
            "gap_stability": round(stability, 4),
            "score_stdev": round(score_stdev, 2),
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
            "parse_stable": parse_stable,
            "parse_statuses": statuses,
        })

    scored = [c for c in case_results if c["runs"] >= 1]
    stabilities = [c["gap_stability"] for c in scored]
    perfectly_stable = sum(1 for c in scored if c["gap_stability"] == 1.0)
    return {
        "model": resolved_model,
        "repeats": n,
        "n_cases": len(case_results),
        "mean_gap_stability": round(sum(stabilities) / len(stabilities), 4) if stabilities else 0.0,
        "perfectly_stable_cases": perfectly_stable,
        "mean_score_stdev": round(sum(c["score_stdev"] for c in scored) / len(scored), 2) if scored else 0.0,
        "cases": case_results,
    }
