"""
tests/test_convert_helpers.py

Unit tests for the two pure-Python helpers in pipeline/convert.py:
  - _salvage_partial_json  — extracts complete objects from a truncated array
  - detect_note_sections   — heuristically splits a long note into sections
"""

import pytest

from pipeline.convert import _salvage_partial_json, detect_note_sections


# ══════════════════════════════════════════════════════════════════════════════
# _salvage_partial_json
# ══════════════════════════════════════════════════════════════════════════════

class TestSalvagePartialJson:

    def test_complete_array_returned_intact(self):
        data = '[{"type": "project", "error": "x", "fix": "y"}]'
        result = _salvage_partial_json(data)
        assert len(result) == 1
        assert result[0]["type"] == "project"

    def test_truncated_before_closing_bracket(self):
        # Second object is cut off before its closing brace
        data = '[{"type": "project", "error": "x", "fix": "y"}, {"type": "exploration", "tool": "k8s"'
        result = _salvage_partial_json(data)
        assert len(result) == 1
        assert result[0]["type"] == "project"

    def test_truncated_mid_string_in_second_object(self):
        data = (
            '[{"type": "project", "error": "fix the bug", "fix": "done"}, '
            '{"type": "exploration", "tool": "helm", "description": "unfinished str'
        )
        result = _salvage_partial_json(data)
        assert len(result) == 1
        assert result[0]["fix"] == "done"

    def test_escaped_quotes_inside_string(self):
        # \" inside a value should not confuse the string-boundary tracker
        data = '[{"type": "project", "error": "said \\"hello\\"", "fix": "done"}]'
        result = _salvage_partial_json(data)
        assert len(result) == 1
        assert "hello" in result[0]["error"]

    def test_nested_object_value(self):
        data = '[{"type": "project", "meta": {"key": "val"}, "fix": "done"}]'
        result = _salvage_partial_json(data)
        assert len(result) == 1
        assert result[0]["meta"] == {"key": "val"}

    def test_empty_and_junk_inputs(self):
        assert _salvage_partial_json("") == []
        assert _salvage_partial_json("[") == []
        assert _salvage_partial_json("not json at all") == []
        assert _salvage_partial_json("null") == []

    def test_multiple_complete_objects_one_truncated(self):
        # Three objects; third is truncated — should return first two
        data = '[{"a": 1}, {"b": 2}, {"c": 3'
        result = _salvage_partial_json(data)
        assert len(result) == 2
        assert result[0] == {"a": 1}
        assert result[1] == {"b": 2}


# ══════════════════════════════════════════════════════════════════════════════
# detect_note_sections
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectNoteSections:

    def test_cpm_section_markers(self):
        # Both chunks exceed the default min_chars=150 threshold
        text = (
            "Section A discusses the first major topic in depth, covering background, "
            "motivation, and key technical details needed to understand the concept fully.\n"
            "——SECTION——\n"
            "Section B covers the second major topic with equal depth, providing background, "
            "motivation, and the key technical details that make this concept worth studying."
        )
        sections, strategy = detect_note_sections(text)
        assert len(sections) == 2
        assert "CPM" in strategy

    def test_markdown_headings(self):
        # Each chunk (heading + body) exceeds the default min_chars=150 threshold
        text = (
            "## Topic A\n"
            "Some detail about topic A that is long enough to exceed the 150-character "
            "minimum needed for each section chunk to pass the detection filter here.\n\n"
            "## Topic B\n"
            "Some detail about topic B that is also long enough to exceed the 150-character "
            "minimum needed for the heuristic to accept this particular chunk."
        )
        sections, strategy = detect_note_sections(text)
        assert len(sections) == 2
        assert "markdown" in strategy

    def test_heading_titles_stripped_of_hashes(self):
        text = (
            "## My First Heading\nContent A with enough words.\n\n"
            "### Sub Heading\nContent B with enough words too."
        )
        sections, _ = detect_note_sections(text)
        assert sections[0]["title"] == "My First Heading"
        assert sections[1]["title"] == "Sub Heading"

    def test_triple_blank_lines(self):
        # min_chars=50 so the test focuses on strategy detection, not threshold filtering
        text = (
            "Block one covers the first topic.\n\n\n\n"
            "Block two covers the second topic."
        )
        sections, strategy = detect_note_sections(text, min_chars=0)
        assert len(sections) == 2
        assert "triple" in strategy

    def test_fallback_midpoint_bisect(self):
        # Dense text with no boundaries — should fall back to bisect
        text = ("word " * 200).strip()
        sections, strategy = detect_note_sections(text)
        assert len(sections) == 2
        assert "midpoint" in strategy

    def test_word_counts_are_accurate(self):
        # Use 30/40 words so each chunk comfortably exceeds the 150-char min_chars threshold
        text = (
            "## Section Alpha\n" + "word " * 30 + "\n\n"
            "## Section Beta\n" + "word " * 40
        )
        sections, _ = detect_note_sections(text)
        # word_count is computed on the stripped chunk content (heading line included)
        assert sections[0]["word_count"] == len(("## Section Alpha\n" + "word " * 30).split())
        assert sections[1]["word_count"] == len(("## Section Beta\n" + "word " * 40).split())

    def test_strategy_label_returned_as_string(self):
        text = "## A\nContent A is long enough.\n\n## B\nContent B is long enough."
        sections, strategy = detect_note_sections(text)
        assert isinstance(strategy, str)
        assert len(strategy) > 0
