# MindCI

A personal knowledge pipeline for Cloud Engineers. Converts raw study notes into structured Anki flashcards and scenario-based interview questions, maps your skills against real job descriptions, simulates mock interviews graded by Claude, and tells you what to learn next.

---

## Setup

```bash
pip install streamlit anthropic python-dotenv
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
├── app.py
├── convert.py
├── generate.py
├── jd_analyze.py
├── mindci.py
├── run_pipeline.py
├── prompts/
│   ├── project.txt
│   ├── cert.txt
│   └── explore.txt
├── raw/           # drop .txt notes here (gitignored)
├── data/          # structured.json lives here (gitignored)
├── output/        # all generated files (gitignored)
└── archive/       # processed notes moved here (gitignored)
```

---

## Workflow

### 1. Convert
Drop `.txt` notes into `raw/`, upload them in the Convert tab. Claude structures them into `data/structured.json` by type: `project`, `certification`, or `exploration`. Files are archived automatically after processing.

### 2. Generate
Three modes:

**Anki Flashcards** — Q&A pairs dynamically calibrated to your confidence level. High confidence entries get edge case and failure mode questions. Low confidence entries get foundational first-principles questions. Filter by entry type or confidence before running.

**Scenario Questions** — Interview-realistic scenarios generated from your actual notes. Four question types:
- `what_does_this_do` — read a code or config snippet and explain its behavior
- `whats_wrong` — identify the bug, misconfiguration, or security issue
- `fix_it` — diagnose the problem and produce a corrected version
- `architecture` — evaluate a system design and identify tradeoffs or failure points

Scenarios built from `project` entries bias toward `whats_wrong` and `fix_it`. Difficulty scales with your confidence level.

**Mock Interview** — Timed interview session pulling from both your flashcards and scenarios. Low confidence entries appear more frequently. You type your answer, Claude grades it 0-10 with a verdict (Strong / Acceptable / Needs Work / Incorrect), tells you what you got right, what you missed, and gives a single coaching note per question. Model answer is collapsible. End of session shows a full breakdown, focus areas for your next study session, and saves a report to `output/interview_report.json`.

### 3. Card Review
Review every generated card before export. Works for both flashcards and scenarios. Approve, reject, or skip each one. Approved flashcards go to `anki.csv`. Rejected cards go to `anki_rejected.csv`. Scenario cards render with setup context, code block, and question visually separated from the answer.

### 4. JD Analyzer
Two modes:

**Single JD** — paste one job description, get a readiness score, skill coverage breakdown, priority gaps, and strengths to lead with.

**Batch** — paste multiple JDs separated by `---` lines, or upload a `.txt` file. Returns individual results per JD plus an aggregate view: most common gaps ranked by frequency, consistent strengths, average readiness score, and best fit role. Batch results feed automatically into Weekly Plan and Topic Suggestions.

### 5. Weekly Plan
Reads your last JD report and generates a 7-day execution plan for your top 2 priority gaps. Per gap: a hands-on GitHub project, a blog article idea, a reusable lab exercise, a ready-to-paste resume bullet, and a STAR-format interview story. Adjust available hours with the slider before generating.

### 6. Topic Suggestions
Compares your knowledge base against real Cloud Engineer JD frequency data. Returns three categories:
- **Not in your notes, high demand** — topics with zero coverage that appear frequently in JDs, with a ready-to-paste note prompt to kick off a new Convert run
- **In your notes but needs work** — Low confidence entries in high-demand areas
- **Emerging** — topics gaining traction worth watching

If a JD report exists, suggestions factor in your active role gaps automatically.

### 7. Knowledge Base
Filterable viewer of your full `structured.json`. Filter by type and confidence. Inspect any entry as raw JSON.

---

## The Feedback Loop

Topic Suggestions feeds back into step 1. Each suggestion includes a note prompt you drop into `raw/` and Convert again. Each cycle your knowledge base gets denser, card quality improves as confidence levels shift, your JD readiness score goes up, and your mock interview scores improve against real roles you're applying to.

```
raw notes → structured JSON → flashcards + scenarios → mock interview
                ↑                                            |
         topic suggestions ← JD analyzer ← knowledge base ←-
```

---

## CLI (no UI)

```bash
python mindci.py run        # full pipeline
python mindci.py convert    # notes to JSON only
python mindci.py generate   # JSON to cards only
python jd_analyze.py        # JD gap analysis in terminal
```

---

## Note Format

Raw `.txt` files can be freeform. Label them clearly — the filename is preserved as `source` in the knowledge base. Claude detects the type automatically.

The more detail you include, the better the generated scenarios and mock interview questions. A note with full root cause, misleading symptoms, and fix produces a more realistic interview scenario than a one-liner.

Example:
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
# API keys
.env

# Raw notes (personal content)
raw/

# Archived notes (personal content)
archive/

# Generated outputs (reproducible)
output/

# Knowledge base (regenerated from notes)
data/structured.json

# Python
__pycache__/
*.pyc
*.pyo

# VSCode
.vscode/

# Windows
Thumbs.db
desktop.ini
```

---

## Output Files

| File | Description |
|---|---|
| `data/structured.json` | Parsed knowledge base |
| `output/anki.csv` | Approved flashcards for Anki import |
| `output/anki_rejected.csv` | Rejected flashcards |
| `output/questions.md` | Full flashcard question set |
| `output/scenarios.json` | Generated scenario questions |
| `output/scenarios.md` | Scenarios in readable markdown |
| `output/interview_report.json` | Last mock interview session results |
| `output/jd_report.json` | Last JD gap analysis (single or batch aggregate) |
| `output/batch_report.json` | Full batch analysis with individual JD results |
| `output/weekly_plan.md` | Last generated study plan |
| `output/topic_suggestions.json` | Last topic suggestion run |