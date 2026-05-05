import streamlit as st
import json
import os
import shutil
from datetime import datetime
from anthropic import Anthropic

from config import VALID_TYPES, JD_SKILL_FREQUENCIES, _FREQ_SOURCE, _FREQ_COUNT, load_jd_frequencies
from utils import (
    load_knowledge_base, load_prompt,
    load_anki_cards, load_scenario_cards, save_reviewed_cards
)
from pipeline.convert import convert_to_json, parse_and_save_json, list_kb_versions
from pipeline.generate import build_dynamic_prompt, parse_qa, classify, generate_flashcards_batched
from pipeline.scenarios import (
    generate_scenarios, parse_scenarios,
    generate_multifile_scenarios, parse_multifile_scenarios
)
from pipeline.interview import score_answer, build_interview_pool, append_session, get_summary_stats, get_topic_progression
from pipeline.jd_analyzer import run_gap_analysis, run_batch_analysis, parse_jds, save_jd_report, trigger_aggregation
from pipeline.weekly import generate_weekly_plan
from pipeline.suggestions import generate_topic_suggestions, generate_cold_test_questions
from pipeline.quality import check_note_quality, score_kb_entry, generate_enrichment_questions, rewrite_enriched_note

client = Anthropic()

# UI

st.title("MindCI")
st.caption("Personal knowledge pipeline for Cloud Engineers")

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "Convert", "Generate", "Card Review",
    "JD Analyzer", "Weekly Plan", "Topic Suggestions", "Knowledge Base"
])

# Tab 1: Convert
with tab1:
    st.subheader("Convert raw notes to structured JSON")

    uploaded_files = st.file_uploader(
        "Upload .txt note files", type="txt", accept_multiple_files=True
    )

    if uploaded_files:
        st.success(f"{len(uploaded_files)} file(s) ready")

    if uploaded_files:
        if st.button("Check Note Quality", key="quality_check"):
            st.markdown("#### Pre-flight quality check")
            all_good = True
            for f in uploaded_files:
                text = f.read().decode("utf-8", errors="ignore")
                result = check_note_quality(f.name, text)
                score = result["score"]
                color = "green" if score >= 8 else "orange" if score >= 5 else "red"
                with st.expander(f"{f.name} -- Quality score: {score}/10"):
                    if result["passes"]:
                        for p in result["passes"]:
                            st.markdown(f"- {p}")
                    if result["issues"]:
                        all_good = False
                        st.markdown("**Issues to fix:**")
                        for issue in result["issues"]:
                            st.warning(issue)
            if all_good:
                st.success("All notes passed quality check -- ready to convert")
            else:
                st.info("Fix issues above for better scenario and flashcard output. You can still convert as-is.")


    st.markdown("---")
    st.markdown("#### Note enrichment assistant")
    st.caption("Upload a thin note, answer a few questions, Claude rewrites it -- then convert")

    enrich_file = st.file_uploader("Upload a note to enrich", type="txt", key="enrich_upload")

    if enrich_file:
        enrich_text = enrich_file.read().decode("utf-8", errors="ignore")

        if "enrich_note" not in st.session_state:
            st.session_state.enrich_note = ""
        if "enrich_questions" not in st.session_state:
            st.session_state.enrich_questions = []
        if "enrich_answers" not in st.session_state:
            st.session_state.enrich_answers = []
        if "enrich_rewritten" not in st.session_state:
            st.session_state.enrich_rewritten = ""
        if "enrich_filename" not in st.session_state:
            st.session_state.enrich_filename = ""

        # Reset if new file uploaded
        if st.session_state.enrich_filename != enrich_file.name:
            st.session_state.enrich_note = enrich_text
            st.session_state.enrich_questions = []
            st.session_state.enrich_answers = []
            st.session_state.enrich_rewritten = ""
            st.session_state.enrich_filename = enrich_file.name

        st.markdown("**Original note:**")
        st.text_area("", value=enrich_text, height=120, disabled=True, key="enrich_preview")

        # Step 1 — generate questions
        if not st.session_state.enrich_questions:
            if st.button("Generate enrichment questions", key="enrich_gen_q"):
                with st.spinner("Analyzing note..."):
                    try:
                        questions = generate_enrichment_questions(enrich_text)
                        st.session_state.enrich_questions = questions
                        st.session_state.enrich_answers = [""] * len(questions)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        # Step 2 — answer questions
        elif not st.session_state.enrich_rewritten:
            st.markdown("**Answer these questions to enrich your note:**")
            for i, q in enumerate(st.session_state.enrich_questions):
                ans = st.text_input(f"Q{i+1}: {q}", key=f"enrich_ans_{i}",
                                    value=st.session_state.enrich_answers[i])
                st.session_state.enrich_answers[i] = ans

            answered = sum(1 for a in st.session_state.enrich_answers if a.strip())
            st.caption(f"{answered} of {len(st.session_state.enrich_questions)} answered")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Rewrite note", disabled=answered < 2, key="enrich_rewrite"):
                    with st.spinner("Rewriting..."):
                        try:
                            rewritten = rewrite_enriched_note(
                                enrich_text,
                                st.session_state.enrich_questions,
                                st.session_state.enrich_answers
                            )
                            st.session_state.enrich_rewritten = rewritten
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
            with col2:
                if st.button("Start over", key="enrich_reset"):
                    st.session_state.enrich_questions = []
                    st.session_state.enrich_answers = []
                    st.rerun()

        # Step 3 — review and approve
        else:
            st.markdown("**Rewritten note:**")
            edited = st.text_area("Review and edit if needed",
                                   value=st.session_state.enrich_rewritten,
                                   height=250, key="enrich_edited")

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("Approve and save to raw/", key="enrich_approve"):
                    os.makedirs("raw", exist_ok=True)
                    save_path = f"raw/enriched_{enrich_file.name}"
                    with open(save_path, "w", encoding="utf-8") as f:
                        f.write(edited)
                    st.success(f"Saved to {save_path} -- upload it in the file uploader above to convert")
                    st.session_state.enrich_questions = []
                    st.session_state.enrich_answers = []
                    st.session_state.enrich_rewritten = ""
                    st.session_state.enrich_filename = ""
            with col2:
                if st.button("Rewrite again", key="enrich_redo"):
                    st.session_state.enrich_rewritten = ""
                    st.rerun()
            with col3:
                st.download_button("Download enriched note",
                                   data=edited.encode("utf-8"),
                                   file_name=f"enriched_{enrich_file.name}",
                                   mime="text/plain",
                                   key="enrich_download")

    if st.button("Run Convert", disabled=not uploaded_files):
        if st.session_state.get("_convert_running"):
            st.warning("Convert already in progress -- wait for it to finish.")
        else:
            st.session_state["_convert_running"] = True
            all_notes = ""
        os.makedirs("raw", exist_ok=True)

        for f in uploaded_files:
            content = f.read().decode("utf-8", errors="ignore")
            all_notes += f"\n\n--- SOURCE: {f.name} ---\n\n{content}"
            with open(f"raw/{f.name}", "w", encoding="utf-8") as out:
                out.write(content)

        with st.spinner("Converting notes with Claude..."):
            try:
                raw_response = convert_to_json(all_notes)
                parsed, val_report = parse_and_save_json(raw_response)

                # Validation results
                if val_report["invalid_count"] > 0:
                    st.warning(f"{val_report['invalid_count']} entries failed validation -- saved to data/invalid_entries.json")
                    for inv in val_report["invalid"]:
                        st.error(f"[{inv.get('label','?')}] {' | '.join(inv['errors'])}")
                if val_report["warning_count"] > 0:
                    with st.expander(f"{val_report['warning_count']} entries have defaulted fields"):
                        for w in val_report["warnings"]:
                            st.caption(f"{w['label']}: {', '.join(w['warnings'])}")

                st.success(f"Saved {len(parsed)} entries to data/structured.json")

                # Post-convert quality scoring
                scores = [score_kb_entry(e) for e in parsed]
                low_quality = [s for s in scores if s["score"] < 6]
                avg_score = sum(s["score"] for s in scores) / len(scores) if scores else 0

                col1, col2, col3 = st.columns(3)
                col1.metric("Entries converted", len(parsed))
                col2.metric("Avg quality score", f"{avg_score:.1f}/10")
                col3.metric("Need enrichment", len(low_quality))

                if low_quality:
                    st.markdown("#### Entries that need enrichment")
                    st.caption("These entries will generate weaker flashcards and scenarios. Go back and add more detail to the source notes.")
                    for s in low_quality:
                        with st.expander(f"[{s['type']}] {s['label']} -- {s['score']}/10"):
                            st.markdown("**Missing:**")
                            for issue in s["issues"]:
                                st.warning(issue)
                else:
                    st.success("All entries scored 6/10 or above -- good note quality")

                st.json(parsed[:3])

                # Show KB version history
                versions = list_kb_versions()
                if len(versions) > 1:
                    st.caption(f"KB version saved to data/history/ -- {len(versions)} version(s) stored")
                    with st.expander("View KB version history"):
                        for v in versions[:5]:
                            st.markdown(f"- `{v}`")

                os.makedirs("archive", exist_ok=True)
                for f in uploaded_files:
                    src = f"raw/{f.name}"
                    dst = f"archive/{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{f.name}"
                    if os.path.exists(src):
                        shutil.move(src, dst)
                st.info("Raw files archived")
                st.session_state["_convert_running"] = False

                # Show version history
                versions = list_kb_versions()
                if versions:
                    with st.expander(f"Knowledge base history ({len(versions)} versions)"):
                        for v in versions[:10]:
                            st.caption(v)

            except Exception as e:
                st.session_state["_convert_running"] = False
                st.error(f"Error: {e}")

# Tab 2: Generate
with tab2:
    st.subheader("Generate study material")

    kb = load_knowledge_base()

    if not kb:
        st.warning("No knowledge base found. Run Convert first.")
    else:
        st.caption(f"{len(kb)} entries in knowledge base")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("High confidence", len([e for e in kb if e.get("confidence") == "High"]))
        with col2:
            st.metric("Medium confidence", len([e for e in kb if e.get("confidence") == "Medium"]))
        with col3:
            st.metric("Low / unknown", len([e for e in kb if e.get("confidence") not in ("High", "Medium")]))

        gen_mode = st.radio("Generation mode", ["Anki Flashcards", "Scenario Questions", "Mock Interview"], horizontal=True)
        st.caption("Anki Flashcards: Q&A pairs  |  Scenario Questions: code review, debug, fix-it  |  Mock Interview: timed session graded by Claude")

        # Filter options
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            filter_type = st.selectbox("Entry type", ["All", "project", "certification", "exploration"], key="gen_filter_type")
        with filter_col2:
            filter_conf = st.selectbox("Confidence", ["All", "High", "Medium", "Low"], key="gen_filter_conf")

        entries_to_run = kb
        if filter_type != "All":
            entries_to_run = [e for e in entries_to_run if e.get("type") == filter_type]
        if filter_conf != "All":
            entries_to_run = [e for e in entries_to_run if e.get("confidence") == filter_conf]

        st.caption(f"{len(entries_to_run)} entries will be processed")

        if gen_mode == "Anki Flashcards":
            if st.button("Run Generate -- Flashcards", disabled=not entries_to_run):
                project_prompt = load_prompt("prompts/project.txt")
                cert_prompt = load_prompt("prompts/cert.txt")
                explore_prompt = load_prompt("prompts/explore.txt")

                os.makedirs("output", exist_ok=True)
                md_output = ""
                anki_cards = []
                progress = st.progress(0)
                status = st.empty()

                for i, entry in enumerate(entries_to_run):
                    tag = classify(entry)
                    entry_type = entry.get("type", "exploration")
                    confidence = entry.get("confidence", "Low")
                    category = entry.get("category", entry.get("tool", "general"))
                    status.text(f"Generating [{confidence}] {entry_type} -- {category}...")

                    base = project_prompt if entry_type == "project" else cert_prompt if entry_type == "certification" else explore_prompt

                    try:
                        full_prompt = build_dynamic_prompt(base, entry)
                        response = client.messages.create(
                            model="claude-sonnet-4-5",
                            max_tokens=4096,
                            messages=[{"role": "user", "content": full_prompt}]
                        )
                        result = response.content[0].text
                        md_output += f"\n\n## [{tag}] {entry_type.upper()} ({category})\n{result}\n"
                        for q, a in parse_qa(result):
                            anki_cards.append((q, a, f"{tag}::{entry_type}::{category}", entry.get("difficulty", ""), confidence))
                    except Exception as e:
                        st.warning(f"Skipped entry {i}: {e}")

                    progress.progress((i + 1) / len(entries_to_run))

                with open("output/questions.md", "w", encoding="utf-8") as f:
                    f.write(md_output)
                with open("output/anki.csv", "w", encoding="utf-8") as f:
                    for q, a, tags, diff, conf in anki_cards:
                        f.write(f"{q}\t{a}\t{tags}\t{diff}\t{conf}\n")

                status.empty()
                progress.empty()
                st.success(f"{len(anki_cards)} cards generated -- go to Card Review before exporting")

                with open("output/anki.csv", "rb") as f:
                    st.download_button("Download anki.csv", f, file_name="anki.csv", mime="text/csv")
                with open("output/questions.md", "rb") as f:
                    st.download_button("Download questions.md", f, file_name="questions.md", mime="text/markdown")

        elif gen_mode == "Scenario Questions":
            st.caption("Generates code review, debug, fix-it, and architecture scenarios from your knowledge base entries")

            scenario_mode = st.radio("Scenario mode", ["Single file", "Multi-file (cross-file interaction)", "Both"], horizontal=True, key="scenario_mode")
            if scenario_mode == "Single file":
                st.caption("Standard scenarios — one code/config snippet per question")
            elif scenario_mode == "Multi-file (cross-file interaction)":
                st.caption("2-3 related files per scenario — tests understanding of how components interact across file boundaries")
            else:
                st.caption("Mix of single-file and multi-file scenarios")

            if st.button("Run Generate -- Scenarios", disabled=not entries_to_run):
                os.makedirs("output", exist_ok=True)
                all_scenarios = []
                md_output = ""
                progress = st.progress(0)
                status = st.empty()

                for i, entry in enumerate(entries_to_run):
                    entry_type = entry.get("type", "exploration")
                    confidence = entry.get("confidence", "Low")
                    label = entry.get("topic") or entry.get("concept") or entry.get("tool") or entry.get("error", "unknown")
                    status.text(f"Generating scenarios [{confidence}] {entry_type} -- {label}...")

                    try:
                        scenario_mode = st.session_state.get("scenario_mode", "Both")
                        md_output += f"\n\n## {entry_type.upper()} -- {label} [{confidence}]\n\n"

                        if scenario_mode in ["Single file", "Both"]:
                            raw = generate_scenarios(entry)
                            parsed = parse_scenarios(raw)
                            for s in parsed:
                                s["topic"] = label
                                s["confidence"] = confidence
                                s["entry_type"] = entry_type
                                all_scenarios.append(s)
                            for s in parsed:
                                md_output += f"### [{s.get('scenario','').upper()}]\n"
                                md_output += f"**Setup:** {s.get('setup','')}\n\n"
                                if s.get("code_or_config"):
                                    md_output += f"```\n{s.get('code_or_config','')}\n```\n\n"
                                md_output += f"**Question:** {s.get('question','')}\n\n"
                                md_output += f"**Answer:** {s.get('answer','')}\n\n---\n\n"

                        if scenario_mode in ["Multi-file (cross-file interaction)", "Both"]:
                            raw_mf = generate_multifile_scenarios(entry)
                            parsed_mf = parse_multifile_scenarios(raw_mf)
                            for s in parsed_mf:
                                s["topic"] = label
                                s["confidence"] = confidence
                                s["entry_type"] = entry_type
                                all_scenarios.append(s)
                            for s in parsed_mf:
                                md_output += f"### [MULTI-FILE]\n"
                                md_output += f"**Setup:** {s.get('setup','')}\n\n"
                                for fi, f in enumerate(s.get("files", [])):
                                    md_output += f"**{f.get('name', f'File {fi+1}')}**\n```\n{f.get('content','')}\n```\n\n"
                                md_output += f"**Question:** {s.get('question','')}\n\n"
                                md_output += f"**Answer:** {s.get('answer','')}\n\n---\n\n"

                    except Exception as e:
                        st.warning(f"Skipped entry {i}: {e}")

                    progress.progress((i + 1) / len(entries_to_run))

                with open("output/scenarios.json", "w", encoding="utf-8") as f:
                    json.dump(all_scenarios, f, indent=2, ensure_ascii=False)
                with open("output/scenarios.md", "w", encoding="utf-8") as f:
                    f.write(md_output)

                status.empty()
                progress.empty()
                st.success(f"{len(all_scenarios)} scenarios generated -- review them in Card Review")

                with open("output/scenarios.md", "rb") as f:
                    st.download_button("Download scenarios.md", f, file_name="scenarios.md", mime="text/markdown")


        elif gen_mode == "Mock Interview":
            st.markdown("#### Mock interview session")

            col1, col2 = st.columns(2)
            with col1:
                num_questions = st.slider("Number of questions", min_value=3, max_value=15, value=8, step=1)
            with col2:
                time_limit = st.slider("Minutes per question", min_value=1, max_value=10, value=3, step=1)

            if "interview_pool" not in st.session_state:
                st.session_state.interview_pool = []
            if "interview_idx" not in st.session_state:
                st.session_state.interview_idx = 0
            if "interview_scores" not in st.session_state:
                st.session_state.interview_scores = []
            if "interview_active" not in st.session_state:
                st.session_state.interview_active = False
            if "interview_answer" not in st.session_state:
                st.session_state.interview_answer = ""
            if "interview_graded" not in st.session_state:
                st.session_state.interview_graded = None

            if not st.session_state.interview_active:
                # Session history summary
                stats = get_summary_stats()
                if stats:
                    st.markdown("#### Your progress")
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Sessions", stats["total_sessions"])
                    col2.metric("Questions answered", stats["total_questions"])
                    col3.metric("Overall avg", f"{stats['overall_avg']}/10")
                    if stats["session_trend"]:
                        trend = stats["session_trend"]
                        delta = round(trend[-1]["avg"] - trend[0]["avg"], 1) if len(trend) > 1 else 0
                        col4.metric("Score trend", f"{trend[-1]['avg']}/10", delta=f"{delta:+.1f}" if delta != 0 else None)

                    if stats["session_trend"] and len(stats["session_trend"]) > 1:
                        st.markdown("**Session history**")
                        for s in reversed(stats["session_trend"][-8:]):
                            bar = "█" * int(s["avg"]) + "░" * (10 - int(s["avg"]))
                            st.markdown(f"`{s['date'][:10]}` [{bar}] {s['avg']}/10 ({s['pct']}%)")

                    col1, col2 = st.columns(2)
                    with col1:
                        if stats["most_improved"]:
                            st.markdown("**Most improved**")
                            for t in stats["most_improved"][:3]:
                                arrow = "↑" if t["delta"] > 0 else "↓" if t["delta"] < 0 else "→"
                                st.markdown(f"{arrow} **{t['topic']}** {t['first']} → {t['last']}/10")
                    with col2:
                        if stats["weak_spots"]:
                            st.markdown("**Still needs work**")
                            for t in stats["weak_spots"][:3]:
                                st.markdown(f"**{t['topic']}** avg {t['avg_score']}/10 over {t['attempts']} attempts")

                    st.markdown("---")

                pool = build_interview_pool(num_questions)
                if not pool:
                    st.warning("No flashcards or scenarios found. Run Generate first.")
                else:
                    src_counts = {}
                    for item in pool[:num_questions]:
                        src_counts[item["source"]] = src_counts.get(item["source"], 0) + 1
                    st.caption(f"Pool ready: {src_counts.get('scenario', 0)} scenarios + {src_counts.get('flashcard', 0)} flashcards -- biased toward Low/Medium confidence")
                    if st.button("Start Interview"):
                        st.session_state.interview_pool = pool[:num_questions]
                        st.session_state.interview_idx = 0
                        st.session_state.interview_scores = []
                        st.session_state.interview_active = True
                        st.session_state.interview_answer = ""
                        st.session_state.interview_graded = None
                        st.rerun()

            else:
                idx = st.session_state.interview_idx
                pool = st.session_state.interview_pool
                total = len(pool)

                if idx < total:
                    q = pool[idx]
                    progress_pct = idx / total

                    st.progress(progress_pct)
                    st.markdown(f"**Question {idx + 1} of {total}** -- [{q['type'].upper()}] `{q['topic']}` | confidence: `{q['confidence']}`")

                    # Timer display
                    st.caption(f"Suggested time: {time_limit} minute(s)")

                    st.markdown("---")

                    if q.get("setup"):
                        st.markdown("**Setup**")
                        st.info(q["setup"])
                    if q.get("files"):
                        for fi, f in enumerate(q["files"]):
                            st.markdown(f"**File {fi+1}: `{f.get('name', f'file_{fi+1}')}`**")
                            st.code(f.get("content", ""), language="python" if f.get("name","").endswith(".py") else None)
                    elif q.get("code"):
                        st.markdown("**Code / Config**")
                        st.code(q["code"])

                    st.markdown("**Question**")
                    st.warning(q["question"])

                    if st.session_state.interview_graded is None:
                        user_ans = st.text_area(
                            "Your answer",
                            value=st.session_state.interview_answer,
                            height=150,
                            key=f"interview_ans_{idx}",
                            placeholder="Type your answer here..."
                        )
                        st.session_state.interview_answer = user_ans

                        col1, col2 = st.columns([1, 4])
                        with col1:
                            if st.button("Submit", disabled=not user_ans.strip()):
                                with st.spinner("Grading..."):
                                    try:
                                        grade = score_answer(
                                            q["question"],
                                            q.get("code", ""),
                                            q["answer"],
                                            user_ans,
                                            q["topic"]
                                        )
                                        st.session_state.interview_graded = grade
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Grading failed: {e}")
                        with col2:
                            if st.button("Skip question"):
                                st.session_state.interview_scores.append({"score": 0, "verdict": "Skipped", "topic": q["topic"], "type": q["type"]})
                                st.session_state.interview_idx += 1
                                st.session_state.interview_answer = ""
                                st.session_state.interview_graded = None
                                st.rerun()

                    else:
                        grade = st.session_state.interview_graded
                        verdict_color = {"Strong": "success", "Acceptable": "info", "Needs Work": "warning", "Incorrect": "error"}.get(grade["verdict"], "info")

                        st.markdown("**Your answer**")
                        st.write(st.session_state.interview_answer)

                        st.markdown("**Claude's grade**")
                        getattr(st, verdict_color)(f"{grade['verdict']} — {grade['score']}/10")

                        if grade.get("what_they_got_right"):
                            st.markdown(f"**Got right:** {grade['what_they_got_right']}")
                        if grade.get("what_they_missed"):
                            st.markdown(f"**Missed:** {grade['what_they_missed']}")
                        if grade.get("coaching_note"):
                            st.markdown(f"**Remember:** {grade['coaching_note']}")

                        with st.expander("Show model answer"):
                            st.success(q["answer"])

                        st.session_state.interview_scores.append({
                            "score": grade["score"],
                            "verdict": grade["verdict"],
                            "topic": q["topic"],
                            "type": q["type"],
                            "coaching_note": grade.get("coaching_note", "")
                        })

                        if st.button("Next question"):
                            st.session_state.interview_idx += 1
                            st.session_state.interview_answer = ""
                            st.session_state.interview_graded = None
                            st.rerun()

                else:
                    # Session complete
                    scores = st.session_state.interview_scores
                    total_score = sum(s["score"] for s in scores)
                    max_score = len(scores) * 10
                    pct = int((total_score / max_score) * 100) if max_score > 0 else 0

                    st.success(f"Interview complete -- {total_score}/{max_score} ({pct}%)")
                    st.progress(pct / 100)

                    verdict_counts = {}
                    for s in scores:
                        verdict_counts[s["verdict"]] = verdict_counts.get(s["verdict"], 0) + 1

                    cols = st.columns(len(verdict_counts))
                    for i, (v, c) in enumerate(verdict_counts.items()):
                        cols[i].metric(v, c)

                    st.markdown("#### Question breakdown")
                    for i, s in enumerate(scores):
                        bar = "█" * s["score"] + "░" * (10 - s["score"])
                        st.markdown(f"**Q{i+1}** `{s['topic']}` [{s['type']}] -- {s['score']}/10 [{bar}] {s['verdict']}")
                        if s.get("coaching_note") and s["verdict"] not in ("Strong", "Skipped"):
                            st.caption(f"    Remember: {s['coaching_note']}")

                    # Find weakest areas
                    weak = [s for s in scores if s["verdict"] in ("Needs Work", "Incorrect", "Skipped")]
                    if weak:
                        st.markdown("#### Focus areas for next session")
                        for s in weak:
                            st.markdown(f"- **{s['topic']}** ({s['type']})")

                    # Save report
                    os.makedirs("output", exist_ok=True)
                    report = {
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "total_score": total_score,
                        "max_score": max_score,
                        "pct": pct,
                        "questions": scores
                    }
                    with open("output/interview_report.json", "w", encoding="utf-8") as f:
                        json.dump(report, f, indent=2)
                    append_session(report)

                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("New session"):
                            st.session_state.interview_active = False
                            st.session_state.interview_pool = []
                            st.session_state.interview_idx = 0
                            st.session_state.interview_scores = []
                            st.session_state.interview_answer = ""
                            st.session_state.interview_graded = None
                            st.rerun()
                    with col2:
                        with open("output/interview_report.json", "rb") as f:
                            st.download_button("Download report", f, file_name="interview_report.json")

# Tab 3: Card Review
with tab3:
    st.subheader("Review flashcards before exporting to Anki")

    if "cards" not in st.session_state:
        st.session_state.cards = []
    if "current_card" not in st.session_state:
        st.session_state.current_card = 0
    if "show_answer" not in st.session_state:
        st.session_state.show_answer = False

    review_mode = st.radio("Review mode", ["Flashcards", "Scenarios"], horizontal=True, key="review_mode")

    if st.button("Load Cards"):
        if review_mode == "Flashcards":
            loaded = load_anki_cards()
        else:
            loaded = load_scenario_cards()
        if loaded:
            st.session_state.cards = loaded
            st.session_state.current_card = 0
            st.session_state.show_answer = False
            st.success(f"Loaded {len(loaded)} {'flashcards' if review_mode == 'Flashcards' else 'scenarios'}")
        else:
            st.warning(f"No {'flashcards' if review_mode == 'Flashcards' else 'scenarios'} found. Run Generate first.")

    cards = st.session_state.cards

    if cards:
        total = len(cards)
        approved = len([c for c in cards if c["status"] == "approved"])
        rejected = len([c for c in cards if c["status"] == "rejected"])
        pending = len([c for c in cards if c["status"] == "pending"])

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total", total)
        col2.metric("Approved", approved)
        col3.metric("Rejected", rejected)
        col4.metric("Pending", pending)

        st.progress(min((approved + rejected) / total, 1.0))

        idx = st.session_state.current_card

        if idx < total:
            card = cards[idx]
            if card.get("setup"):
                is_multi = card.get("type") == "multi_file" or bool(card.get("files"))
                label = "Multi-File Scenario" if is_multi else "Scenario"
                st.markdown(f"**{label} {idx + 1} of {total}** -- type: `{card.get('type','')}` | topic: `{card.get('topic','')}` | confidence: `{card.get('confidence','')}`")
                st.markdown("---")
                st.markdown("**Setup**")
                st.info(card["setup"])
                if is_multi and card.get("files"):
                    for fi, f in enumerate(card["files"]):
                        st.markdown(f"**File {fi+1}: `{f.get('name', f'file_{fi+1}')}`**")
                        st.code(f.get("content", ""), language="python" if f.get("name","").endswith(".py") else None)
                elif card.get("code"):
                    st.markdown("**Code / Config**")
                    st.code(card["code"])
                st.markdown("**Question**")
                st.warning(card["question"])
            else:
                # Flashcard
                st.markdown(f"**Card {idx + 1} of {total}** -- `{card.get('tags','')}` | confidence: `{card.get('confidence','')}` | difficulty: `{card.get('difficulty','')}`")
                st.markdown("---")
                st.markdown("**Question**")
                st.info(card["question"])

            if st.session_state.show_answer:
                st.markdown("**Answer**")
                st.success(card["answer"])

                col1, col2, col3 = st.columns(3)
                with col1:
                    if st.button("Approve", key=f"approve_{idx}"):
                        st.session_state.cards[idx]["status"] = "approved"
                        st.session_state.current_card += 1
                        st.session_state.show_answer = False
                        st.rerun()
                with col2:
                    if st.button("Reject", key=f"reject_{idx}"):
                        st.session_state.cards[idx]["status"] = "rejected"
                        st.session_state.current_card += 1
                        st.session_state.show_answer = False
                        st.rerun()
                with col3:
                    if st.button("Skip", key=f"skip_{idx}"):
                        st.session_state.current_card += 1
                        st.session_state.show_answer = False
                        st.rerun()
            else:
                if st.button("Show Answer"):
                    st.session_state.show_answer = True
                    st.rerun()
        else:
            st.success("All cards reviewed!")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Save and Export"):
                    a_count, r_count = save_reviewed_cards(st.session_state.cards)
                    st.success(f"Saved {a_count} approved, {r_count} moved to anki_rejected.csv")
                    with open("output/anki.csv", "rb") as f:
                        st.download_button("Download approved anki.csv", f, file_name="anki.csv", mime="text/csv")
            with col2:
                if st.button("Review Again"):
                    st.session_state.current_card = 0
                    st.session_state.show_answer = False
                    st.rerun()

# Tab 4: JD Analyzer
with tab4:
    st.subheader("JD gap analyzer")

    kb = load_knowledge_base()

    if not kb:
        st.warning("No knowledge base found. Run Convert first.")
    else:
        mode = st.radio("Mode", ["Single JD", "Batch (multiple JDs)"], horizontal=True)

        if mode == "Single JD":
            jd_text = st.text_area("Paste job description", height=200, placeholder="Paste a Cloud Engineer JD here...")

            if st.button("Analyze JD", disabled=not jd_text):
                with st.spinner("Running gap analysis..."):
                    try:
                        result = run_gap_analysis(jd_text, kb)

                        covered = len([s for s in result["matched_skills"] if s["status"] == "covered"])
                        partial = len([s for s in result["matched_skills"] if s["status"] == "partial"])
                        gaps = len([s for s in result["matched_skills"] if s["status"] == "gap"])

                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Readiness", f"{result['readiness_score']}/100")
                        col2.metric("Covered", covered)
                        col3.metric("Partial", partial)
                        col4.metric("Gaps", gaps)

                        st.info(result["summary"])

                        st.markdown("#### Skill coverage")
                        for s in result["matched_skills"]:
                            label = "covered" if s["status"] == "covered" else "partial" if s["status"] == "partial" else "gap"
                            st.markdown(f"[{label}] **{s['domain']}** -- {s['candidate_confidence']} confidence")

                        if result["priority_gaps"]:
                            st.markdown("#### Priority gaps")
                            for g in result["priority_gaps"]:
                                st.markdown(f"[{g['urgency']}] **{g['domain']}** -- {g['action']}")

                        if result["strengths"]:
                            st.markdown("#### Lead with these")
                            for s in result["strengths"]:
                                st.markdown(f"- {s}")

                        os.makedirs("output", exist_ok=True)
                        with open("output/jd_report.json", "w", encoding="utf-8") as f:
                            json.dump(result, f, indent=2)

                        saved_path = save_jd_report(result, prefix="single")
                        report_count = trigger_aggregation()
                        if report_count >= 3:
                            st.caption(f"Market frequencies updated from {report_count} JD reports")
                        else:
                            st.caption(f"JD report saved ({report_count}/3 needed to activate live frequencies)")

                        with open("output/jd_report.json", "rb") as f:
                            st.download_button("Download full report", f, file_name="jd_report.json")

                    except Exception as e:
                        st.error(f"Error: {e}")

        else:
            st.caption("Separate each JD with a --- line, or upload a .txt file with multiple JDs")

            batch_input_mode = st.radio("Input method", ["Paste text", "Upload file"], horizontal=True)

            if batch_input_mode == "Paste text":
                batch_text = st.text_area(
                    "Paste multiple JDs separated by ---",
                    height=300,
                    placeholder="Cloud Engineer at CompanyA...\n\n---\n\nDevOps Engineer at CompanyB...\n\n---\n\nSRE at CompanyC..."
                )
                jd_list = parse_jds(batch_text) if batch_text else []
            else:
                uploaded = st.file_uploader("Upload .txt file with multiple JDs", type="txt")
                if uploaded:
                    batch_text = uploaded.read().decode("utf-8", errors="ignore")
                    jd_list = parse_jds(batch_text)
                else:
                    jd_list = []

            if jd_list:
                st.caption(f"{len(jd_list)} JD(s) detected")

            if st.button("Run Batch Analysis", disabled=not jd_list):
                with st.spinner(f"Analyzing {len(jd_list)} JDs..."):
                    try:
                        batch_result = run_batch_analysis(jd_list, kb)
                        agg = batch_result["aggregate"]

                        col1, col2, col3 = st.columns(3)
                        col1.metric("JDs analyzed", len(jd_list))
                        col2.metric("Avg readiness", f"{agg['avg_readiness_score']}/100")
                        col3.metric("Best fit", agg["best_fit_role"])

                        st.info(agg["summary"])

                        st.markdown("#### Individual results")
                        for r in batch_result["individual_results"]:
                            with st.expander(f"{r['role_title']} -- {r['readiness_score']}/100 ({r['overall_readiness']})"):
                                if r["top_gaps"]:
                                    st.markdown("**Gaps:** " + ", ".join(r["top_gaps"]))
                                if r["top_strengths"]:
                                    st.markdown("**Strengths:** " + ", ".join(r["top_strengths"]))

                        if agg["most_common_gaps"]:
                            st.markdown("#### Most common gaps across all JDs")
                            for g in agg["most_common_gaps"]:
                                st.markdown(f"[{g['urgency']}] **{g['skill']}** -- appears in {g['appears_in']} of {len(jd_list)} JDs")

                        if agg["consistent_strengths"]:
                            st.markdown("#### Consistent strengths across all JDs")
                            for s in agg["consistent_strengths"]:
                                st.markdown(f"- {s}")

                        os.makedirs("output", exist_ok=True)
                        with open("output/batch_report.json", "w", encoding="utf-8") as f:
                            json.dump(batch_result, f, indent=2)

                        save_jd_report(batch_result, prefix="batch")
                        report_count = trigger_aggregation()
                        if report_count >= 3:
                            st.caption(f"Market frequencies updated from {report_count} JD reports")
                        else:
                            st.caption(f"Batch report saved ({report_count}/3 needed to activate live frequencies)")

                        # Save top gaps as jd_report so Weekly Plan can use it
                        if agg["most_common_gaps"]:
                            top_gaps = [
                                {"domain": g["skill"], "urgency": g["urgency"], "action": f"Appears in {g['appears_in']} of {len(jd_list)} JDs"}
                                for g in agg["most_common_gaps"][:5]
                            ]
                            synthetic_report = {
                                "role_title": agg["best_fit_role"],
                                "readiness_score": agg["avg_readiness_score"],
                                "priority_gaps": top_gaps,
                                "strengths": agg["consistent_strengths"],
                                "summary": agg["summary"]
                            }
                            with open("output/jd_report.json", "w", encoding="utf-8") as f:
                                json.dump(synthetic_report, f, indent=2)
                            st.caption("jd_report.json updated -- Weekly Plan and Topic Suggestions will use batch results")

                        with open("output/batch_report.json", "rb") as f:
                            st.download_button("Download batch_report.json", f, file_name="batch_report.json")

                    except Exception as e:
                        st.error(f"Error: {e}")

# Tab 5: Weekly Plan
with tab5:
    st.subheader("Weekly execution plan")

    jd_report_path = "output/jd_report.json"

    if not os.path.exists(jd_report_path):
        st.warning("No JD report found. Run the JD Analyzer first.")
    else:
        with open(jd_report_path, "r", encoding="utf-8") as f:
            jd_report = json.load(f)

        role_title = jd_report.get("role_title", "Cloud Engineer")
        priority_gaps = jd_report.get("priority_gaps", [])

        if not priority_gaps:
            st.warning("No priority gaps found in last JD report.")
        else:
            st.caption(f"Based on: {role_title}")
            st.markdown("#### Top gaps from last JD analysis")
            for g in priority_gaps[:2]:
                st.markdown(f"[{g['urgency']}] **{g['domain']}** -- {g['action']}")

            hours = st.slider("Hours available this week", min_value=2, max_value=20, value=10, step=1)

            if st.button("Generate Weekly Plan"):
                with st.spinner("Building your execution plan..."):
                    try:
                        plan = generate_weekly_plan(priority_gaps, role_title, hours)
                        st.markdown("---")
                        st.markdown(plan)

                        os.makedirs("output", exist_ok=True)
                        plan_path = "output/weekly_plan.md"
                        with open(plan_path, "w", encoding="utf-8") as f:
                            f.write(f"# Weekly Execution Plan -- {role_title}\n\n")
                            f.write(f"Generated {datetime.now().strftime('%Y-%m-%d')} | {hours}hrs available\n\n")
                            f.write(plan)

                        with open(plan_path, "rb") as f:
                            st.download_button("Download weekly_plan.md", f, file_name="weekly_plan.md", mime="text/markdown")

                    except Exception as e:
                        st.error(f"Error: {e}")

# Tab 6: Topic Suggestions
with tab6:
    st.subheader("What to learn next")

    kb = load_knowledge_base()

    if not kb:
        st.warning("No knowledge base found. Run Convert first.")
    else:
        jd_report = None
        jd_report_path = "output/jd_report.json"
        # Reload frequencies in case new reports were added this session
        current_freqs, freq_source, freq_count = load_jd_frequencies()

        if os.path.exists(jd_report_path):
            with open(jd_report_path, "r", encoding="utf-8") as f:
                jd_report = json.load(f)
            st.caption(f"JD report detected -- suggestions factoring in role gaps | frequency source: {freq_source}")
        else:
            st.caption(f"No JD report found -- market frequency source: {freq_source}")

        if freq_count < 3 and freq_count > 0:
            st.info(f"Run {3 - freq_count} more JD analysis to activate live market frequencies. Using baseline data until then.")

        if st.button("Generate Suggestions"):
            with st.spinner("Analyzing your knowledge gaps against market demand..."):
                try:
                    suggestions = generate_topic_suggestions(kb, jd_report)

                    st.info(suggestions["summary"])

                    if suggestions.get("uncovered_high_demand"):
                        st.markdown("#### Not in your notes -- high market demand")
                        for item in suggestions["uncovered_high_demand"]:
                            freq_pct = int(item["market_frequency"] * 100)
                            with st.expander(f"{item['topic']} -- appears in {freq_pct}% of JDs"):
                                st.markdown(f"**Why now:** {item['reason']}")
                                st.code(item["suggested_note_prompt"], language=None)
                                st.caption("Paste the above into your raw notes to get started")
                                col1, col2 = st.columns([1, 3])
                                with col1:
                                    if st.button("Cold test me on this", key=f"cold_{item['topic']}"):
                                        with st.spinner(f"Generating questions on {item['topic']}..."):
                                            try:
                                                raw = generate_cold_test_questions(
                                                    item["topic"],
                                                    item["market_frequency"],
                                                    "High"
                                                )
                                                from pipeline.generate import parse_qa
                                                cards = parse_qa(raw)
                                                if cards:
                                                    st.session_state["cold_test_cards"] = [
                                                        {"question": q, "answer": a, "topic": item["topic"]}
                                                        for q, a in cards
                                                    ]
                                                    st.session_state["cold_test_idx"] = 0
                                                    st.session_state["cold_test_show"] = False
                                                    st.rerun()
                                            except Exception as e:
                                                st.error(f"Error: {e}")
                                with col2:
                                    st.caption("No notes needed -- tests what you actually know right now")

                    # Cold test inline runner
                    if st.session_state.get("cold_test_cards"):
                        cards = st.session_state["cold_test_cards"]
                        idx = st.session_state.get("cold_test_idx", 0)
                        total = len(cards)
                        if idx < total:
                            card = cards[idx]
                            st.markdown("---")
                            st.markdown(f"#### Cold Test: {card['topic']} -- Question {idx+1} of {total}")
                            st.info(card["question"])
                            if st.session_state.get("cold_test_show"):
                                st.success(card["answer"])
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.button("Next", key=f"cold_next_{idx}"):
                                        st.session_state["cold_test_idx"] += 1
                                        st.session_state["cold_test_show"] = False
                                        st.rerun()
                                with col2:
                                    if st.button("End test", key=f"cold_end_{idx}"):
                                        st.session_state["cold_test_cards"] = []
                                        st.session_state["cold_test_idx"] = 0
                                        st.rerun()
                            else:
                                if st.button("Show answer", key=f"cold_show_{idx}"):
                                    st.session_state["cold_test_show"] = True
                                    st.rerun()
                        else:
                            st.success("Cold test complete -- if you struggled, add notes on this topic and run Convert")
                            if st.button("Clear test"):
                                st.session_state["cold_test_cards"] = []
                                st.session_state["cold_test_idx"] = 0
                                st.rerun()

                    if suggestions.get("weak_but_in_demand"):
                        st.markdown("#### In your notes but needs work -- high market demand")
                        for item in suggestions["weak_but_in_demand"]:
                            freq_pct = int(item["market_frequency"] * 100)
                            with st.expander(f"{item['topic']} -- {item['current_confidence']} confidence | {freq_pct}% of JDs"):
                                st.markdown(f"**Why prioritize:** {item['reason']}")
                                st.code(item["suggested_note_prompt"], language=None)
                                col1, col2 = st.columns([1, 3])
                                with col1:
                                    if st.button("Cold test me on this", key=f"weak_{item['topic']}"):
                                        with st.spinner(f"Generating questions on {item['topic']}..."):
                                            try:
                                                raw = generate_cold_test_questions(
                                                    item["topic"],
                                                    item["market_frequency"],
                                                    item.get("urgency", "Medium")
                                                )
                                                from pipeline.generate import parse_qa
                                                cards = parse_qa(raw)
                                                if cards:
                                                    st.session_state["cold_test_cards"] = [
                                                        {"question": q, "answer": a, "topic": item["topic"]}
                                                        for q, a in cards
                                                    ]
                                                    st.session_state["cold_test_idx"] = 0
                                                    st.session_state["cold_test_show"] = False
                                                    st.rerun()
                                            except Exception as e:
                                                st.error(f"Error: {e}")
                                with col2:
                                    st.caption("Tests your current knowledge before committing to a full study session")

                    if suggestions.get("emerging_to_watch"):
                        st.markdown("#### Emerging -- worth watching")
                        for item in suggestions["emerging_to_watch"]:
                            st.markdown(f"- **{item['topic']}** -- {item['reason']}")

                    os.makedirs("output", exist_ok=True)
                    with open("output/topic_suggestions.json", "w", encoding="utf-8") as f:
                        json.dump(suggestions, f, indent=2)

                except Exception as e:
                    st.error(f"Error: {e}")

# Tab 7: Knowledge Base
with tab7:
    st.subheader("Knowledge base viewer")

    kb = load_knowledge_base()

    if not kb:
        st.warning("No knowledge base found. Run Convert first.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            filter_type = st.selectbox("Filter by type", ["All", "project", "certification", "exploration"])
        with col2:
            filter_conf = st.selectbox("Filter by confidence", ["All", "High", "Medium", "Low"])

        filtered = kb
        if filter_type != "All":
            filtered = [e for e in filtered if e.get("type") == filter_type]
        if filter_conf != "All":
            filtered = [e for e in filtered if e.get("confidence") == filter_conf]

        st.caption(f"Showing {len(filtered)} of {len(kb)} entries")

        for entry in filtered:
            conf = entry.get("confidence", "Low")
            conf_label = "High" if conf == "High" else "Med" if conf == "Medium" else "Low"
            label = entry.get("topic") or entry.get("concept") or entry.get("tool") or entry.get("error", "entry")
            qs = score_kb_entry(entry)
            q_score = qs["score"]
            q_indicator = "★" * min(q_score // 2, 5) + "☆" * (5 - min(q_score // 2, 5))
            with st.expander(f"[{conf_label}] [{entry.get('type', '?')}] {label}  |  quality: {q_indicator} {q_score}/10"):
                if qs["issues"]:
                    st.caption("Enrichment suggestions: " + " | ".join(qs["issues"]))
                st.json(entry)