from anthropic import Anthropic
import json
import os
import sys

client = Anthropic()

def load_knowledge_base():
    path = "data/structured.json"
    if not os.path.exists(path):
        print("❌ No structured.json found. Run 'python mindci.py convert' first.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_jd(path=None):
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    print("Paste your job description below. Press Enter twice when done:\n")
    lines = []
    while True:
        line = input()
        if line == "" and lines and lines[-1] == "":
            break
        lines.append(line)
    return "\n".join(lines)

def run_gap_analysis(jd_text, knowledge_base):
    kb_summary = []
    for entry in knowledge_base:
        kb_summary.append({
            "domain": entry.get("topic") or entry.get("concept") or entry.get("tool") or entry.get("error", "unknown"),
            "type": entry.get("type"),
            "confidence": entry.get("confidence", "Unknown"),
            "difficulty": entry.get("difficulty", "Unknown"),
            "source": entry.get("source", "")
        })

    prompt = f"""You are analyzing a job description against a candidate's technical knowledge base.

CANDIDATE KNOWLEDGE BASE:
{json.dumps(kb_summary, indent=2)}

JOB DESCRIPTION:
{jd_text}

Return ONLY a JSON object with this exact structure, no markdown, no code fences, no extra text:
{{
  "role_title": "extracted role title",
  "overall_readiness": "Ready|Partial|Not Ready",
  "readiness_score": <0-100 integer>,
  "matched_skills": [
    {{
      "domain": "skill name from JD",
      "jd_term": "exact wording from JD",
      "candidate_confidence": "High|Medium|Low|None",
      "status": "covered|partial|gap"
    }}
  ],
  "priority_gaps": [
    {{
      "domain": "skill name",
      "urgency": "High|Medium",
      "action": "one-line study recommendation"
    }}
  ],
  "strengths": ["strength 1", "strength 2"],
  "summary": "2 sentence plain-English readiness summary"
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

def print_report(result):
    score = result["readiness_score"]
    readiness = result["overall_readiness"]
    bar = "█" * (score // 5) + "░" * (20 - score // 5)

    print("\n" + "="*52)
    print(f"  {result['role_title']}")
    print("="*52)
    print(f"\n  Readiness: {readiness} ({score}/100)")
    print(f"  [{bar}]")
    print(f"\n  {result['summary']}")

    print("\n── Skill Coverage ──────────────────────────────")
    covered = [s for s in result["matched_skills"] if s["status"] == "covered"]
    partial  = [s for s in result["matched_skills"] if s["status"] == "partial"]
    gaps     = [s for s in result["matched_skills"] if s["status"] == "gap"]

    if covered:
        print("\n  ✅ Covered")
        for s in covered:
            print(f"     {s['domain']} ({s['candidate_confidence']} confidence)")
    if partial:
        print("\n  ⚠️  Partial")
        for s in partial:
            print(f"     {s['domain']} ({s['candidate_confidence']} confidence)")
    if gaps:
        print("\n  ❌ Gaps")
        for s in gaps:
            print(f"     {s['domain']}")

    if result["priority_gaps"]:
        print("\n── Priority Gaps ───────────────────────────────")
        for g in result["priority_gaps"]:
            urgency_icon = "🔴" if g["urgency"] == "High" else "🟡"
            print(f"\n  {urgency_icon} {g['domain']}")
            print(f"     → {g['action']}")

    if result["strengths"]:
        print("\n── Lead With These ─────────────────────────────")
        for s in result["strengths"]:
            print(f"  • {s}")

    print("\n" + "="*52 + "\n")

def save_report(result):
    os.makedirs("output", exist_ok=True)
    path = "output/jd_report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"📄 Full report saved to {path}")

def main():
    jd_path = sys.argv[1] if len(sys.argv) > 1 else None
    jd_text = load_jd(jd_path)

    print("\n🔍 Loading knowledge base...")
    kb = load_knowledge_base()

    print("🤖 Running gap analysis...")
    result = run_gap_analysis(jd_text, kb)

    print_report(result)
    save_report(result)

if __name__ == "__main__":
    main()