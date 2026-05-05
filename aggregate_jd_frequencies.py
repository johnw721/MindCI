"""
aggregate_jd_frequencies.py

Reads all saved JD reports from jd_reports/ folder,
counts skill frequency across all reports,
and writes market_frequencies.json.

Run manually: python aggregate_jd_frequencies.py
Also called automatically after each JD analysis run.
"""

import json
import os
from collections import defaultdict
from datetime import datetime

JD_REPORTS_DIR = "jd_reports"
OUTPUT_PATH = "data/market_frequencies.json"


def load_all_reports():
    if not os.path.exists(JD_REPORTS_DIR):
        return []
    reports = []
    for fname in os.listdir(JD_REPORTS_DIR):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(JD_REPORTS_DIR, fname), "r", encoding="utf-8") as f:
                    reports.append(json.load(f))
            except Exception:
                continue
    return reports


def aggregate(reports):
    if not reports:
        return {}

    skill_counts = defaultdict(int)
    total_reports = len(reports)

    for report in reports:
        # Single JD report — has matched_skills
        if "matched_skills" in report:
            seen_in_this = set()
            for s in report.get("matched_skills", []):
                skill = s.get("domain", "").strip()
                if skill and skill not in seen_in_this:
                    skill_counts[skill] += 1
                    seen_in_this.add(skill)

        # Batch report — has individual_results
        elif "individual_results" in report:
            for result in report.get("individual_results", []):
                seen_in_this = set()
                for gap in result.get("top_gaps", []):
                    if gap and gap not in seen_in_this:
                        skill_counts[gap] += 1
                        seen_in_this.add(gap)
                for strength in result.get("top_strengths", []):
                    if strength and strength not in seen_in_this:
                        skill_counts[strength] += 1
                        seen_in_this.add(strength)

        # Aggregate report (from batch) — has most_common_gaps
        if "aggregate" in report:
            agg = report["aggregate"]
            for g in agg.get("most_common_gaps", []):
                skill = g.get("skill", "").strip()
                if skill:
                    skill_counts[skill] += g.get("appears_in", 1)

    # Convert counts to frequencies (0.0 - 1.0)
    frequencies = {
        skill: round(min(count / total_reports, 1.0), 2)
        for skill, count in skill_counts.items()
        if skill
    }

    # Sort by frequency descending
    return dict(sorted(frequencies.items(), key=lambda x: x[1], reverse=True))


def save_frequencies(frequencies, total_reports):
    os.makedirs("data", exist_ok=True)
    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_reports": total_reports,
        "frequencies": frequencies
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    return output


def run_aggregation():
    reports = load_all_reports()
    if not reports:
        return None, 0
    frequencies = aggregate(reports)
    result = save_frequencies(frequencies, len(reports))
    return result, len(reports)


if __name__ == "__main__":
    result, count = run_aggregation()
    if result:
        print(f"Aggregated {count} JD reports")
        print(f"Top 10 skills by frequency:")
        for skill, freq in list(result["frequencies"].items())[:10]:
            print(f"  {skill}: {int(freq * 100)}%")
        print(f"Saved to {OUTPUT_PATH}")
    else:
        print("No JD reports found in jd_reports/ folder")
        print("Run the JD Analyzer to start building market frequency data")