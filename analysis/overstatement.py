"""Decomposes the harness's Section 2 overstatement on engineers-estimate-check. Saved data only.

Inputs
  experiments/evidence/veriflow_decisions.json  the nine changed lines of the memo the agent delivered
  experiments/cases/oracle.json                 the graded-correct decision for each line (see its "note")
The reference subtotal reproduces the task rubric's expected subtotal to the cent from the estimate's carried
subtotal plus exactly four changes; every other change the agent made is counted as unlicensed.
"""

from __future__ import annotations

import json
import pathlib

EXP = pathlib.Path(__file__).resolve().parent.parent / "experiments"
CARRIED_SUBTOTAL = 1_276_934.00  # Section 2 subtotal in the estimate as issued
OTHER_CONSTRUCTION = 4_892_105.75 - 1_312_474.00  # Sections 1 and 3-8, from the agent's own bond roll-up (unchanged)
AGREED_LUMP_SUMS = 500_000.00
ROW_TO_DECISION = {"2.35": "D1-2.35", "2.36": "D2-2.36", "2.38": "D3-2.38", "2.03": "D4-2.03",
                   "2.06": "W1-2.06", "2.08": "W2-2.08", "2.44": "W3-2.44"}
CAUSES = {"W4-ramp": "substitute item priced where the rule says flag", "W5-path": "substitute item priced where the rule says flag",
          "W3-2.44": "proxy quantity: storage volume used as excavation", "W1-2.06": "over-reading: pipe length re-derived from a curve radius",
          "W2-2.08": "over-reading: pipe length re-derived from a curve radius"}
# The organizers' Pi run on the same task, from the public template artifacts
# (artifacts/engineers-estimate-check/final_message.md at commit d988c45): change in dollars per line.
TEMPLATE_RUN = {"2.35 -> 2.36": -2_100, "2.38": 15_084, "2.03": 13_800, "3.05 ramp": 2_107, "3.05 path": 3_614}


def bond(section2: float) -> int:
    total = OTHER_CONSTRUCTION + section2 + AGREED_LUMP_SUMS
    return int(-(-total // 1000) * 1000)  # rounded up to the next $1,000


def decompose() -> dict:
    rows = json.loads((EXP / "evidence/veriflow_decisions.json").read_text())
    oracle = json.loads((EXP / "cases/oracle.json").read_text())["decisions"]
    lines = []
    for r in rows:
        did = ROW_TO_DECISION.get(r["item"]) or ("W4-ramp" if "ramp" in r["description"] else "W5-path")
        ref = oracle[did]["reference"]
        carried = r["carried_qty"] * r["unit_price"]
        agent_delta = r["corrected_amount"] - carried
        ref_delta = ref["quantity"] * r["unit_price"] - carried if "quantity" in ref else 0.0  # a flagged item adds nothing
        lines.append({"decision": did, "item": r["item"], "agent_delta": agent_delta, "reference_delta": round(ref_delta, 2),
                      "excess": round(agent_delta - ref_delta, 2), "licensed": oracle[did]["veriflow_correct"],
                      "arithmetic_check": r["arithmetic_check"], "cause": CAUSES.get(did, "licensed change (dollar rounding only)")})
    by_cause: dict[str, float] = {}
    for ln in lines:
        by_cause[ln["cause"]] = round(by_cause.get(ln["cause"], 0.0) + ln["excess"], 2)
    agent_subtotal = CARRIED_SUBTOTAL + sum(ln["agent_delta"] for ln in lines)
    ref_subtotal = round(CARRIED_SUBTOTAL + sum(ln["reference_delta"] for ln in lines), 2)
    template_subtotal = CARRIED_SUBTOTAL + sum(TEMPLATE_RUN.values())
    return {"lines": lines, "by_cause": by_cause, "agent_subtotal": agent_subtotal, "reference_subtotal": ref_subtotal,
            "overstatement": round(agent_subtotal - ref_subtotal, 2),
            "unlicensed_changes": sum(not ln["licensed"] for ln in lines), "licensed_changes": sum(ln["licensed"] for ln in lines),
            "lines_passing_arithmetic_check": sum(ln["arithmetic_check"] == "pass" for ln in lines),
            "agent_bond": bond(agent_subtotal), "reference_bond": bond(ref_subtotal),
            "template_run_subtotal": template_subtotal, "template_run_overstatement": round(template_subtotal - ref_subtotal, 2)}


def main() -> None:
    d = decompose()
    print(f"{'decision':9} {'item':5} {'agent':>11} {'reference':>11} {'excess':>10}  arithmetic  cause")
    for ln in d["lines"]:
        print(f"{ln['decision']:9} {ln['item']:5} {ln['agent_delta']:>11,.2f} {ln['reference_delta']:>11,.2f} {ln['excess']:>10,.2f}  "
              f"{ln['arithmetic_check']:10}  {ln['cause']}")
    print(f"\nagent subtotal     ${d['agent_subtotal']:,.2f}   bond ${d['agent_bond']:,}")
    print(f"reference subtotal ${d['reference_subtotal']:,.2f}   bond ${d['reference_bond']:,}")
    print(f"overstatement      ${d['overstatement']:,.2f} from {d['unlicensed_changes']} unlicensed changes "
          f"({d['licensed_changes']} licensed changes all found); {d['lines_passing_arithmetic_check']}/{len(d['lines'])} lines pass the arithmetic check")
    for cause, v in sorted(d["by_cause"].items(), key=lambda kv: -abs(kv[1])):
        print(f"  {v:>+10,.2f}  {cause}")
    print(f"organizers' Pi run, same task: subtotal ${d['template_run_subtotal']:,.2f}, overstatement ${d['template_run_overstatement']:,.2f} "
          f"(the same ramp and path substitution, +${TEMPLATE_RUN['3.05 ramp'] + TEMPLATE_RUN['3.05 path']:,})")


if __name__ == "__main__":
    main()
