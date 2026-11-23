"""Experiment 1, evidence-dependence tests: scores the 27 saved replies (9 decisions x 3 conditions).

Same scoring as harness.py's main loop, read from saved replies instead of a live responder. No model calls.
  baseline     original evidence            -> is the decision correct?
  invariance   presentation-only change     -> must equal the baseline decision
  sensitivity  decisive evidence change     -> must move as the instruction's rules require
A decision is flagged when it fails invariance or sensitivity.
"""

from __future__ import annotations

import json

import harness
import replies


def score() -> dict:
    root = replies.root()
    tests = replies.tests()
    oracle = json.loads((replies.PUBLIC / "cases/oracle.json").read_text())["decisions"]
    saved = {(r["decision"], r["test"]): r for r in replies.records(root / "results/pilot-opus55")}
    rows = []
    for d in tests["decisions"]:
        o, item, base = oracle[d["id"]], d["id"].split("-", 1)[1], None
        row = {"decision": d["id"], "veriflow_correct": o["veriflow_correct"]}
        for test in ("baseline", "invariance", "sensitivity"):
            rec = saved[(d["id"], test)]
            ans = replies.answer(rec)
            key = harness.decision_key(ans, d["compare"], item)
            if test == "baseline":
                base = key
                ok = harness.matches(o["reference"], key, d["compare"])
                row.update(answer=key, confidence=(ans or {}).get("confidence"), arithmetic_self_check=harness.self_consistent(ans))
            elif test == "invariance":
                ok = key is not None and key == base
            elif d[test].get("criterion") == "delta":
                ok = key is not None and base is not None and abs((key - base) - o["sensitivity"]["delta"]) < 0.005
            else:
                ok = harness.matches(o["sensitivity"], key, d["compare"])
            row[test] = ok
            row[f"{test}_answer"] = key
        row["flagged"] = not (row["invariance"] and row["sensitivity"])
        rows.append(row)
    correct = [r for r in rows if r["baseline"]]
    wrong = [r for r in rows if not r["baseline"]]
    return {
        "rows": rows,
        "baseline_correct": f"{len(correct)}/{len(rows)}",
        "false_flags_on_correct": f"{sum(r['flagged'] for r in correct)}/{len(correct)}",
        "wrong_decisions_flagged": f"{sum(r['flagged'] for r in wrong)}/{len(wrong)}",
        "wrong_decisions_following_injected_evidence": f"{sum(r['sensitivity'] for r in wrong)}/{len(wrong)}",
        "wrong_decisions_passing_arithmetic_self_check": f"{sum(r['arithmetic_self_check'] == 'pass' for r in wrong)}/{len(wrong)}",
        "veriflow_errors_fixed_in_isolation": f"{sum(r['baseline'] and not r['veriflow_correct'] for r in rows)}/"
                                              f"{sum(not r['veriflow_correct'] for r in rows)}",
        "cost_usd": round(sum(r["cost_usd"] for r in saved.values()), 3),
    }


def main() -> None:
    s = score()
    print(f"{'decision':9} {'baseline answer':34} {'conf':>5} {'arith.':7} {'invariance':11} {'sensitivity':12} flagged")
    for r in s["rows"]:
        print(f"{r['decision']:9} {str(r['answer']) + (' ok' if r['baseline'] else ' WRONG'):34} {str(r['confidence']):>5} "
              f"{r['arithmetic_self_check']:7} {'pass' if r['invariance'] else 'FAIL':11} "
              f"{'pass' if r['sensitivity'] else 'FAIL':12} {'YES' if r['flagged'] else 'no'}")
    for k in ("baseline_correct", "veriflow_errors_fixed_in_isolation", "false_flags_on_correct", "wrong_decisions_flagged",
              "wrong_decisions_following_injected_evidence", "wrong_decisions_passing_arithmetic_self_check", "cost_usd"):
        print(f"{k.replace('_', ' ')}: {s[k]}")


if __name__ == "__main__":
    main()
