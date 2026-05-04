import json
import os
from anthropic import Anthropic
client = Anthropic()

def convert_to_json(raw_text):
    prompt = f"""
Convert the following raw technical notes into structured JSON.
Preserve the source filename in each JSON entry as "source".
Return ONLY raw JSON with no markdown, no code fences, no explanation.

Rules:
- Detect type: project, certification, exploration
- Return a JSON array
- Use fields:

project:
  error, root_cause, fix, concept, confidence, difficulty

certification:
  topic, key_points, confusion, importance, confidence, difficulty

exploration:
  tool, description, comparison, use_cases, confidence, difficulty

RAW NOTES:
{raw_text}
"""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def parse_and_save_json(raw_response):
    clean = raw_response.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    parsed = json.loads(clean.strip())
    os.makedirs("data", exist_ok=True)
    with open("data/structured.json", "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)
    return parsed