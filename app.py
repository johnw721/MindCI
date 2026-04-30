import streamlit as st
import json
import os
import shutil
from datetime import datetime
from anthropic import Anthropic

client = Anthropic()

st.set_page_config(page_title="MindCI", page_icon="🧠", layout="wide")

# Shared helpers

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

# Convert logic

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
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def parse_and_save_json(raw_response):
    clean = raw_response.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    parsed = json.loads(clean.strip())
    os.makedirs("data", exist_ok=True)
    with open("data/structured.json", "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)
    return parsed

# Generate logic

def build_dynamic_prompt(base_prompt, entry):
    confidence = entry.get("confidence", "Low")
    difficulty = entry.get("difficulty", "Medium")

    if confidence == "High":
        modifier = """
CONFIDENCE LEVEL: High - candidate knows this well.
Generate questions that test edge cases, failure modes, and cross-domain scenarios.
Avoid surface-level definitions.
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""
    elif confidence == "Medium":
        modifier = """
CONFIDENCE LEVEL: Medium - candidate has partial understanding.
Generate questions that reinforce core mechanisms, common mistakes, and misconceptions.
Include one "explain to a junior engineer" question.
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""
    else:
        modifier = """
CONFIDENCE LEVEL: Low - candidate has minimal understanding.
Generate foundational questions from first principles. Answers should teach, not just hint.
Format each question and answer exactly like this:
Q: your question here
A: your answer here
"""

    difficulty_note = ""
    if difficulty == "Hard":
        difficulty_note = "\nDIFFICULTY: Hard - push deeper, assume technical audience.\n"
    elif difficulty == "Easy":
        difficulty_note = "\nDIFFICULTY: Easy - keep language approachable.\n"

    return f"{base_prompt}{modifier}{difficulty_note}\n\nDATA:\n{json.dumps(entry, indent=2)}"

def parse_qa(text):
    cards = []
    lines = text.split("\n")
    q, a_lines = None, []
    for line in lines:
        line = line.strip()
        if line.startswith("Q:"):
            if q and a_lines:
                cards.append((q, " ".join(a_lines)))
            q = line.replace("Q:", "").strip()
            a_lines = []
        elif line.startswith("A:"):
            a_lines = [line.replace("A:", "").strip()]
        elif a_lines and line:
            a_lines.append(line)
    if q and a_lines:
        cards.append((q, " ".join(a_lines)))
    return cards

def classify(entry):
    c = entry.get("confidence", "Low")
    if c == "High":
        return "AUTO-PASS"
    elif c == "Medium":
        return "REVIEW"
    return "PRIORITY"

# Flashcard review logic

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

# Topic suggestion logic

JD_SKILL_FREQUENCIES = {
    "Kubernetes": 0.85, "Terraform": 0.80, "AWS": 0.90, "CI/CD": 0.82,
    "Python": 0.75, "Docker": 0.78, "Helm": 0.65, "Prometheus": 0.60,
    "Grafana": 0.58, "ArgoCD": 0.55, "GitOps": 0.52, "Ansible": 0.50,
    "Linux": 0.72, "Networking/VPC": 0.68, "IAM": 0.70, "Security/WAF": 0.62,
    "Observability": 0.60, "EKS": 0.65, "Lambda": 0.70, "API Gateway": 0.58,
    "AIOps": 0.40, "MLOps": 0.35, "Cost Optimization": 0.48, "SRE practices": 0.55
}

def generate_topic_suggestions(knowledge_base, jd_report=None):
    kb_summary = [{
        "domain": e.get("topic") or e.get("concept") or e.get("tool") or e.get("error", "unknown"),
        "confidence": e.get("confidence", "Low"),
        "type": e.get("type")
    } for e in knowledge_base]

    jd_gaps = []
    if jd_report:
        jd_gaps = [g["domain"] for g in jd_report.get("priority_gaps", [])]
    jd_gap_block = f"\nActive JD priority gaps: {jd_gaps}" if jd_gaps else ""

    prompt = f"""You are a learning advisor for a Cloud/DevOps engineer preparing for job interviews.

CURRENT KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}

MARKET SKILL FREQUENCIES (how often skills appear in Cloud Engineer JDs):
{json.dumps(JD_SKILL_FREQUENCIES, indent=2)}
{jd_gap_block}

Analyze the knowledge base against market demand and return ONLY a JSON object, no markdown:
{{
  "uncovered_high_demand": [
    {{"topic": "...", "market_frequency": 0.0, "reason": "one sentence why this matters now", "suggested_note_prompt": "a specific prompt they can use to start learning this"}}
  ],
  "weak_but_in_demand": [
    {{"topic": "...", "current_confidence": "Low|Medium", "market_frequency": 0.0, "reason": "one sentence", "suggested_note_prompt": "..."}}
  ],
  "emerging_to_watch": [
    {{"topic": "...", "reason": "one sentence on why this is gaining traction"}}
  ],
  "summary": "2 sentence overview of biggest gaps relative to market demand"
}}

uncovered_high_demand: topics with market_frequency > 0.5 with ZERO entries in knowledge base
weak_but_in_demand: topics present but Low confidence AND market_frequency > 0.5
emerging_to_watch: up to 3 topics gaining traction in Cloud/DevOps not yet in knowledge base
Limit each list to top 5 items maximum."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

# JD Analysis logic

def run_gap_analysis(jd_text, knowledge_base):
    kb_summary = [{
        "domain": e.get("topic") or e.get("concept") or e.get("tool") or e.get("error", "unknown"),
        "type": e.get("type"),
        "confidence": e.get("confidence", "Unknown"),
        "difficulty": e.get("difficulty", "Unknown"),
    } for e in knowledge_base]

    prompt = f"""You are analyzing a job description against a candidate's technical knowledge base.

CANDIDATE KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}

JOB DESCRIPTION:
{jd_text}

Return ONLY a JSON object, no markdown, no extra text:
{{
  "role_title": "extracted role title",
  "overall_readiness": "Ready|Partial|Not Ready",
  "readiness_score": <0-100>,
  "matched_skills": [{{"domain": "...", "candidate_confidence": "High|Medium|Low|None", "status": "covered|partial|gap"}}],
  "priority_gaps": [{{"domain": "...", "urgency": "High|Medium", "action": "one-line recommendation"}}],
  "strengths": ["..."],
  "summary": "2 sentence readiness summary"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# Batch JD analysis logic

def run_batch_analysis(jd_texts, knowledge_base):
    kb_summary = [{
        "domain": e.get("topic") or e.get("concept") or e.get("tool") or e.get("error", "unknown"),
        "type": e.get("type"),
        "confidence": e.get("confidence", "Unknown"),
        "difficulty": e.get("difficulty", "Unknown"),
    } for e in knowledge_base]

    jd_block = ""
    for i, jd in enumerate(jd_texts):
        jd_block += f"\n--- JD {i+1} ---\n{jd.strip()}\n"

    prompt = f"""You are analyzing multiple job descriptions against a candidate's technical knowledge base.

CANDIDATE KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}

JOB DESCRIPTIONS:
{jd_block}

Return ONLY a JSON object, no markdown, no extra text:
{{
  "individual_results": [
    {{
      "jd_number": 1,
      "role_title": "...",
      "readiness_score": 0,
      "overall_readiness": "Ready|Partial|Not Ready",
      "top_gaps": ["gap1", "gap2", "gap3"],
      "top_strengths": ["strength1", "strength2"]
    }}
  ],
  "aggregate": {{
    "most_common_gaps": [{{"skill": "...", "appears_in": 0, "urgency": "High|Medium"}}],
    "consistent_strengths": ["skill1", "skill2"],
    "avg_readiness_score": 0,
    "best_fit_role": "role title from above",
    "summary": "2-3 sentence summary of overall market fit and biggest pattern across all JDs"
  }}
}}

most_common_gaps: skills that appear as gaps in 2 or more JDs, sorted by frequency
consistent_strengths: skills covered across most JDs
Limit most_common_gaps to top 8."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def parse_jds(text):
    import re
    parts = re.split(r"(?m)^---+\s*$", text.strip())
    jds = [p.strip() for p in parts if len(p.strip()) > 100]
    return jds if len(jds) > 1 else [text.strip()]

# Weekly Plan logic

def generate_weekly_plan(priority_gaps, role_title, hours_per_week):
    top_gaps = priority_gaps[:2]
    skills_block = "\n".join(
        [f"- {g['domain']} (urgency: {g['urgency']}): {g['action']}" for g in top_gaps]
    )
    prompt = f"""You are a career coach for a Cloud/DevOps engineer actively job hunting.

Target role: {role_title}
Available study time: {hours_per_week} hours this week
Top skill gaps to close:
{skills_block}

For EACH skill gap above, generate exactly:
1. Hands-on project - a concrete mini-project they can build and put on GitHub
2. Blog/article idea - a specific title they could write to demonstrate knowledge
3. Lab/tutorial - a reusable step-by-step exercise
4. Resume bullet - one ready-to-paste bullet point assuming they complete the project
5. Interview story - a 2-3 sentence STAR-format story they can tell in interviews

Then generate a single 7-day execution plan covering both skills within {hours_per_week} hours.
Be specific with days (Day 1, Day 2 etc.) and time estimates per task.

Format clearly with headers for each skill and the weekly plan."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# Scenario generation logic

SCENARIO_TYPES = {
    "what_does_this_do": "Read a code/config snippet and explain its behavior",
    "whats_wrong":       "Identify the bug, misconfiguration, or security issue",
    "fix_it":            "Diagnose the problem and produce a corrected version",
    "architecture":      "Evaluate a system design and identify tradeoffs or failure points"
}

def generate_scenarios(entry):
    entry_type = entry.get("type", "exploration")
    confidence = entry.get("confidence", "Low")
    label = entry.get("topic") or entry.get("concept") or entry.get("tool") or entry.get("error", "unknown")

    # Bias scenario types by entry type
    if entry_type == "project":
        types_to_use = ["whats_wrong", "fix_it", "what_does_this_do"]
    elif entry_type == "certification":
        types_to_use = ["what_does_this_do", "whats_wrong", "architecture"]
    else:
        types_to_use = ["what_does_this_do", "architecture", "whats_wrong"]

    # High confidence gets harder scenario types
    if confidence == "High":
        difficulty_instruction = "Make scenarios complex. Combine multiple concepts. Include subtle bugs that are easy to miss."
    elif confidence == "Medium":
        difficulty_instruction = "Make scenarios moderately complex. Bugs should be identifiable with careful reading."
    else:
        difficulty_instruction = "Keep scenarios foundational. Bugs should be clear once you know the concept."

    prompt = f"""You are a senior Cloud/DevOps engineer writing technical interview scenarios.

Topic: {label}
Entry type: {entry_type}
Candidate confidence: {confidence}

Source material:
{json.dumps(entry, indent=2)}

Generate exactly 3 scenario-based interview questions using this material.
Use a mix of these types: {types_to_use}
{difficulty_instruction}

Each scenario must follow this EXACT format with no deviation:

SCENARIO: [what_does_this_do|whats_wrong|fix_it|architecture]
SETUP:
<2-4 sentences setting the scene. e.g. "You are reviewing a Lambda function that processes S3 events...">
CODE_OR_CONFIG:
<the actual code, YAML, policy, or architecture description — make it realistic and specific to the topic>
QUESTION:
<the specific question being asked>
ANSWER:
<thorough explanation of the correct answer, including why common wrong answers are wrong>
---

Repeat for each of the 3 scenarios. Separate with ---
Do not include any text before the first SCENARIO or after the last ANSWER."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def parse_scenarios(text):
    scenarios = []
    blocks = text.strip().split("---")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        s = {}
        for field in ["SCENARIO", "SETUP", "CODE_OR_CONFIG", "QUESTION", "ANSWER"]:
            start = block.find(f"{field}:")
            if start == -1:
                continue
            end = len(block)
            for next_field in ["SCENARIO", "SETUP", "CODE_OR_CONFIG", "QUESTION", "ANSWER"]:
                nf_pos = block.find(f"{next_field}:", start + len(field) + 1)
                if nf_pos != -1 and nf_pos < end:
                    end = nf_pos
            s[field.lower()] = block[start + len(field) + 1:end].strip()
        if "question" in s and "answer" in s:
            scenarios.append(s)
    return scenarios

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
            "question": s.get("question", ""),
            "answer": s.get("answer", ""),
            "topic": s.get("topic", ""),
            "confidence": s.get("confidence", ""),
            "status": "pending"
        })
    return cards


# Mock interview logic

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

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def build_interview_pool(n=8):
    import random
    pool = []

    # Load scenarios
    scenario_path = "output/scenarios.json"
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
                "question": s.get("question", ""),
                "answer": s.get("answer", "")
            })

    # Load flashcards
    anki_path = "output/anki.csv"
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

    if st.button("Run Convert", disabled=not uploaded_files):
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
                parsed = parse_and_save_json(raw_response)

                VALID_TYPES = {"project", "certification", "exploration"}
                for w in [e for e in parsed if e.get("type") not in VALID_TYPES]:
                    st.warning(f"Unknown type: {w.get('type')} -- {w.get('source')}")

                st.success(f"Saved {len(parsed)} entries to data/structured.json")
                st.json(parsed[:3])

                os.makedirs("archive", exist_ok=True)
                for f in uploaded_files:
                    src = f"raw/{f.name}"
                    dst = f"archive/{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{f.name}"
                    if os.path.exists(src):
                        shutil.move(src, dst)
                st.info("Raw files archived")

            except Exception as e:
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
                        raw = generate_scenarios(entry)
                        parsed = parse_scenarios(raw)
                        for s in parsed:
                            s["topic"] = label
                            s["confidence"] = confidence
                            s["entry_type"] = entry_type
                            all_scenarios.append(s)

                        md_output += f"\n\n## {entry_type.upper()} -- {label} [{confidence}]\n\n"
                        for s in parsed:
                            md_output += f"### [{s.get('scenario','').upper()}]\n"
                            md_output += f"**Setup:** {s.get('setup','')}\n\n"
                            if s.get("code_or_config"):
                                md_output += f"```\n{s.get('code_or_config','')}\n```\n\n"
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
                    if q.get("code"):
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
                # Scenario card
                st.markdown(f"**Scenario {idx + 1} of {total}** -- type: `{card.get('type','')}` | topic: `{card.get('topic','')}` | confidence: `{card.get('confidence','')}`")
                st.markdown("---")
                st.markdown("**Setup**")
                st.info(card["setup"])
                if card.get("code"):
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
        if os.path.exists(jd_report_path):
            with open(jd_report_path, "r", encoding="utf-8") as f:
                jd_report = json.load(f)
            st.caption("JD report detected -- suggestions will factor in your active role gaps")
        else:
            st.caption("No JD report found -- suggestions based on market frequency only")

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

                    if suggestions.get("weak_but_in_demand"):
                        st.markdown("#### In your notes but needs work -- high market demand")
                        for item in suggestions["weak_but_in_demand"]:
                            freq_pct = int(item["market_frequency"] * 100)
                            with st.expander(f"{item['topic']} -- {item['current_confidence']} confidence | {freq_pct}% of JDs"):
                                st.markdown(f"**Why prioritize:** {item['reason']}")
                                st.code(item["suggested_note_prompt"], language=None)

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
            with st.expander(f"[{conf_label}] [{entry.get('type', '?')}] {label}"):
                st.json(entry)