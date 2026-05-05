import json
import os
from anthropic import Anthropic
client = Anthropic()

import re

def run_gap_analysis(jd_text, knowledge_base):
    kb_summary = [{
        "domain": e.get("topic") or e.get("concept") or e.get("tool") or e.get("error", "unknown"),
        "type": e.get("type"),
        "confidence": e.get("confidence", "Unknown"),
        "difficulty": e.get("difficulty", "Unknown"),
    } for e in knowledge_base]

    prompt = f"""You are analyzing a job description against a candidate's technical knowledge base.

CANDIDATE KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}

JOB DESCRIPTION:
{jd_text}

Return ONLY a JSON object, no markdown, no extra text:
{{
  "role_title": "extracted role title",
  "overall_readiness": "Ready|Partial|Not Ready",
  "readiness_score": <0-100>,
  "matched_skills": [{{"domain": "...", "candidate_confidence": "High|Medium|Low|None", "status": "covered|partial|gap"}}],
  "priority_gaps": [{{"domain": "...", "urgency": "High|Medium", "action": "one-line recommendation"}}],
  "strengths": ["..."],
  "summary": "2 sentence readiness summary"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# Batch JD analysis logic

def run_batch_analysis(jd_texts, knowledge_base):
    kb_summary = [{
        "domain": e.get("topic") or e.get("concept") or e.get("tool") or e.get("error", "unknown"),
        "type": e.get("type"),
        "confidence": e.get("confidence", "Unknown"),
        "difficulty": e.get("difficulty", "Unknown"),
    } for e in knowledge_base]

    jd_block = ""
    for i, jd in enumerate(jd_texts):
        jd_block += f"\n--- JD {i+1} ---\n{jd.strip()}\n"

    prompt = f"""You are analyzing multiple job descriptions against a candidate's technical knowledge base.

CANDIDATE KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}

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
    "summary": "2-3 sentence summary of overall market fit and biggest pattern across all JDs"
  }}
}}

most_common_gaps: skills that appear as gaps in 2 or more JDs, sorted by frequency
consistent_strengths: skills covered across most JDs
Limit most_common_gaps to top 8."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def parse_jds(text):
    import re
    parts = re.split(r"(?m)^---+\s*$", text.strip())
    jds = [p.strip() for p in parts if len(p.strip()) > 100]
    return jds if len(jds) > 1 else [text.strip()]


JD_REPORTS_DIR = "jd_reports"


def save_jd_report(report, prefix="single"):
    """Save individual JD report to jd_reports/ for frequency aggregation."""
    from datetime import datetime
    import os
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