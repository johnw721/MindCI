import json
from anthropic import Anthropic

client = Anthropic()

def load_json():
    with open("data/structured.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_prompt(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def classify(entry):
    if entry.get("confidence") == "High":
        return "AUTO-PASS"
    elif entry.get("confidence") == "Medium":
        return "REVIEW"
    else:
        return "PRIORITY"

def build_dynamic_prompt(base_prompt, entry):
    confidence = entry.get("confidence", "Low")
    difficulty = entry.get("difficulty", "Medium")

    if confidence == "High":
        modifier = """
CONFIDENCE LEVEL: High — candidate knows this well.
Generate questions that:
- Test edge cases and failure modes, not basic recall
- Ask "what breaks this" and "when would you NOT use this"
- Include at least one scenario-based question combining this topic with another domain
- Avoid surface-level definitions
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""
    elif confidence == "Medium":
        modifier = """
CONFIDENCE LEVEL: Medium — candidate has partial understanding.
Generate questions that:
- Reinforce the core mechanism, not just the name
- Include one "explain to a junior engineer" question to solidify understanding
- Ask about common mistakes or misconceptions
- Bridge from what they know toward what they're fuzzy on
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""
    else:
        modifier = """
CONFIDENCE LEVEL: Low — candidate has minimal or no understanding.
Generate questions that:
- Start with foundational "what is" and "why does this exist" questions
- Build understanding from first principles before any implementation detail
- Use analogies where helpful
- Keep answers thorough enough to teach, not just hint
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""

    difficulty_note = ""
    if difficulty == "Hard":
        difficulty_note = "\nDIFFICULTY: Hard — push deeper, assume technical audience.\n"
    elif difficulty == "Easy":
        difficulty_note = "\nDIFFICULTY: Easy — keep language approachable, no jargon without explanation.\n"

    return f"{base_prompt}{modifier}{difficulty_note}\n\nDATA:\n{json.dumps(entry, indent=2)}"

def generate(entry, base_prompt):
    full_prompt = build_dynamic_prompt(base_prompt, entry)

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": full_prompt}]
    )

    return response.content[0].text

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

def main():
    data = load_json()

    project_prompt = load_prompt("prompts/project.txt")
    cert_prompt = load_prompt("prompts/cert.txt")
    explore_prompt = load_prompt("prompts/explore.txt")

    md_output = ""
    anki_cards = []

    for entry in data:
        tag = classify(entry)
        entry_type = entry.get("type")
        confidence = entry.get("confidence", "Low")
        category = entry.get("category", entry.get("tool", "general"))

        if entry_type == "project":
            result = generate(entry, project_prompt)
        elif entry_type == "certification":
            result = generate(entry, cert_prompt)
        else:
            result = generate(entry, explore_prompt)

        md_output += f"\n\n## [{tag}] {entry_type.upper()} ({category})\n{result}\n"

        cards = parse_qa(result)
        print(f"[{confidence}] {entry_type} ({category}) → {len(cards)} cards")

        for q, a in cards:
            tag_str = f"{tag}::{entry_type}::{category}"
            anki_cards.append((q, a, tag_str, entry.get("difficulty", ""), confidence))

    with open("output/questions.md", "w", encoding="utf-8") as f:
        f.write(md_output)

    with open("output/anki.csv", "w", encoding="utf-8") as f:
        for q, a, tags, difficulty, confidence in anki_cards:
            f.write(f"{q}\t{a}\t{tags}\t{difficulty}\t{confidence}\n")

    print("✅ Questions + Anki CSV generated")

if __name__ == "__main__":
    main()