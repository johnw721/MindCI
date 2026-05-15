"""
End-to-end integration tests.

Exercises cross-module flows that the per-module unit tests don't cover:
  (1) raw note → convert → KB write → generate flashcards → anki rows
  (2) build interview pool → score answer → append session → recalibrate KB

Both tests stub `pipeline._client.call_with_retry` with canned responses so
nothing hits the network. Filesystem state is sandboxed via DATA_DIR / OUTPUT_DIR
overrides set in tests/conftest.py.
"""

import json
from pathlib import Path

import pytest

from pipeline import _client, calibration, convert, generate, interview, jd_analyzer


def _stub_responses(monkeypatch, responses):
    """Replace call_with_retry with a generator yielding canned strings.
    `responses` is a list of strings (or callables taking the prompt) returned
    in order on each call_with_retry invocation. Also captures the prompts
    that were passed in for assertions about prompt content."""
    state = {"i": 0, "prompts": []}

    def fake(prompt, *, max_tokens, model=None):
        state["prompts"].append(prompt)
        i = state["i"]
        state["i"] = i + 1
        r = responses[i] if i < len(responses) else responses[-1]
        return r(prompt) if callable(r) else r

    monkeypatch.setattr(_client, "call_with_retry", fake)
    # Also patch the per-module imports (they imported `call_with_retry` by name).
    monkeypatch.setattr(convert,     "call_with_retry", fake)
    monkeypatch.setattr(generate,    "call_with_retry", fake)
    monkeypatch.setattr(interview,   "call_with_retry", fake)
    monkeypatch.setattr(jd_analyzer, "call_with_retry", fake)
    return state


# ── (1) Convert → Generate flow ───────────────────────────────────────────────
def test_convert_then_generate_writes_kb_and_flashcards(monkeypatch, tmp_path):
    """A raw note becomes a structured KB entry, then a flashcard run produces
    parsable Q/A pairs the downstream save path can consume."""
    from config import DATA_DIR, OUTPUT_DIR

    # --- Stage 1: Convert returns a JSON array with one project entry.
    structured_response = json.dumps([{
        "type": "project",
        "error": "Lambda cold start failed",
        "root_cause": "Circular import between logger and client modules",
        "fix": "Lazy import the client inside the handler",
        "concept": "Python module-level import semantics in Lambda",
        "confidence": "Medium",
        "difficulty": "Hard",
    }])
    # --- Stage 2: Generate returns a batched flashcard payload.
    flashcards_response = (
        "ENTRY: 0\n"
        "Q: What causes a circular import in Lambda?\n"
        "A: When two modules at module-scope both try to import each other.\n"
        "Q: Why does it only fail on cold start?\n"
        "A: Module-level code runs once per cold start, not per warm invocation.\n"
    )

    _stub_responses(monkeypatch, [structured_response, flashcards_response])

    # --- Run convert
    raw_response = convert.convert_to_json("--- SOURCE: t.txt ---\nLambda cold start broke...")
    parsed, report = convert.parse_and_save_json(raw_response)

    assert len(parsed) == 1
    assert report["valid_count"] == 1
    kb_path = Path(DATA_DIR) / "structured.json"
    assert kb_path.exists()
    saved = json.loads(kb_path.read_text(encoding="utf-8"))
    assert saved[0]["error"] == "Lambda cold start failed"

    # --- Run generate against the just-written KB
    results = generate.generate_flashcards_batched(saved, base_prompt="<base>", batch_size=4)
    assert len(results) == 1
    entry, cards = results[0]
    assert entry["error"] == "Lambda cold start failed"
    assert len(cards) == 2
    assert cards[0][0].startswith("What causes")
    assert "import" in cards[0][1].lower()


# ── (2) Pool → Score → Recalibrate flow ───────────────────────────────────────
def test_interview_pool_score_and_recalibrate_round_trip(monkeypatch, tmp_path):
    """Build a pool from saved scenarios, grade an answer, append session,
    recalibrate. The KB entry's auto_confidence should reflect the new score."""
    from config import DATA_DIR, OUTPUT_DIR

    # Seed a KB with one entry and a scenarios file referencing it by topic.
    kb_path        = Path(DATA_DIR) / "structured.json"
    scenarios_path = Path(OUTPUT_DIR) / "scenarios.json"
    history_path   = Path(OUTPUT_DIR) / "interview_history.json"
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    scenarios_path.parent.mkdir(parents=True, exist_ok=True)

    kb_path.write_text(json.dumps([{
        "type": "exploration", "tool": "Karpenter",
        "description": "Open-source Kubernetes node autoscaler from AWS.",
        "confidence": "Low",
    }]), encoding="utf-8")
    scenarios_path.write_text(json.dumps([{
        "scenario": "whats_wrong",
        "topic": "Karpenter",
        "confidence": "Low",
        "setup": "Cluster won't scale up under load.",
        "code_or_config": "spec: {}",
        "question": "What's wrong with this NodePool?",
        "answer": "It has no requirements set.",
    }] * 3), encoding="utf-8")  # ×3 so they all get pulled

    # Point calibration at the same files.
    monkeypatch.setattr(calibration, "KB_PATH", kb_path)
    monkeypatch.setattr(calibration, "HISTORY_PATH", history_path)

    # Build the pool — should pick up our scenario.
    pool = interview.build_interview_pool(n=3)
    assert len(pool) >= 1
    assert any(item.get("topic") == "Karpenter" for item in pool)

    # Stub the grader to return a strong score on every call.
    grade_payload = json.dumps({
        "score": 9, "verdict": "Strong",
        "what_they_got_right": "Correctly identified missing requirements",
        "what_they_missed": "",
        "coaching_note": "Solid.",
    })
    _stub_responses(monkeypatch, [grade_payload, grade_payload, grade_payload])

    # Grade three Karpenter answers and persist a session.
    questions = []
    for _ in range(3):
        g = interview.score_answer("q", "code", "model_answer", "user_answer", "Karpenter")
        questions.append({"score": g["score"], "verdict": g["verdict"],
                          "topic": "Karpenter", "type": "scenario"})

    interview.HISTORY_PATH = history_path  # ensure session writes to sandbox
    interview.append_session({
        "date": "2026-05-06 10:00", "total_score": 27, "max_score": 30,
        "pct": 90, "questions": questions,
    })

    # Recalibrate — Low → High based on three 9s (avg 9.0, ≥ 8.5 buffer).
    changes = calibration.recalibrate_kb()

    assert len(changes) == 1
    assert changes[0]["new"] == "High"
    assert changes[0]["label"] == "Karpenter"

    written = json.loads(kb_path.read_text(encoding="utf-8"))
    assert written[0]["auto_confidence"]      == "High"
    assert written[0]["confidence"]           == "Low"      # manual seed preserved
    assert written[0]["confidence_history"]   == [[written[0]["confidence_updated_at"], "High"]]


# ── (3) JD analysis with resume claims → three-way bucketing ──────────────────
def test_jd_analysis_with_resume_claims_includes_resume_block_and_buckets(monkeypatch):
    """When resume_claims is passed, the prompt must include the CANDIDATE RESUME
    CLAIMS block, the schema must request the new buckets, and the response must
    be parsed and returned with all four buckets accessible."""
    kb = [
        {"type": "exploration", "tool": "Karpenter",  "description": "Node autoscaler.",
         "confidence": "High"},
        {"type": "project",     "error": "EKS pod scheduling", "concept": "K8s scheduling",
         "fix": "...", "root_cause": "...", "confidence": "Medium"},
    ]
    resume_claims = {
        "skills":    ["AWS Lambda", "Kubernetes", "Karpenter"],
        "projects":  ["AD Onboarding"],
        "companies": ["Acme Corp"],
    }
    canned_response = json.dumps({
        "role_title": "Senior Cloud Engineer",
        "overall_readiness": "Partial",
        "readiness_score": 65,
        "matched_skills": [{"domain": "Karpenter", "candidate_confidence": "High", "status": "covered"}],
        "priority_gaps": [{"domain": "Istio", "urgency": "High", "action": "Lab it"}],
        "strengths": ["Karpenter"],
        "summary": "Strong on EKS adjacency, exposed on Lambda.",
        "strengths_to_lead_with": [
            {"domain": "Karpenter", "reason": "On resume, in KB at High, on JD."}
        ],
        "exposures": [
            {"domain": "AWS Lambda", "urgency": "High",
             "study_action": "Build a Lambda lab covering cold-start mitigations."}
        ],
        "hidden_assets": [
            {"domain": "K8s scheduling",
             "resume_action": "Add 'pod scheduling debugging' to resume bullets."}
        ],
    })
    state = _stub_responses(monkeypatch, [canned_response])

    out = jd_analyzer.run_gap_analysis("JD text mentioning Lambda, Karpenter, Istio.",
                                       kb, resume_claims=resume_claims)

    # Prompt contained the resume block
    assert "CANDIDATE RESUME CLAIMS" in state["prompts"][0]
    assert "AWS Lambda" in state["prompts"][0]
    # Response was parsed with all four buckets present
    assert out["strengths_to_lead_with"][0]["domain"] == "Karpenter"
    assert out["exposures"][0]["domain"]              == "AWS Lambda"
    assert out["hidden_assets"][0]["domain"]          == "K8s scheduling"
    assert out["priority_gaps"][0]["domain"]          == "Istio"


def test_jd_analysis_without_resume_claims_falls_back_to_original_schema(monkeypatch):
    """No resume_claims → no resume block in prompt, no extra-bucket schema requested."""
    canned = json.dumps({
        "role_title": "X", "overall_readiness": "Partial", "readiness_score": 50,
        "matched_skills": [], "priority_gaps": [], "strengths": [], "summary": "ok",
    })
    state = _stub_responses(monkeypatch, [canned])

    out = jd_analyzer.run_gap_analysis("JD", [], resume_claims=None)

    assert "CANDIDATE RESUME CLAIMS" not in state["prompts"][0]
    assert "strengths_to_lead_with" not in state["prompts"][0]
    assert "exposures" not in out  # canned response didn't include it; not invented


@pytest.fixture(autouse=True)
def _isolate_filesystem_writes(tmp_path, monkeypatch):
    """Each test gets a fresh data/ + output/ dir so writes don't leak between tests.
    Several pipeline modules import DATA_DIR / OUTPUT_DIR at import time, so we
    rebind their module-local copies here too."""
    import os

    import config
    from pipeline import convert as _conv
    from pipeline import interview as _iv
    from pipeline import scenarios as _sc

    data_dir = tmp_path / "data"
    output_dir = tmp_path / "output"
    data_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setenv("MINDCI_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MINDCI_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("MINDCI_CACHE_DISABLE", "1")

    monkeypatch.setattr(config, "DATA_DIR",   str(data_dir))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(_conv,  "DATA_DIR",   str(data_dir))
    monkeypatch.setattr(_iv,    "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(_iv,    "HISTORY_PATH",
                        os.path.join(str(output_dir), "interview_history.json"))
    monkeypatch.setattr(_sc,    "OUTPUT_DIR", str(output_dir))
    yield
