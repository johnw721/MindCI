import json
import os
from anthropic import Anthropic
client = Anthropic()

from config import JD_SKILL_FREQUENCIES

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


def generate_cold_test_questions(topic, market_frequency, urgency="High"):
    """Generate test questions for a topic with no notes — purely from topic name and market context."""
    from anthropic import Anthropic
    client = Anthropic()

    prompt = f"""You are a senior Cloud/DevOps engineer writing interview questions.

Topic: {topic}
Market demand: appears in {int(market_frequency * 100)}% of Cloud Engineer job descriptions
Urgency: {urgency}

The candidate has NO existing notes on this topic. Generate foundational questions
to help them discover what they actually know vs what they need to study.

Generate exactly 3 questions that progress from foundational to applied:
1. A "what is" or "explain" question testing basic understanding
2. A "when would you use" or "how does it work" question testing applied knowledge
3. A "what could go wrong" or "compare to X" question testing deeper understanding

Format exactly like this:
Q: question here
A: thorough answer that teaches if they don't know it

Return ONLY the Q/A pairs, no intro text."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text