"""Loaders for the golden test set and the shared KB snapshot.

The golden cases all run against ONE frozen knowledge base
(``kb_snapshot.json``, a copy of ``data/structured.json``) so a label like
"EKS is a gap" is a fact about a fixed KB and stays true across runs. Snapshot
it again only when you deliberately want the eval to track a newer KB.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
KB_SNAPSHOT_PATH = _HERE / "kb_snapshot.json"
GOLDEN_PATH = _HERE / "golden_cases.json"


@dataclass
class GoldenCase:
    """One hand-labeled evaluation case.

    ``expected_gaps`` / ``expected_matches`` are the COMPLETE set of distinct
    skills the JD explicitly demands, partitioned by whether the snapshot KB
    genuinely covers them. Because the lists are complete, a gap the model
    invents counts as a false positive and a real gap it misses counts as a
    false negative.
    """

    id: str
    jd_text: str
    expected_gaps: list[str] = field(default_factory=list)
    expected_matches: list[str] = field(default_factory=list)
    rationale: str = ""
    role_title_hint: str = ""


def load_kb_snapshot(path: Path | None = None) -> list[dict]:
    """Return the frozen knowledge base every golden case is scored against."""
    p = path or KB_SNAPSHOT_PATH
    with open(p, encoding="utf-8") as f:
        kb = json.load(f)
    if not isinstance(kb, list):
        raise ValueError(f"KB snapshot at {p} must be a JSON array of entries.")
    return kb


def load_golden_cases(path: Path | None = None) -> list[GoldenCase]:
    """Parse golden_cases.json into GoldenCase objects, validating shape."""
    p = path or GOLDEN_PATH
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)

    raw_cases = doc.get("cases") if isinstance(doc, dict) else doc
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"No cases found in golden file {p}.")

    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for i, rc in enumerate(raw_cases):
        cid = rc.get("id")
        if not cid:
            raise ValueError(f"Case at index {i} is missing an 'id'.")
        if cid in seen:
            raise ValueError(f"Duplicate case id: {cid!r}.")
        seen.add(cid)
        if not rc.get("jd_text"):
            raise ValueError(f"Case {cid!r} is missing 'jd_text'.")
        cases.append(
            GoldenCase(
                id=cid,
                jd_text=rc["jd_text"],
                expected_gaps=list(rc.get("expected_gaps", [])),
                expected_matches=list(rc.get("expected_matches", [])),
                rationale=rc.get("rationale", ""),
                role_title_hint=rc.get("role_title_hint", ""),
            )
        )
    return cases
