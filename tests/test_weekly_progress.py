"""
Tests for pipeline.weekly_progress — parsing checklist lines from generated
plans and persisting per-task completion state.
"""

from pathlib import Path

from pipeline import weekly_progress
from pipeline.weekly_progress import (
    load_progress,
    parse_checklist,
    save_progress,
    week_completion,
)

SAMPLE_PLAN = """\
## Karpenter

- [ ] Build a Karpenter autoscaler lab on EKS (Day 1, 2h)
- [x] Read the Karpenter consolidator RFC (Day 2, 0.5h)
- An informational bullet, not actionable
- [ ] Write a blog post: "When Karpenter consolidation surprises you" (Day 5, 1.5h)

## Cilium

- [ ] Stand up a Cilium-on-kind cluster (Day 3, 2h)
"""


def _redirect_progress(tmp_path: Path):
    weekly_progress.PROGRESS_PATH = tmp_path / "weekly_progress.json"


def test_parse_checklist_extracts_only_task_list_lines():
    items = parse_checklist(SAMPLE_PLAN)
    assert len(items) == 4
    # Index re-counts task lines only, skipping informational bullets.
    assert items[0] == (0, "Build a Karpenter autoscaler lab on EKS (Day 1, 2h)", False)
    assert items[1][2] is True  # the [x] one is pre-checked
    assert "blog post" in items[2][1]
    assert items[3][1].startswith("Stand up a Cilium")


def test_save_and_load_progress_round_trip(tmp_path):
    _redirect_progress(tmp_path)
    save_progress("2026-W19", 0, True)
    save_progress("2026-W19", 2, True)
    save_progress("2026-W20", 0, True)

    data = load_progress()
    assert data["2026-W19"] == {"0": True, "2": True}
    assert data["2026-W20"] == {"0": True}


def test_week_completion_counts_baseline_plus_overrides(tmp_path):
    _redirect_progress(tmp_path)
    # User overrides idx 0 → done. idx 1 already pre-checked in markdown.
    save_progress("2026-W19", 0, True)

    done, total = week_completion("2026-W19", SAMPLE_PLAN)
    assert total == 4
    assert done == 2  # idx 0 (override) + idx 1 (baseline [x])


def test_week_completion_zero_when_no_tasks(tmp_path):
    _redirect_progress(tmp_path)
    done, total = week_completion("2026-W21", "## Just prose, no tasks here.")
    assert (done, total) == (0, 0)
