"""
mindci.py — Headless CLI for MindCI.

Drives the same `pipeline.*` modules the Streamlit dashboards use, so output
matches what you'd get through the UI. No subprocess shelling, no ghost
top-level scripts.

Subcommands
-----------
  convert    Convert raw/*.txt notes into data/structured.json.
  generate   Generate Anki flashcards from data/structured.json.
  aggregate  Rebuild data/market_frequencies.json from jd_reports/.
  run        convert + generate.
  dashboard  Launch the Streamlit dashboard (alias for `streamlit run app_dashboard.py`).

Examples
--------
  python mindci.py run
  python mindci.py convert
  python mindci.py generate --batch-size 4
  python mindci.py aggregate
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ── convert ───────────────────────────────────────────────────────────────────
def cmd_convert(args) -> int:
    from config import RAW_DIR, log
    from pipeline.convert import convert_to_json, parse_and_save_json

    raw_dir = Path(RAW_DIR)
    archive_dir = Path("archive")
    archive_dir.mkdir(parents=True, exist_ok=True)

    notes = sorted(raw_dir.glob("*.txt")) if raw_dir.exists() else []
    if not notes:
        print(f"No .txt files in {raw_dir}/ — drop notes there and rerun.")
        return 0

    combined = []
    for path in notes:
        text = path.read_text(encoding="utf-8")
        combined.append(f"--- SOURCE: {path.name} ---\n\n{text}")
    payload = "\n\n".join(combined)

    print(f"Converting {len(notes)} note(s) with Claude…")
    raw_response = convert_to_json(payload)
    parsed, report = parse_and_save_json(raw_response)

    print(f"  ✓ {len(parsed)} valid entr{'y' if len(parsed) == 1 else 'ies'} saved")
    if report["invalid_count"]:
        print(f"  ! {report['invalid_count']} invalid → data/invalid_entries.json")
    if report["warning_count"]:
        print(f"  ! {report['warning_count']} soft warning(s)")

    if not args.no_archive:
        for path in notes:
            shutil.move(str(path), str(archive_dir / path.name))
        print(f"  → archived {len(notes)} note(s) to {archive_dir}/")
    return 0


# ── generate ──────────────────────────────────────────────────────────────────
def cmd_generate(args) -> int:
    from config import OUTPUT_DIR
    from pipeline.generate import (
        build_dynamic_prompt,
        classify,
        generate_flashcards_batched,
        parse_qa,
    )
    from utils import load_knowledge_base, load_prompt

    kb = load_knowledge_base()
    if not kb:
        print("data/structured.json is empty or missing — run `convert` first.")
        return 1

    prompts = {
        "project":       load_prompt("prompts/project.txt"),
        "certification": load_prompt("prompts/cert.txt"),
        "exploration":   load_prompt("prompts/explore.txt"),
    }

    # Group by entry type so each batch shares the right base prompt.
    by_type: dict[str, list[dict]] = {"project": [], "certification": [], "exploration": []}
    for e in kb:
        by_type.setdefault(e.get("type", "exploration"), []).append(e)

    md_output = ""
    anki_rows: list[tuple[str, str, str, str, str]] = []

    for entry_type, entries in by_type.items():
        if not entries:
            continue
        base_prompt = prompts.get(entry_type, prompts["exploration"])
        print(f"Generating {len(entries)} {entry_type} entries (batch_size={args.batch_size})…")

        results = generate_flashcards_batched(entries, base_prompt, batch_size=args.batch_size)

        for entry, cards in results:
            tag = classify(entry)
            category = entry.get("category", entry.get("tool", "general"))
            confidence = entry.get("confidence", "Low")
            difficulty = entry.get("difficulty", "")
            md_output += f"\n\n## [{tag}] {entry_type.upper()} ({category})\n"
            for q, a in cards:
                md_output += f"Q: {q}\nA: {a}\n\n"
                anki_rows.append((q, a, f"{tag}::{entry_type}::{category}", difficulty, confidence))

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "questions.md").write_text(md_output, encoding="utf-8")
    with (out_dir / "anki.csv").open("w", encoding="utf-8") as f:
        for q, a, tags, diff, conf in anki_rows:
            f.write(f"{q}\t{a}\t{tags}\t{diff}\t{conf}\n")

    print(f"  ✓ {len(anki_rows)} flashcards → {out_dir}/anki.csv")
    print(f"  ✓ Markdown summary → {out_dir}/questions.md")
    return 0


# ── aggregate ─────────────────────────────────────────────────────────────────
def cmd_aggregate(_args) -> int:
    from aggregate_jd_frequencies import run_aggregation

    print("Aggregating JD reports…")
    result, count = run_aggregation()
    print(f"  ✓ aggregated {count} report(s) → data/market_frequencies.json")
    return 0


# ── run (convert + generate) ──────────────────────────────────────────────────
def cmd_run(args) -> int:
    rc = cmd_convert(args)
    if rc != 0:
        return rc
    return cmd_generate(args)


# ── dashboard ─────────────────────────────────────────────────────────────────
def cmd_dashboard(_args) -> int:
    print("Launching Streamlit dashboard…")
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", "app_dashboard.py"]
    )


# ── cache ─────────────────────────────────────────────────────────────────────
def cmd_cache_stats(_args) -> int:
    """Show current response-cache size + hit rate."""
    from pipeline._client import _cache_load, get_usage_summary

    entries = _cache_load()
    summary = get_usage_summary()
    cache = summary["cache"]
    total = cache["hits"] + cache["misses"]
    hit_pct = int(100 * cache["hits"] / total) if total else 0
    print(f"  cache entries: {len(entries)}")
    print(f"  cumulative:    {cache['hits']}/{total} hits ({hit_pct}%)")
    return 0


def cmd_cache_clear(_args) -> int:
    """Delete the on-disk response cache. Cumulative hit/miss counts are preserved."""
    from pipeline._client import _cache_path

    p = _cache_path()
    if p.exists():
        p.unlink()
        print(f"  ✓ removed {p}")
    else:
        print(f"  (no cache file at {p})")
    return 0


# ── watch ─────────────────────────────────────────────────────────────────────
def cmd_watch(args) -> int:
    """Watch RAW_DIR and auto-convert when .txt files settle."""
    from pipeline.watcher import watch

    class _ConvertArgs:
        no_archive = args.no_archive

    def on_settled(file_paths):
        print(f"\n→ {len(file_paths)} file(s) settled:")
        for p in file_paths:
            print(f"   {p}")
        try:
            cmd_convert(_ConvertArgs())
        except Exception as e:
            print(f"  ! convert failed: {e}")

    watch(on_settled)
    return 0


# ── argparse wiring ───────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mindci",
        description="Headless CLI for the MindCI pipeline.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="convert + generate")
    p_run.add_argument("--no-archive", action="store_true",
                       help="leave raw notes in raw/ instead of moving to archive/")
    p_run.add_argument("--batch-size", type=int, default=4)
    p_run.set_defaults(func=cmd_run)

    p_conv = sub.add_parser("convert", help="raw/*.txt → data/structured.json")
    p_conv.add_argument("--no-archive", action="store_true")
    p_conv.set_defaults(func=cmd_convert)

    p_gen = sub.add_parser("generate", help="data/structured.json → output/anki.csv")
    p_gen.add_argument("--batch-size", type=int, default=4)
    p_gen.set_defaults(func=cmd_generate)

    p_agg = sub.add_parser("aggregate", help="rebuild data/market_frequencies.json")
    p_agg.set_defaults(func=cmd_aggregate)

    p_dash = sub.add_parser("dashboard", help="launch the Streamlit dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    p_watch = sub.add_parser("watch", help="watch raw/ and auto-convert dropped .txt files")
    p_watch.add_argument("--no-archive", action="store_true",
                         help="leave raw notes in raw/ instead of moving to archive/")
    p_watch.set_defaults(func=cmd_watch)

    p_cache_stats = sub.add_parser("cache-stats", help="show response cache size + hit rate")
    p_cache_stats.set_defaults(func=cmd_cache_stats)

    p_cache_clear = sub.add_parser("cache-clear", help="delete the response cache file")
    p_cache_clear.set_defaults(func=cmd_cache_clear)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
