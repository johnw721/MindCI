# MindCI

A personal knowledge pipeline for Cloud Engineers. Converts raw study notes into structured flashcards and interview scenarios, maps your skills against real job descriptions, simulates graded mock interviews, tracks score progression over time, and tells you what to learn next.

---

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`config.py` validates required env vars at import time and fails fast if any are missing. Set `MINDCI_SKIP_ENV_CHECK=1` to bypass the env check for tooling that doesn't need the API key (e.g. `py_compile`, unit tests).

Run the dashboard (recommended):

```bash
streamlit run app_dashboard.py
```

Or the legacy 7-tab UI:

```bash
streamlit run app.py
```

Or in a container:

```bash
docker compose up --build   # then visit http://localhost:8501
```

The app auto-loads your last generated flashcards or scenarios on startup so you can get straight to reviewing.

---

## Project Structure

```
MindCI/
├── app_dashboard.py               # dashboard-first UI (sidebar nav + st.dialog modals)
├── app.py                         # legacy 7-tab Streamlit interface
├── config.py                      # env validation, paths, model + token caps, JD freq loader
├── validation.py                  # Pydantic schemas for all entry types
├── aggregate_jd_frequencies.py    # aggregates saved JD reports into market_frequencies.json
├── utils.py                       # shared file I/O helpers
├── mindci.py                      # argparse CLI: run / convert / generate / aggregate / dashboard
├── run_pipeline.py                # thin alias for `mindci.py run`
├── requirements.txt               # pinned Python deps (incl. pytest)
├── ruff.toml                      # lint config — conservative ruleset
├── Dockerfile                     # python:3.11-slim image, streamlit healthcheck
├── docker-compose.yml             # local container orchestration with volume mounts
├── .dockerignore
├── .pre-commit-config.yaml        # ruff + pytest before every commit
├── .github/workflows/ci.yml       # lint, test, compile-check, smoke-import on push/PR
├── pipeline/
│   ├── __init__.py
│   ├── _client.py                 # lazy Anthropic client + universal retry + cost telemetry
│   ├── convert.py                 # note → structured JSON, JSON repair, KB versioning
│   ├── generate.py                # flashcard generation, dynamic prompts, batched API calls
│   ├── scenarios.py               # single-file and multi-file scenario generation
│   ├── interview.py               # mock interview grading, pool building, session history
│   ├── jd_analyzer.py             # single and batch JD gap analysis, report saving
│   ├── weekly.py                  # weekly execution plan generation
│   ├── suggestions.py             # topic suggestions, cold-test question generation
│   └── quality.py                 # CPM markers + cheat sheet, quality scoring, enrichment assistant, live preview
├── prompts/
│   ├── project.txt
│   ├── cert.txt
│   └── explore.txt
├── tests/                         # pytest suite (25 tests, runs in <1s)
│   ├── conftest.py                # env stubbing + lazy-client monkeypatch
│   ├── test_client_retry.py
│   ├── test_config.py
│   ├── test_cost_telemetry.py
│   ├── test_jd_parsing.py
│   ├── test_quality.py
│   ├── test_scenarios.py
│   └── test_validation.py
├── raw/           # drop .txt notes here (gitignored)
├── data/          # structured.json, market_frequencies.json, usage.json, history/ (gitignored)
├── jd_reports/    # saved JD reports for frequency aggregation (gitignored)
├── output/        # all generated files (gitignored)
└── archive/       # processed notes moved here (gitignored)
```

The pipeline modules are imported directly by the Streamlit apps and the CLI — no top-level `convert.py` / `generate.py` / `jd_analyze.py` scripts.

---

## Dashboard (`app_dashboard.py`)

**Sidebar nav:** Dashboard · Mock Interview · Knowledge Base · Weekly Plan · Topic Suggestions.
**Quick actions** open as modals: New Note (Convert), Generate, Card Review, JD Analyzer.

### 1. Convert (modal)
Two quality layers before the API call:

**Pre-flight quality check** — rule-based check on raw text. Flags word count, missing confidence/difficulty, root cause language, misleading symptoms, fix documentation, lesson capture. Scored 0-10.

**Note enrichment assistant** — Claude generates 4-5 targeted follow-up questions for thin notes. Answer inline, Claude rewrites the note into a CPM-marked version, one click promotes it to the editor.

After commit, Claude structures notes into `data/structured.json` by type: `project`, `certification`, or `exploration`. Pydantic-validated; invalid entries saved separately to `data/invalid_entries.json`. Defaulted fields surface as warnings. Previous versions of `structured.json` are versioned to `data/history/` (copy-on-write).

### 2. Generate (modal)
Three sub-modes with type and confidence filters:

**Flashcards** — Q&A pairs calibrated to confidence level (High → edge cases, Low → first-principles). Batched 4 entries per API call.

**Scenarios (single file)** — `what_does_this_do`, `whats_wrong`, `fix_it`, `architecture` per entry.

**Scenarios (multi-file)** — 2-3 related files per scenario with realistic filenames. Tests cross-file architecture understanding.

### 3. Card Review (modal)
Approve / reject / skip flow for flashcards (flip mechanic) and scenarios (rendered setup + code + question). Approved → `output/anki.csv`, rejected → `output/anki_rejected.csv`.

### 4. JD Analyzer (modal)
**Single** — readiness score 0-100, skill coverage, priority gaps with one-line recommendations, strengths to lead with.

**Batch** — multiple JDs separated by `---` or uploaded as a `.txt`. Aggregate view: most common gaps, consistent strengths, average readiness, best-fit role. Auto-saves to `jd_reports/` and triggers `aggregate_jd_frequencies.py`.

### 5. Mock Interview (sidebar view)
Multi-step session: question-count slider → per-question UI with setup, code/files, your answer → Claude grades 0-10 with verdict (Strong/Acceptable/Needs Work/Incorrect), what you got right, what you missed, coaching note → end screen with breakdown bars, focus areas, auto-saved to `output/interview_report.json` and appended to history.

### 6. Weekly Plan
Reads last JD report, generates a 7-day execution plan for top 2 priority gaps. Per gap: hands-on GitHub project, blog article title, reusable lab exercise, resume bullet, STAR-format interview story. Hours slider before generating.

### 7. Topic Suggestions
Compares KB against market frequency data. Three categories: uncovered high-demand topics, weak-but-in-demand, emerging. **Cold test button** on each item generates 3 questions from the topic name alone — tests whether the gap is knowledge or confidence before committing to a study session.

### 8. Knowledge Base
Filterable viewer by type and confidence. Each entry shows a quality score badge with enrichment suggestions inline; raw JSON expandable.

---

## Cost Telemetry

Every API call goes through `pipeline/_client.call_with_retry`, which records token counts to `data/usage.json` and surfaces them on the dashboard footer:

> API today: **$0.42** (8 calls, 12,304 tokens) · 7-day: **$2.18** (54 calls)

Pricing is configurable via env vars (defaults track Claude Sonnet 4.5 list pricing — $3/MTok input, $15/MTok output):

```
MINDCI_INPUT_PRICE_PER_MTOK=3.0
MINDCI_OUTPUT_PRICE_PER_MTOK=15.0
```

---

## Configuration (env vars)

All env-overridable, sensible defaults in `config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API auth |
| `MINDCI_SKIP_ENV_CHECK` | unset | Bypass env validation (for `py_compile`, tests) |
| `MINDCI_DATA_DIR` | `data` | Structured KB, history, frequencies, usage log |
| `MINDCI_OUTPUT_DIR` | `output` | Flashcards, scenarios, reports, plans |
| `MINDCI_RAW_DIR` | `raw` | Drop-zone for `.txt` notes |
| `MINDCI_JD_REPORTS_DIR` | `jd_reports` | Saved JD analyses for aggregation |
| `MINDCI_LOG_LEVEL` | `INFO` | stdout log level |
| `MINDCI_MODEL` | `claude-sonnet-4-5` | Anthropic model id used by every call |
| `MINDCI_MAX_TOKENS_GRADE` | `512` | Interview grading + enrichment questions |
| `MINDCI_MAX_TOKENS_REVIEW` | `1024` | Preview, enrichment, rewrite |
| `MINDCI_MAX_TOKENS_ANALYSIS` | `2048` | Gap analysis, suggestions |
| `MINDCI_MAX_TOKENS_BATCH` | `3000` | Batch JD analysis |
| `MINDCI_MAX_TOKENS_GENERATION` | `4096` | Flashcards, scenarios, weekly plan |
| `MINDCI_INPUT_PRICE_PER_MTOK` | `3.0` | USD per million input tokens |
| `MINDCI_OUTPUT_PRICE_PER_MTOK` | `15.0` | USD per million output tokens |

---

## CLI

`mindci.py` is a real argparse CLI that drives the same `pipeline.*` modules the dashboard uses.

```bash
python mindci.py run                  # convert + generate
python mindci.py convert              # raw/*.txt → data/structured.json
python mindci.py generate             # KB → output/anki.csv + questions.md
python mindci.py aggregate            # rebuild data/market_frequencies.json
python mindci.py dashboard            # launch streamlit dashboard

python mindci.py generate --batch-size 4
python mindci.py convert --no-archive   # leave notes in raw/ instead of archiving
```

`run_pipeline.py` is a thin alias for `mindci.py run`.

---

## Tests

```bash
pytest tests/ -v
```

25 deterministic tests, runs in well under a second. Coverage:

- `test_validation.py` — Pydantic schemas, type rejection, normalization, warnings
- `test_quality.py` — note quality scoring, KB entry scoring, type detection
- `test_jd_parsing.py` — single, multi-JD, short-chunk filtering
- `test_config.py` — baseline / blended / live frequency resolution
- `test_scenarios.py` — single + multi-file scenario parsers
- `test_client_retry.py` — success, retry-then-success, exhaustion
- `test_cost_telemetry.py` — usage recording, pricing math, end-to-end via fake client

`tests/conftest.py` sets `MINDCI_SKIP_ENV_CHECK=1`, a dummy `ANTHROPIC_API_KEY`, redirects `MINDCI_*` paths to a temp directory, and stubs `pipeline._client.get_client` so the suite never touches the network.

---

## CI + pre-commit

`.github/workflows/ci.yml` runs on every push to `main` and every PR: ruff lint, full pytest, `py_compile` on entry points, smoke-import all pipeline modules.

`.pre-commit-config.yaml` runs the same checks locally before every commit:

```bash
pip install pre-commit
pre-commit install
```

Lint config in `ruff.toml` — pyflakes + import sorting + common style, ignores tuned to the codebase quirks.

---

## Note Format

Raw `.txt` files can be freeform. The more detail you include, the better the generated scenarios. A note with full root cause, misleading symptoms, and fix produces a more realistic interview scenario than a one-liner.

```
Debugging a circular import error in Lambda when using a shared logger module.
Root cause: logger imported client at module level, client imported logger.
Symptoms: worked locally, failed on cold start in Lambda only.
Fix: lazy import inside the handler function.
Confidence: Medium
Difficulty: Hard
```

### Cognitive Payload Markers (CPM)

A tiny optional vocabulary you can sprinkle into notes so downstream prompts (and the enrichment assistant) know what each line represents. Five markers, no parser required:

```
#tag           keyword / topic, e.g. #kubernetes #etcd
→ step         an ordered step or transition (also accepts ->)
🧠 model       a mental model, framing, or first principle
! gotcha       an error, surprise, or thing that bit you
——SECTION——   optional delimiter between sub-blocks of one note
```

Example:

```
#etcd #raft
——SECTION——
🧠 etcd is just a Raft log with a kv API on top
→ write hits leader → leader replicates to majority → ack
! split-brain shows up when only 2/3 quorum is reachable
```

The full cheat sheet lives in `pipeline/quality.py` (`CPM_CHEAT_SHEET`) and is surfaced inline in the dashboard's enrichment assistant.

---

## Live Market Frequencies

JD Analyzer saves each report to `jd_reports/` and triggers aggregation automatically. After 3 reports, Topic Suggestions switches from hardcoded baseline frequencies to live data derived from your actual job searches. Between 1-2 reports, live and baseline are blended. The active source is always shown in the Topic Suggestions header. Frequencies are reloaded on every dashboard render — new reports take effect without an app restart.

Run aggregation manually at any time:

```bash
python mindci.py aggregate
```

---

## The Feedback Loop

```
raw notes → structured JSON → flashcards + scenarios → mock interview
       ↑                                                      |
topic suggestions ← JD analyzer ← knowledge base ←-----------
       ↑
live JD frequency aggregation
```

---

## Output Files

| File | Description |
|---|---|
| `data/structured.json` | Validated knowledge base |
| `data/invalid_entries.json` | Entries that failed validation |
| `data/market_frequencies.json` | Aggregated JD skill frequencies |
| `data/usage.json` | Daily API token + cost log |
| `data/history/` | Versioned KB snapshots (copy-on-write) |
| `output/anki.csv` | Approved flashcards for Anki import |
| `output/anki_rejected.csv` | Rejected flashcards |
| `output/questions.md` | Full flashcard question set |
| `output/scenarios.json` | Generated scenario questions |
| `output/interview_report.json` | Last mock interview session |
| `output/interview_history.json` | All session history |
| `output/jd_report.json` | Last JD analysis (single or batch aggregate) |
| `output/weekly_plan.md` | Last generated study plan |
| `jd_reports/` | Individual saved JD reports for aggregation |
