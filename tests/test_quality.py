"""
Tests for pipeline/quality.py — the rule-based note quality scoring and
entry-type detection. No network.
"""

from pipeline.quality import (
    check_note_quality,
    score_kb_entry,
    _detect_entry_type,
)


def test_thin_note_scores_low_and_flags_word_count():
    result = check_note_quality("thin.txt", "broke. fixed it.")
    assert result["score"] <= 3
    assert any("Too short" in issue for issue in result["issues"])


def test_rich_note_with_all_signals_scores_high():
    text = (
        "Investigated a Lambda cold-start failure caused by a circular import "
        "between our shared logger module and the API client. Symptoms: worked "
        "locally, failed only on cold start in production. Root cause: logger "
        "imported client at module scope, client imported logger. Fix: lazy "
        "import inside the handler function. Lesson: avoid module-level imports "
        "of internal collaborators in Lambda handlers. "
        "Confidence: Medium. Difficulty: Hard."
    )
    result = check_note_quality("rich.txt", text)
    assert result["score"] >= 8
    assert result["word_count"] >= 50


def test_score_kb_entry_rewards_detail_in_project_fields():
    entry = {
        "type": "project",
        "error": "Cold start failure on Lambda after deploy of shared module",
        "root_cause": "Circular import between logger and API client modules",
        "fix": "Lazy-import the client inside the handler instead of module scope",
        "concept": "Python module-import semantics in Lambda runtimes",
        "confidence": "Medium",
        "difficulty": "Hard",
    }
    result = score_kb_entry(entry)
    assert result["score"] >= 8
    assert result["type"] == "project"


def test_detect_entry_type_routes_by_keyword():
    assert _detect_entry_type("we hit a stack trace and broke prod") == "project"
    assert _detect_entry_type("studying for the SAA-C03 exam, domain 2") == "certification"
    assert _detect_entry_type("playing with vector databases") == "exploration"
