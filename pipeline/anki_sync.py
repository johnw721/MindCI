"""
AnkiConnect bridge — push approved flashcards directly into the user's Anki app.

AnkiConnect is a popular Anki add-on that exposes a localhost JSON-RPC API on
port 8765. With it installed and Anki running, MindCI can deposit notes
straight into a deck instead of leaving the user with a manual CSV import.

Endpoints used (action / version 6 envelope):
  - "version"     — health probe
  - "createDeck"  — idempotent; safe to call repeatedly
  - "addNote"     — actually deposit a card

Failure mode: every public function returns clean Pythonic results; callers
should treat AnkiConnect as best-effort and fall back to the CSV path on any
exception. Anki not running → connection refused; AnkiConnect not installed
→ 404 / weird response. Both are normal and shouldn't crash the dashboard.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ANKI_URL      = os.environ.get("MINDCI_ANKI_URL",   "http://localhost:8765")
DEFAULT_DECK  = os.environ.get("MINDCI_ANKI_DECK",  "MindCI")
DEFAULT_MODEL = os.environ.get("MINDCI_ANKI_MODEL", "Basic")
TIMEOUT       = 5  # seconds


def _invoke(action: str, **params) -> dict:
    """Send one AnkiConnect JSON-RPC call. Raises on transport or protocol error."""
    payload = json.dumps({
        "action": action, "version": 6, "params": params,
    }).encode("utf-8")
    req = urllib.request.Request(
        ANKI_URL, data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error"):
        raise RuntimeError(f"AnkiConnect error on {action}: {body['error']}")
    return body.get("result")


def is_available() -> bool:
    """Quick health probe. True if AnkiConnect responds within TIMEOUT seconds."""
    try:
        _invoke("version")
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError, OSError, RuntimeError):
        return False


def ensure_deck(deck: str | None = None) -> str:
    """Idempotent: creates the deck if it doesn't exist. Returns the deck name."""
    name = deck or DEFAULT_DECK
    _invoke("createDeck", deck=name)
    return name


def push_card(question: str, answer: str,
              tags: list[str] | None = None,
              deck: str | None = None,
              model: str | None = None) -> int:
    """Push one Q/A pair as a note. Returns the AnkiConnect note id (int).

    Duplicates within the deck are rejected by AnkiConnect (allowDuplicate=False).
    Caller can catch RuntimeError if duplicate-handling matters."""
    note = {
        "deckName":  deck  or DEFAULT_DECK,
        "modelName": model or DEFAULT_MODEL,
        "fields":    {"Front": question, "Back": answer},
        "options":   {"allowDuplicate": False, "duplicateScope": "deck"},
        "tags":      tags or [],
    }
    return _invoke("addNote", note=note)
