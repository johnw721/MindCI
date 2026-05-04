from anthropic import Anthropic
import json
import os
import shutil
from datetime import datetime

client = Anthropic()  # ← paste API key later via env
# The above line initializes the Anthropic client with the API key from environment variables. Make sure to set the "ANTHROPIC_ACCESS_KEY" environment variable before running the script.



def load_raw_notes():
    all_notes = ""

    for filename in os.listdir("raw"):
        if filename.endswith(".txt"):
            with open(f"raw/{filename}", "r", encoding="utf-8") as f:
                content = f.read()
                all_notes += f"\n\n--- SOURCE: {filename} ---\n\n{content}"

    return all_notes

def convert_to_json(raw_text):
    prompt = f"""
Convert the following raw technical notes into structured JSON.
Preserve the source filename in each JSON entry as "source".
Return ONLY raw JSON with no markdown, no code fences, no explanation, no commentary, no apologies, no disclaimers, no extra text.

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

Return ONLY valid JSON.

RAW NOTES:
{raw_text}
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text

def save_json(data):
    try:
        # Strip markdown code fences if present
        clean = data.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        
        parsed = json.loads(clean.strip())
        VALID_TYPES = {"project", "certification", "exploration"}
        
        for entry in parsed:
            if entry.get("type") not in VALID_TYPES:
                print(f"⚠️ Unknown type: {entry.get('type')} — source: {entry.get('source')}")
    except json.JSONDecodeError as e:
        print("❌ Invalid JSON:", e)
        print("Raw response:", data[:200])  # helps debug
        exit(1)

    with open("data/structured.json", "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)

    print("✅ Structured JSON saved")

def archive_files():
    os.makedirs("archive", exist_ok=True)

    for filename in os.listdir("raw"):
        if filename.endswith(".txt"):
            src = f"raw/{filename}"

            # 👇 ADD TIMESTAMP LOGIC HERE
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            dst = f"archive/{timestamp}_{filename}"

            shutil.move(src, dst)

    print("📦 Raw files moved to archive/")

def main():
    raw = load_raw_notes()
    structured = convert_to_json(raw)
    save_json(structured)
    archive_files()

if __name__ == "__main__":
    main()