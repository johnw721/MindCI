"""
Tests for pipeline.anki_sync — AnkiConnect bridge.

We don't actually contact AnkiConnect; we stub urllib.request.urlopen and
verify (a) failure paths return clean Pythonic results, (b) the JSON-RPC
payload shape matches what AnkiConnect expects.
"""

import json
import urllib.error

from pipeline import anki_sync


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self._body


def test_is_available_returns_false_when_anki_not_running(monkeypatch):
    def boom(*_, **__):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert anki_sync.is_available() is False


def test_is_available_returns_true_on_clean_version_response(monkeypatch):
    def fake(*_, **__):
        return _FakeResp(json.dumps({"result": 6, "error": None}).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert anki_sync.is_available() is True


def test_push_card_sends_correct_jsonrpc_payload(monkeypatch):
    captured: dict = {}
    def fake(req, timeout):
        captured["url"]    = req.full_url
        captured["body"]   = json.loads(req.data.decode())
        return _FakeResp(json.dumps({"result": 12345, "error": None}).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake)

    note_id = anki_sync.push_card(
        "What is etcd?", "A distributed key-value store.",
        tags=["k8s", "raft"], deck="Test Deck", model="Basic",
    )

    assert note_id == 12345
    assert captured["url"] == anki_sync.ANKI_URL
    assert captured["body"]["action"] == "addNote"
    assert captured["body"]["version"] == 6
    note = captured["body"]["params"]["note"]
    assert note["deckName"]               == "Test Deck"
    assert note["modelName"]              == "Basic"
    assert note["fields"]["Front"]        == "What is etcd?"
    assert note["fields"]["Back"]         == "A distributed key-value store."
    assert note["tags"]                   == ["k8s", "raft"]
    assert note["options"]["allowDuplicate"]   is False
    assert note["options"]["duplicateScope"]   == "deck"


def test_push_card_raises_on_anki_error_response(monkeypatch):
    def fake(*_, **__):
        return _FakeResp(json.dumps({
            "result": None, "error": "cannot create note because it is a duplicate",
        }).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake)

    import pytest
    with pytest.raises(RuntimeError, match="duplicate"):
        anki_sync.push_card("dup q", "dup a")
