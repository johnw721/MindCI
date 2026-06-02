"""
tests/test_multifile_queue.py

Unit tests for the multi-file convert-queue logic introduced in app_dashboard.py.

When a user converts and saves a file from a multi-file staged list, the file
should be removed from the queue and the active index should stay in bounds.
When the last file in the queue is converted, the modal should auto-close
(active_modal → None) and all staged-file state should be cleared.

We test the pure state-transition logic in isolation by replicating the same
operations performed by modal_convert() in app_dashboard.py after a successful
"Convert & save".  This avoids the need to spin up a real Streamlit session.
"""

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers that mirror the state-mutation logic inside modal_convert()
# ─────────────────────────────────────────────────────────────────────────────

def _make_state(files: list[dict], idx: int = 0) -> dict:
    """Build a minimal fake session_state dict for multi-file convert tests."""
    return {
        "active_modal": "convert",
        "convert_uploaded_files": list(files),
        "convert_file_idx": idx,
        "convert_preview": {"some": "data"},
        "convert_quality": {"score": 5},
        "convert_enrich_questions": ["q1"],
        "convert_enrich_answers": ["a1"],
        "convert_enrich_rewritten": "rewritten",
        "convert_splits": [{"title": "s", "content": "c", "word_count": 10}],
        "convert_splits_enabled": [True],
        "convert_splits_strategy": "markdown",
    }


def _apply_post_commit(state: dict) -> dict:
    """
    Apply exactly the post-commit state mutations from modal_convert().

    Returns the mutated state dict.  'rerun' is simulated by returning early;
    the caller inspects state afterwards.
    """
    staged = state["convert_uploaded_files"]

    if len(staged) > 1:
        idx = state["convert_file_idx"]
        staged.pop(idx)
        state["convert_uploaded_files"] = staged
        state["convert_file_idx"] = min(idx, len(staged) - 1)
        # Reset per-note state for the next file
        state["convert_preview"] = None
        state["convert_quality"] = None
        state["convert_enrich_questions"] = None
        state["convert_enrich_answers"] = []
        state["convert_enrich_rewritten"] = None
        state["convert_splits"] = None
        state["convert_splits_enabled"] = []
        state["convert_splits_strategy"] = ""
        # (rerun would happen here)

    elif len(staged) == 1:
        # Last file — auto-close modal
        state["active_modal"] = None
        state["convert_uploaded_files"] = []
        state["convert_file_idx"] = 0
        state["convert_preview"] = None
        state["convert_quality"] = None
        state["convert_splits"] = None
        state["convert_splits_enabled"] = []
        state["convert_splits_strategy"] = ""
        # (rerun would happen here)

    return state


_FILE_A = {"name": "a.md", "content": "content A"}
_FILE_B = {"name": "b.md", "content": "content B"}
_FILE_C = {"name": "c.md", "content": "content C"}


# ─────────────────────────────────────────────────────────────────────────────
# Tests: file is removed after a successful commit
# ─────────────────────────────────────────────────────────────────────────────

class TestFileRemovedAfterCommit:

    def test_converts_first_of_two_removes_it(self):
        state = _make_state([_FILE_A, _FILE_B], idx=0)
        state = _apply_post_commit(state)
        assert len(state["convert_uploaded_files"]) == 1
        assert state["convert_uploaded_files"][0]["name"] == "b.md"

    def test_converts_second_of_two_removes_it(self):
        state = _make_state([_FILE_A, _FILE_B], idx=1)
        state = _apply_post_commit(state)
        assert len(state["convert_uploaded_files"]) == 1
        assert state["convert_uploaded_files"][0]["name"] == "a.md"

    def test_converts_middle_of_three_removes_it(self):
        state = _make_state([_FILE_A, _FILE_B, _FILE_C], idx=1)
        state = _apply_post_commit(state)
        remaining = [f["name"] for f in state["convert_uploaded_files"]]
        assert remaining == ["a.md", "c.md"]

    def test_converts_first_of_three_removes_it(self):
        state = _make_state([_FILE_A, _FILE_B, _FILE_C], idx=0)
        state = _apply_post_commit(state)
        remaining = [f["name"] for f in state["convert_uploaded_files"]]
        assert remaining == ["b.md", "c.md"]

    def test_converts_last_of_three_removes_it(self):
        state = _make_state([_FILE_A, _FILE_B, _FILE_C], idx=2)
        state = _apply_post_commit(state)
        remaining = [f["name"] for f in state["convert_uploaded_files"]]
        assert remaining == ["a.md", "b.md"]


# ─────────────────────────────────────────────────────────────────────────────
# Tests: active index stays in bounds after removal
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexAdjustmentAfterRemoval:

    def test_index_stays_0_when_first_file_removed(self):
        state = _make_state([_FILE_A, _FILE_B, _FILE_C], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_file_idx"] == 0

    def test_index_stays_same_when_middle_file_removed(self):
        # removing index 1 from [A,B,C] → [A,C]; new idx=1 points at C
        state = _make_state([_FILE_A, _FILE_B, _FILE_C], idx=1)
        state = _apply_post_commit(state)
        assert state["convert_file_idx"] == 1

    def test_index_clamped_when_last_file_of_list_removed(self):
        # removing index 2 from [A,B,C] → [A,B]; idx must clamp to 1, not 2
        state = _make_state([_FILE_A, _FILE_B, _FILE_C], idx=2)
        state = _apply_post_commit(state)
        assert state["convert_file_idx"] == 1
        assert state["convert_file_idx"] < len(state["convert_uploaded_files"])

    def test_index_clamped_when_second_of_two_removed(self):
        state = _make_state([_FILE_A, _FILE_B], idx=1)
        state = _apply_post_commit(state)
        assert state["convert_file_idx"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Tests: per-note state is cleared for the incoming file
# ─────────────────────────────────────────────────────────────────────────────

class TestPerNoteStateReset:

    def test_preview_cleared_after_commit(self):
        state = _make_state([_FILE_A, _FILE_B], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_preview"] is None

    def test_quality_cleared_after_commit(self):
        state = _make_state([_FILE_A, _FILE_B], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_quality"] is None

    def test_enrich_state_cleared_after_commit(self):
        state = _make_state([_FILE_A, _FILE_B], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_enrich_questions"] is None
        assert state["convert_enrich_answers"] == []
        assert state["convert_enrich_rewritten"] is None

    def test_splits_state_cleared_after_commit(self):
        state = _make_state([_FILE_A, _FILE_B], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_splits"] is None
        assert state["convert_splits_enabled"] == []
        assert state["convert_splits_strategy"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# Tests: auto-close when last file is converted
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoCloseOnLastFile:

    def test_modal_closes_after_last_file_converted(self):
        state = _make_state([_FILE_A], idx=0)
        state = _apply_post_commit(state)
        assert state["active_modal"] is None

    def test_staged_list_emptied_on_auto_close(self):
        state = _make_state([_FILE_A], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_uploaded_files"] == []

    def test_file_idx_reset_on_auto_close(self):
        state = _make_state([_FILE_A], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_file_idx"] == 0

    def test_display_state_cleared_on_auto_close(self):
        state = _make_state([_FILE_A], idx=0)
        state = _apply_post_commit(state)
        assert state["convert_preview"] is None
        assert state["convert_quality"] is None
        assert state["convert_splits"] is None
        assert state["convert_splits_enabled"] == []
        assert state["convert_splits_strategy"] == ""

    def test_single_file_mode_does_not_auto_close(self):
        """Converting with no files in the staged list (plain text mode)
        should not change the modal state at all."""
        state = _make_state([], idx=0)
        state["active_modal"] = "convert"
        state = _apply_post_commit(state)
        # Neither branch fires — modal stays open
        assert state["active_modal"] == "convert"
