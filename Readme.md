# MindCI

A personal knowledge pipeline for Cloud Engineers. Converts raw study notes into structured flashcards and interview scenarios, maps your skills against real job descriptions, simulates graded mock interviews, tracks score progression over time, and tells you what to learn next.

---

## Setup

```bash
pip install streamlit anthropic python-dotenv pydantic
```

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Run the app:

```bash
streamlit run app.py
```

The app auto-loads your last generated flashcards or scenarios on startup so you can get straight to reviewing.

---

## Project Structure

```
MindCI/
├── app.py                         # UI only — 7-tab Streamlit interface
├── config.py                      # constants, dynamic JD frequency loader
├── validation.py                  # Pydantic schemas for all entry types
├── aggregate_jd_frequencies.py    # aggregates saved JD reports into market_frequencies.json
├── utils.py                       # shared file I/O helpers
├── convert.py                     # CLI convert script
├── generate.py                    # CLI generate script
├── jd_analyze.py                  # CLI JD analysis script
├── mindci.py                      # CLI entrypoint
├── run_pipeline.py                # CLI full pipeline runner
├── pipeline/
│   ├── __init__.py
│   ├── convert.py                 # note → structured JSON, retry + repair + versioning
│   ├── generate.py                # flashcard generation, dynamic prompt calibration, batching
│   ├── scenarios.py               # single-file and multi-file scenario generation
│   ├── interview.py               # mock interview grading, pool building, session history
│   ├── jd_analyzer.py             # single and batch JD gap analysis, report saving
│   ├── weekly.py                  # weekly execution plan generation
│   ├── suggestions.py             # topic suggestions, cold-test question generation
│   └── quality.py                 # note quality scoring, enrichment assistant
├── prompts/
│   ├── project.txt
│   ├── cert.txt
│   └── explore.txt
├── raw/           # drop .txt notes here (gitignored)
├── data/          # structured.json, market_frequencies.json, history/ (gitignored)
├── jd_reports/    # saved JD reports for frequency aggregation (gitignored)
├── output/        # all generated files (gitignored)
└── archive/       # processed notes moved here (gitignored)
```

---

## Workflow

### 1. Convert
Upload `.txt` notes in the Convert tab. Includes two quality layers before conversion:

**Pre-flight quality check** — rule-based check on raw files before any API call. Flags word count, missing confidence/difficulty fields, root cause language, misleading symptoms, fix documentation, lesson capture. Scored 0-10.

**Note enrichment assistant** — upload a thin note, Claude generates 4-5 targeted follow-up questions based on what's missing. Answer inline. Claude rewrites the note into a rich structured version. Approve and save directly to `raw/` for conversion.

After conversion, Claude structures notes into `data/structured.json` by type: `project`, `certification`, or `exploration`. Each entry is validated against Pydantic schemas — invalid entries are reported with field-level detail and saved to `data/invalid_entries.json`. Defaulted fields are surfaced as warnings. Post-convert quality scoring flags entries that will generate weak output. Raw files archived automatically. Previous versions of `structured.json` saved to `data/history/`.

### 2. Generate
Three modes with entry type and confidence filters:

**Anki Flashcards** — Q&A pairs dynamically calibrated to your confidence level. High confidence entries get edge case and failure mode questions. Low confidence entries get first-principles foundational questions. Runs in batches of 4 entries per API call — approximately 4x fewer API calls than individual generation.

**Scenario Questions** — Two sub-modes:

*Single file* — generates `what_does_this_do`, `whats_wrong`, `fix_it`, and `architecture` scenarios from actual note content. Entry type and confidence level shape question difficulty and focus.

*Multi-file* — generates 2-3 related files per scenario with realistic filenames. Tests cross-file architecture understanding — import chains, interface contracts, config/code mismatches, cross-service interactions.

**Mock Interview** — timed session pulling from both flashcards and scenarios, weighted toward Low/Medium confidence entries. Type your answer. Claude grades 0-10 with verdict (Strong/Acceptable/Needs Work/Incorrect), coaching note, and collapsible model answer. End-of-session shows score breakdown, focus areas, and session appended to history.

### 3. Card Review
Approve/reject/skip interface for flashcards and scenarios. Flashcards use flip mechanic. Scenario cards render setup, code blocks with syntax highlighting, and question in separate visual zones. Multi-file scenarios render each file labeled. Approved cards saved to `anki.csv`, rejected to `anki_rejected.csv`.

### 4. JD Analyzer
Two modes:

**Single JD** — readiness score 0-100, skill coverage map, priority gaps with one-line recommendations, strengths to lead with. Report saved to `output/jd_report.json` and `jd_reports/` for frequency aggregation.

**Batch** — multiple JDs separated by `---` or uploaded as a file. Individual results per JD plus aggregate: most common gaps by cross-JD frequency, consistent strengths, average readiness, best-fit role. Batch results feed Weekly Plan and Topic Suggestions automatically.

After each analysis, `aggregate_jd_frequencies.py` runs automatically and updates `data/market_frequencies.json`. Progress shown in UI — "2/3 reports needed to activate live frequencies."

### 5. Weekly Plan
Reads last JD report, generates a 7-day execution plan for top 2 priority gaps. Per gap: hands-on GitHub project, blog article title, reusable lab exercise, resume bullet, STAR-format interview story. Hours slider before generating.

### 6. Topic Suggestions
Compares knowledge base against market frequency data. Shows active frequency source in UI — `live (7 JD reports)`, `blended (live 2 + baseline)`, or `baseline (no JD reports yet)`. Three categories: uncovered high-demand topics, low-confidence entries in high-demand areas, emerging topics.

**Cold test button** — on every uncovered or weak topic. Generates 3 questions from topic name alone (no notes required). Tests whether the gap is knowledge or confidence before committing to a study session.

### 7. Knowledge Base
Filterable viewer by type and confidence. Each entry shows a quality score with enrichment suggestions inline. Raw JSON expandable.

---

## Session History

Every completed mock interview is appended to `output/interview_history.json`. The mock interview start screen shows total sessions, questions answered, overall average, score trend across last 8 sessions, most improved topics, and persistent weak spots (topics averaging below 6 across 2+ sessions).

---

## Live Market Frequencies

JD Analyzer saves each report to `jd_reports/` and triggers aggregation automatically. After 3 reports, Topic Suggestions switches from hardcoded baseline frequencies to live data derived from your actual job searches. Between 1-2 reports, live and baseline data are blended. The active source is always displayed in the Topic Suggestions tab.

Run aggregation manually at any time:
```bash
python aggregate_jd_frequencies.py
```

---

## Validation

Every Convert run validates entries against Pydantic schemas before writing to disk. Confidence and difficulty values are normalized (`"medium"` → `"Medium"`, `"2"` → `"Medium"`). Unknown entry types are rejected. Missing required fields produce field-level error messages in the UI. Invalid entries are saved separately to `data/invalid_entries.json` — nothing is silently dropped.

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

## CLI (no UI)

```bash
python mindci.py run        # full pipeline
python mindci.py convert    # notes to JSON only
python mindci.py generate   # JSON to cards only
python jd_analyze.py        # JD gap analysis in terminal
python aggregate_jd_frequencies.py  # rebuild market frequencies manually
```

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

---

## .gitignore

```gitignore
.env
raw/
archive/
output/
jd_reports/
data/structured.json
data/market_frequencies.json
data/history/
__pycache__/
*.pyc
*.pyo
.vscode/
Thumbs.db
desktop.ini
```

---

## Output Files

| File | Description |
|---|---|
| `data/structured.json` | Validated knowledge base |
| `data/invalid_entries.json` | Entries that failed validation |
| `data/market_frequencies.json` | Aggregated JD skill frequencies |
| `data/history/` | Versioned KB snapshots (copy-on-write) |
| `output/anki.csv` | Approved flashcards for Anki import |
| `output/anki_rejected.csv` | Rejected flashcards |
| `output/questions.md` | Full flashcard question set |
| `output/scenarios.json` | Generated scenario questions |
| `output/scenarios.md` | Scenarios in readable markdown |
| `output/interview_report.json` | Last mock interview session |
| `output/interview_history.json` | All session history |
| `output/jd_report.json` | Last JD analysis (single or batch aggregate) |
| `output/batch_report.json` | Full batch analysis results |
| `output/weekly_plan.md` | Last generated study plan |
| `output/topic_suggestions.json` | Last topic suggestion run |
| `jd_reports/` | Individual saved JD reports for aggregation |