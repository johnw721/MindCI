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


def _build_batch_prompt(base_prompt, entries):
    """Build a single prompt for a batch of 3-5 entries."""
    entries_block = json.dumps(entries, indent=2)
    return f"""{base_prompt}

Generate flashcard Q&A pairs for EACH of the following entries.
For each entry, output a section header like:
ENTRY: <index starting at 0>
Then list the Q&A pairs for that entry.
Format each question and answer exactly like this:
Q: your question here
A: your answer here

ENTRIES:
{entries_block}"""


def _parse_batch_response(text, num_entries):
    """Parse a batched response into per-entry card lists."""
    result = {i: [] for i in range(num_entries)}
    current_idx = None
    current_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("ENTRY:"):
            # Flush previous
            if current_idx is not None:
                result[current_idx] = parse_qa("\n".join(current_lines))
            try:
                current_idx = int(stripped.replace("ENTRY:", "").strip())
            except ValueError:
                current_idx = None
            current_lines = []
        else:
            current_lines.append(line)

    # Flush last
    if current_idx is not None:
        result[current_idx] = parse_qa("\n".join(current_lines))

    return result


def generate_flashcards_batched(entries, base_prompt, batch_size=4):
    """
    Generate flashcards for a list of entries using batched API calls.
    Returns list of (entry, cards) tuples.
    Reduces API calls from N to ceil(N / batch_size).
    """
    results = []
    for i in range(0, len(entries), batch_size):
        batch = entries[i:i + batch_size]
        prompt = _build_batch_prompt(base_prompt, batch)
        try:
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text
            parsed = _parse_batch_response(raw, len(batch))
            for j, entry in enumerate(batch):
                results.append((entry, parsed.get(j, [])))
        except Exception as e:
            # Fall back to individual calls for this batch on failure
            for entry in batch:
                try:
                    single_prompt = build_dynamic_prompt(base_prompt, entry)
                    response = client.messages.create(
                        model="claude-sonnet-4-5",
                        max_tokens=4096,
                        messages=[{"role": "user", "content": single_prompt}]
                    )
                    cards = parse_qa(response.content[0].text)
                    results.append((entry, cards))
                except Exception as inner_e:
                    results.append((entry, []))
    return results