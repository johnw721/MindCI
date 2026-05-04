import os
from config import QUALITY_SIGNALS, MIN_WORD_COUNT

def check_note_quality(filename, text):
    words = text.split()
    word_count = len(words)
    text_lower = text.lower()
    issues = []
    passes = []

    if word_count < MIN_WORD_COUNT:
        issues.append(f"Too short ({word_count} words) — aim for 50+ words to generate good scenarios")
    else:
        passes.append(f"Length OK ({word_count} words)")

    for signal, keywords in QUALITY_SIGNALS.items():
        found = any(kw in text_lower for kw in keywords)
        label = signal.replace("_", " ").title()
        if found:
            passes.append(f"{label} present")
        else:
            if signal == "confidence":
                issues.append("Missing Confidence field — add 'Confidence: Low/Medium/High' at the bottom")
            elif signal == "difficulty":
                issues.append("Missing Difficulty field — add 'Difficulty: Easy/Medium/Hard' at the bottom")
            elif signal == "root_cause":
                issues.append("No root cause mentioned — explain WHY this happened, not just what you did")
            elif signal == "symptoms":
                issues.append("No misleading symptoms — what made this hard to find? This generates better scenarios")
            elif signal == "fix":
                issues.append("No fix documented — what was the actual solution?")
            elif signal == "lesson":
                issues.append("No lesson captured — add what you'd tell a junior engineer about this")

    score = int((len(passes) / (len(passes) + len(issues))) * 10) if (passes or issues) else 0
    return {"filename": filename, "word_count": word_count, "score": score, "issues": issues, "passes": passes}

def score_kb_entry(entry):
    score = 0
    issues = []
    passes = []

    label = entry.get("topic") or entry.get("concept") or entry.get("tool") or entry.get("error", "unknown")
    entry_type = entry.get("type", "unknown")

    if entry.get("confidence") in ("High", "Medium", "Low"):
        score += 2
        passes.append("Confidence set")
    else:
        issues.append("Confidence missing or unrecognized")

    if entry.get("difficulty") in ("Hard", "Medium", "Easy"):
        score += 2
        passes.append("Difficulty set")
    else:
        issues.append("Difficulty missing or unrecognized")

    # Type-specific field checks
    if entry_type == "project":
        for field in ["error", "root_cause", "fix", "concept"]:
            val = entry.get(field, "")
            if val and len(str(val)) > 20:
                score += 1
                passes.append(f"{field} has detail")
            else:
                issues.append(f"{field} is thin or missing")

    elif entry_type == "certification":
        for field in ["topic", "key_points", "confusion"]:
            val = entry.get(field, "")
            if val and len(str(val)) > 20:
                score += 1
                passes.append(f"{field} has detail")
            else:
                issues.append(f"{field} is thin or missing")

    elif entry_type == "exploration":
        for field in ["tool", "description", "use_cases"]:
            val = entry.get(field, "")
            if val and len(str(val)) > 20:
                score += 1
                passes.append(f"{field} has detail")
            else:
                issues.append(f"{field} is thin or missing")

    score = min(score, 10)
    return {"label": label, "type": entry_type, "score": score, "issues": issues, "passes": passes}


def generate_enrichment_questions(note_text):
    from anthropic import Anthropic
    import json
    client = Anthropic()

    prompt = f"""You are helping a Cloud/DevOps engineer enrich a rough technical note so it generates better flashcards and interview scenarios.

RAW NOTE:
{note_text}

Analyze the note and ask exactly 4-5 targeted follow-up questions to extract missing detail.
Focus only on what is genuinely missing -- do not ask about things already covered.

Always ask about these if missing:
- Root cause (WHY did this happen, not just what you did)
- Misleading symptoms (what made it hard to find)
- Confidence level (1=Low, 2=Medium, 3=High)
- Difficulty (1=Easy, 2=Medium, 3=Hard)

Ask about these only if relevant and missing:
- The fix or solution
- What you would tell a junior engineer
- Related tools or services involved

Return ONLY a JSON array of question strings, no markdown, no extra text:
["question 1", "question 2", "question 3", "question 4"]"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def rewrite_enriched_note(original_note, questions, answers):
    from anthropic import Anthropic
    client = Anthropic()

    qa_block = "\n".join(
        f"Q: {q}\nA: {a}" for q, a in zip(questions, answers) if a.strip()
    )

    prompt = f"""You are rewriting a rough technical note into a rich structured note for a Cloud/DevOps engineer.

ORIGINAL NOTE:
{original_note}

ADDITIONAL CONTEXT PROVIDED:
{qa_block}

Rewrite this as a single cohesive note that:
- Leads with what happened or what the topic is
- Includes root cause or core concept
- Captures misleading symptoms or common misconceptions if provided
- Documents the fix or how to apply it
- Ends with a lesson or takeaway
- Includes exactly these two lines at the end:
  Confidence: Low/Medium/High
  Difficulty: Easy/Medium/Hard

Write in clear plain English. No bullet points -- flowing prose is fine.
Return ONLY the rewritten note text, nothing else."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()