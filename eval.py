#!/usr/bin/env python3
"""MindCI gap-analysis evaluation harness -- runnable entry point.

    python eval.py                  # all golden cases against config.MODEL
    python eval.py --model X        # evaluate a specific model string
    python eval.py --case id ...    # run only named cases
    python eval.py --limit 3        # first N cases (cheap smoke run)
    python eval.py --json out.json  # also dump full per-case results

Phase 2 (opt-in, extra API calls):
    python eval.py --consistency 5  # run each case 5x, report flake rate
    python eval.py --judge          # LLM-as-judge on explanation usefulness

Phase 1 reports parse reliability (first-try / repaired / failed), gap/skill
precision + recall against the golden set, and a deterministic hallucination
(grounding) rate. Requires ANTHROPIC_API_KEY (loaded from .env automatically).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evals import consistency as consistency_mod
from evals import harness, judge


def _bar(label: str, width: int = 22) -> str:
    return f"{label:<{width}}"


def _fmt_pr(score: dict) -> str:
    return f"P {score['precision']:>5.0%}  R {score['recall']:>5.0%}  F1 {score['f1']:>4.2f}"


def print_summary(results: dict) -> None:
    pc = results["parse_counts"]
    n = results["n_cases"]
    transport = results.get("transport_errors", 0)
    scored = results.get("n_scored", n - transport)  # cases that got a response
    first, repaired, failed = pc["first_try"], pc["repaired"], pc["failed"]

    def _pct(x, d):
        return f"{x / d:.0%}" if d else "n/a"

    line = "=" * 72
    print("\n" + line)
    print(f" MindCI gap-analysis eval   model={results['model']}   "
          f"cases={n}   KB entries={results['kb_entries']}")
    print(line)

    if transport:
        print(f"\n !! {transport} / {n} cases failed at the transport layer "
              f"(network/auth) and are excluded from the metrics below.")
        print("    Fix: ensure a valid ANTHROPIC_API_KEY in .env, then re-run.")

    print("\n PARSE RELIABILITY (structured-output reliability, over responded cases)")
    print(f"   first-try valid : {first:>3} / {scored}   ({_pct(first, scored)})")
    print(f"   repaired        : {repaired:>3} / {scored}   ({_pct(repaired, scored)})")
    print(f"   failed          : {failed:>3} / {scored}   ({_pct(failed, scored)})")

    cw = max(12, max((len(c["id"]) for c in results["cases"]), default=12)) + 1
    print("\n PER-CASE RESULTS")
    print(f"   {_bar('case', cw)} {_bar('parse',10)} {_bar('gaps',26)} matches")
    print(f"   {'-' * (cw + 60)}")
    for c in results["cases"]:
        gaps = _fmt_pr(c["gaps"])
        matches = _fmt_pr(c["matches"])
        flag = "" if c["error"] is None else "  <ERROR>"
        print(f"   {_bar(c['id'], cw)} {_bar(c['parse_status'],10)} {_bar(gaps,26)} {matches}{flag}")

    g, m = results["gaps_micro"], results["matches_micro"]
    print("\n OVERALL (micro-averaged across all cases)")
    print(f"   gap detection    : {_fmt_pr(g)}   "
          f"({g['total_predicted']} predicted vs {g['total_expected']} labeled)")
    print(f"   skill matching   : {_fmt_pr(m)}   "
          f"({m['total_predicted']} predicted vs {m['total_expected']} labeled)")

    h = results.get("hallucination")
    if h:
        print("\n HALLUCINATION (asserted skills absent from the JD text)")
        print(f"   rate : {h['hallucinated']} / {h['asserted']} asserted skills "
              f"({h['rate']:.0%}) not grounded in the JD")

    # Surface the most useful failure detail: what was missed / invented / hallucinated.
    notable = [
        c for c in results["cases"]
        if c["parse_status"] != "transport_error"
        and (c["gaps"]["false_negatives"] or c["gaps"]["false_positives"]
             or c.get("grounding", {}).get("hallucinated"))
    ]
    if notable:
        print("\n GAP ERRORS (for debugging prompt/model changes)")
        for c in notable:
            bits = []
            if c["gaps"]["false_negatives"]:
                bits.append(f"missed: {', '.join(c['gaps']['false_negatives'])}")
            if c["gaps"]["false_positives"]:
                bits.append(f"invented: {', '.join(c['gaps']['false_positives'])}")
            halluc = c.get("grounding", {})
            ungrounded = (halluc.get("hallucinated_gaps", []) +
                          halluc.get("hallucinated_matches", []))
            if ungrounded:
                bits.append(f"not-in-JD: {', '.join(ungrounded)}")
            print(f"   {_bar(c['id'], cw)} {'; '.join(bits)}")
    print(line + "\n")


def print_consistency(res: dict) -> None:
    line = "=" * 72
    print("\n" + line)
    print(f" CONSISTENCY / FLAKE RATE   model={res['model']}   "
          f"repeats={res['repeats']}   cases={res['n_cases']}")
    print(line)
    print(f"\n mean gap-set stability : {res['mean_gap_stability']:.2f}  "
          f"(1.00 = identical gaps every run)")
    print(f" perfectly stable cases : {res['perfectly_stable_cases']} / {res['n_cases']}")
    print(f" mean readiness stdev   : {res['mean_score_stdev']:.1f} points")

    cw = max(12, max((len(c["id"]) for c in res["cases"]), default=12)) + 1
    print("\n PER-CASE")
    print(f"   {_bar('case', cw)} {_bar('runs',6)} {_bar('gap_stability',15)} "
          f"{_bar('score_spread',14)} parse_stable")
    print(f"   {'-' * (cw + 50)}")
    for c in res["cases"]:
        spread = ("-" if c["score_min"] is None
                  else f"{c['score_min']:.0f}-{c['score_max']:.0f} (sd {c['score_stdev']:.1f})")
        stab = f"{c['gap_stability']:.2f}"
        print(f"   {_bar(c['id'], cw)} {_bar(str(c['runs']),6)} "
              f"{_bar(stab,15)} {_bar(spread,14)} {c['parse_stable']}")
    print(line + "\n")


def print_judge(judged: list[dict], validation: dict, judge_model: str = "") -> None:
    line = "=" * 72
    print("\n" + line)
    print(f" LLM-AS-JUDGE   explanation usefulness (1-5 per criterion)   judge={judge_model}")
    print(line)

    print("\n JUDGE VALIDATION (calibration smoke test)")
    gv, bv = validation["good_overall"], validation["bad_overall"]
    verdict = "PASS" if validation["passed"] else "FAIL"
    print(f"   strong explanation -> {gv}   useless explanation -> {bv}   [{verdict}]")
    print("   (Judge must rank the strong explanation higher. Fuller validation"
          " plan in evals/README.md.)")

    rated = [j for j in judged if j["judge"].get("overall") is not None]
    if rated:
        cw = max(12, max((len(j["id"]) for j in rated), default=12)) + 1
        print("\n PER-CASE")
        print(f"   {_bar('case', cw)} {_bar('spec',6)} {_bar('action',7)} "
              f"{_bar('ground',7)} overall")
        print(f"   {'-' * (cw + 32)}")
        for j in rated:
            r = j["judge"]
            print(f"   {_bar(j['id'], cw)} {_bar(str(r['specificity']),6)} "
                  f"{_bar(str(r['actionability']),7)} {_bar(str(r['grounding']),7)} {r['overall']}")
        mean = round(sum(j["judge"]["overall"] for j in rated) / len(rated), 2)
        print(f"\n mean overall usefulness : {mean} / 5  (n={len(rated)})")
    else:
        print("\n No cases could be judged (transport/parse failures).")
    print(line + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the MindCI gap-analysis eval.")
    ap.add_argument("--model", default=None,
                    help="Model string to evaluate (default: config.MODEL).")
    ap.add_argument("--case", nargs="*", default=None, dest="cases",
                    help="Only run these case ids.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Run only the first N cases.")
    ap.add_argument("--json", default=None, dest="json_out",
                    help="Write full results JSON to this path.")
    ap.add_argument("--allow-cache", action="store_true",
                    help="Do NOT disable the response cache (faster reruns, but "
                         "cached replies hide regressions).")
    ap.add_argument("--consistency", type=int, default=None, metavar="N",
                    help="Phase 2: run each case N times and report flake rate.")
    ap.add_argument("--judge", action="store_true",
                    help="Phase 2: LLM-as-judge scoring of explanation usefulness.")
    ap.add_argument("--judge-model", default=None,
                    help="Model to use as the judge (default: config.MODEL_FAST, a "
                         "different model than the generator to limit self-preference bias).")
    args = ap.parse_args(argv)

    disable_cache = not args.allow_cache
    need_parsed = args.judge

    results = harness.run(
        model=args.model,
        case_ids=args.cases,
        limit=args.limit,
        disable_cache=disable_cache,
        keep_parsed=need_parsed,
    )
    print_summary(results)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f" Full results written to {args.json_out}\n")

    if args.consistency:
        cres = consistency_mod.run_consistency(
            n=args.consistency, model=args.model, case_ids=args.cases,
            limit=args.limit, disable_cache=disable_cache,
        )
        print_consistency(cres)

    if args.judge:
        from config import MODEL_FAST
        from pipeline._client import call_with_retry
        from pipeline.convert import _repair_json
        # Default the judge to a different model than the generator so the judge
        # is not grading its own family's output (limits self-preference bias).
        judge_model = args.judge_model or MODEL_FAST
        validation = judge.validate_judge(call_with_retry, model=judge_model,
                                          repair_fn=_repair_json)
        judged = []
        for c in results["cases"]:
            if c["parse_status"] == "transport_error" or c.get("analysis") is None:
                judged.append({"id": c["id"], "judge": {"overall": None}})
                continue
            r = judge.judge_explanation(
                c["jd_text"], c["analysis"], call_with_retry,
                model=judge_model, repair_fn=_repair_json,
            )
            judged.append({"id": c["id"], "judge": r})
        print_judge(judged, validation, judge_model)

    return 1 if results["parse_counts"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
