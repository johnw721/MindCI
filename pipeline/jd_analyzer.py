import json
import os
import re

from config import JD_REPORTS_DIR, MAX_TOKENS_ANALYSIS, MAX_TOKENS_BATCH
from pipeline._client import call_with_retry
from pipeline.calibration import effective_confidence


def run_gap_analysis(jd_text, knowledge_base, resume_claims=None):
    """Single-JD readiness analysis.

    When `resume_claims` (dict with `skills`/`projects`/`companies`) is provided,
    the prompt includes the resume context and the response gains three
    additional buckets (`strengths_to_lead_with`, `exposures`, `hidden_assets`)
    that classify each domain by where it appears across JD ⨯ Resume ⨯ KB.
    Falls back to the original schema when `resume_claims` is None.
    """
    kb_summary = [{
        "domain": e.get("topic") or e.get("concept") or e.get("tool") or e.get("error", "unknown"),
        "type": e.get("type"),
        "confidence": effective_confidence(e) if (e.get("auto_confidence") or e.get("confidence")) else "Unknown",
        "difficulty": e.get("difficulty", "Unknown"),
    } for e in knowledge_base]

    resume_block = ""
    extra_schema = ""
    if resume_claims:
        resume_block = (
            f"\nCANDIDATE RESUME CLAIMS (what they market themselves on):\n"
            f"{json.dumps(resume_claims, indent=2)}\n"
        )
        extra_schema = """,
  "strengths_to_lead_with": [{"domain": "...", "reason": "why this is a confident interview asset"}],
  "exposures":              [{"domain": "...", "urgency": "High|Medium", "study_action": "what to study before the interview"}],
  "hidden_assets":          [{"domain": "...", "resume_action": "how to add this to the resume to surface it"}]"""

    bucketing_rules = ""
    if resume_claims:
        bucketing_rules = """

CLASSIFY each JD-relevant domain into exactly ONE of these buckets:
- "strengths_to_lead_with" — domain appears in BOTH the resume claims AND the KB. Highest interview leverage; lead with these.
- "exposures"              — domain is on the resume claims AND the JD, but NOT in the KB. The candidate is exposed: they've claimed it, the interviewer will probe it, they have no notes to back it up. Mark urgency=High.
- "hidden_assets"          — domain is in the KB AND on the JD, but NOT on the resume. The candidate knows it but hasn't claimed it. Add it to the resume.
- "priority_gaps"          — domain is on the JD but NOT on the resume AND NOT in the KB. True study target.

Each domain belongs to exactly one bucket. Don't double-count."""

    prompt = f"""You are analyzing a job description against a candidate's technical knowledge base{" and resume claims" if resume_claims else ""}.

CANDIDATE KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}
{resume_block}
JOB DESCRIPTION:
{jd_text}
{bucketing_rules}

Return ONLY a JSON object, no markdown, no extra text:
{{
  "role_title": "extracted role title",
  "overall_readiness": "Ready|Partial|Not Ready",
  "readiness_score": <0-100>,
  "matched_skills": [{{"domain": "...", "candidate_confidence": "High|Medium|Low|None", "status": "covered|partial|gap"}}],
  "priority_gaps": [{{"domain": "...", "urgency": "High|Medium", "action": "one-line recommendation"}}],
  "strengths": ["..."],
  "summary": "2 sentence readiness summary"{extra_schema}
}}"""

    _text = call_with_retry(prompt, max_tokens=MAX_TOKENS_ANALYSIS)
    raw = _text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# Batch JD analysis logic

def run_batch_analysis(jd_texts, knowledge_base, resume_claims=None):
    """Multi-JD batch analysis. When `resume_claims` is provided, the aggregate
    gains a `cross_jd_exposures` field — domains that recur as gaps AND show up
    on the resume claims. Those are the highest-priority study targets across
    your active job search."""
    kb_summary = [{
        "domain": e.get("topic") or e.get("concept") or e.get("tool") or e.get("error", "unknown"),
        "type": e.get("type"),
        "confidence": effective_confidence(e) if (e.get("auto_confidence") or e.get("confidence")) else "Unknown",
        "difficulty": e.get("difficulty", "Unknown"),
    } for e in knowledge_base]

    jd_block = ""
    for i, jd in enumerate(jd_texts):
        jd_block += f"\n--- JD {i+1} ---\n{jd.strip()}\n"

    resume_block = ""
    extra_aggregate = ""
    if resume_claims:
        resume_block = (
            f"\nCANDIDATE RESUME CLAIMS (what they market themselves on):\n"
            f"{json.dumps(resume_claims, indent=2)}\n"
        )
        extra_aggregate = """,
    "cross_jd_exposures": [{"domain": "...", "appears_in": 0, "reason": "on resume + on JD + missing from KB — these are the highest-priority study targets across your active search"}]"""

    prompt = f"""You are analyzing multiple job descriptions against a candidate's technical knowledge base{" and resume claims" if resume_claims else ""}.

CANDIDATE KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}
{resume_block}
JOB DESCRIPTIONS:
{jd_block}

Return ONLY a JSON object, no markdown, no extra text:
{{
  "individual_results": [
    {{
      "jd_number": 1,
      "role_title": "...",
      "readiness_score": 0,
      "overall_readiness": "Ready|Partial|Not Ready",
      "top_gaps": ["gap1", "gap2", "gap3"],
      "top_strengths": ["strength1", "strength2"]
    }}
  ],
  "aggregate": {{
    "most_common_gaps": [{{"skill": "...", "appears_in": 0, "urgency": "High|Medium"}}],
    "consistent_strengths": ["skill1", "skill2"],
    "avg_readiness_score": 0,
    "best_fit_role": "role title from above",
    "summary": "2-3 sentence summary of overall market fit and biggest pattern across all JDs"{extra_aggregate}
  }}
}}

most_common_gaps: skills that appear as gaps in 2 or more JDs, sorted by frequency
consistent_strengths: skills covered across most JDs
Limit most_common_gaps to top 8."""

    _text = call_with_retry(prompt, max_tokens=MAX_TOKENS_BATCH)
    raw = _text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def parse_jds(text):
    parts = re.split(r"(?m)^---+\s*$", text.strip())
    jds = [p.strip() for p in parts if len(p.strip()) > 100]
    return jds if len(jds) > 1 else [text.strip()]


def save_jd_report(report, prefix="single"):
    """Save individual JD report to jd_reports/ for frequency aggregation."""
    import os
    from datetime import datetime
    os.makedirs(JD_REPORTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(JD_REPORTS_DIR, f"{prefix}_{timestamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return path


def trigger_aggregation():
    """Run aggregation script after saving a new report."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from aggregate_jd_frequencies import run_aggregation
        result, count = run_aggregation()
        return count
    except Exception:
        return 0
