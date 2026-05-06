"""
validation.py

Pydantic models for MindCI structured.json entry types.
Called after every parse_and_save_json to catch missing or
malformed fields before downstream stages run.
"""

import json
import os
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

# ── Confidence / Difficulty enums ─────────────────────────────────────────────

CONFIDENCE_VALUES = {"High", "Medium", "Low"}
DIFFICULTY_VALUES = {"Hard", "Medium", "Easy"}


def _normalize_confidence(v):
    if not v:
        return "Low"
    mapped = {"high": "High", "medium": "Medium", "med": "Medium", "low": "Low",
              "3": "High", "2": "Medium", "1": "Low"}
    return mapped.get(str(v).strip().lower(), str(v).strip())


def _normalize_difficulty(v):
    if not v:
        return "Medium"
    mapped = {"hard": "Hard", "medium": "Medium", "med": "Medium",
              "easy": "Easy", "3": "Hard", "2": "Medium", "1": "Easy"}
    return mapped.get(str(v).strip().lower(), str(v).strip())


# ── Entry models ───────────────────────────────────────────────────────────────

class ProjectEntry(BaseModel):
    type: Literal["project"]
    error: str
    root_cause: str
    fix: str
    concept: Optional[str] = ""
    confidence: str = "Low"
    difficulty: str = "Medium"
    source: Optional[str] = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def norm_confidence(cls, v):
        return _normalize_confidence(v)

    @field_validator("difficulty", mode="before")
    @classmethod
    def norm_difficulty(cls, v):
        return _normalize_difficulty(v)

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v):
        if v not in CONFIDENCE_VALUES:
            return "Low"
        return v

    @field_validator("difficulty")
    @classmethod
    def check_difficulty(cls, v):
        if v not in DIFFICULTY_VALUES:
            return "Medium"
        return v


class CertificationEntry(BaseModel):
    type: Literal["certification"]
    topic: str
    key_points: str
    confusion: Optional[str] = ""
    importance: Optional[str] = ""
    confidence: str = "Low"
    difficulty: str = "Medium"
    source: Optional[str] = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def norm_confidence(cls, v):
        return _normalize_confidence(v)

    @field_validator("difficulty", mode="before")
    @classmethod
    def norm_difficulty(cls, v):
        return _normalize_difficulty(v)

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v):
        if v not in CONFIDENCE_VALUES:
            return "Low"
        return v

    @field_validator("difficulty")
    @classmethod
    def check_difficulty(cls, v):
        if v not in DIFFICULTY_VALUES:
            return "Medium"
        return v


class ExplorationEntry(BaseModel):
    type: Literal["exploration"]
    tool: str
    description: str
    comparison: Optional[str] = ""
    use_cases: Optional[str] = ""
    confidence: str = "Low"
    difficulty: str = "Medium"
    source: Optional[str] = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def norm_confidence(cls, v):
        return _normalize_confidence(v)

    @field_validator("difficulty", mode="before")
    @classmethod
    def norm_difficulty(cls, v):
        return _normalize_difficulty(v)

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v):
        if v not in CONFIDENCE_VALUES:
            return "Low"
        return v

    @field_validator("difficulty")
    @classmethod
    def check_difficulty(cls, v):
        if v not in DIFFICULTY_VALUES:
            return "Medium"
        return v


# ── Validator ──────────────────────────────────────────────────────────────────

MODEL_MAP = {
    "project": ProjectEntry,
    "certification": CertificationEntry,
    "exploration": ExplorationEntry,
}


def validate_entries(entries):
    """
    Validate a list of parsed KB entries.

    Returns:
        valid   — list of validated + normalized dicts
        invalid — list of {"entry": raw, "errors": [str]}
        warnings — list of {"entry": raw, "warnings": [str]}
    """
    valid = []
    invalid = []
    warnings = []

    for i, entry in enumerate(entries):
        entry_type = entry.get("type", "").lower()
        model_cls = MODEL_MAP.get(entry_type)

        if not model_cls:
            invalid.append({
                "index": i,
                "entry": entry,
                "errors": [f"Unknown type: '{entry_type}' -- must be project, certification, or exploration"]
            })
            continue

        try:
            validated = model_cls(**entry)
            validated_dict = validated.model_dump()

            # Collect soft warnings (fields that defaulted)
            entry_warnings = []
            if not entry.get("confidence"):
                entry_warnings.append("confidence defaulted to Low")
            if not entry.get("difficulty"):
                entry_warnings.append("difficulty defaulted to Medium")

            label = (entry.get("topic") or entry.get("tool") or
                     entry.get("error") or f"entry {i}")

            if entry_warnings:
                warnings.append({
                    "index": i,
                    "label": label,
                    "warnings": entry_warnings
                })

            valid.append(validated_dict)

        except Exception as e:
            label = (entry.get("topic") or entry.get("tool") or
                     entry.get("error") or f"entry {i}")
            error_msgs = []
            if hasattr(e, "errors"):
                for err in e.errors():
                    field = " -> ".join(str(x) for x in err.get("loc", []))
                    msg = err.get("msg", str(err))
                    error_msgs.append(f"{field}: {msg}")
            else:
                error_msgs.append(str(e))

            invalid.append({
                "index": i,
                "label": label,
                "entry": entry,
                "errors": error_msgs
            })

    return valid, invalid, warnings


def validate_and_save(entries, path=None):
    """
    Validate entries, save only valid ones, return validation report.
    Invalid entries are saved separately to <DATA_DIR>/invalid_entries.json.
    """
    from config import DATA_DIR
    if path is None:
        path = os.path.join(DATA_DIR, "structured.json")
    valid, invalid, warnings = validate_entries(entries)

    os.makedirs(DATA_DIR, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(valid, f, indent=2, ensure_ascii=False)

    if invalid:
        invalid_path = os.path.join(DATA_DIR, "invalid_entries.json")
        with open(invalid_path, "w", encoding="utf-8") as f:
            json.dump(invalid, f, indent=2, ensure_ascii=False)

    return {
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "warning_count": len(warnings),
        "invalid": invalid,
        "warnings": warnings
    }
