import json
import os
import random

from config import MAX_TOKENS_GRADE, OUTPUT_DIR
from pipeline._client import call_with_retry


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

    _text = call_with_retry(prompt, max_tokens=MAX_TOKENS_GRADE)
    raw = _text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def build_interview_pool(n=8):
    pool = []

    # Load scenarios
    scenario_path = os.path.join(OUTPUT_DIR, "scenarios.json")
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
    anki_path = os.path.join(OUTPUT_DIR, "anki.csv")
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


HISTORY_PATH = os.path.join(OUTPUT_DIR, "interview_history.json")

def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def append_session(session_report):
    history = load_history()
    history.append(session_report)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

def get_topic_progression():
    history = load_history()
    if not history:
        return {}

    # topic -> list of {date, avg_score, verdict_counts}
    progression = {}
    for session in history:
        date = session.get("date", "unknown")
        for q in session.get("questions", []):
            topic = q.get("topic", "unknown")
            score = q.get("score", 0)
            verdict = q.get("verdict", "unknown")
            if topic not in progression:
                progression[topic] = []
            progression[topic].append({
                "date": date,
                "score": score,
                "verdict": verdict
            })

    return progression

def get_summary_stats():
    history = load_history()
    if not history:
        return None

    total_sessions = len(history)
    all_scores = [q["score"] for s in history for q in s.get("questions", []) if "score" in q]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0

    # Score trend across sessions
    session_avgs = []
    for s in history:
        scores = [q["score"] for q in s.get("questions", []) if "score" in q]
        if scores:
            session_avgs.append({
                "date": s.get("date", ""),
                "avg": round(sum(scores) / len(scores), 1),
                "pct": s.get("pct", 0)
            })

    # Most improved topics
    progression = get_topic_progression()
    improvements = []
    for topic, entries in progression.items():
        if len(entries) >= 2:
            first = entries[0]["score"]
            last = entries[-1]["score"]
            delta = last - first
            improvements.append({"topic": topic, "delta": delta, "first": first, "last": last})
    improvements.sort(key=lambda x: x["delta"], reverse=True)

    # Persistent weak spots
    weak_spots = []
    for topic, entries in progression.items():
        if len(entries) >= 2:
            avg = sum(e["score"] for e in entries) / len(entries)
            if avg < 6:
                weak_spots.append({"topic": topic, "avg_score": round(avg, 1), "attempts": len(entries)})
    weak_spots.sort(key=lambda x: x["avg_score"])

    return {
        "total_sessions": total_sessions,
        "total_questions": len(all_scores),
        "overall_avg": round(avg_score, 1),
        "session_trend": session_avgs,
        "most_improved": improvements[:5],
        "weak_spots": weak_spots[:5]
    }
