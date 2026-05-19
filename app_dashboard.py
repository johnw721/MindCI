"""
app_dashboard.py — Dashboard-first UI for MindCI.

Sidebar nav:
  • Dashboard          — readiness, weak spots, recent scores, market signal
  • Mock Interview     — full session runner (start → answer → grade → end)
  • Knowledge Base     — filterable viewer with inline quality scores
  • Weekly Plan        — render existing plan + generate new one from last JD report
  • Topic Suggestions  — structured market-aware suggestions + cold-test buttons

Quick-action modals (st.dialog):
  • New Note (Convert) — quality check, live preview, enrichment assistant, commit
  • Generate           — flashcards (batched), scenarios, multi-file scenarios
  • Card Review        — flip / approve / reject / skip for flashcards or scenarios
  • JD Analyzer        — single or batch gap analysis, save report, trigger aggregation

Run:
    streamlit run app_dashboard.py
"""

import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

# ── Project imports ───────────────────────────────────────────────────────────
from config import (
    DATA_DIR,
    OUTPUT_DIR,
    RAW_DIR,
    SPLIT_THRESHOLD_WORDS,
    load_jd_frequencies,
)
from pipeline.convert import convert_to_json, detect_note_sections, parse_and_save_json
from pipeline.generate import (
    build_dynamic_prompt,
    classify,
    generate_flashcards_batched,
    parse_qa,
)
from pipeline.interview import (
    append_session,
    build_interview_pool,
    get_summary_stats,
    score_answer,
)
from pipeline.jd_analyzer import (
    parse_jds,
    run_batch_analysis,
    run_gap_analysis,
    save_jd_report,
    trigger_aggregation,
)
from pipeline.quality import (
    CPM_CHEAT_SHEET,
    check_note_quality,
    generate_enrichment_questions,
    preview_extraction,
    rewrite_enriched_note,
    score_kb_entry,
)
from pipeline.scenarios import (
    generate_multifile_scenarios,
    generate_scenarios,
    parse_multifile_scenarios,
    parse_scenarios,
)
from pipeline.suggestions import (
    generate_cold_test_questions,
    generate_topic_suggestions,
)
from pipeline.weekly import generate_weekly_plan
from utils import (
    load_anki_cards,
    load_knowledge_base,
    load_prompt,
    load_scenario_cards,
    save_reviewed_cards,
)
from validation import validate_entries

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MindCI",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── st.dialog compatibility shim ──────────────────────────────────────────────
def _dialog_decorator(title):
    if hasattr(st, "dialog"):
        return st.dialog(title)
    if hasattr(st, "experimental_dialog"):
        return st.experimental_dialog(title)
    def _wrap(fn):
        def _inner(*a, **kw):
            with st.expander(title, expanded=True):
                return fn(*a, **kw)
        return _inner
    return _wrap


# ── Session-state defaults ────────────────────────────────────────────────────
def _init_state():
    defaults = {
        # Modal switching
        "active_modal": None,           # convert | generate | review | jd
        # Convert modal
        "convert_text": "",
        "convert_filename": "",
        "convert_preview": None,
        "convert_quality": None,
        "convert_enrich_questions": None,
        "convert_enrich_answers": [],
        "convert_enrich_rewritten": None,
        "convert_uploaded_files": [],   # [{name, content}] when multiple files staged
        "convert_file_idx": 0,          # which file is active in multi-file mode
        "convert_splits": None,             # list[{title, content, word_count}] | None
        "convert_splits_enabled": [],       # list[bool] — which sections are approved
        "convert_splits_strategy": "",      # heuristic label from detect_note_sections
        # Generate modal
        "gen_mode": "Flashcards",
        # Card Review modal
        "review_kind": "Flashcards",
        "review_cards": [],
        "review_idx": 0,
        "review_show_answer": False,
        # JD modal
        "jd_text": "",
        "jd_mode": "Single",
        "jd_result": None,
        "jd_batch_result": None,
        # Mock interview view
        "iv_active": False,
        "iv_pool": [],
        "iv_idx": 0,
        "iv_answer": "",
        "iv_graded": None,
        "iv_scores": [],
        # Topic suggestions
        "suggestions_data": None,
        "cold_test_results": {},        # topic → Q/A text
        "cold_test_idx": {},            # topic → current question index
        "cold_test_revealed": {},       # topic → bool (answer visible)
        # Weekly plan
        "weekly_hours": 8,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


_init_state()


# ── Dashboard data loaders ────────────────────────────────────────────────────
def load_kb_safe():
    return load_knowledge_base() or []


def load_recent_weekly_plan():
    p = Path(OUTPUT_DIR) / "weekly_plan.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def load_last_jd_report():
    p = Path(OUTPUT_DIR) / "jd_report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def current_freqs():
    """Re-read JD frequencies on every call so new reports show up live."""
    return load_jd_frequencies()  # (frequencies_dict, source_label, report_count)


# ── Mock-interview state persistence ──────────────────────────────────────────
# Snapshots in-progress interview state so a Streamlit refresh / app restart
# doesn't lose progress. File is removed when the session ends or is reset.
IV_SESSION_PATH = Path(OUTPUT_DIR) / "iv_session.json"


def _save_iv_state():
    if not st.session_state.iv_active:
        return
    snapshot = {
        "iv_active": True,
        "iv_pool":   st.session_state.iv_pool,
        "iv_idx":    st.session_state.iv_idx,
        "iv_answer": st.session_state.iv_answer,
        "iv_graded": st.session_state.iv_graded,
        "iv_scores": st.session_state.iv_scores,
    }
    try:
        IV_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        IV_SESSION_PATH.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except Exception:
        pass  # persistence is best-effort, never block the UI


def _load_iv_state():
    """Restore state from disk if no active session is in memory."""
    if st.session_state.iv_active or not IV_SESSION_PATH.exists():
        return
    try:
        snap = json.loads(IV_SESSION_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    for k, v in snap.items():
        st.session_state[k] = v


def _clear_iv_state():
    if IV_SESSION_PATH.exists():
        try:
            IV_SESSION_PATH.unlink()
        except Exception:
            pass


def market_signal_top(n=8):
    freqs, _, _ = current_freqs()
    return sorted(freqs.items(), key=lambda x: -x[1])[:n]


def quality_color(score):
    if score >= 8:
        return "#22c55e"
    if score >= 5:
        return "#f59e0b"
    return "#ef4444"


def quality_badge(score, label="quality"):
    color = quality_color(score)
    st.markdown(
        f"<span style='background:{color};color:white;padding:4px 10px;"
        f"border-radius:12px;font-weight:600;font-size:0.85rem;'>"
        f"{label}: {score}/10</span>",
        unsafe_allow_html=True,
    )


def _code_download_button(code: str, label_seed: str, key_suffix: str):
    """Add a download button for a code/config blob, with a sane filename + ext."""
    from pipeline.scenarios import guess_extension
    ext, mime = guess_extension(code)
    safe = "".join(c if c.isalnum() else "_" for c in label_seed.lower())[:40] or "snippet"
    st.download_button(
        label=f"⬇ Download as .{ext}",
        data=code,
        file_name=f"{safe}.{ext}",
        mime=mime,
        key=f"dl_{key_suffix}",
    )


def render_scenario_card(card):
    """Shared renderer for scenario cards (single or multi-file)."""
    if card.get("setup"):
        st.markdown(f"**Setup:** {card['setup']}")
    card_id = str(card.get("id", id(card)))
    if card.get("files"):
        for i, f in enumerate(card["files"]):
            fname = f.get("name", "file")
            st.markdown(f"**`{fname}`**")
            st.code(f.get("content", ""), language="text")
            _code_download_button(f.get("content", ""), fname, f"sc_{card_id}_{i}")
    elif card.get("code"):
        st.code(card["code"], language="text")
        _code_download_button(card["code"], card.get("topic") or "scenario",
                              f"sc_{card_id}")
    if card.get("question"):
        st.markdown(f"**Q:** {card['question']}")


# ── Sidebar navigation ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🧠 MindCI")
    st.caption("Personal knowledge pipeline")

    nav = st.radio(
        "View",
        ["Dashboard", "Mock Interview", "Knowledge Base",
         "Weekly Plan", "Topic Suggestions"],
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("### Quick actions")
    if st.button("📝 New Note (Convert)", use_container_width=True):
        st.session_state.active_modal = "convert"
    if st.button("⚡ Generate", use_container_width=True):
        st.session_state.active_modal = "generate"
    if st.button("🃏 Card Review", use_container_width=True):
        st.session_state.active_modal = "review"
    if st.button("🎯 JD Analyzer", use_container_width=True):
        st.session_state.active_modal = "jd"
    if st.button("📄 Resume Check", use_container_width=True):
        st.session_state.active_modal = "resume"

    st.markdown("---")
    # ── Reminder prompts ────────────────────────────────────────────────────
    # Any .md / .txt file dropped into reminder_prompts/ shows up here as a
    # tab inside the expander. `st.code` gives a built-in copy-to-clipboard
    # button so you can grab the prompt and paste it into any Claude chat.
    _prompt_files = sorted(Path("reminder_prompts").glob("*.md")) + \
                    sorted(Path("reminder_prompts").glob("*.txt"))
    if _prompt_files:
        with st.expander("📋 Reminder prompts", expanded=False):
            if len(_prompt_files) == 1:
                p = _prompt_files[0]
                st.caption(p.stem.replace("_", " "))
                st.code(p.read_text(encoding="utf-8"), language="markdown")
            else:
                tabs = st.tabs([p.stem.replace("_", " ") for p in _prompt_files])
                for tab, p in zip(tabs, _prompt_files):
                    with tab:
                        st.code(p.read_text(encoding="utf-8"), language="markdown")

    st.markdown("---")
    _, _sidebar_source, _ = current_freqs()
    st.caption(f"Market data: {_sidebar_source}")


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard view
# ══════════════════════════════════════════════════════════════════════════════
def render_dashboard():
    st.title("Dashboard")
    kb = load_kb_safe()
    stats = get_summary_stats()
    plan = load_recent_weekly_plan()

    left, right = st.columns([1.2, 1])

    with left:
        st.markdown("#### Readiness")
        if stats:
            score = stats["overall_avg"]
            quality_badge(int(round(score)), "readiness")
            st.metric(
                "Overall avg score",
                f"{round(score, 1)}/10",
                delta=f"{stats['total_sessions']} sessions",
            )
        else:
            st.info("No mock interview history yet — start a Mock Interview from the sidebar to seed this.")

        st.markdown("#### Weak spots")
        if stats and stats["weak_spots"]:
            for w in stats["weak_spots"]:
                st.markdown(
                    f"- **{w['topic']}** — avg {w['avg_score']}/10 "
                    f"across {w['attempts']} attempts"
                )
        else:
            st.caption("No persistent weak spots (need ≥2 sessions per topic).")

        st.markdown("#### Recent mock scores")
        if stats and stats["session_trend"]:
            recent = stats["session_trend"][-10:]
            chart_data = {row["date"]: row["avg"] for row in recent}
            st.bar_chart(chart_data)
        else:
            st.caption("No sessions yet.")

    with right:
        st.markdown("#### Today's focus")
        if plan:
            preview = plan.strip().splitlines()[:6]
            st.markdown("\n".join(preview))
            with st.expander("Full weekly plan"):
                st.markdown(plan)
        else:
            st.caption("Run JD Analyzer → generate a weekly plan to populate this.")

        _, _dash_source, _dash_count = current_freqs()
        st.markdown("#### Market signal")
        st.caption(f"Top JD-frequency skills ({_dash_source})")
        for skill, freq in market_signal_top():
            st.markdown(f"- {skill} — {int(freq*100)}%")

    st.markdown("---")
    cols = st.columns(4)
    cols[0].metric("KB entries", len(kb))
    cols[1].metric("Sessions", stats["total_sessions"] if stats else 0)
    cols[2].metric("Questions answered", stats["total_questions"] if stats else 0)
    cols[3].metric("JD reports", _dash_count)

    # Resume coverage tile — only renders if a resume has been parsed
    from pipeline.resume_check import compute_coverage, load_resume_claims
    _claims = load_resume_claims()
    if _claims:
        _coverage = compute_coverage(_claims, kb)
        t = _coverage["totals"]
        gap = t["claims"] - t["covered"]
        delta_str = f"-{gap} unbacked" if gap else "all backed"
        st.markdown("#### Resume reality check")
        st.metric(
            "Resume claims backed by notes",
            f"{t['covered']}/{t['claims']}",
            delta=delta_str,
            delta_color="inverse",
        )
        if gap:
            st.caption("Open the **📄 Resume Check** modal in the sidebar to see specific gaps.")

    # API spend telemetry — recorded per call in pipeline/_client.py
    from pipeline._client import get_usage_summary
    usage = get_usage_summary(days=7)
    cache = usage["cache"]
    total_lookups = cache["hits"] + cache["misses"]
    cache_str = (
        f" · cache: {cache['hits']}/{total_lookups} hits "
        f"({int(100 * cache['hits'] / total_lookups)}%)"
        if total_lookups else ""
    )
    st.caption(
        f"API today: **${usage['today']['cost_usd']:.2f}** "
        f"({usage['today']['calls']} call{'s' if usage['today']['calls'] != 1 else ''}, "
        f"{usage['today']['input_tokens'] + usage['today']['output_tokens']:,} tokens) · "
        f"7-day: **${usage['window']['cost_usd']:.2f}** "
        f"({usage['window']['calls']} calls)"
        f"{cache_str}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Mock Interview view (multi-step, full-page, persistent across reruns)
# ══════════════════════════════════════════════════════════════════════════════
def render_mock_interview():
    st.title("Mock Interview")
    _load_iv_state()  # rehydrate from disk if no in-memory session
    stats = get_summary_stats()

    # Header stats line
    if stats:
        cols = st.columns(4)
        cols[0].metric("Sessions", stats["total_sessions"])
        cols[1].metric("Questions answered", stats["total_questions"])
        cols[2].metric("Overall avg", f"{stats['overall_avg']}/10")
        cols[3].metric("Weak spots", len(stats["weak_spots"]))

    # ── Start screen ─────────────────────────────────────────────────────────
    if not st.session_state.iv_active:
        st.markdown("#### Start a new session")
        n = st.slider("How many questions?", 3, 15, 8)
        if st.button("Build pool & start", type="primary"):
            with st.spinner("Building question pool…"):
                pool = build_interview_pool(n)
            if not pool:
                st.error("Pool is empty — generate flashcards or scenarios first.")
                return
            st.session_state.iv_pool = pool[:n]
            st.session_state.iv_idx = 0
            st.session_state.iv_scores = []
            st.session_state.iv_answer = ""
            st.session_state.iv_graded = None
            st.session_state.iv_active = True
            _save_iv_state()
            st.rerun()
        return

    # ── Active session ───────────────────────────────────────────────────────
    idx = st.session_state.iv_idx
    pool = st.session_state.iv_pool

    if idx < len(pool):
        q = pool[idx]
        progress = (idx) / len(pool)
        st.progress(progress, text=f"Question {idx+1} of {len(pool)}")

        st.markdown(f"##### Question {idx+1} of {len(pool)} — `{q.get('topic', '?')}` [{q['type']}]")
        if q.get("setup"):
            st.markdown(f"**Setup:** {q['setup']}")
        if q.get("files"):
            for fi, f in enumerate(q["files"]):
                fname = f.get("name", "file")
                st.markdown(f"**`{fname}`**")
                st.code(f.get("content", ""), language="text")
                _code_download_button(f.get("content", ""), fname, f"iv_{idx}_{fi}")
        elif q.get("code"):
            st.code(q["code"], language="text")
            _code_download_button(q["code"], q.get("topic") or "interview", f"iv_{idx}")
        st.markdown(f"**Q:** {q['question']}")

        if st.session_state.iv_graded is None:
            user_ans = st.text_area(
                "Your answer",
                value=st.session_state.iv_answer,
                key=f"iv_ans_{idx}",
                height=160,
            )
            st.session_state.iv_answer = user_ans
            cols = st.columns(3)
            with cols[0]:
                if st.button("Submit answer", type="primary"):
                    with st.spinner("Grading…"):
                        try:
                            grade = score_answer(
                                q["question"], q.get("code", ""),
                                q["answer"], user_ans, q.get("topic", ""),
                            )
                            st.session_state.iv_graded = grade
                            _save_iv_state()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Grading failed: {e}")
            with cols[1]:
                if st.button("Skip"):
                    st.session_state.iv_scores.append({
                        "score": 0, "verdict": "Skipped",
                        "topic": q.get("topic", ""), "type": q["type"],
                    })
                    st.session_state.iv_idx += 1
                    st.session_state.iv_answer = ""
                    st.session_state.iv_graded = None
                    _save_iv_state()
                    st.rerun()
            with cols[2]:
                if st.button("End session early"):
                    st.session_state.iv_idx = len(pool)
                    _save_iv_state()
                    st.rerun()
        else:
            grade = st.session_state.iv_graded
            verdict = grade.get("verdict", "")
            st.markdown(f"**Verdict:** {verdict} — **{grade['score']}/10**")
            st.markdown("**Your answer**")
            st.write(st.session_state.iv_answer)
            if grade.get("what_they_got_right"):
                st.success(f"What you got right: {grade['what_they_got_right']}")
            if grade.get("what_they_missed"):
                st.warning(f"What you missed: {grade['what_they_missed']}")
            if grade.get("coaching_note"):
                st.info(f"Coaching note: {grade['coaching_note']}")
            with st.expander("Model answer"):
                st.markdown(q["answer"])

            if st.button("Next →", type="primary"):
                st.session_state.iv_scores.append({
                    "score": grade["score"],
                    "verdict": grade["verdict"],
                    "topic": q.get("topic", ""),
                    "type": q["type"],
                    "coaching_note": grade.get("coaching_note", ""),
                })
                st.session_state.iv_idx += 1
                st.session_state.iv_answer = ""
                st.session_state.iv_graded = None
                _save_iv_state()
                st.rerun()
        return

    # ── End screen ───────────────────────────────────────────────────────────
    scores = st.session_state.iv_scores
    if not scores:
        st.warning("Session ended with no answers recorded.")
        if st.button("New session"):
            st.session_state.iv_active = False
            _clear_iv_state()
            st.rerun()
        return

    total = sum(s["score"] for s in scores)
    max_total = len(scores) * 10
    pct = int((total / max_total) * 100) if max_total else 0
    st.success(f"Session complete — {total}/{max_total} ({pct}%)")
    st.progress(pct / 100)

    verdict_counts = {}
    for s in scores:
        verdict_counts[s["verdict"]] = verdict_counts.get(s["verdict"], 0) + 1
    if verdict_counts:
        cols = st.columns(len(verdict_counts))
        for i, (v, c) in enumerate(verdict_counts.items()):
            cols[i].metric(v, c)

    st.markdown("#### Question breakdown")
    for i, s in enumerate(scores):
        bar = "█" * s["score"] + "░" * (10 - s["score"])
        st.markdown(
            f"**Q{i+1}** `{s['topic']}` [{s['type']}] — {s['score']}/10 [{bar}] {s['verdict']}"
        )
        if s.get("coaching_note") and s["verdict"] not in ("Strong", "Skipped"):
            st.caption(f"    Remember: {s['coaching_note']}")

    weak = [s for s in scores if s["verdict"] in ("Needs Work", "Incorrect", "Skipped")]
    if weak:
        st.markdown("#### Focus areas for next session")
        for s in weak:
            st.markdown(f"- **{s['topic']}** ({s['type']})")

    # Persist report
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_score": total,
        "max_score": max_total,
        "pct": pct,
        "questions": scores,
    }
    with open(Path(OUTPUT_DIR) / "interview_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    append_session(report)

    # Adaptive recalibration — update each entry's auto_confidence from rolling
    # interview history. Tight trigger surface: only after a session is committed.
    try:
        from pipeline.calibration import recalibrate_kb
        changes = recalibrate_kb()
        if changes:
            st.markdown("#### Calibration update")
            for c in changes:
                arrow = "↑" if ("Low", "Medium", "High").index(c["new"]) > ("Low", "Medium", "High").index(c["old"]) else "↓"
                st.caption(
                    f"{arrow} **{c['label']}**  {c['old']} → {c['new']}  "
                    f"(avg {c['avg']}/10 over {c['samples']} attempts)"
                )
    except Exception as e:
        st.caption(f"Calibration skipped: {e}")

    # Session is complete and persisted to history; remove the in-progress snapshot.
    _clear_iv_state()

    if st.button("New session", type="primary"):
        st.session_state.iv_active = False
        st.session_state.iv_pool = []
        st.session_state.iv_idx = 0
        st.session_state.iv_scores = []
        st.session_state.iv_answer = ""
        st.session_state.iv_graded = None
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Knowledge Base view (with inline quality scores)
# ══════════════════════════════════════════════════════════════════════════════
def render_knowledge_base():
    st.title("Knowledge Base")
    kb = load_kb_safe()
    if not kb:
        st.info("No entries yet. Use **New Note (Convert)** to add some.")
        return

    # Filters
    cols = st.columns(2)
    type_filter = cols[0].multiselect(
        "Type", options=sorted({e.get("type", "?") for e in kb}),
    )
    conf_filter = cols[1].multiselect(
        "Confidence", options=["Low", "Medium", "High"],
    )
    from pipeline.calibration import effective_confidence
    filtered = kb
    if type_filter:
        filtered = [e for e in filtered if e.get("type") in type_filter]
    if conf_filter:
        # Filter on effective (auto if set, else manual) so the user sees what
        # the system is actually using.
        filtered = [e for e in filtered if effective_confidence(e) in conf_filter]

    st.caption(f"{len(filtered)} of {len(kb)} entries")

    by_type = {}
    for e in filtered:
        by_type.setdefault(e.get("type", "?"), []).append(e)

    for t, entries in by_type.items():
        st.markdown(f"### {t.title()} ({len(entries)})")
        for e in entries:
            label = (e.get("topic") or e.get("concept") or
                     e.get("tool") or e.get("error", "—"))
            q = score_kb_entry(e)
            eff = effective_confidence(e)
            manual = e.get("confidence", "?")
            # Surface a recalibration flag in the header when auto ≠ manual
            if e.get("auto_confidence") and e["auto_confidence"] != manual:
                conf_str = f"conf **{eff}** (manual: {manual} · auto-updated)"
            else:
                conf_str = f"conf {eff}"
            with st.expander(f"{label}  ·  {conf_str}  ·  quality {q['score']}/10"):
                quality_badge(q["score"])
                if e.get("auto_confidence") and e["auto_confidence"] != manual:
                    ts = e.get("confidence_updated_at", "")
                    st.caption(f"Auto-confidence: **{e['auto_confidence']}** (manual seed: {manual}) · updated {ts}")
                # Confidence sparkline — Unicode block chars, last 8 transitions
                history = e.get("confidence_history") or []
                if history:
                    block_for = {"Low": "▂", "Medium": "▅", "High": "█"}
                    recent = history[-8:]
                    spark = "".join(block_for.get(tier, "·") for _, tier in recent)
                    tiers = " → ".join(tier for _, tier in recent)
                    st.caption(f"Confidence over last {len(recent)} updates: `{spark}`  ({tiers})")
                if q["issues"]:
                    st.warning("Enrichment suggestions: " + "; ".join(q["issues"]))
                st.json(e)


# ══════════════════════════════════════════════════════════════════════════════
# Weekly Plan view (render existing + generate new)
# ══════════════════════════════════════════════════════════════════════════════
def render_weekly_plan():
    from pipeline.weekly_progress import (
        archived_weeks,
        load_progress,
        parse_checklist,
        save_progress,
        week_completion,
    )

    st.title("Weekly Plan")
    last_jd = load_last_jd_report()

    # ── Week selector ───────────────────────────────────────────────────────
    weeks = archived_weeks()
    options = ["Current (weekly_plan.md)"] + weeks
    chosen = st.selectbox("Select week", options, key="weekly_plan_week")

    if chosen == "Current (weekly_plan.md)":
        plan = load_recent_weekly_plan()
        week_key = ""  # current plan has no archive key, no checkboxes
    else:
        path = Path(OUTPUT_DIR) / f"weekly_plan_{chosen}.md"
        plan = path.read_text(encoding="utf-8") if path.exists() else None
        week_key = chosen

    if plan and week_key:
        items = parse_checklist(plan)
        if items:
            done, total = week_completion(week_key, plan)
            pct = int((done / total) * 100) if total else 0
            st.markdown(f"**{chosen}** — {done}/{total} tasks done ({pct}%)")
            saved = load_progress().get(week_key, {})
            for idx, text, baseline in items:
                checked = saved.get(str(idx), baseline)
                new_state = st.checkbox(text, value=checked, key=f"wkpl_{week_key}_{idx}")
                if new_state != checked:
                    save_progress(week_key, idx, new_state)
                    st.rerun()
            with st.expander("Full plan markdown"):
                st.markdown(plan)
        else:
            st.markdown(plan)  # plan with no parseable tasks — just render
    elif plan:
        st.markdown(plan)
    else:
        st.caption(f"No plan saved for {chosen} yet.")

    st.markdown("---")
    st.markdown("#### Generate a new plan")
    if not last_jd:
        st.info("Run a JD gap analysis first — the weekly plan needs your last JD report's priority gaps.")
        return

    role = last_jd.get("role_title") or (last_jd.get("aggregate", {}) or {}).get("best_fit_role") or "Cloud Engineer"
    gaps = last_jd.get("priority_gaps") or []
    if not gaps and last_jd.get("aggregate"):
        # Fall back to batch aggregate gaps when running off batch results
        gaps = [
            {"domain": g["skill"], "urgency": g.get("urgency", "Medium"), "action": "Close this market-frequent gap."}
            for g in last_jd["aggregate"].get("most_common_gaps", [])[:2]
        ]
    if not gaps:
        st.warning("Last JD report has no priority gaps to plan against.")
        return

    st.caption(f"Target role: **{role}** · top gaps: " + ", ".join(g["domain"] for g in gaps[:2]))
    hours = st.slider("Available study hours this week",
                      4, 30, st.session_state.weekly_hours, key="weekly_hours_slider")
    st.session_state.weekly_hours = hours

    if st.button("Generate weekly plan", type="primary"):
        with st.spinner("Asking Claude…"):
            try:
                text = generate_weekly_plan(gaps, role, hours)
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                Path(OUTPUT_DIR, "weekly_plan.md").write_text(text, encoding="utf-8")
                st.success("Saved to output/weekly_plan.md")
                st.markdown(text)
            except Exception as e:
                st.error(f"Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Topic Suggestions view (structured + cold test)
# ══════════════════════════════════════════════════════════════════════════════
def render_topic_suggestions():
    st.title("Topic Suggestions")
    _, _ts_source, _ = current_freqs()
    st.caption(f"Active frequency source: **{_ts_source}**")

    kb = load_kb_safe()
    if not kb:
        st.info("Need at least one KB entry.")
        return

    last_jd = load_last_jd_report()

    if st.button("Generate suggestions", type="primary"):
        with st.spinner("Comparing your KB against market demand…"):
            try:
                st.session_state.suggestions_data = generate_topic_suggestions(kb, last_jd)
            except Exception as e:
                st.error(f"Error: {e}")

    data = st.session_state.suggestions_data
    if not data:
        return

    if data.get("summary"):
        st.markdown(f"_{data['summary']}_")

    def _section(title, items, freq_key="market_frequency"):
        if not items:
            return
        st.markdown(f"#### {title}")
        for item in items:
            topic = item.get("topic", "?")
            freq = item.get(freq_key)
            header = f"**{topic}**"
            if freq is not None:
                header += f" · market freq {int(freq*100)}%"
            if item.get("current_confidence"):
                header += f" · current conf {item['current_confidence']}"
            st.markdown(header)
            if item.get("reason"):
                st.caption(item["reason"])
            if item.get("suggested_note_prompt"):
                with st.expander("Suggested note prompt"):
                    st.code(item["suggested_note_prompt"], language="text")

            cold_key = f"cold__{topic}"
            if st.button("Cold test", key=cold_key):
                with st.spinner(f"Generating 3 cold-test questions for {topic}…"):
                    try:
                        urgency = "High" if (freq or 0) >= 0.65 else "Medium"
                        out = generate_cold_test_questions(topic, freq or 0, urgency)
                        st.session_state.cold_test_results[topic] = out
                    except Exception as e:
                        st.error(f"Cold test failed: {e}")
            if topic in st.session_state.cold_test_results:
                # Parse Q/A pairs from raw text
                import re as _re
                raw = st.session_state.cold_test_results[topic]
                pairs = _re.findall(
                    r"Q:\s*(.+?)\s*A:\s*(.+?)(?=\nQ:|\Z)",
                    raw, _re.DOTALL
                )
                if not pairs:
                    with st.expander(f"Cold test for {topic}"):
                        st.markdown(raw)
                else:
                    n = len(pairs)
                    idx = st.session_state.cold_test_idx.get(topic, 0)
                    idx = max(0, min(idx, n - 1))
                    revealed = st.session_state.cold_test_revealed.get(topic, False)

                    with st.container(border=True):
                        st.caption(f"Cold test · {topic}  —  question {idx + 1} of {n}")
                        st.markdown(f"**Q: {pairs[idx][0].strip()}**")

                        if not revealed:
                            if st.button("Reveal answer", key=f"cold_reveal__{topic}"):
                                st.session_state.cold_test_revealed[topic] = True
                                st.rerun()
                        else:
                            st.info(pairs[idx][1].strip())
                            nav_cols = st.columns([1, 1, 4])
                            if idx > 0:
                                if nav_cols[0].button("← Prev", key=f"cold_prev__{topic}"):
                                    st.session_state.cold_test_idx[topic] = idx - 1
                                    st.session_state.cold_test_revealed[topic] = False
                                    st.rerun()
                            if idx < n - 1:
                                if nav_cols[1].button("Next →", key=f"cold_next__{topic}"):
                                    st.session_state.cold_test_idx[topic] = idx + 1
                                    st.session_state.cold_test_revealed[topic] = False
                                    st.rerun()
            st.markdown("---")

    _section("Uncovered, high demand", data.get("uncovered_high_demand", []))
    _section("In your notes but weak", data.get("weak_but_in_demand", []))
    _section("Emerging — worth watching", data.get("emerging_to_watch", []),
             freq_key=None)


# ══════════════════════════════════════════════════════════════════════════════
# Modal: Convert (with enrichment assistant)
# ══════════════════════════════════════════════════════════════════════════════
@_dialog_decorator("📝 New Note — Convert")
def modal_convert():
    st.markdown("##### Cognitive Payload Markers — drop these into your note")
    st.code(CPM_CHEAT_SHEET, language="text")

    text = st.text_area(
        "Paste raw note OR upload a .txt / .md file below",
        value=st.session_state.convert_text,
        height=240,
        key="modal_convert_text",
    )
    uploaded_files = st.file_uploader(
        "…or upload .txt / .md (hold Ctrl/⌘ to select multiple)",
        type=["txt", "md"],
        accept_multiple_files=True,
        key="modal_convert_upload",
    )
    if uploaded_files:
        file_data = [
            {"name": f.name, "content": f.read().decode("utf-8", errors="ignore")}
            for f in uploaded_files
        ]
        if len(file_data) == 1:
            # Single upload — same behaviour as before
            text = file_data[0]["content"]
            st.session_state.convert_filename = file_data[0]["name"]
            st.session_state.convert_uploaded_files = file_data
        else:
            # Multiple uploads: show a file selector; interactive tools work on
            # whichever file is "active"; batch convert handles all at once.
            st.session_state.convert_uploaded_files = file_data
            names = [f["name"] for f in file_data]
            idx = min(st.session_state.convert_file_idx, len(file_data) - 1)
            selected_name = st.selectbox(
                f"Active file — {len(file_data)} uploaded"
                " (edit/convert individually, or batch-convert all below):",
                names,
                index=idx,
                key="modal_conv_file_pick",
            )
            selected_idx = names.index(selected_name)
            if selected_idx != st.session_state.convert_file_idx:
                # File switched — clear per-note state so we start fresh
                st.session_state.convert_file_idx = selected_idx
                st.session_state.convert_preview = None
                st.session_state.convert_quality = None
                st.session_state.convert_enrich_questions = None
                st.session_state.convert_enrich_answers = []
                st.session_state.convert_enrich_rewritten = None
                st.session_state.convert_splits = None
                st.session_state.convert_splits_enabled = []
                st.session_state.convert_splits_strategy = ""
            text = file_data[selected_idx]["content"]
            st.session_state.convert_filename = file_data[selected_idx]["name"]
    st.session_state.convert_text = text

    # ── URL ingest via Jina Reader ──────────────────────────────────────────
    url_cols = st.columns([5, 1])
    url_input = url_cols[0].text_input(
        "…or paste a URL (docs page, blog post, AWS announcement)",
        placeholder="https://aws.amazon.com/blogs/...",
        key="modal_convert_url",
    )
    if url_cols[1].button("Fetch", key="m_conv_fetch_url",
                          disabled=not url_input.strip()):
        from pipeline.convert import fetch_url_as_markdown
        with st.spinner(f"Fetching {url_input}…"):
            try:
                fetched = fetch_url_as_markdown(url_input.strip())
                st.session_state.convert_text = fetched
                st.session_state.convert_filename = url_input.strip()
                text = fetched
                st.success(f"Fetched {len(fetched):,} chars. Click Convert & save when ready.")
                st.rerun()
            except Exception as e:
                st.error(f"Fetch failed: {e}")

    # If this looks like a markdown note with frontmatter, extract metadata
    # so the user (and Claude) can see what's pre-known. Pure read; we don't
    # mutate st.session_state.convert_text here.
    from pipeline.convert import parse_markdown_with_frontmatter
    fm_meta, fm_body = parse_markdown_with_frontmatter(text)
    if fm_meta:
        st.caption("Detected frontmatter: " +
                   " · ".join(f"`{k}={v}`" for k, v in fm_meta.items()))

    # ── Note splitting (shown when note exceeds threshold) ─────────────────
    _word_count = len(text.split()) if text.strip() else 0
    if _word_count >= SPLIT_THRESHOLD_WORDS:
        _sp_col1, _sp_col2 = st.columns([3, 1])
        _sp_col1.info(
            f"This note is long (~{_word_count:,} words). "
            "Detect sections to convert each chunk separately.",
            icon="📄",
        )
        with _sp_col1:
            if st.button("Detect sections", key="m_conv_detect_splits"):
                sections, strategy = detect_note_sections(text)
                st.session_state.convert_splits = sections
                st.session_state.convert_splits_enabled = [True] * len(sections)
                st.session_state.convert_splits_strategy = strategy
                st.rerun()
        if st.session_state.convert_splits is not None:
            with _sp_col2:
                if st.button("Clear splits", key="m_conv_clear_splits"):
                    st.session_state.convert_splits = None
                    st.session_state.convert_splits_enabled = []
                    st.rerun()

    if st.session_state.convert_splits is not None:
        _splits = st.session_state.convert_splits
        _enabled = list(st.session_state.convert_splits_enabled)
        if len(_enabled) != len(_splits):
            _enabled = [True] * len(_splits)

        st.markdown(f"##### {len(_splits)} detected sections — select which to convert")
        if st.session_state.convert_splits_strategy:
            st.caption(f"Split on: {st.session_state.convert_splits_strategy}")
        for _si, _sec in enumerate(_splits):
            _c1, _c2 = st.columns([1, 12])
            _enabled[_si] = _c1.checkbox(
                "", value=_enabled[_si], key=f"m_conv_split_en_{_si}",
                label_visibility="collapsed",
            )
            _c2.markdown(f"**{_sec['title']}** · {_sec['word_count']:,} words")
            with st.expander("Preview", expanded=False):
                st.text(_sec["content"][:400] +
                        ("…" if len(_sec["content"]) > 400 else ""))
        st.session_state.convert_splits_enabled = _enabled

        _n_sel = sum(_enabled)
        if st.button(
            f"Convert {_n_sel} selected section{'s' if _n_sel != 1 else ''}",
            type="primary", key="m_conv_splits_commit", disabled=_n_sel == 0,
        ):
            _split_saved = 0
            _split_invalid = 0
            _base_fname = (st.session_state.convert_filename
                           or f"note_{datetime.now():%Y%m%d_%H%M%S}")
            os.makedirs(RAW_DIR, exist_ok=True)
            with st.spinner(f"Converting {_n_sel} sections with Claude…"):
                for _si, _sec in enumerate(_splits):
                    if not _enabled[_si]:
                        continue
                    _sec_fname = f"{_base_fname}_part{_si + 1}.txt"
                    Path(RAW_DIR, _sec_fname).write_text(
                        _sec["content"], encoding="utf-8"
                    )
                    try:
                        _payload = (f"--- SOURCE: {_sec_fname} ---\n\n"
                                    f"{_sec['content']}")
                        _raw = convert_to_json(_payload)
                        _parsed_s, _report_s = parse_and_save_json(_raw)
                        _split_saved += len(_parsed_s)
                        _split_invalid += _report_s["invalid_count"]
                        if _report_s.get("salvage_warning"):
                            st.warning(
                                f"'{_sec['title']}': {_report_s['salvage_warning']}"
                            )
                    except Exception as _exc:
                        st.warning(f"'{_sec['title']}': failed — {_exc}")
            st.success(
                f"Saved {_split_saved} entries from {_n_sel} sections "
                f"to {DATA_DIR}/structured.json"
            )
            if _split_invalid:
                st.warning(f"{_split_invalid} invalid entries → data/invalid_entries.json")
            st.session_state.convert_splits = None
            st.session_state.convert_splits_enabled = []
            st.session_state.convert_preview = None
            st.session_state.convert_quality = None

        st.markdown("---")

    cols = st.columns(4)
    with cols[0]:
        do_quality = st.button("Quality check", key="m_conv_q")
    with cols[1]:
        do_preview = st.button("Preview extraction", key="m_conv_p")
    with cols[2]:
        do_enrich = st.button("Enrichment assistant", key="m_conv_e")
    with cols[3]:
        do_commit = st.button("Convert & save", type="primary", key="m_conv_c")

    # ── Batch convert (visible only when multiple files are staged) ─────────
    _staged = st.session_state.convert_uploaded_files
    if len(_staged) > 1:
        st.caption(
            f"{len(_staged)} files staged — convert them one-by-one above, "
            "or batch-convert all at once (skips quality/enrichment steps)."
        )
        if st.button(f"⚡ Batch convert all {len(_staged)} files",
                     key="m_conv_batch"):
            total_saved = 0
            total_invalid = 0
            os.makedirs(RAW_DIR, exist_ok=True)
            with st.spinner(f"Converting {len(_staged)} files with Claude…"):
                for _fdata in _staged:
                    try:
                        _fname = _fdata["name"]
                        _fcontent = _fdata["content"]
                        Path(RAW_DIR, _fname).write_text(_fcontent, encoding="utf-8")
                        _fm_meta, _fm_body = parse_markdown_with_frontmatter(_fcontent)
                        if _fm_meta:
                            _hint = "\n".join(f"{k}: {v}" for k, v in _fm_meta.items())
                            _payload = (
                                f"--- SOURCE: {_fname} ---\n"
                                f"--- KNOWN METADATA (use as-is, don't override) ---\n"
                                f"{_hint}\n"
                                f"--- NOTE BODY ---\n{_fm_body}"
                            )
                        else:
                            _payload = f"--- SOURCE: {_fname} ---\n\n{_fcontent}"
                        _raw = convert_to_json(_payload)
                        _parsed, _report = parse_and_save_json(_raw)
                        total_saved += len(_parsed)
                        total_invalid += _report["invalid_count"]
                        if _report.get("salvage_warning"):
                            st.warning(f"{_fname}: {_report['salvage_warning']}")
                    except Exception as _exc:
                        st.warning(f"{_fdata['name']}: failed — {_exc}")
            st.success(
                f"Batch complete — {total_saved} entries saved "
                f"across {len(_staged)} files."
            )
            if total_invalid:
                st.warning(f"{total_invalid} invalid entries → data/invalid_entries.json")
            st.session_state.convert_uploaded_files = []
            st.session_state.convert_file_idx = 0
            st.session_state.convert_preview = None
            st.session_state.convert_quality = None

    # Quality
    if do_quality and text.strip():
        st.session_state.convert_quality = check_note_quality(
            st.session_state.convert_filename or "note.txt", text
        )
    if st.session_state.convert_quality:
        q = st.session_state.convert_quality
        quality_badge(q["score"])
        if q["issues"]:
            st.warning("Issues:")
            for i in q["issues"]:
                st.markdown(f"- {i}")
        if q["passes"]:
            with st.expander(f"{len(q['passes'])} checks passed"):
                for p in q["passes"]:
                    st.markdown(f"- {p}")

    # Preview
    if do_preview and text.strip():
        with st.spinner("Previewing fields Claude would extract…"):
            try:
                st.session_state.convert_preview = preview_extraction(text)
            except Exception as e:
                st.error(f"Preview failed: {e}")
    if st.session_state.convert_preview:
        prev = st.session_state.convert_preview
        st.markdown("##### Live preview")
        st.markdown(
            f"**Type:** `{prev.get('type', '?')}`  ·  **Label:** {prev.get('label', '—')}"
        )
        st.json(prev.get("fields", {}))
        if prev.get("missing"):
            st.warning("Likely missing/weak: " + ", ".join(prev["missing"]))
        if prev.get("detected_markers"):
            st.caption("Detected markers: " + " ".join(prev["detected_markers"]))
        candidate = {"type": prev.get("type", ""), **prev.get("fields", {})}
        _, invalid, warnings = validate_entries([candidate])
        if invalid:
            st.error("Validation errors (must fix before saving):")
            for inv in invalid:
                for err in inv["errors"]:
                    st.markdown(f"- {err}")
        if warnings:
            with st.expander(f"{len(warnings)} soft warnings"):
                for w in warnings:
                    st.caption(", ".join(w["warnings"]))

    # Enrichment assistant
    if do_enrich and text.strip():
        with st.spinner("Asking Claude what's missing…"):
            try:
                qs = generate_enrichment_questions(text)
                st.session_state.convert_enrich_questions = qs
                st.session_state.convert_enrich_answers = ["" for _ in qs]
                st.session_state.convert_enrich_rewritten = None
            except Exception as e:
                st.error(f"Enrichment failed: {e}")

    if st.session_state.convert_enrich_questions:
        st.markdown("##### Enrichment questions")
        answers = list(st.session_state.convert_enrich_answers)
        for i, q in enumerate(st.session_state.convert_enrich_questions):
            answers[i] = st.text_area(
                f"Q{i+1}: {q}", value=answers[i], height=80, key=f"m_enrich_{i}",
            )
        st.session_state.convert_enrich_answers = answers
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Rewrite note", key="m_conv_rewrite"):
                with st.spinner("Rewriting…"):
                    try:
                        st.session_state.convert_enrich_rewritten = rewrite_enriched_note(
                            text,
                            st.session_state.convert_enrich_questions,
                            answers,
                        )
                    except Exception as e:
                        st.error(f"Rewrite failed: {e}")
        if st.session_state.convert_enrich_rewritten:
            st.markdown("##### Rewritten note")
            st.code(st.session_state.convert_enrich_rewritten, language="text")
            with c2:
                if st.button("Use rewritten as new note text", key="m_conv_use_rewrite"):
                    st.session_state.convert_text = st.session_state.convert_enrich_rewritten
                    st.session_state.convert_enrich_questions = None
                    st.session_state.convert_enrich_answers = []
                    st.session_state.convert_enrich_rewritten = None
                    st.rerun()

    # Commit
    if do_commit and text.strip():
        os.makedirs(RAW_DIR, exist_ok=True)
        fname = st.session_state.convert_filename or f"note_{datetime.now():%Y%m%d_%H%M%S}.txt"
        Path(RAW_DIR, fname).write_text(text, encoding="utf-8")
        with st.spinner("Converting with Claude…"):
            try:
                # If frontmatter is present, hand Claude the metadata + body
                # so any pre-known fields don't need to be re-inferred.
                if fm_meta:
                    hint_lines = "\n".join(f"{k}: {v}" for k, v in fm_meta.items())
                    payload = (f"--- SOURCE: {fname} ---\n"
                               f"--- KNOWN METADATA (use as-is, don't override) ---\n"
                               f"{hint_lines}\n"
                               f"--- NOTE BODY ---\n{fm_body}")
                else:
                    payload = f"--- SOURCE: {fname} ---\n\n{text}"
                raw = convert_to_json(payload)
                parsed, report = parse_and_save_json(raw)
                st.success(f"Saved {len(parsed)} entries to {DATA_DIR}/structured.json")
                if report.get("salvage_warning"):
                    st.warning(f"⚠️ Truncated response — {report['salvage_warning']}")
                if report["invalid_count"]:
                    st.warning(f"{report['invalid_count']} invalid → data/invalid_entries.json")
                if report["warning_count"]:
                    with st.expander(f"{report['warning_count']} soft warnings"):
                        for w in report["warnings"]:
                            st.caption(f"{w['label']}: {', '.join(w['warnings'])}")
                st.session_state.convert_preview = None
                st.session_state.convert_quality = None
            except Exception as e:
                st.error(f"Convert failed: {e}")

    if st.button("Close", key="m_conv_close"):
        st.session_state.active_modal = None
        st.session_state.convert_uploaded_files = []
        st.session_state.convert_file_idx = 0
        st.session_state.convert_splits = None
        st.session_state.convert_splits_enabled = []
        st.session_state.convert_splits_strategy = ""
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Modal: Generate (flashcards / scenarios / multi-file scenarios)
# ══════════════════════════════════════════════════════════════════════════════
@_dialog_decorator("⚡ Generate")
def modal_generate():
    kb = load_kb_safe()
    if not kb:
        st.info("Convert a note first.")
        if st.button("Close", key="m_gen_close_empty"):
            st.session_state.active_modal = None
            st.rerun()
        return

    st.write(f"{len(kb)} entries in KB.")
    mode = st.radio(
        "Mode", ["Flashcards", "Scenarios (single file)", "Scenarios (multi-file)"],
        horizontal=True, key="modal_gen_mode",
    )

    # Filters
    c1, c2 = st.columns(2)
    type_pick = c1.multiselect(
        "Filter by type", sorted({e.get("type", "?") for e in kb}),
    )
    conf_pick = c2.multiselect(
        "Filter by confidence", ["Low", "Medium", "High"],
    )
    pool = kb
    if type_pick:
        pool = [e for e in pool if e.get("type") in type_pick]
    if conf_pick:
        pool = [e for e in pool if e.get("confidence") in conf_pick]

    st.caption(f"{len(pool)} entries match.")

    if mode == "Flashcards":
        if st.button("Generate flashcards (batched)", type="primary", key="m_gen_run_fc"):
            prompts = {
                "project":       load_prompt("prompts/project.txt"),
                "certification": load_prompt("prompts/cert.txt"),
                "exploration":   load_prompt("prompts/explore.txt"),
            }
            # Group by type so each batch shares the right base prompt.
            by_type = {"project": [], "certification": [], "exploration": []}
            for e in pool:
                by_type.setdefault(e.get("type", "exploration"), []).append(e)

            md_output = ""
            anki_rows = []
            with st.spinner("Generating flashcards…"):
                for entry_type, entries in by_type.items():
                    if not entries:
                        continue
                    base = prompts.get(entry_type, prompts["exploration"])
                    try:
                        results = generate_flashcards_batched(entries, base)
                    except Exception as e:
                        st.warning(f"{entry_type}: batch failed — {e}")
                        continue
                    for entry, cards in results:
                        tag = classify(entry)
                        category = entry.get("category", entry.get("tool", "general"))
                        confidence = entry.get("confidence", "Low")
                        difficulty = entry.get("difficulty", "")
                        md_output += f"\n\n## [{tag}] {entry_type.upper()} ({category})\n"
                        for q, a in cards:
                            md_output += f"Q: {q}\nA: {a}\n\n"
                            anki_rows.append((q, a, f"{tag}::{entry_type}::{category}",
                                              difficulty, confidence))

            os.makedirs(OUTPUT_DIR, exist_ok=True)
            Path(OUTPUT_DIR, "questions.md").write_text(md_output, encoding="utf-8")
            with Path(OUTPUT_DIR, "anki.csv").open("w", encoding="utf-8") as f:
                for q, a, tags, diff, conf in anki_rows:
                    f.write(f"{q}\t{a}\t{tags}\t{diff}\t{conf}\n")
            st.success(f"Generated {len(anki_rows)} flashcards → {OUTPUT_DIR}/anki.csv")

    elif mode == "Scenarios (single file)":
        if st.button("Generate scenarios", type="primary", key="m_gen_run_sc"):
            all_scenarios = []
            with st.spinner("Generating scenarios…"):
                for entry in pool:
                    try:
                        raw = generate_scenarios(entry)
                        parsed = parse_scenarios(raw)
                        for p in parsed:
                            label = (entry.get("topic") or entry.get("concept")
                                     or entry.get("tool") or entry.get("error", "—"))
                            p["topic"] = label
                            p["confidence"] = entry.get("confidence", "Low")
                        all_scenarios.extend(parsed)
                    except Exception as e:
                        st.warning(f"Skipped one entry: {e}")
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = Path(OUTPUT_DIR) / "scenarios.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(all_scenarios, f, indent=2)
            st.success(f"Saved {len(all_scenarios)} scenarios to {out_path}")

    else:  # Multi-file
        if st.button("Generate multi-file scenarios", type="primary", key="m_gen_run_mf"):
            all_scenarios = []
            with st.spinner("Generating multi-file scenarios…"):
                for entry in pool:
                    try:
                        raw = generate_multifile_scenarios(entry)
                        parsed = parse_multifile_scenarios(raw)
                        for p in parsed:
                            label = (entry.get("topic") or entry.get("concept")
                                     or entry.get("tool") or entry.get("error", "—"))
                            p["topic"] = label
                            p["confidence"] = entry.get("confidence", "Low")
                        all_scenarios.extend(parsed)
                    except Exception as e:
                        st.warning(f"Skipped one entry: {e}")
            # Append to existing scenarios.json so multi-file and single-file can coexist
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = Path(OUTPUT_DIR) / "scenarios.json"
            existing = []
            if out_path.exists():
                try:
                    existing = json.loads(out_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = []
            combined = existing + all_scenarios
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(combined, f, indent=2)
            st.success(f"Added {len(all_scenarios)} multi-file scenarios (total {len(combined)})")

    if st.button("Close", key="m_gen_close"):
        st.session_state.active_modal = None
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Modal: Card Review (full approve / reject / skip flow)
# ══════════════════════════════════════════════════════════════════════════════
@_dialog_decorator("🃏 Card Review")
def modal_review():
    kind = st.radio(
        "Review", ["Flashcards", "Scenarios"],
        horizontal=True, key="modal_review_kind",
    )

    # Reload when kind changes
    if kind != st.session_state.review_kind:
        st.session_state.review_kind = kind
        st.session_state.review_cards = []
        st.session_state.review_idx = 0
        st.session_state.review_show_answer = False

    if st.button("Load cards", key="m_rev_load"):
        st.session_state.review_cards = (
            load_anki_cards() if kind == "Flashcards" else load_scenario_cards()
        )
        st.session_state.review_idx = 0
        st.session_state.review_show_answer = False

    cards = st.session_state.review_cards
    if not cards:
        st.info(f"No {kind.lower()} loaded. Click **Load cards** above.")
        if st.button("Close", key="m_rev_close_empty"):
            st.session_state.active_modal = None
            st.rerun()
        return

    pending = [c for c in cards if c["status"] == "pending"]
    idx = st.session_state.review_idx
    if idx >= len(pending):
        approved = sum(1 for c in cards if c["status"] == "approved")
        rejected = sum(1 for c in cards if c["status"] == "rejected")
        skipped = sum(1 for c in cards if c["status"] == "skipped")
        st.success(f"All cards reviewed — {approved} approved, {rejected} rejected, {skipped} skipped.")
        if kind == "Flashcards":
            a, r = save_reviewed_cards(cards)
            st.caption(f"Saved → output/anki.csv ({a}) & output/anki_rejected.csv ({r}).")
        if st.button("Close", key="m_rev_close_done"):
            st.session_state.active_modal = None
            st.rerun()
        return

    card = pending[idx]
    st.progress((idx) / max(len(pending), 1),
                text=f"{idx+1} of {len(pending)} pending")

    if kind == "Flashcards":
        st.markdown(f"**Q:** {card['question']}")
        if st.session_state.review_show_answer:
            st.markdown(f"**A:** {card['answer']}")
            st.caption(f"tags={card['tags']} · diff={card['difficulty']} · conf={card['confidence']}")
        else:
            if st.button("Flip", key="m_rev_flip"):
                st.session_state.review_show_answer = True
                st.rerun()
    else:
        render_scenario_card(card)
        if st.session_state.review_show_answer:
            st.markdown(f"**Model answer:** {card['answer']}")
            st.caption(f"topic={card.get('topic','—')} · conf={card.get('confidence','—')}")
        else:
            if st.button("Show answer", key="m_rev_flip"):
                st.session_state.review_show_answer = True
                st.rerun()

    # Cache AnkiConnect availability per session — avoid spamming the probe.
    if "anki_available" not in st.session_state:
        from pipeline.anki_sync import is_available as _anki_available
        st.session_state.anki_available = _anki_available()
    if st.session_state.anki_available and kind == "Flashcards":
        st.caption("🟢 Anki detected — approved flashcards will auto-push to your deck.")

    cols = st.columns(3)
    with cols[0]:
        if st.button("✅ Approve", key="m_rev_approve"):
            card["status"] = "approved"
            # Best-effort Anki push for flashcards. Failures fall through to CSV.
            if st.session_state.anki_available and kind == "Flashcards":
                try:
                    from pipeline.anki_sync import push_card
                    tags = [t for t in (card.get("tags") or "").split("::") if t]
                    push_card(card["question"], card["answer"], tags=tags)
                except Exception as e:
                    st.warning(f"Anki push failed (still saved to CSV): {e}")
            st.session_state.review_idx += 1
            st.session_state.review_show_answer = False
            st.rerun()
    with cols[1]:
        if st.button("❌ Reject", key="m_rev_reject"):
            card["status"] = "rejected"
            st.session_state.review_idx += 1
            st.session_state.review_show_answer = False
            st.rerun()
    with cols[2]:
        if st.button("⏭ Skip", key="m_rev_skip"):
            card["status"] = "skipped"
            st.session_state.review_idx += 1
            st.session_state.review_show_answer = False
            st.rerun()

    if st.button("Close", key="m_rev_close"):
        st.session_state.active_modal = None
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Modal: JD Analyzer (single + batch)
# ══════════════════════════════════════════════════════════════════════════════
def _maybe_autogen_weekly_plan(jd_result):
    """Generate this ISO-week's plan if it doesn't already exist.
    Returns the filename written, or None if skipped."""
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    week_filename = f"weekly_plan_{iso_year}-W{iso_week:02d}.md"
    week_path = Path(OUTPUT_DIR) / week_filename
    if week_path.exists():
        return None  # already done this week

    # Extract gaps + role from either single or batch shape.
    gaps = jd_result.get("priority_gaps") or []
    role = jd_result.get("role_title")
    if not gaps and "aggregate" in jd_result:
        agg = jd_result["aggregate"] or {}
        gaps = [
            {"domain": g["skill"], "urgency": g.get("urgency", "Medium"),
             "action": "Close this market-frequent gap."}
            for g in agg.get("most_common_gaps", [])[:2]
        ]
        role = role or agg.get("best_fit_role")
    role = role or "Cloud Engineer"
    if not gaps:
        return None

    plan = generate_weekly_plan(gaps, role, st.session_state.weekly_hours)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    week_path.write_text(plan, encoding="utf-8")
    (Path(OUTPUT_DIR) / "weekly_plan.md").write_text(plan, encoding="utf-8")
    return week_filename


@_dialog_decorator("🎯 JD Analyzer")
def modal_jd():
    kb = load_kb_safe()
    if not kb:
        st.info("Need a KB before running gap analysis.")
        if st.button("Close", key="m_jd_close_empty"):
            st.session_state.active_modal = None
            st.rerun()
        return

    mode = st.radio("Mode", ["Single", "Batch"],
                    horizontal=True, key="modal_jd_mode")
    st.session_state.jd_mode = mode

    st.session_state.jd_text = st.text_area(
        "Paste JD text" if mode == "Single"
        else "Paste multiple JDs separated by `---` (or upload a .txt below)",
        value=st.session_state.jd_text,
        height=220,
        key="modal_jd_textarea",
    )
    if mode == "Batch":
        up = st.file_uploader("…or upload .txt", type="txt", key="m_jd_up")
        if up is not None:
            st.session_state.jd_text = up.read().decode("utf-8", errors="ignore")

    run_label = "Run gap analysis" if mode == "Single" else "Run batch analysis"
    if st.button(run_label, type="primary", key="m_jd_run"):
        # Load resume claims if present — enables the three-way bucketing
        # (strengths_to_lead_with / exposures / hidden_assets) in both modes.
        from pipeline.resume_check import load_resume_claims
        resume_claims = load_resume_claims()
        if resume_claims:
            st.caption(
                "📄 Resume claims detected — analysis will surface exposures "
                "(claimed but unbacked) and hidden assets (known but unclaimed)."
            )

        with st.spinner("Analyzing…"):
            try:
                analyzed = None
                if mode == "Single":
                    jds = parse_jds(st.session_state.jd_text)
                    result = run_gap_analysis(jds[0], kb, resume_claims=resume_claims)
                    st.session_state.jd_result = result
                    st.session_state.jd_batch_result = None
                    save_jd_report(result, prefix="single")
                    trigger_aggregation()
                    analyzed = result
                else:
                    jds = parse_jds(st.session_state.jd_text)
                    if len(jds) < 2:
                        st.warning("Couldn't split into multiple JDs — use `---` separators.")
                    else:
                        batch = run_batch_analysis(jds, kb, resume_claims=resume_claims)
                        st.session_state.jd_batch_result = batch
                        st.session_state.jd_result = None
                        save_jd_report(batch, prefix="batch")
                        trigger_aggregation()
                        analyzed = batch

                # Auto-generate this week's plan if there isn't one yet.
                if analyzed is not None:
                    try:
                        new_plan = _maybe_autogen_weekly_plan(analyzed)
                        if new_plan:
                            st.success(f"Auto-generated weekly plan → {new_plan}")
                    except Exception as e:
                        st.caption(f"Auto-plan skipped: {e}")
            except Exception as e:
                st.error(f"Error: {e}")

    # ── Single result rendering ──────────────────────────────────────────────
    if st.session_state.jd_result:
        r = st.session_state.jd_result
        quality_badge(int(round(r.get("readiness_score", 0) / 10)), "readiness")
        st.markdown(f"**{r.get('role_title', '?')}** — {r.get('overall_readiness', '?')}")
        st.markdown(r.get("summary", ""))

        # Three-way buckets surface only when resume claims fed the analysis.
        if r.get("strengths_to_lead_with"):
            st.success("✅ Strengths to lead with (resume + KB + JD)")
            for s in r["strengths_to_lead_with"]:
                st.markdown(f"- **{s['domain']}** — {s.get('reason', '')}")
        if r.get("exposures"):
            st.error("🚩 Exposures (claimed on resume + on JD, no KB notes)")
            for e in r["exposures"]:
                st.markdown(f"- **{e['domain']}** ({e.get('urgency', 'High')}) — {e.get('study_action', '')}")
        if r.get("hidden_assets"):
            st.warning("💡 Hidden assets (in KB + on JD, missing from resume)")
            for h in r["hidden_assets"]:
                st.markdown(f"- **{h['domain']}** — {h.get('resume_action', '')}")

        if r.get("priority_gaps"):
            st.markdown("##### Priority gaps (true study targets)")
            for g in r["priority_gaps"]:
                st.markdown(f"- **{g['domain']}** ({g['urgency']}) — {g['action']}")
        # Legacy strengths field — only render if the new bucket isn't already populated.
        if r.get("strengths") and not r.get("strengths_to_lead_with"):
            st.markdown("##### Strengths to lead with")
            for s in r["strengths"]:
                st.markdown(f"- {s}")

    # ── Batch result rendering ───────────────────────────────────────────────
    if st.session_state.jd_batch_result:
        b = st.session_state.jd_batch_result
        agg = b.get("aggregate", {}) or {}
        st.markdown("##### Aggregate")
        if agg.get("avg_readiness_score") is not None:
            quality_badge(int(round(agg["avg_readiness_score"] / 10)), "avg readiness")
        if agg.get("best_fit_role"):
            st.caption(f"Best-fit role: {agg['best_fit_role']}")
        if agg.get("summary"):
            st.markdown(agg["summary"])
        if agg.get("most_common_gaps"):
            st.markdown("**Most common gaps**")
            for g in agg["most_common_gaps"]:
                st.markdown(f"- **{g['skill']}** — appears in {g['appears_in']} JDs · {g.get('urgency','')}")
        if agg.get("consistent_strengths"):
            st.markdown("**Consistent strengths:** " + ", ".join(agg["consistent_strengths"]))
        if agg.get("cross_jd_exposures"):
            st.error("🚩 Cross-JD exposures (claimed on resume + repeating gap across JDs)")
            for ex in agg["cross_jd_exposures"]:
                st.markdown(
                    f"- **{ex['domain']}** — appears in {ex.get('appears_in', '?')} JDs · {ex.get('reason', '')}"
                )
        st.markdown("##### Per-JD")
        for r in b.get("individual_results", []):
            with st.expander(f"JD {r['jd_number']}: {r.get('role_title','?')} — {r.get('readiness_score',0)}/100"):
                st.markdown(f"Top gaps: {', '.join(r.get('top_gaps', []))}")
                st.markdown(f"Top strengths: {', '.join(r.get('top_strengths', []))}")

    if st.button("Close", key="m_jd_close"):
        st.session_state.active_modal = None
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Modal: Resume Check
# ══════════════════════════════════════════════════════════════════════════════
@_dialog_decorator("📄 Resume reality check")
def modal_resume():
    """Upload a resume → extract claimed skills/projects/companies → diff against KB."""
    from pipeline.resume_check import (
        compute_coverage,
        load_resume_claims,
        parse_resume_to_claims,
        save_resume_claims,
    )

    st.caption(
        "Upload your resume as .txt or .md. Claude extracts the skills, "
        "projects, and companies you claim; the system diffs them against your KB "
        "and surfaces gaps — claims you can't back up with notes."
    )

    uploaded = st.file_uploader(
        "Resume (.txt / .md)", type=["txt", "md"], key="m_resume_upload"
    )

    cols = st.columns(2)
    with cols[0]:
        do_parse = st.button("Parse & save claims", type="primary",
                             key="m_resume_parse", disabled=uploaded is None)
    with cols[1]:
        do_recheck = st.button("Re-check coverage", key="m_resume_recheck")

    if do_parse and uploaded is not None:
        text = uploaded.read().decode("utf-8", errors="ignore")
        with st.spinner("Asking Claude to extract claims…"):
            try:
                claims = parse_resume_to_claims(text)
                save_resume_claims(claims)
                st.success(
                    f"Saved {len(claims.get('skills', []))} skills, "
                    f"{len(claims.get('projects', []))} projects, "
                    f"{len(claims.get('companies', []))} companies."
                )
            except Exception as e:
                st.error(f"Parse failed: {e}")

    claims = load_resume_claims()
    if not claims:
        st.info("No resume claims on file yet. Upload one above to begin.")
        if st.button("Close", key="m_resume_close_empty"):
            st.session_state.active_modal = None
            st.rerun()
        return

    if do_recheck or claims:
        kb = load_kb_safe()
        coverage = compute_coverage(claims, kb)
        t = coverage["totals"]
        quality_badge(int(t["pct"] / 10), "coverage")
        st.markdown(
            f"**{t['covered']}/{t['claims']} claims backed by KB notes** ({t['pct']}%)"
        )

        for bucket_name, label in [
            ("skills", "Skills"),
            ("projects", "Projects"),
            ("companies", "Companies"),
        ]:
            bucket = coverage[bucket_name]
            if not (bucket["covered"] or bucket["missing"]):
                continue
            st.markdown(f"##### {label}")
            if bucket["missing"]:
                st.error(f"🚩 Missing notes ({len(bucket['missing'])})")
                for item in bucket["missing"]:
                    cols = st.columns([4, 1])
                    cols[0].markdown(f"- **{item['claim']}**")
                    if cols[1].button("Draft note", key=f"m_resume_draft_{bucket_name}_{item['claim']}"):
                        st.session_state.convert_text = (
                            f"#{item['claim'].lower().replace(' ', '-')}\n\n"
                            f"[draft a note here covering what you actually know about {item['claim']}]\n\n"
                            f"Confidence: Low\nDifficulty: Medium\n"
                        )
                        st.session_state.active_modal = "convert"
                        st.rerun()
            if bucket["covered"]:
                with st.expander(f"✅ Backed by notes ({len(bucket['covered'])})"):
                    for item in bucket["covered"]:
                        match_str = ", ".join(item["matches"][:3]) + (
                            f" (+{len(item['matches']) - 3} more)"
                            if len(item["matches"]) > 3 else ""
                        )
                        st.markdown(f"- **{item['claim']}** ← {match_str}")

    if st.button("Close", key="m_resume_close"):
        st.session_state.active_modal = None
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Main render dispatch
# ══════════════════════════════════════════════════════════════════════════════
if nav == "Dashboard":
    render_dashboard()
elif nav == "Mock Interview":
    render_mock_interview()
elif nav == "Knowledge Base":
    render_knowledge_base()
elif nav == "Weekly Plan":
    render_weekly_plan()
elif nav == "Topic Suggestions":
    render_topic_suggestions()

if st.session_state.active_modal == "convert":
    modal_convert()
elif st.session_state.active_modal == "generate":
    modal_generate()
elif st.session_state.active_modal == "review":
    modal_review()
elif st.session_state.active_modal == "jd":
    modal_jd()
elif st.session_state.active_modal == "resume":
    modal_resume()
