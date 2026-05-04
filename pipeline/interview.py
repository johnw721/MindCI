import json
import os
from anthropic import Anthropic
client = Anthropic()

import random
import os

def score_answer(question, code_or_config, correct_answer, user_answer, topic):
    prompt = f"""You are a senior Cloud/DevOps engineer grading a technical interview answer.

Topic: {topic}

Question asked:
{question}

Code/config shown (if any):
{code_or_config or "N/A"}

Correct answer:
{correct_answer}

Candidate answer:
{user_answer}

Grade the candidate answer and return ONLY a JSON object, no markdown:
{{
  "score": <0-10 integer>,
  "verdict": "Strong|Acceptable|Needs Work|Incorrect",
  "what_they_got_right": "specific things correct in their answer, or empty string",
  "what_they_missed": "key concepts or details missing, or empty string",
  "coaching_note": "one concrete thing to study or remember"
}}"""

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

def build_interview_pool(n=8):
    import random
    pool = []

    # Load scenarios
    scenario_path = "output/scenarios.json"
    if os.path.exists(scenario_path):
        with open(scenario_path, "r", encoding="utf-8") as f:
            scenarios = json.load(f)
        for s in scenarios:
            pool.append({
                "source": "scenario",
                "type": s.get("scenario", "whats_wrong"),
                "topic": s.get("topic", ""),
                "confidence": s.get("confidence", "Low"),
                "setup": s.get("setup", ""),
                "code": s.get("code_or_config", ""),
                "files": s.get("files", []),
                "question": s.get("question", ""),
                "answer": s.get("answer", "")
            })

    # Load flashcards
    anki_path = "output/anki.csv"
    if os.path.exists(anki_path):
        with open(anki_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    pool.append({
                        "source": "flashcard",
                        "type": "recall",
                        "topic": parts[2].split("::")[-1] if len(parts) > 2 else "",
                        "confidence": parts[4].strip() if len(parts) > 4 else "Low",
                        "setup": "",
                        "code": "",
                        "question": parts[0],
                        "answer": parts[1]
                    })

    if not pool:
        return []

    # Bias toward Low/Medium confidence
    weighted = []
    for item in pool:
        weight = 3 if item["confidence"] == "Low" else 2 if item["confidence"] == "Medium" else 1
        weighted.extend([item] * weight)

    random.shuffle(weighted)
    seen = set()
    selected = []
    for item in weighted:
        key = item["question"][:60]
        if key not in seen:
            seen.add(key)
            selected.append(item)
        if len(selected) >= n:
            break

    return selected