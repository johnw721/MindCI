"""Run the golden set against the live gap-analysis path and collect metrics.

Each case is scored on two axes:

* parse reliability  -- first_try / repaired / failed, via metrics.classify_parse
* detection quality  -- precision/recall of priority_gaps and matched_skills
  against the golden labels, via metrics.score_set

The harness calls the SAME prompt the app ships (jd_analyzer.build_gap_analysis_prompt)
and the SAME transport (pipeline._client.call_with_retry), so it measures the
real system, not a re-implementation. The response cache is disabled for the
run so every case is a fresh generation -- a cached reply would make a
prompt/model regression invisible.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

# Make the repo root importable when run as `python eval.py` or `python -m`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals import hallucination, metrics
from evals.cases import GoldenCase, load_golden_cases, load_kb_snapshot

_MATCH_STATUSES = {"covered", "partial"}  # statuses that count as "candidate has it"
TRANSPORT_ERROR = "transport_error"  # network/auth failure, not a parse outcome


def _extract_matched_domains(obj: object) -> list[str]:
    """Domains from matched_skills the model marks as covered/partial. Items it
    marks status='gap' are not matches and are excluded."""
    if not isinstance(obj, dict):
        return []
    items = obj.get("matched_skills") or []
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for it in items:
        if isinstance(it, dict):
            status = str(it.get("status", "")).lower()
            domain = it.get("domain") or it.get("skill") or it.get("name")
            if domain and (status in _MATCH_STATUSES or status == ""):
                out.append(str(domain))
        elif isinstance(it, str):
            out.append(it)
    return out


def _score_case(case: GoldenCase, parsed: Optional[object]) -> dict:
    gaps_pred = metrics.extract_domains(parsed, "priority_gaps")
    matches_pred = _extract_matched_domains(parsed)
    return {
        "gaps": metrics.score_set(gaps_pred, case.expected_gaps),
        "matches": metrics.score_set(matches_pred, case.expected_matches),
    }


def run(
    model: Optional[str] = None,
    case_ids: Optional[list[str]] = None,
    limit: Optional[int] = None,
    caller: Optional[Callable[..., str]] = None,
    repair_fn: Optional[Callable[[str], str]] = None,
    disable_cache: bool = True,
    keep_parsed: bool = False,
) -> dict:
    """Execute the eval and return a results dict.

    Args:
        model: model string to evaluate. Defaults to config.MODEL (so the eval
            tracks whatever the app runs). Pass --model on the CLI to override.
        case_ids / limit: optionally restrict which cases run.
        caller: transport function (prompt, *, max_tokens, model) -> text.
            Defaults to pipeline._client.call_with_retry. Injectable for tests.
        repair_fn: raw_text -> repaired_text for the parse repair tier.
            Defaults to pipeline.convert._repair_json. Injectable for tests.
        disable_cache: when True (default) sets MINDCI_CACHE_DISABLE so every
            case is a fresh generation.
    """
    # Env vars are not inherited in this project -- load .env explicitly.
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

    parse_counts = {
        metrics.PARSE_FIRST_TRY: 0,
        metrics.PARSE_REPAIRED: 0,
        metrics.PARSE_FAILED: 0,
    }
    transport_errors = 0
    case_results: list[dict] = []

    for case in cases:
        prompt = build_gap_analysis_prompt(case.jd_text, kb)
        started = time.time()
        error = None
        # Transport failures (network/auth) are NOT a parse outcome -- they are
        # infra, and counting them as parse "failed" would misreport reliability.
        parse_status = TRANSPORT_ERROR
        parsed: Optional[object] = None
        try:
            raw = caller(prompt, max_tokens=MAX_TOKENS_ANALYSIS, model=resolved_model)
            parse_status, parsed = metrics.classify_parse(raw, repair_fn=repair_fn)
        except Exception as e:  # transport failure -- record, keep going
            error = f"{type(e).__name__}: {e}"

        if parse_status == TRANSPORT_ERROR:
            transport_errors += 1
        else:
            parse_counts[parse_status] = parse_counts.get(parse_status, 0) + 1
        scored = _score_case(case, parsed)
        grounding = hallucination.check_grounding(parsed, case.jd_text)

        case_results.append({
            "id": case.id,
            "role_title_hint": case.role_title_hint,
            "parse_status": parse_status,
            "error": error,
            "elapsed_s": round(time.time() - started, 2),
            "role_title": (parsed or {}).get("role_title") if isinstance(parsed, dict) else None,
            "readiness_score": (parsed or {}).get("readiness_score") if isinstance(parsed, dict) else None,
            "gaps": scored["gaps"],
            "matches": scored["matches"],
            "grounding": grounding,
            "analysis": parsed if keep_parsed else None,
            "jd_text": case.jd_text if keep_parsed else None,
        })

    scored_cases = [c for c in case_results if c["parse_status"] != TRANSPORT_ERROR]
    gaps_micro = metrics.micro_average([c["gaps"] for c in scored_cases])
    matches_micro = metrics.micro_average([c["matches"] for c in scored_cases])

    total_asserted = sum(c["grounding"]["asserted"] for c in scored_cases)
    total_hallucinated = sum(c["grounding"]["hallucinated"] for c in scored_cases)
    hallucination_summary = {
        "asserted": total_asserted,
        "hallucinated": total_hallucinated,
        "rate": round(total_hallucinated / total_asserted, 4) if total_asserted else 0.0,
    }

    return {
        "model": resolved_model,
        "n_cases": len(case_results),
        "n_scored": len(scored_cases),
        "transport_errors": transport_errors,
        "kb_entries": len(kb),
        "parse_counts": parse_counts,
        "gaps_micro": gaps_micro,
        "matches_micro": matches_micro,
        "hallucination": hallucination_summary,
        "cases": case_results,
    }
