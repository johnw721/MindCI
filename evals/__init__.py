"""MindCI gap-analysis evaluation harness.

Phase 1 measures two things about ``pipeline.jd_analyzer.run_gap_analysis``:

* parse reliability  -- how often the model's structured output parses on the
  first try, only after a repair call, or not at all; and
* gap-detection quality -- precision and recall of detected gaps (and matched
  skills) against a hand-labeled golden set.

Run it with ``python eval.py`` from the repo root.
"""
