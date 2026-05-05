"""
Tests for validation.py — the Pydantic gate that protects structured.json.
"""

from validation import validate_entries


def test_valid_project_entry_round_trips():
    entries = [{
        "type": "project",
        "error": "Lambda cold start failed",
        "root_cause": "Circular import between logger and client modules",
        "fix": "Lazy import inside the handler",
        "concept": "Python import resolution",
        "confidence": "Medium",
        "difficulty": "Hard",
    }]
    valid, invalid, warnings = validate_entries(entries)

    assert len(valid) == 1
    assert not invalid
    assert valid[0]["confidence"] == "Medium"
    assert valid[0]["difficulty"] == "Hard"


def test_unknown_type_is_rejected():
    entries = [{"type": "musings", "error": "x", "root_cause": "y", "fix": "z"}]
    valid, invalid, _ = validate_entries(entries)

    assert not valid
    assert len(invalid) == 1
    assert "Unknown type" in invalid[0]["errors"][0]


def test_confidence_and_difficulty_are_normalized():
    """Lowercase strings and numeric-as-string values map onto canonical labels."""
    entries = [{
        "type": "project",
        "error": "x",
        "root_cause": "y",
        "fix": "z",
        "confidence": "medium",   # lowercase
        "difficulty": "3",        # numeric-as-string → Hard
    }]
    valid, invalid, _ = validate_entries(entries)

    assert not invalid
    assert valid[0]["confidence"] == "Medium"
    assert valid[0]["difficulty"] == "Hard"


def test_missing_required_field_routes_to_invalid():
    """A project entry missing root_cause should fail validation, not silently default."""
    entries = [{
        "type": "project",
        "error": "Outage",
        # root_cause intentionally absent
        "fix": "Restarted the service",
    }]
    valid, invalid, _ = validate_entries(entries)

    assert not valid
    assert len(invalid) == 1
    joined_errors = " ".join(invalid[0]["errors"]).lower()
    assert "root_cause" in joined_errors


def test_missing_optional_confidence_emits_warning_not_error():
    """Confidence is optional but absence should produce a soft warning."""
    entries = [{
        "type": "exploration",
        "tool": "Karpenter",
        "description": "Open-source Kubernetes node autoscaler from AWS.",
    }]
    valid, invalid, warnings = validate_entries(entries)

    assert len(valid) == 1
    assert not invalid
    assert valid[0]["confidence"] == "Low"  # defaulted
    assert warnings and "confidence defaulted to Low" in warnings[0]["warnings"]
