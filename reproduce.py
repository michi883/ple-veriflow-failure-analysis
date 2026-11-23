#!/usr/bin/env python3
"""Recomputes every number the Writeup reports from the files in this repository and checks it.

  python3 reproduce.py

Python 3.10+ standard library only. No API key, no network, no Docker, no gated files. Exits non-zero if
any recomputed value differs from the value the Writeup states.
"""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(REPO / "experiments"), str(REPO / "analysis")]

import overstatement  # noqa: E402
import score_arms  # noqa: E402
import score_heldout  # noqa: E402
import score_pilot  # noqa: E402
import trajectory_passes  # noqa: E402

CHECKS: list[tuple[str, str, object, object]] = []


def check(section: str, claim: str, got, want) -> None:
    CHECKS.append((section, claim, got, want))


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob(path: pathlib.Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main() -> int:
    # 1. The failure: one run of the public starter on engineers-estimate-check
    s = "1 failure"
    run = json.loads((REPO / "data/veriflow_run/run.json").read_text())
    grades = json.loads((REPO / "data/veriflow_run/grade_summary.json").read_text())["criteria"]
    d = overstatement.decompose()
    failed = [g for g in grades if not g["passed"]]
    check(s, "score on engineers-estimate-check", round(run["grading"]["score"], 3), 0.543)
    check(s, "rubric points earned / possible", (run["grading"]["weighted_points"], run["grading"]["possible_weighted_points"]), (25.0, 46.0))
    check(s, "rubric points lost, by failed criterion", sorted(g["weight"] for g in failed), [1.0, 10.0, 10.0])
    check(s, "failed criteria", sorted(g["label"] for g in failed),
          ["access ramp flagged as unmatched", "corrected Section 2 subtotal", "revised bond figure"])
    check(s, "agent subtotal ($)", d["agent_subtotal"], 1312474.0)
    check(s, "reference subtotal ($)", d["reference_subtotal"], 1303717.70)
    check(s, "overstatement ($)", d["overstatement"], 8756.30)
    check(s, "licensed changes found / unlicensed changes made", (d["licensed_changes"], d["unlicensed_changes"]), (4, 5))
    check(s, "changed lines passing the arithmetic check", f"{d['lines_passing_arithmetic_check']}/{len(d['lines'])}", "9/9")
    check(s, "substitute-item pricing ($)", d["by_cause"]["substitute item priced where the rule says flag"], 5721.0)
    check(s, "proxy quantity ($)", d["by_cause"]["proxy quantity: storage volume used as excavation"], 3179.0)
    check(s, "over-reading ($)", d["by_cause"]["over-reading: pipe length re-derived from a curve radius"], -144.0)
    check(s, "agent bond / reference bond ($)", (d["agent_bond"], d["reference_bond"]), (5393000, 5384000))
    check(s, "organizers' Pi run, same task: subtotal and overstatement ($)", (d["template_run_subtotal"], d["template_run_overstatement"]), (1309439.0, 5721.30))

    # 2. Why it was confident: three passes over the same sources
    s = "2 passes"
    p = trajectory_passes.passes()
    check(s, "memo written at step", p["memo_written_at_step"], 30)
    check(s, "workspace digests (bracket the verifier) at steps", p["workspace_digest_steps"], [44, 65])
    check(s, "step ranges of the three passes", [v["steps"] for v in p["passes"].values()], ["5-31", "32-43", "45-64"])
    check(s, "each pass re-read all four source files", [v["reads_all_four_sources"] for v in p["passes"].values()], [True, True, True])
    check(s, "memo writes per pass", [v["memo_writes"] for v in p["passes"].values()], [1, 0, 0])
    check(s, "model requests / steps", (run["usage"]["model_requests"], run["trajectory"]["steps"]), (64, 66))
    check(s, "input tokens (cached) / output tokens", (run["usage"]["input_tokens"], run["usage"]["cached_input_tokens"], run["usage"]["output_tokens"]),
          (3547663, 0, 199967))
    check(s, "agent time (s)", run["timing"]["agent_seconds"], 1946)
    usd = (run["usage"]["input_tokens"] * 4.00 + run["usage"]["output_tokens"] * 20.00) / 1e6
    check(s, "cost at list price ($4 / $20 per 1M tokens)", round(usd), 18)

    # 3. Experiment 1: evidence-dependence tests
    s = "3 exp 1"
    e1 = score_pilot.score()
    for key, want in (("baseline_correct", "7/9"), ("veriflow_errors_fixed_in_isolation", "3/5"), ("false_flags_on_correct", "0/7"),
                      ("wrong_decisions_flagged", "1/2"), ("wrong_decisions_following_injected_evidence", "2/2"),
                      ("wrong_decisions_passing_arithmetic_self_check", "2/2")):
        check(s, key.replace("_", " "), e1[key], want)

    # 4. Experiment 2: in-sample arms
    s = "4 exp 2"
    e2 = score_arms.score()
    for arm, want in (("baseline", "20/27"), ("plain", "23/27"), ("protocol", "24/27"), ("gate v1", "24/27"), ("gate v2", "27/27")):
        check(s, f"{arm}: correct samples", e2["totals"][arm], want)
    check(s, "gate v1 on D3 (a correct addition)", e2["per_decision"]["D3-2.38"]["gate v1"], "0/3")
    check(s, "gate v1 on W3 and W5 (the two reproduced errors)", (e2["per_decision"]["W3-2.44"]["gate v1"], e2["per_decision"]["W5-path"]["gate v1"]), ("3/3", "3/3"))

    # 5. Experiment 3: held-out test of the frozen gate
    s = "5 exp 3"
    e3 = score_heldout.score()
    a = e3["by_task"]["all"]
    check(s, "held-out decisions", a["n"], 25)
    check(s, "baseline / frozen gate / protocol correct", (a["baseline"], a["gate"], a["protocol"]), (22, 21, 22))
    check(s, "errors fixed / new errors introduced by the gate", (a["fixed"], a["new_errors"]), (1, 2))
    check(s, "$ per decision, baseline / gate", (round(e3["calls"]["baseline"]["usd_per_decision_cached_prefix"], 3),
                                                round(e3["calls"]["gate"]["usd_per_decision_cached_prefix"], 3)), (0.033, 0.067))
    check(s, "mean latency (s), baseline / gate", (e3["calls"]["baseline"]["mean_latency_s"], e3["calls"]["gate"]["mean_latency_s"]), (4.8, 12.4))
    spend = sum(json.loads(f.read_text())["cost_usd"] for f in (REPO / "experiments/results").rglob("*.json"))
    check(s, "total API spend on all three experiments ($)", int(spend * 100) / 100, 8.56)
    check(s, "saved model replies", sum(1 for _ in (REPO / "experiments/results").rglob("*.json")), 149)

    # 6. The organizers' 29-task dev run of the unmodified template (their public artifacts)
    s = "6 template"
    rows = list(csv.DictReader((REPO / "data/template_baseline/dev_tasks.csv").open()))
    score = [float(r["reward"]) for r in rows]
    cls = lambda r: (0 if int(r["deliverable_files"]) == 0 else 1 if float(r["reward"]) == 0 else 2 if float(r["reward"]) < 0.4  # noqa: E731
                     else 3 if float(r["reward"]) < 0.8 else 4)
    counts = [sum(cls(r) == k for r in rows) for k in range(5)]
    lost = [round(sum(1 - float(r["reward"]) for r in rows if cls(r) == k), 2) for k in range(5)]
    check(s, "dev tasks / mean score", (len(rows), round(sum(score) / len(score), 3)), (29, 0.491))
    check(s, "tasks per outcome class", counts, [2, 2, 9, 9, 7])
    check(s, "points lost per outcome class", lost, [2.0, 2.0, 6.26, 3.78, 0.71])
    check(s, "tasks with token totals recorded", sum(bool(r["prompt_tokens"]) for r in rows), 3)
    by_steps = lambda lo, hi: round(sum(float(r["reward"]) for r in rows if lo <= int(r["agent_steps"]) <= hi)  # noqa: E731
                                    / sum(lo <= int(r["agent_steps"]) <= hi for r in rows), 2)
    check(s, "mean score by agent steps (<=65, 66-100, >100)", [by_steps(0, 65), by_steps(66, 100), by_steps(101, 10 ** 6)], [0.58, 0.54, 0.31])
    est = next(r for r in rows if r["task"] == "engineers-estimate-check")
    check(s, "template on engineers-estimate-check: score, agent steps", (round(float(est["reward"]), 3), int(est["agent_steps"])), (0.543, 38))

    # 7. Integrity: frozen code and the third-party harness are the files that were run
    s = "7 integrity"
    frozen = json.loads((REPO / "experiments/cases/frozen_mechanism.json").read_text())
    check(s, "rule_gate.py is the frozen gate (hash recorded before any held-out model call)",
          sha256(REPO / "experiments/rule_gate.py"), frozen["io_generalization"]["rule_gate.py_sha256"])
    for name in ("pilot.py", "harness.py"):
        check(s, f"{name} is unchanged since the freeze", sha256(REPO / "experiments" / name), frozen["sha256"][name])
    for name, entry in run["harness"]["files"].items():
        check(s, f"third_party/veriflow-ple-starter/{name} is the file the run used (git blob)",
              git_blob(REPO / "third_party/veriflow-ple-starter" / name), entry.split()[1])

    # 8. Our one run of the submitted harness (template + Scope Guard) on engineers-estimate-check
    s = "8 guarded run"
    g = json.loads((REPO / "data/scope_guard_run/run.json").read_text())
    u, price = g["usage"], g["price_usd_per_mtok"]
    fresh = u["input_tokens"] - u["cache_read_tokens"]
    rest = u["cache_read_tokens"] * price["cache_read"] + u["output_tokens"] * price["output"]
    usd = ((fresh * price["input"] + rest) / 1e6, (fresh * price["cache_write"] + rest) / 1e6)
    check(s, "score, rubric points earned / possible", (g["grading"]["reward"], g["grading"]["weighted_points"],
                                                        g["grading"]["possible_weighted_points"]), (1.0, 46.0, 46.0))
    check(s, "dev tasks with artifacts / dev tasks", (g["dev_set"]["tasks_with_artifacts"], g["dev_set"]["tasks"]), (1, 29))
    check(s, "dev score with the other 28 tasks counted 0", int(g["grading"]["reward"] / g["dev_set"]["tasks"] * 1000) / 1000, 0.034)
    check(s, "tokens: input (M), output (k), cached (M)", (round(u["input_tokens"] / 1e6, 2), round(u["output_tokens"] / 1e3),
                                                          round(u["cache_read_tokens"] / 1e6, 2)), (9.42, 176, 8.79))
    check(s, "cached share of input (%)", round(100 * u["cache_read_tokens"] / u["input_tokens"]), 93)
    check(s, "agent time (min), model calls, tool calls", (round(u["agent_seconds"] / 60), u["model_calls"], u["tool_calls"]), (27, 45, 120))
    check(s, "cost at list price ($): fresh input all plain, all cache-write", (round(usd[0], 2), round(usd[1], 2)), (7.79, 8.42))
    check(s, "cost, rounded ('about $8')", round(sum(usd) / 2), 8)
    check(s, "guard log: active lines, flags, notices the model saw, flags on the final memo",
          (g["guard"]["log_lines_active"], g["guard"]["log_lines_flagged"], g["guard"]["notices_in_what_the_model_saw"],
           g["guard"]["flags_on_final_deliverable"]), (1, 0, 0, 0))
    check(s, "its Section 2 subtotal is the reference subtotal, to the dollar", g["deliverable"]["section_2_subtotal"],
          round(d["reference_subtotal"]))
    check(s, "its bond figure ($)", g["deliverable"]["bond"], 5384000)

    width = max(len(c[1]) for c in CHECKS)
    bad, last = 0, None
    for section, claim, got, want in CHECKS:
        if section != last:
            print(f"\n[{section}]")
            last = section
        ok = got == want
        bad += not ok
        shown = str(got) if len(str(got)) <= 44 else str(got)[:20] + "..." + str(got)[-20:]
        print(f"  {'ok ' if ok else 'BAD'} {claim:{width}}  {shown}" + ("" if ok else f"   (Writeup: {want})"))
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} checks reproduce" + ("" if not bad else f"; {bad} differ"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
