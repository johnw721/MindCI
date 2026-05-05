"""
app_dashboard.py — Dashboard-first UI for MindCI.

Replaces the 7-tab layout with:
  • Sidebar nav (Dashboard + quick actions)
  • Two-column dashboard with readiness, weak spots, recent scores,
    today's focus, and a market signal block.
  • Convert / Generate / Card Review / JD Analyzer rendered as dialogs
    (st.dialog when available) or conditional sections.

Backend pipeline functions are reused unchanged. To run:

    streamlit run app_dashboard.py
"""

import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

# ── Project imports (unchanged backend) ───────────────────────────────────────
from config import (
    JD_SKILL_FREQUENCIES, _FREQ_SOURCE, _FREQ_COUNT,
    DATA_DIR, OUTPUT_DIR, RAW_DIR,
)
from utils import (
    load_knowledge_base, load_anki_cards, load_scenario_cards,
    save_reviewed_cards,
)
from pipeline.convert import convert_to_json, parse_and_save_json
from pipeline.quality import (
    check_note_quality, score_kb_entry,
    generate_enrichment_questions, rewrite_enriched_note,
    preview_extraction,
    CPM_CHEAT_SHEET,
)
from pipeline.generate import (
    build_dynamic_prompt, parse_qa, classify, generate_flashcards_batched,
)
from pipeline.interview import (
    build_interview_pool, score_answer, append_session,
    get_summary_stats,
)
from pipeline.jd_analyzer import (
    run_gap_analysis, parse_jds, save_jd_report, trigger_aggregation,
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
# st.dialog is experimental — fall back to a plain container if unavailable.
def _dialog_decorator(title):
    if hasattr(st, "dialog"):
        return st.dialog(title)
    if hasattr(st, "experimental_dialog"):
        return st.experimental_dialog(title)
    # Fallback: render as expander inline.
    def _wrap(fn):
        def _inner(*a, **kw):
            with st.expander(title, expanded=True):
                return fn(*a, **kw)
        return _inner
    return _wrap


# ── Session-state defaults ────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "active_modal": None,           # "convert" | "generate" | "review" | "jd"
        "convert_text": "",
        "convert_filename": "",
        "convert_preview": None,
        "convert_quality": None,
        "convert_validation": None,
        "jd_text": "",
        "jd_result": None,
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


def market_signal_top(n=8):
    items = sorted(JD_SKILL_FREQUENCIES.items(), key=lambda x: -x[1])[:n]
    return items


def quality_color(score):
    if score >= 8:
        return "#22c55e"   # green
    if score >= 5:
        return "#f59e0b"   # amber
    return "#ef4444"       # red


def quality_badge(score, label="quality"):
    color = quality_color(score)
    st.markdown(
        f"<span style='background:{color};color:white;padding:4px 10px;"
        f"border-radius:12px;font-weight:600;font-size:0.85rem;'>"
        f"{label}: {score}/10</span>",
        unsafe_allow_html=True,
    )


# ── Sidebar navigation ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🧠 MindCI")
    st.caption("Personal knowledge pipeline")

    nav = st.radio(
        "View",
        ["Dashboard", "Knowledge Base", "Weekly Plan", "Topic Suggestions"],
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("### Quick actions")
    if st.button("📝 New Note (Convert)", use_container_width=True):
        st.session_state.active_modal = "convert"
    if st.button("🎯 JD Analyzer", use_container_width=True):
        st.session_state.active_modal = "jd"
    if st.button("🃏 Card Review", use_container_width=True):
        st.session_state.active_modal = "review"
    if st.button("⚡ Generate", use_container_width=True):
        st.session_state.active_modal = "generate"

    st.markdown("---")
    st.caption(f"Market data: {_FREQ_SOURCE}")


# ── Dashboard view ────────────────────────────────────────────────────────────
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
            score10 = round(score, 1)
            quality_badge(int(round(score)), "readiness")
            st.metric(
                "Overall avg score",
                f"{score10}/10",
                delta=f"{stats['total_sessions']} sessions",
            )
        else:
            st.info("No mock interview history yet — run a Card Review session to seed this.")

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

        st.markdown("#### Market signal")
        st.caption(f"Top JD-frequency skills ({_FREQ_SOURCE})")
        for skill, freq in market_signal_top():
            st.markdown(f"- {skill} — {int(freq*100)}%")

    st.markdown("---")
    cols = st.columns(4)
    cols[0].metric("KB entries", len(kb))
    cols[1].metric("Sessions", stats["total_sessions"] if stats else 0)
    cols[2].metric("Questions answered", stats["total_questions"] if stats else 0)
    cols[3].metric("JD reports", _FREQ_COUNT)


def render_knowledge_base():
    st.title("Knowledge Base")
    kb = load_kb_safe()
    if not kb:
        st.info("No entries yet. Use **New Note (Convert)** to add some.")
        return
    by_type = {}
    for e in kb:
        by_type.setdefault(e.get("type", "?"), []).append(e)
    for t, entries in by_type.items():
        st.markdown(f"### {t.title()} ({len(entries)})")
        for e in entries:
            label = (e.get("topic") or e.get("concept") or
                     e.get("tool") or e.get("error", "—"))
            with st.expander(f"{label}  ·  conf {e.get('confidence', '?')}"):
                st.json(e)


def render_weekly_plan():
    st.title("Weekly Plan")
    plan = load_recent_weekly_plan()
    if plan:
        st.markdown(plan)
    else:
        st.info("Generate one from the JD Analyzer modal after a gap analysis.")


def render_topic_suggestions():
    st.title("Topic Suggestions")
    from pipeline.suggestions import generate_topic_suggestions
    kb = load_kb_safe()
    if not kb:
        st.info("Need at least one KB entry.")
        return
    if st.button("Generate suggestions"):
        with st.spinner("Asking Claude…"):
            try:
                out = generate_topic_suggestions(kb)
                st.markdown(out)
            except Exception as e:
                st.error(f"Error: {e}")


# ── Modals ────────────────────────────────────────────────────────────────────

@_dialog_decorator("📝 New Note — Convert")
def modal_convert():
    """
    Convert modal with:
      • Marker cheat sheet (sticky at top)
      • Live preview of fields Claude will extract
      • Coloured quality badge
      • Inline Pydantic validation errors before commit
    """
    st.markdown("##### Cognitive Payload Markers — drop these into your note")
    st.code(CPM_CHEAT_SHEET, language="text")

    text = st.text_area(
        "Paste raw note OR upload a .txt file below",
        value=st.session_state.convert_text,
        height=240,
        key="modal_convert_text",
    )
    uploaded = st.file_uploader("…or upload .txt", type="txt",
                                key="modal_convert_upload")
    if uploaded is not None:
        text = uploaded.read().decode("utf-8", errors="ignore")
        st.session_state.convert_filename = uploaded.name
    st.session_state.convert_text = text

    cols = st.columns(3)
    with cols[0]:
        do_quality = st.button("Quality check", key="modal_convert_qcheck")
    with cols[1]:
        do_preview = st.button("Preview extraction", key="modal_convert_preview")
    with cols[2]:
        do_commit = st.button("Convert & save", type="primary",
                              key="modal_convert_commit")

    # ── Quality badge ─────────────────────────────────────────────────────
    if do_quality and text.strip():
        q = check_note_quality(st.session_state.convert_filename or "note.txt", text)
        st.session_state.convert_quality = q
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

    # ── Live preview ──────────────────────────────────────────────────────
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

        # Inline Pydantic validation BEFORE commit
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

    # ── Commit (real convert) ─────────────────────────────────────────────
    if do_commit and text.strip():
        os.makedirs(RAW_DIR, exist_ok=True)
        fname = st.session_state.convert_filename or f"note_{datetime.now():%Y%m%d_%H%M%S}.txt"
        Path(RAW_DIR, fname).write_text(text, encoding="utf-8")
        with st.spinner("Converting with Claude…"):
            try:
                raw = convert_to_json(f"--- SOURCE: {fname} ---\n\n{text}")
                parsed, report = parse_and_save_json(raw)
                st.success(f"Saved {len(parsed)} entries to {DATA_DIR}/structured.json")
                if report["invalid_count"]:
                    st.warning(f"{report['invalid_count']} invalid → data/invalid_entries.json")
                if report["warning_count"]:
                    with st.expander(f"{report['warning_count']} soft warnings"):
                        for w in report["warnings"]:
                            st.caption(f"{w['label']}: {', '.join(w['warnings'])}")
                # Reset preview
                st.session_state.convert_preview = None
                st.session_state.convert_quality = None
            except Exception as e:
                st.error(f"Convert failed: {e}")

    if st.button("Close", key="modal_convert_close"):
        st.session_state.active_modal = None
        st.rerun()


@_dialog_decorator("⚡ Generate flashcards & scenarios")
def modal_generate():
    kb = load_kb_safe()
    if not kb:
        st.info("Convert a note first.")
    else:
        st.write(f"{len(kb)} entries in KB.")
        if st.button("Generate flashcards (batched)", type="primary"):
            with st.spinner("Generating…"):
                try:
                    cards = generate_flashcards_batched(kb)
                    st.success(f"Generated {len(cards)} flashcards.")
                except Exception as e:
                    st.error(f"Error: {e}")
    if st.button("Close", key="modal_gen_close"):
        st.session_state.active_modal = None
        st.rerun()


@_dialog_decorator("🃏 Card Review")
def modal_review():
    cards = load_anki_cards()
    if not cards:
        st.info("No flashcards yet — run Generate first.")
    else:
        st.write(f"{len(cards)} flashcards.")
        for c in cards[:10]:
            with st.expander(c["question"][:80]):
                st.markdown(f"**A:** {c['answer']}")
                st.caption(f"tags={c['tags']} · diff={c['difficulty']} · conf={c['confidence']}")
    if st.button("Close", key="modal_review_close"):
        st.session_state.active_modal = None
        st.rerun()


@_dialog_decorator("🎯 JD Analyzer")
def modal_jd():
    kb = load_kb_safe()
    if not kb:
        st.info("Need a KB before running gap analysis.")
        if st.button("Close", key="modal_jd_close_empty"):
            st.session_state.active_modal = None
            st.rerun()
        return

    st.session_state.jd_text = st.text_area(
        "Paste a job description (or several separated by `---`)",
        value=st.session_state.jd_text,
        height=200,
        key="modal_jd_text",
    )
    if st.button("Run gap analysis", type="primary", key="modal_jd_run"):
        with st.spinner("Analyzing…"):
            try:
                jds = parse_jds(st.session_state.jd_text)
                result = run_gap_analysis(jds[0], kb)
                st.session_state.jd_result = result
                save_jd_report(result, prefix="single")
                trigger_aggregation()
            except Exception as e:
                st.error(f"Error: {e}")

    if st.session_state.jd_result:
        r = st.session_state.jd_result
        quality_badge(int(round(r.get("readiness_score", 0) / 10)), "readiness")
        st.markdown(f"**{r.get('role_title', '?')}** — {r.get('overall_readiness', '?')}")
        st.markdown(r.get("summary", ""))
        if r.get("priority_gaps"):
            st.markdown("##### Priority gaps")
            for g in r["priority_gaps"]:
                st.markdown(f"- **{g['domain']}** ({g['urgency']}) — {g['action']}")

    if st.button("Close", key="modal_jd_close"):
        st.session_state.active_modal = None
        st.rerun()


# ── Main render ───────────────────────────────────────────────────────────────
if nav == "Dashboard":
    render_dashboard()
elif nav == "Knowledge Base":
    render_knowledge_base()
elif nav == "Weekly Plan":
    render_weekly_plan()
elif nav == "Topic Suggestions":
    render_topic_suggestions()

# Conditional modal rendering — st.dialog calls render the dialog,
# the fallback decorator inlines an expander.
if st.session_state.active_modal == "convert":
    modal_convert()
elif st.session_state.active_modal == "generate":
    modal_generate()
elif st.session_state.active_modal == "review":
    modal_review()
elif st.session_state.active_modal == "jd":
    modal_jd()
