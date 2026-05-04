import json
import os
from anthropic import Anthropic
client = Anthropic()

import random

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

def generate_multifile_scenarios(entry):
    import random
    entry_type = entry.get("type", "exploration")
    confidence = entry.get("confidence", "Low")
    label = entry.get("topic") or entry.get("concept") or entry.get("tool") or entry.get("error", "unknown")

    num_files = random.choice([2, 2, 3])  # bias toward 2 files

    if confidence == "High":
        difficulty_instruction = "Make the interaction subtle and complex. The bug or issue should span multiple files and require understanding how they interact."
    elif confidence == "Medium":
        difficulty_instruction = "Make the cross-file relationship clear but the issue non-obvious without reading both files together."
    else:
        difficulty_instruction = "Keep the cross-file relationship straightforward. The issue should be identifiable once you trace the call chain."

    if entry_type == "project":
        scenario_focus = "Focus on bugs, misconfigurations, or architectural issues that span file boundaries — import errors, shared state problems, config/code mismatches, or interface contract violations."
    elif entry_type == "certification":
        scenario_focus = "Focus on how a tool or service is configured across multiple files — for example a Terraform module split across main.tf, variables.tf, and outputs.tf, or a Kubernetes deployment with a misconfigured service and deployment manifest."
    else:
        scenario_focus = "Focus on how two or three technologies interact — for example a Lambda function calling an SDK configured elsewhere, or a CI/CD pipeline YAML referencing a Dockerfile with incompatible settings."

    prompt = f"""You are a senior Cloud/DevOps engineer writing multi-file technical interview scenarios.

Topic: {label}
Entry type: {entry_type}
Candidate confidence: {confidence}
Number of files: {num_files}

Source material:
{json.dumps(entry, indent=2)}

{scenario_focus}
{difficulty_instruction}

Generate exactly 2 multi-file scenarios. Each must show how {num_files} related files interact.
The goal is to test whether the candidate understands cross-file architecture, not just isolated syntax.

Each scenario must follow this EXACT format:

SCENARIO: multi_file
SETUP:
<2-4 sentences describing the system context and what the files represent>
FILE_1_NAME: <realistic filename e.g. handler.py, main.tf, deployment.yaml>
FILE_1:
<complete realistic file content>
FILE_2_NAME: <realistic filename>
FILE_2:
<complete realistic file content>
{f"FILE_3_NAME: <realistic filename>" + chr(10) + "FILE_3:" + chr(10) + "<complete realistic file content>" if num_files == 3 else ""}
QUESTION:
<specific question — what is wrong, what does this do, or how would you fix it — that requires reading ALL files together>
ANSWER:
<thorough explanation referencing specific lines or sections across the files, explaining the interaction>
---

Repeat for the 2nd scenario. Separate with ---
Do not include any text before the first SCENARIO or after the last ANSWER."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def parse_multifile_scenarios(text):
    scenarios = []
    blocks = text.strip().split("---")
    for block in blocks:
        block = block.strip()
        if not block or "SCENARIO:" not in block:
            continue
        s = {"scenario": "multi_file", "files": []}

        for field in ["SETUP", "QUESTION", "ANSWER"]:
            start = block.find(f"{field}:")
            if start == -1:
                continue
            end = len(block)
            for nf in ["SETUP", "FILE_1_NAME", "FILE_1", "FILE_2_NAME", "FILE_2",
                        "FILE_3_NAME", "FILE_3", "QUESTION", "ANSWER"]:
                nf_pos = block.find(f"{nf}:", start + len(field) + 1)
                if nf_pos != -1 and nf_pos < end:
                    end = nf_pos
            s[field.lower()] = block[start + len(field) + 1:end].strip()

        # Extract files
        for n in [1, 2, 3]:
            name_key = f"FILE_{n}_NAME:"
            content_key = f"FILE_{n}:"
            ni = block.find(name_key)
            ci = block.find(content_key)
            if ni == -1 or ci == -1:
                break
            # filename is between FILE_N_NAME: and FILE_N:
            fname = block[ni + len(name_key):ci].strip().split("\n")[0].strip()
            # content is between FILE_N: and next FILE or QUESTION
            next_markers = [f"FILE_{n+1}_NAME:", "QUESTION:"]
            end_ci = len(block)
            for nm in next_markers:
                pos = block.find(nm, ci + len(content_key))
                if pos != -1 and pos < end_ci:
                    end_ci = pos
            fcontent = block[ci + len(content_key):end_ci].strip()
            s["files"].append({"name": fname, "content": fcontent})

        if s.get("question") and s.get("answer") and s["files"]:
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
            "files": s.get("files", []),
            "question": s.get("question", ""),
            "answer": s.get("answer", ""),
            "topic": s.get("topic", ""),
            "confidence": s.get("confidence", ""),
            "status": "pending"
        })
    return cards