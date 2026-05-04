import json
import os
from anthropic import Anthropic
client = Anthropic()

def build_dynamic_prompt(base_prompt, entry):
    confidence = entry.get("confidence", "Low")
    difficulty = entry.get("difficulty", "Medium")

    if confidence == "High":
        modifier = """
CONFIDENCE LEVEL: High - candidate knows this well.
Generate questions that test edge cases, failure modes, and cross-domain scenarios.
Avoid surface-level definitions.
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""
    elif confidence == "Medium":
        modifier = """
CONFIDENCE LEVEL: Medium - candidate has partial understanding.
Generate questions that reinforce core mechanisms, common mistakes, and misconceptions.
Include one "explain to a junior engineer" question.
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""
    else:
        modifier = """
CONFIDENCE LEVEL: Low - candidate has minimal understanding.
Generate foundational questions from first principles. Answers should teach, not just hint.
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""

    difficulty_note = ""
    if difficulty == "Hard":
        difficulty_note = "\nDIFFICULTY: Hard - push deeper, assume technical audience.\n"
    elif difficulty == "Easy":
        difficulty_note = "\nDIFFICULTY: Easy - keep language approachable.\n"

    return f"{base_prompt}{modifier}{difficulty_note}\n\nDATA:\n{json.dumps(entry, indent=2)}"

def parse_qa(text):
    cards = []
    lines = text.split("\n")
    q, a_lines = None, []
    for line in lines:
        line = line.strip()
        if line.startswith("Q:"):
            if q and a_lines:
                cards.append((q, " ".join(a_lines)))
            q = line.replace("Q:", "").strip()
            a_lines = []
        elif line.startswith("A:"):
            a_lines = [line.replace("A:", "").strip()]
        elif a_lines and line:
            a_lines.append(line)
    if q and a_lines:
        cards.append((q, " ".join(a_lines)))
    return cards

def classify(entry):
    c = entry.get("confidence", "Low")
    if c == "High":
        return "AUTO-PASS"
    elif c == "Medium":
        return "REVIEW"
    return "PRIORITY"