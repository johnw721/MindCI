# MindCI gap-analysis evaluation harness

A re-runnable evaluation of `pipeline.jd_analyzer.run_gap_analysis` -- the call
that cross-references a candidate's knowledge base against a job description and
emits structured gaps/matches. The harness exists to (a) catch regressions when
the prompt or model changes, and (b) describe MindCI's reliability as a genuine
evaluation, not as error handling.

## Run it

```bash
python eval.py                 # all golden cases against config.MODEL
python eval.py --model X       # evaluate a specific model string
python eval.py --case mixed-cloud-engineer all-gap-sanity
python eval.py --limit 3       # first N cases (cheap smoke run)
python eval.py --json runs/today.json   # also dump full per-case results
```

Requires `ANTHROPIC_API_KEY` (loaded from `.env` automatically). The harness
disables the response cache by default so every case is a fresh generation --
a cached reply would hide exactly the regression you are testing for. Exit code
is non-zero if any case fails to parse, so CI can gate on it.

## What it measures (Phase 1)

**Parse reliability.** Every response is classified as `first_try` (parsed
directly), `repaired` (parsed only after a repair-prompt call), or `failed`.
Note: the production gap-analysis path does *not* currently have a repair
fallback -- only the note-conversion path does. The harness models the repair
tier (reusing `pipeline.convert._repair_json`), so the `repaired` count
quantifies what adding a repair fallback to the analysis path would recover.

**Gap-detection quality.** Precision and recall of `priority_gaps`, reported
separately, against hand-labeled golden gaps. Skill matching (`matched_skills`
with status covered/partial) is scored the same way. Numbers are micro-averaged
across cases (every individual gap weighted equally).

**Hallucination rate (deterministic).** Every run also reports the share of
asserted skills (gaps + matches) whose tokens never appear in the JD text --
fabricated requirements the candidate would waste time on. No API cost; always
on. See Phase 2 below for the grounding rule.

## How scoring matches domain strings

The model says "Amazon EKS / Kubernetes orchestration"; the golden label says
"EKS". `evals/metrics.domain_match` lines these up with word-boundary
tokenization plus token-subset matching.

It deliberately does **not** reuse `pipeline.resume_check._claim_matches_entry`.
That matcher is loose on purpose (raw substring + short tokens) because a false
match is cheap in the app -- but it reports "RDS" as covered because "rds" sits
inside "gua**rds**", and "Service Mesh" as covered because of "**service**
worker". Baking those false positives into the score would make the eval lie, so
scoring uses a stricter matcher. The two matchers disagreeing is itself a
finding the eval surfaces.

## The golden set

`golden_cases.json` holds ~12 hand-labeled cases, each scored against the shared
KB in `kb_snapshot.json` (a frozen copy of `data/structured.json`). Labels are
**complete**: `expected_gaps` and `expected_matches` together enumerate every
distinct skill the JD demands, partitioned by whether the snapshot KB genuinely
covers it. Completeness is what makes false positives (invented gaps) and false
negatives (missed gaps) both measurable. Labels were verified by inspecting the
KB, not by the app's loose matcher.

### Add a case

Append an object to `cases` in `golden_cases.json`:

```json
{
  "id": "unique-slug",
  "role_title_hint": "Role name",
  "jd_text": "Tightly scoped JD that names a clear, enumerable skill list.",
  "expected_gaps": ["SkillNotInKB"],
  "expected_matches": ["SkillInKB"],
  "rationale": "Why each label is what it is (cite KB evidence)."
}
```

Keep JDs tightly scoped so the gap/match sets stay complete, then re-run
`python eval.py`. Re-snapshot the KB (`cp data/structured.json
evals/kb_snapshot.json`) only when you deliberately want the eval to track a
newer KB -- and re-check labels when you do.

## Files

| File | Role |
| --- | --- |
| `eval.py` (repo root) | CLI entry point + summary table |
| `evals/cases.py` | golden-set + KB-snapshot loaders, `GoldenCase` |
| `evals/metrics.py` | `domain_match`, `score_set`, `micro_average`, `classify_parse` |
| `evals/harness.py` | runs each case through the real prompt + transport |
| `evals/hallucination.py` | deterministic JD-grounding check |
| `evals/consistency.py` | repeat-run flake-rate / stability metrics |
| `evals/judge.py` | LLM-as-judge rubric + judge self-validation |
| `evals/golden_cases.json` | hand-labeled cases |
| `evals/kb_snapshot.json` | frozen KB the cases score against |
| `tests/test_eval_harness.py` | offline tests for scoring + parse classification |
| `tests/test_jd_prompt_builder.py` | guards the prompt-builder extraction |
| `tests/test_eval_phase2.py` | offline tests for grounding, consistency, judge |

## Phase 2 (opt-in)

Three additions beyond the Phase 1 accuracy/reliability numbers.

```bash
python eval.py --consistency 5     # run each case 5x, report flake rate
python eval.py --judge             # LLM-as-judge on explanation usefulness
python eval.py --judge --judge-model claude-sonnet-4-6   # override the judge model
```

**Consistency / flake rate** (`evals/consistency.py`). The gap-analysis call
runs at the model's default temperature, so the same JD can yield different gaps
on different runs. `--consistency N` runs each case N times (cache disabled) and
reports per-case **gap-set stability** -- the mean pairwise Jaccard overlap of
the N gap sets, where 1.00 means identical gaps every time. Surface variants are
folded by canonical key (significant-token set) so "EKS" and "Amazon EKS" count
as the same gap. It also reports readiness-score spread (stdev) and whether the
parse outcome was stable. Aggregate: mean stability and count of perfectly
stable cases. This lets a prompt/temperature change be judged on whether it
makes output more *stable*, not just more accurate on a single lucky run.

**Hallucination / grounding** (`evals/hallucination.py`, runs in Phase 1 too). A
gap or matched skill is *grounded* when at least one of its significant tokens
appears in the JD text (same word-boundary tokenization as scoring; vendor words
like "AWS" can't ground a domain on their own). Anything ungrounded is flagged
as a fabricated requirement. The check is deterministic and lenient by design --
one shared significant token grounds the domain -- so it catches invented skills
("Kafka" in a JD that never says Kafka) without punishing paraphrase.

**LLM-as-judge** (`evals/judge.py`). Precision/recall can't tell you whether the
per-gap `action` and the `summary` are actually *useful*. `--judge` asks a model
to score each result on a 1-5 rubric: **specificity**, **actionability**, and
**grounding**, returning the mean. The judge runs through the same
strip-and-parse path as everything else. By default the judge model is
`config.MODEL_FAST` (currently Haiku) -- deliberately a *different* model than
the Sonnet generator, so the judge is not grading its own family's output;
override with `--judge-model`.

*Validating the judge.* A judge you don't validate is just another unverified
model. `judge.validate_judge` runs a built-in **calibration smoke test**: it
judges a deliberately strong explanation and a deliberately useless one and
asserts the judge ranks the good one higher (a flat/blind judge fails this).
That is necessary, not sufficient. The fuller plan: hand-score ~15 explanations,
measure judge-vs-human rank correlation (Spearman) and exact-agreement rate, and
run the judge on a *different* model family than the generator to limit
self-preference bias. The calibration test ships and runs on every `--judge`
invocation; the human-correlation study is the next step.
