# MindCI – a learning system that watches the job market for you

## What it does

MindCI turns your study notes into flashcards, mock interviews, and a weekly study plan – but it doesn't stop there. It also looks at real job descriptions, compares them to what you know, and tells you exactly what to learn next.

Think of it as a personal tutor that:

- Reads your rough notes and turns them into structured flashcard questions.
- Runs a mock interview with you, grades your answers, and adjusts difficulty based on how you did.
- Scans job descriptions (yours or any you paste) and shows you: *"You claimed this skill, but you have no notes on it – that's risky."* and *"You know this, but you're not mentioning it on your resume – add it."*

Then it builds a weekly plan with hands-on projects, blog topics, and resume bullets to close the gaps.

## Why you might use it

- You're studying for cloud certifications (AWS, CKA, etc.) and want to know what actually appears in job postings.
- You take lots of notes but never turn them into practice questions.
- You've had a mock interview where you froze – you want to practice on questions generated from your own study material.
- You want your resume to match what you can actually talk about.

## How it works (the simple version)

1. **Drop notes into a folder** – plain text, markdown, even Obsidian files with frontmatter.
2. **MindCI converts them** into structured knowledge (project, certification, or exploration).
3. **Generate flashcards or scenario questions.** Review them in the dashboard or export to Anki.
4. **Mock interview** – MindCI picks questions from your knowledge base, grades your answers, and adjusts each topic's difficulty for next time.
5. **Paste job descriptions** – MindCI shows you where your resume and knowledge don't match the market, then builds a weekly plan.

All of this runs on your own computer (or in Docker). You need an Anthropic API key for the AI parts.

## What you control

- **Your own API key** – you pay Anthropic directly. A response cache eliminates repeated calls so re-running the same analysis is free.
- **Everything stays local** – notes, flashcard history, interview reports. No cloud upload.
- **Confidence calibration** – you can override the AI's difficulty ratings anytime.

## The smart bits you don't have to think about

- **Auto-confidence** – After each mock interview, MindCI lowers the difficulty for topics you struggled with and raises it for topics you aced. Next round's questions fit you better.
- **Market frequencies** – After you analyze a few job descriptions, MindCI starts weighting topic suggestions by what employers actually ask for.
- **Resume reality check** – Upload your resume once. MindCI extracts what you claimed, compares it to your notes, and highlights claims you can't back up. One click drafts a study note for each gap.

## What it looks like

The dashboard runs in your browser (Streamlit). You get:

- A sidebar with tabs: Dashboard, Mock Interview, Knowledge Base, Weekly Plan, Topic Suggestions.
- Pop-up modals for quick actions: New Note, Generate, Card Review, JD Analyzer, Resume Check.
- A footer showing API cost today and cache hit rate.

## Requirements

- Python 3.11+ (or Docker)
- An Anthropic API key (free trial credit available)
- About 5 minutes to run `pip install -r requirements.txt` and `streamlit run app_dashboard.py`

## The long version

If you want all the technical details – file structure, CLI commands, 59 tests, CPM markers, confidence sparklines, and the exact hysteresis algorithm – read the [full README](README.md). But you don't need any of that to start using it.
