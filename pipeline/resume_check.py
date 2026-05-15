"""
Resume → KB coverage check.

Compares the skills, projects, and companies a user claims on their resume
against the entries in `data/structured.json`. Surfaces gaps where the
resume asserts competence but the KB has nothing backing it — the same gap
an interviewer would expose by asking "tell me more about that."

Flow:
  upload resume text → parse_resume_to_claims (one LLM call, cached)
       → save_resume_claims (data/resume_claims.json)
       → compute_coverage(claims, kb) — pure Python, no LLM
       → render covered / missing lists in the dashboard

Re-running coverage after every Convert pulls in newly-added notes for free
because the claims file is persistent and the comparison is local.
"""

from __future__ import annotations

import json
from pathlib import Path

from config import DATA_DIR, MAX_TOKENS_ANALYSIS, MODEL_FAST
from pipeline._client import call_with_retry

CLAIMS_PATH = Path(DATA_DIR) / "resume_claims.json"

PARSE_PROMPT = """You are extracting verifiable claims from a Cloud Engineer's resume. \
Return ONLY a JSON object with three arrays — no markdown, no preamble.

RESUME:
{resume_text}

Extract:
- "skills": specific technical skills the resume explicitly claims (e.g., "AWS Lambda", \
"Terraform", "Kubernetes"). Be conservative — use the most specific name available, \
don't infer skills from project descriptions.
- "projects": named projects, products, or systems the candidate built or contributed to.
- "companies": company or organization names where the candidate worked.

Return ONLY this JSON shape, no fences:
{{
  "skills":    ["...", "..."],
  "projects":  ["...", "..."],
  "companies": ["...", "..."]
}}

Limit each array to the 15 most prominent items."""


def _strip_fences(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    return clean.strip()


def parse_resume_to_claims(resume_text: str) -> dict:
    """One LLM call that turns resume text into a `{skills, projects, companies}` dict.
    Uses the fast tier — this is structured extraction, not reasoning."""
    prompt = PARSE_PROMPT.format(resume_text=resume_text)
    raw = call_with_retry(prompt, max_tokens=MAX_TOKENS_ANALYSIS, model=MODEL_FAST)
    return json.loads(_strip_fences(raw))


def save_resume_claims(claims: dict) -> Path:
    CLAIMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLAIMS_PATH.write_text(json.dumps(claims, indent=2), encoding="utf-8")
    return CLAIMS_PATH


def load_resume_claims() -> dict | None:
    if not CLAIMS_PATH.exists():
        return None
    try:
        return json.loads(CLAIMS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _kb_candidates(entry: dict) -> set[str]:
    """All lowercase strings that could plausibly match a claim for this KB entry."""
    fields = [
        entry.get("topic"), entry.get("concept"), entry.get("tool"),
        entry.get("error"), entry.get("category"), entry.get("description"),
        entry.get("key_points"),
    ]
    return {str(f).lower() for f in fields if f}


_MIN_TOKEN_LEN = 4  # tokens shorter than this are too noisy to match on alone


def _claim_matches_entry(claim: str, entry_blob: set[str]) -> bool:
    """Case-insensitive match in three tiers, from tightest to loosest:

      1) The whole claim is a substring of a field (or vice versa) — catches
         'Lambda' ↔ 'AWS Lambda' style matches.
      2) Any token of the claim ≥4 chars long is a substring of a field —
         catches 'AWS Lambda' ↔ 'Lambda cold start' via the shared token.

    Tokens 1–3 chars (e.g. 'AWS', 'CI') are excluded from tier 2 because
    they false-match aggressively across unrelated entries.
    """
    c = claim.lower().strip()
    if not c:
        return False
    for field in entry_blob:
        if c in field or field in c:
            return True
    tokens = [t for t in c.replace("/", " ").replace("-", " ").split()
              if len(t) >= _MIN_TOKEN_LEN]
    for tok in tokens:
        for field in entry_blob:
            if tok in field:
                return True
    return False


def compute_coverage(claims: dict, kb: list[dict]) -> dict:
    """Return per-bucket coverage. Pure Python — no LLM.

    Result shape:
      {
        "skills":    {"covered": [{"claim":..., "matches":[label,...]}], "missing": [...]},
        "projects":  {...},
        "companies": {...},
        "totals":    {"claims": N, "covered": M, "pct": int},
      }
    """
    kb_blobs = [(entry, _kb_candidates(entry)) for entry in kb]

    def _bucket(items: list[str]) -> dict:
        covered, missing = [], []
        for claim in items:
            matches = [
                (e.get("topic") or e.get("concept") or e.get("tool")
                 or e.get("error") or "—")
                for (e, blob) in kb_blobs if _claim_matches_entry(claim, blob)
            ]
            row = {"claim": claim, "matches": matches}
            (covered if matches else missing).append(row)
        return {"covered": covered, "missing": missing}

    skills    = _bucket(claims.get("skills",    []))
    projects  = _bucket(claims.get("projects",  []))
    companies = _bucket(claims.get("companies", []))

    total_claims  = sum(len(b["covered"]) + len(b["missing"]) for b in (skills, projects, companies))
    total_covered = sum(len(b["covered"]) for b in (skills, projects, companies))
    pct = int(100 * total_covered / total_claims) if total_claims else 0

    return {
        "skills":    skills,
        "projects":  projects,
        "companies": companies,
        "totals":    {"claims": total_claims, "covered": total_covered, "pct": pct},
    }
