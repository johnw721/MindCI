import json
import os

from config import MAX_TOKENS_GENERATION
from pipeline._client import call_with_retry


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

FORMATTING REQUIREMENT — every actionable item (hands-on project, blog idea,
lab/tutorial, resume bullet, interview story, and each day's task in the
plan) MUST be emitted as a markdown task list line:

    - [ ] Item description (Day X, Yh)

Use plain bullets (`- ` without `[ ]`) only for non-actionable context.
Headers (`##`) and prose are fine. The task list lines will be parsed for
completion tracking, so consistency matters."""

    _text = call_with_retry(prompt, max_tokens=MAX_TOKENS_GENERATION)
    return _text
