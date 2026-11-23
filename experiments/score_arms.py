"""Experiment 2, in-sample arms on engineers-estimate-check: 9 decisions x 3 samples per arm. No model calls.

  baseline     single-decision prompt
  plain        + one sentence: justify a change or keep the original
  protocol     + rule protocol; the model's own final answer
  gate v1      protocol + code gate that requires a direct evidence link for every change
  gate v2      protocol + code gate tuned afterwards on these 9 decisions, then frozen (rule_gate.apply default)
  gate nolink  gate without the link requirement (shown for comparison)
"""

from __future__ import annotations

import collections
import json

import harness
import replies
import rule_gate

ARMS = ["baseline", "plain", "protocol", "gate v1", "gate v2", "gate nolink"]


def score() -> dict:
    root = replies.root()
    tests = {d["id"]: d for d in replies.tests()["decisions"]}
    oracle = json.loads((replies.PUBLIC / "cases/oracle.json").read_text())["decisions"]
    instruction, packet = replies.evidence_texts(root / "evidence")
    res = root / "results"
    sources = {"baseline": [res / "pilot-opus55", *sorted((res / "arms/baseline").glob("s*"))],
               "plain": sorted((res / "arms/plain").glob("s*")), "gate": sorted((res / "arms/gate").glob("s*"))}
    hits = collections.defaultdict(lambda: collections.defaultdict(list))  # arm -> decision -> [correct]
    gate_notes = collections.defaultdict(list)
    calls = collections.defaultdict(list)
    for src, dirs in sources.items():
        for directory in dirs:
            for rec in replies.records(directory):
                if rec["test"] != "baseline":
                    continue
                d = tests[rec["decision"]]
                item = d["id"].split("-", 1)[1]
                calls[src].append(rec)
                reply = replies.answer(rec)
                if src == "gate":
                    ins, ev = replies.sources(rec, instruction, packet)
                    arms = {"protocol": (reply.get("final") if reply else None, ""),
                            "gate v1": rule_gate.apply(reply, ins, ev, require_direct=True),
                            "gate v2": rule_gate.apply(reply, ins, ev),
                            "gate nolink": rule_gate.apply(reply, ins, ev, require_direct=False)}
                else:
                    arms = {src: (reply, "")}
                for arm, (ans, reason) in arms.items():
                    key = harness.decision_key(ans, d["compare"], item)
                    hits[arm][d["id"]].append(harness.matches(oracle[d["id"]]["reference"], key, d["compare"]))
                    if arm == "gate v2":
                        gate_notes[d["id"]].append(reason)
    out = {"per_decision": {did: {a: f"{sum(hits[a][did])}/{len(hits[a][did])}" for a in ARMS} for did in tests},
           "gate_v2_reasons": {did: sorted(set(v)) for did, v in gate_notes.items()}, "totals": {}, "calls": {}}
    for a in ARMS:
        flat = [x for did in tests for x in hits[a][did]]
        out["totals"][a] = f"{sum(flat)}/{len(flat)}"
    for src, recs in calls.items():
        steady = [r["cost_usd"] for r in recs if not r["usage"].get("cache_creation_input_tokens")]
        out["calls"][src] = {"n": len(recs), "mean_latency_s": round(sum(r["latency_s"] for r in recs) / len(recs), 1),
                             "usd_per_decision_cached_prefix": round(sum(steady) / len(steady), 4),
                             "usd_total": round(sum(r["cost_usd"] for r in recs), 3)}
    return out


def main() -> None:
    s = score()
    print(f"{'decision':9} " + " ".join(f"{a:>11}" for a in ARMS) + "   gate v2 reason")
    for did, row in s["per_decision"].items():
        print(f"{did:9} " + " ".join(f"{row[a]:>11}" for a in ARMS) + "   " + " | ".join(s["gate_v2_reasons"][did]))
    print(f"{'correct':9} " + " ".join(f"{s['totals'][a]:>11}" for a in ARMS))
    for src, c in s["calls"].items():
        print(f"{src:9} calls={c['n']} mean latency {c['mean_latency_s']}s  ${c['usd_per_decision_cached_prefix']}/decision "
              f"with the evidence prefix cached  (${c['usd_total']} total)")


if __name__ == "__main__":
    main()
