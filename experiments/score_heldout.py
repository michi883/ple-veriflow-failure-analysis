"""Experiment 3, held-out test: baseline prompt vs the frozen gate on 25 decisions from two unseen dev tasks.

No model calls. The reference answers of these two tasks come from their graders and are not published; each
saved reply carries the verdict the scorer gave each candidate answer against them. With PLE_PRIVATE set, the
verdicts are recomputed from the references.
"""

from __future__ import annotations

import json

import replies
import rule_gate

TASKS = ("eor-review-corrections", "landscape-sheet-revision")


def score() -> dict:
    root = replies.root()
    rows, usage = [], {}
    for task in TASKS:
        d = root / "heldout" / task
        ins, packet = replies.evidence_texts(d)
        oracle = json.loads((d / "oracle.json").read_text())["decisions"] if (d / "oracle.json").exists() else None
        decisions = [x["id"] for x in json.loads((replies.PUBLIC / "heldout" / task / "decisions.public.json").read_text())["decisions"]]
        got = {}
        for arm in ("baseline", "gate"):
            for rec in replies.records(root / "results/heldout" / task / arm):
                got[(rec["decision"], arm)] = rec
                usage.setdefault(arm, []).append(rec)
        for did in decisions:
            accept = oracle[did]["accept"] if oracle else None
            b, g = got[(did, "baseline")], got[(did, "gate")]
            base = replies.value(replies.answer(b))
            reply = replies.answer(g)
            gated, why = rule_gate.apply(reply, *replies.sources(g, ins, packet))
            which = "proposed" if why in ("no change", "justified change") else "original" if why.startswith("kept original") else "flag"
            final = replies.value((reply or {}).get("final"))
            rows.append({"task": task, "decision": did, "baseline_answer": base, "gate_answer": replies.value(gated), "gate_reason": why,
                         "baseline": replies.heldout_verdict(b, "answer", base, accept),
                         "gate": gated is not None and replies.heldout_verdict(g, which, replies.value(gated), accept),
                         "protocol": replies.heldout_verdict(g, "final", final, accept)})
    out = {"rows": rows, "by_task": {}, "calls": {}}
    for t in (*TASKS, "all"):
        sel = [r for r in rows if t in ("all", r["task"])]
        out["by_task"][t] = {"n": len(sel), "baseline": sum(r["baseline"] for r in sel), "gate": sum(r["gate"] for r in sel),
                             "protocol": sum(r["protocol"] for r in sel),
                             "fixed": sum(not r["baseline"] and r["gate"] for r in sel),
                             "new_errors": sum(r["baseline"] and not r["gate"] for r in sel)}
    for arm, recs in usage.items():
        steady = [r["cost_usd"] for r in recs if not r["usage"].get("cache_creation_input_tokens")]
        out["calls"][arm] = {"n": len(recs), "mean_latency_s": round(sum(r["latency_s"] for r in recs) / len(recs), 1),
                             "usd_per_decision_cached_prefix": round(sum(steady) / len(steady), 4),
                             "usd_total": round(sum(r["cost_usd"] for r in recs), 3)}
    return out


def main() -> None:
    s = score()
    print(f"{'task':9} {'decision':10} base gate prot | baseline answer -> gate answer [gate reason]")
    for r in s["rows"]:
        mark = lambda ok: "ok" if ok else "--"  # noqa: E731
        print(f"{r['task'][:9]:9} {r['decision']:10} {mark(r['baseline']):4} {mark(r['gate']):4} {mark(r['protocol']):4} | "
              f"{r['baseline_answer'][:28]} -> {r['gate_answer'][:28]} [{r['gate_reason'][:60]}]")
    for t, v in s["by_task"].items():
        print(f"{t:24} n={v['n']:2}  baseline {v['baseline']}/{v['n']}  gate {v['gate']}/{v['n']}  protocol {v['protocol']}/{v['n']}  "
              f"fixed {v['fixed']}  new errors {v['new_errors']}")
    for arm, c in s["calls"].items():
        print(f"{arm:8} calls={c['n']} mean latency {c['mean_latency_s']}s  ${c['usd_per_decision_cached_prefix']}/decision "
              f"with the evidence prefix cached  (${c['usd_total']} total)")


if __name__ == "__main__":
    main()
