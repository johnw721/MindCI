import json
import os
import shutil
from datetime import datetime

def load_knowledge_base():
    path = "data/structured.json"
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_prompt(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def load_anki_cards():
    path = "output/anki.csv"
    if not os.path.exists(path):
        return []
    cards = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                cards.append({
                    "id": i,
                    "question": parts[0],
                    "answer": parts[1],
                    "tags": parts[2] if len(parts) > 2 else "",
                    "difficulty": parts[3] if len(parts) > 3 else "",
                    "confidence": parts[4] if len(parts) > 4 else "",
                    "status": "pending"
                })
    return cards

def save_reviewed_cards(cards):
    os.makedirs("output", exist_ok=True)
    approved = [c for c in cards if c["status"] != "rejected"]
    rejected = [c for c in cards if c["status"] == "rejected"]
    with open("output/anki.csv", "w", encoding="utf-8") as f:
        for c in approved:
            f.write(f"{c['question']}\t{c['answer']}\t{c['tags']}\t{c['difficulty']}\t{c['confidence']}\n")
    with open("output/anki_rejected.csv", "w", encoding="utf-8") as f:
        for c in rejected:
            f.write(f"{c['question']}\t{c['answer']}\t{c['tags']}\t{c['difficulty']}\t{c['confidence']}\n")
    return len(approved), len(rejected)

def load_scenario_cards():
    path = "output/scenarios.json"
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cards = []
    for i, s in enumerate(data):
        cards.append({
            "id": i,
            "type": s.get("scenario", "unknown"),
            "setup": s.get("setup", ""),
            "code": s.get("code_or_config", ""),
            "files": s.get("files", []),
            "question": s.get("question", ""),
            "answer": s.get("answer", ""),
            "topic": s.get("topic", ""),
            "confidence": s.get("confidence", ""),
            "status": "pending"
        })
    return cards