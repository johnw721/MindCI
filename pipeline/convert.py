import json
import os
import sys
from datetime import datetime

# Add project root to path for validation + config imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATA_DIR, MAX_TOKENS_GENERATION
from pipeline._client import call_with_retry


def _repair_json(bad_json):
    """Ask Claude to fix its own malformed JSON output."""
    repair_prompt = f"""The following JSON is malformed. Fix it and return ONLY valid JSON, nothing else.

MALFORMED JSON:
{bad_json}"""
    return call_with_retry(repair_prompt, max_tokens=MAX_TOKENS_GENERATION)


def _strip_fences(text):
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    return clean.strip()


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
    return call_with_retry(prompt, max_tokens=MAX_TOKENS_GENERATION)


def parse_and_save_json(raw_response):
    clean = _strip_fences(raw_response)

    # Attempt 1: parse directly
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        # Attempt 2: ask Claude to repair
        try:
            repaired = _strip_fences(_repair_json(clean))
            parsed = json.loads(repaired)
        except (json.JSONDecodeError, RuntimeError) as e:
            raise ValueError(
                f"Could not parse Claude response as JSON after repair attempt.\n"
                f"Error: {e}\n"
                f"First 300 chars of response: {clean[:300]}"
            )

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array, got {type(parsed).__name__}")

    history_dir = os.path.join(DATA_DIR, "history")
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(history_dir, exist_ok=True)

    # Copy-on-write: version the existing file before overwriting
    current_path = os.path.join(DATA_DIR, "structured.json")
    if os.path.exists(current_path):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        versioned_path = os.path.join(history_dir, f"structured_{timestamp}.json")
        with open(current_path, "r", encoding="utf-8") as f:
            existing = f.read()
        with open(versioned_path, "w", encoding="utf-8") as f:
            f.write(existing)

    # Validate and normalize before saving
    try:
        from validation import validate_and_save
        report = validate_and_save(parsed, current_path)
        return parsed, report
    except ImportError:
        # Fallback if validation not available
        with open(current_path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)
        return parsed, {"valid_count": len(parsed), "invalid_count": 0, "warning_count": 0, "invalid": [], "warnings": []}


def list_kb_versions():
    """Return list of versioned KB files sorted newest first."""
    history_dir = os.path.join(DATA_DIR, "history")
    if not os.path.exists(history_dir):
        return []
    files = [f for f in os.listdir(history_dir) if f.startswith("structured_")]
    return sorted(files, reverse=True)
