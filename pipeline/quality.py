import os

from config import MAX_TOKENS_GRADE, MAX_TOKENS_REVIEW, MIN_WORD_COUNT, QUALITY_SIGNALS
from pipeline._client import call_with_retry

# ── Cognitive Payload Markers (CPM) ───────────────────────────────────────────
# A tiny vocabulary the user (and the enrichment assistant) can sprinkle into
# raw .txt notes so downstream prompts know what each line represents.
# Keep this set small — 5 markers is plenty.

CPM_MARKERS = {
    "#":              "keyword / tag (e.g. #kubernetes, #vpc-peering)",
    "->":             "step in a procedure or sequence (rendered as →)",
    "🧠":             "mental model / framing / first principle",
    "!":              "error, gotcha, or thing that bit me",
    "——SECTION——":    "optional section delimiter between blocks of a note",
}

CPM_CHEAT_SHEET = """\
COGNITIVE PAYLOAD MARKERS (CPM) — drop these into raw notes, no parser required.

  #tag           a keyword/topic, e.g. #kubernetes #etcd
  → step         an ordered step or transition (also accepts ->)
  🧠 model       a mental model, framing, or "the way to think about it"
  ! gotcha       an error, surprise, or thing that bit you
  ——SECTION——    optional delimiter between sub-blocks of one note

Example:
  #etcd #raft
  ——SECTION——
  🧠 etcd is just a Raft log with a kv API on top
  → write hits leader → leader replicates to majority → ack
  ! split-brain shows up when only 2/3 quorum is reachable
"""

CPM_PROMPT_BLOCK = """\
COGNITIVE PAYLOAD MARKERS (CPM):
The user marks raw notes with a tiny vocabulary. Respect these markers in your
output and use them yourself when rewriting:
  #tag           keyword / topic
  → step         a step or transition (use the arrow character)
  🧠 model       a mental model or first principle
  ! gotcha       an error, surprise, or pitfall
  ——SECTION——    optional delimiter between sub-blocks

When rewriting a note: lead with #tags on the first line, then use → for
procedures, 🧠 for the mental model, and ! for gotchas. Use ——SECTION—— only
when the note clearly has more than one logical block.
"""


# Per-entry-type "what's missing" question hints used by the enrichment
# assistant. Kept declarative so it's easy to tweak without touching prompt
# logic.
ENRICHMENT_FOCUS = {
    "project": [
        "the precise error message or failure mode (! marker territory)",
        "the root cause — WHY it happened, not just what fixed it",
        "misleading symptoms that sent you down the wrong path",
        "the actual fix and what made it work",
        "the lesson you'd hand a junior engineer (🧠 marker territory)",
    ],
    "certification": [
        "the topic or service in one line, with #tags",
        "key points / mechanisms (good for → step markers)",
        "common confusion or trap-door wrong answers (! marker)",
        "why this matters in real systems (🧠 marker)",
    ],
    "exploration": [
        "what the tool or concept actually is, in plain language",
        "comparison with the closest alternative (! when you tripped)",
        "concrete use cases — when you'd actually reach for it",
        "the mental model that makes it click (🧠 marker)",
    ],
}

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

    from pipeline.calibration import effective_confidence
    if effective_confidence(entry) in ("High", "Medium", "Low"):
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


def _detect_entry_type(text):
    """Best-effort guess at which schema this rough note is targeting."""
    t = text.lower()
    if any(k in t for k in ["error", "failed", "broke", "outage", "incident", "stack trace"]):
        return "project"
    if any(k in t for k in ["exam", "cert", "domain", "whitepaper", "study guide"]):
        return "certification"
    return "exploration"


def generate_enrichment_questions(note_text, entry_type=None):
    """
    Generate 4-5 targeted follow-up questions for a thin note.
    Questions are tailored to the entry type's missing CPM sections.
    """
    import json

    et = entry_type or _detect_entry_type(note_text)
    focus_lines = ENRICHMENT_FOCUS.get(et, ENRICHMENT_FOCUS["project"])
    focus_block = "\n".join(f"- {f}" for f in focus_lines)

    prompt = f"""You are helping a Cloud/DevOps engineer enrich a rough technical note so it generates better flashcards and interview scenarios.

{CPM_PROMPT_BLOCK}

DETECTED ENTRY TYPE: {et}

RAW NOTE:
{note_text}

Analyze the note and ask exactly 4-5 targeted follow-up questions to extract
missing detail. Map each question to a CPM marker the answer should produce
(#tag, →, 🧠, !) so the rewritten note will be richly marked.

For a {et} entry, prioritize coverage of:
{focus_block}

Always ask about these if missing:
- Confidence level (1=Low, 2=Medium, 3=High)
- Difficulty (1=Easy, 2=Medium, 3=Hard)

Focus only on what is genuinely missing -- do not ask about things already covered.

Return ONLY a JSON array of question strings, no markdown, no extra text:
["question 1", "question 2", "question 3", "question 4"]"""

    _text = call_with_retry(prompt, max_tokens=MAX_TOKENS_GRADE)
    raw = _text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def rewrite_enriched_note(original_note, questions, answers, entry_type=None):
    """
    Rewrite a thin note into a CPM-marked note. Output preserves and uses
    the marker vocabulary so downstream prompts can rely on it.
    """

    qa_block = "\n".join(
        f"Q: {q}\nA: {a}" for q, a in zip(questions, answers) if a.strip()
    )

    et = entry_type or _detect_entry_type(original_note)

    prompt = f"""You are rewriting a rough technical note into a rich CPM-marked note for a Cloud/DevOps engineer.

{CPM_PROMPT_BLOCK}

DETECTED ENTRY TYPE: {et}

ORIGINAL NOTE:
{original_note}

ADDITIONAL CONTEXT PROVIDED:
{qa_block}

Rewrite this as a single cohesive note. The output MUST:
- Open with a line of #tags (3-6 tags, lowercase, hyphenated where needed).
- Use → at the start of each step in any procedure or sequence.
- Use 🧠 to flag the mental model / first principle / "the way to think about it".
- Use ! to flag any error, gotcha, misleading symptom, or pitfall.
- Use ——SECTION—— only if the note has clearly distinct sub-blocks (e.g. setup vs failure vs fix).
- End with EXACTLY these two lines, in this order, no markers:
  Confidence: Low/Medium/High
  Difficulty: Easy/Medium/Hard

Style: clear plain English between markers. Markers are anchors, not bullet points.
Do not invent facts not present in the original note or the Q&A.
Return ONLY the rewritten note text, nothing else."""

    _text = call_with_retry(prompt, max_tokens=MAX_TOKENS_REVIEW)
    return _text.strip()


def preview_extraction(note_text):
    """
    Live preview — ask Claude what fields it WOULD extract from this note,
    without saving anything. Used by the Convert tab to show the user a dry
    run before they commit.
    """
    import json

    prompt = f"""You are previewing how a raw note would be converted to a structured KB entry.
Do NOT invent fields. If a field is unclear, leave it as an empty string.

{CPM_PROMPT_BLOCK}

RAW NOTE:
{note_text}

Return ONLY a single JSON object (not an array), no markdown:
{{
  "type": "project|certification|exploration",
  "label": "the topic/error/tool in one line",
  "fields": {{
      "...": "values that would populate the schema"
  }},
  "missing": ["fields that would be empty or weak"],
  "detected_markers": ["#tag1", "→", "🧠", "!"]
}}"""

    _text = call_with_retry(prompt, max_tokens=MAX_TOKENS_REVIEW)
    raw = _text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
