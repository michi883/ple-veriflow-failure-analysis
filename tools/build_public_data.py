#!/usr/bin/env python3
"""Derives the public data in this repository from the private working files.

Only the authors can run it: it needs the gated PLE dev-task files, the raw model replies and the run
logs. It is published so that anyone can see exactly how each public file was derived, what was kept,
what was replaced by a recorded check result, and what was withheld (docs/DATA_POLICY.md).

Usage (from the repository root):
  python3 tools/build_public_data.py --work ~/ple-1

--work is the directory holding evidence-control/, ple-kit/, ple-baseline/ and veriflow-ple-starter/.
No model calls, no network.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "experiments"))

import harness  # noqa: E402  (frozen, unmodified)
import rule_gate  # noqa: E402  (frozen, unmodified)

MAX_STR = 40
DECISION_KEYS = ("treatment", "schedule_item", "quantity", "unit", "unit_price", "amount", "value")
TASK = "engineers-estimate-check"
RUN = "veriflow-ple-starter-20261122-135556"
GRADE_RUN = "veriflow-ple-starter-artifacts-20261122-152143"
FLAG_VALUE = "flag: change not justified"  # what rule_gate.apply returns for a blocked addition


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def public_str(s: str) -> str:
    """Short strings are kept. Long ones keep 40 characters plus a hash of the normalized full text,
    so two values compare equal after rule_gate._norm() exactly when the originals did."""
    c = collapse(s)
    if len(c) <= MAX_STR:
        return c
    return f"{c[:MAX_STR]}… [withheld; sha256:{sha256(c.lower().encode())[:12]}]"


def public_decision(obj):
    if isinstance(obj, str):
        return public_str(obj)
    if not isinstance(obj, dict):
        return obj
    out, dropped = {}, []
    for k, v in obj.items():
        if isinstance(v, str):
            if k in DECISION_KEYS:
                out[k] = public_str(v)
            else:
                dropped.append(k)
        elif isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        else:
            dropped.append(k)
    if dropped:
        out["_withheld_keys"] = sorted(dropped)
    return out


def redact_answer(ans: dict | None, instruction: str, evidence: str) -> dict | None:
    if ans is None:
        return None
    if "proposed" not in ans and "governing_rules" not in ans:  # plain single-decision answer
        out = public_decision({k: v for k, v in ans.items() if k != "evidence"})
        out.pop("_withheld_keys", None)
        return out
    out = {k: public_decision(ans[k]) for k in ("original", "proposed", "final") if k in ans}
    for k in ("changes_original", "link", "confidence"):
        if k in ans:
            out[k] = public_str(ans[k]) if isinstance(ans[k], str) else ans[k]
    rid = lambda r: public_str(r.get("id", ""))  # noqa: E731
    out["governing_rules"] = [{"id": rid(r), "verbatim": rule_gate.verbatim(r.get("quote", ""), instruction)}
                              for r in ans.get("governing_rules") or []]
    out["evidence_verbatim"] = [rule_gate.verbatim(q, evidence) for q in ans.get("evidence") or []]
    out["keep_or_flag_rules"] = [{"id": rid(r), "condition_met": r.get("condition_met")}
                                 for r in ans.get("keep_or_flag_rules") or []]
    out["scope_rules"] = [{"id": rid(r), "in_scope": r.get("in_scope")} for r in ans.get("scope_rules") or []]
    return out


def value(ans) -> str:  # as in score_heldout.py
    return re.sub(r"\s+", " ", str((ans or {}).get("value", ""))).strip().lower()


def redact_record(rec: dict, instruction: str, evidence: str, accept: list[str] | None) -> dict:
    ans = harness.parse_answer(rec["text"])
    out = {k: v for k, v in rec.items() if k not in ("text", "request_id")}
    out["redacted"] = True
    out["text_sha256"] = sha256(rec["text"].encode())
    out["text_chars"] = len(rec["text"])
    out["prose_outside_json"] = not rec["text"].strip().startswith(("{", "```"))
    out["answer"] = redact_answer(ans, instruction, evidence)
    if accept is not None:  # held-out: the reference stays private, the scorer's verdicts are published
        ok = lambda v: any(re.search(p, v) for p in accept)  # noqa: E731
        if ans is not None and "proposed" in ans:
            orig = ans.get("original")
            out["verdicts"] = {"proposed": ok(value(ans.get("proposed"))),
                               "original": ok(value(orig)) if isinstance(orig, dict) else None,
                               "final": ok(value(ans.get("final"))), "flag": ok(FLAG_VALUE)}
        else:
            out["verdicts"] = {"answer": ok(value(ans))}
    return out


def write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def build_replies(ec: pathlib.Path, out: pathlib.Path, withheld: list) -> int:
    n = 0
    instruction = (ec / "evidence/instruction.txt").read_text()
    packet = (ec / "evidence/packet.txt").read_text()
    for p in sorted((ec / "results").rglob("*.json")):
        rel = p.relative_to(ec / "results")
        if rel.parts[0] == "heldout":
            continue
        rec = json.loads(p.read_text())
        write_json(out / rel, redact_record(rec, instruction, packet, None))
        withheld.append({"path": f"results/{rel}", "sha256": sha256(p.read_bytes()), "bytes": p.stat().st_size,
                         "why": "raw model reply: quotes gated task text"})
        n += 1
    for task_dir in sorted((ec / "heldout").iterdir()):
        if not task_dir.is_dir():
            continue
        ins, pk = (task_dir / "instruction.txt").read_text(), (task_dir / "packet.txt").read_text()
        oracle = json.loads((task_dir / "oracle.json").read_text())["decisions"]
        for p in sorted((ec / "results/heldout" / task_dir.name).rglob("*.json")):
            rel = p.relative_to(ec / "results")
            rec = json.loads(p.read_text())
            write_json(out / rel, redact_record(rec, ins, pk, oracle[rec["decision"]]["accept"]))
            withheld.append({"path": f"results/{rel}", "sha256": sha256(p.read_bytes()), "bytes": p.stat().st_size,
                             "why": "raw model reply: quotes gated task text"})
            n += 1
    return n


def build_cases(ec: pathlib.Path, exp: pathlib.Path, withheld: list) -> None:
    tests = json.loads((ec / "cases/tests.json").read_text())
    pub = {"task": tests["task"],
           "note": "Public form of cases/tests.json. Questions and descriptions are ours. The literal anchor strings "
                   "of each evidence operation quote the gated task files, so only the operation type and a hash of "
                   "the operation are published.",
           "answer_format": tests["answer_format"], "decisions": []}
    for d in tests["decisions"]:
        row = {"id": d["id"], "compare": d["compare"], "question": d["question"]}
        for test in ("invariance", "sensitivity"):
            spec = d[test]
            row[test] = {"description": spec["description"],
                         "ops": [{"op": op["op"], "sha256": sha256(json.dumps(op, sort_keys=True).encode())} for op in spec["ops"]]}
            if "criterion" in spec:
                row[test]["criterion"] = spec["criterion"]
        pub["decisions"].append(row)
    write_json(exp / "cases/tests.public.json", pub)
    for name in ("oracle.json", "frozen_mechanism.json"):
        (exp / "cases" / name).write_bytes((ec / "cases" / name).read_bytes())
    (exp / "evidence").mkdir(exist_ok=True)
    for name in ("manifest.json", "veriflow_decisions.json"):
        (exp / "evidence" / name).write_bytes((ec / "evidence" / name).read_bytes())
    for task_dir in sorted((ec / "heldout").iterdir()):
        if not task_dir.is_dir():
            continue
        dst = exp / "heldout" / task_dir.name
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "manifest.json").write_bytes((task_dir / "manifest.json").read_bytes())
        full = json.loads((task_dir / "tests.json").read_text())
        write_json(dst / "decisions.public.json", {
            "task": full["task"],
            "note": "Decision ids with our own short topic labels. The full question texts follow the wording of the task's "
                    "grading data, so they are withheld with the rest of tests.json.",
            "answer_format": full["answer_format"], "gate_fields": full["gate_fields"],
            "decisions": [{"id": d["id"], "topic": HELDOUT_TOPICS.get(d["id"], "one review comment: is it incorporated as a "
                           "change to the drawings in this issue, or not")} for d in full["decisions"]]})
        for name, why in (("instruction.txt", "task prompt (gated dataset)"), ("packet.txt", "text of task files (gated dataset)"),
                          ("tests.json", "decision questions: follow the wording of the task's grading data"),
                          ("oracle.json", "reference answers derived from the task's grader")):
            f = task_dir / name
            withheld.append({"path": f"heldout/{task_dir.name}/{name}", "sha256": sha256(f.read_bytes()), "bytes": f.stat().st_size, "why": why})
    for rel, why in (("cases/tests.json", "full test definitions: operation anchors quote gated task text"),
                     ("evidence/instruction.txt", "task prompt (gated dataset)"),
                     ("evidence/packet.txt", "text of task files (gated dataset)")):
        f = ec / rel
        withheld.append({"path": rel, "sha256": sha256(f.read_bytes()), "bytes": f.stat().st_size, "why": why})
    for f in sorted((ec / "results").glob("*.jsonl")):
        withheld.append({"path": f"results/{f.name}", "sha256": sha256(f.read_bytes()), "bytes": f.stat().st_size,
                         "why": "harness run log: embeds evidence diffs and raw replies"})


HELDOUT_TOPICS = {  # our labels for the nine landscape-sheet-revision decisions
    "L1-c11": "can the resubmittal date be committed to now (yes / no)",
    "L2-c4": "has a significant-change determination been issued (yes / no)",
    "L3-c5": "which party makes that determination (name)",
    "L4-c0": "which approval condition governs staff-level action (number)",
    "L5-c7": "closing date of an advisory-board application window (date)",
    "L6-c8": "next available advisory-board meeting (date)",
    "L7-c13": "street trees shown on the current sheets (count)",
    "L8-c14": "street trees the spacing standard requires (count)",
    "L9-c15": "must the revised planting plan be sealed (yes / no)"}
CRITERIA = {  # our labels for the task rubric's criteria; the rubric text itself is gated
    0: "revised bond figure", 1: "corrected Section 2 subtotal", 2: "item 2.36 quantity", 3: "item 2.38 amount",
    4: "item 2.03 amount", 5: "item 2.30 flagged, amount retained", 6: "memo addressed to the client",
    7: "line-by-line table with sheet sources", 8: "memo date", 9: "access ramp flagged as unmatched"}
SOURCE_FILES = {"estimate": "Engineers_Estimate", "plans": ("Improvement_Plans", "/tmp/plans.txt"),
                "unit_cost_schedule": ("Unit_Cost_Schedule", "/tmp/sched.txt", "/tmp/s.txt"), "hgl_pipe_schedule": "StormDrain_HGL"}


def build_veriflow_run(work: pathlib.Path, out: pathlib.Path, withheld: list) -> None:
    run = work / "ple-kit/runs" / RUN
    trial = next((run / "attempt1/jobs/dev").glob(f"{TASK}__*"))
    graded = next((work / "ple-kit/runs" / GRADE_RUN / "attempt1/jobs/dev").glob(f"{TASK}__*"))
    results, export = json.loads((run / "results.json").read_text()), json.loads((run / "export.json").read_text())
    result, grade = json.loads((trial / "result.json").read_text()), json.loads((graded / "verifier/grade.json").read_text())
    traj = json.loads((trial / "agent/trajectory.json").read_text())
    final_msg = (trial / "deliverables/final_message.md").read_text()
    memo = work / "veriflow-ple-starter/artifacts" / TASK / "files/filesystem/Correspondence/Client/Memo_to_Callaway_2026-10-31.docx"
    ar, ex = result["agent_result"], result["agent_execution"]
    steps = traj["steps"]
    write_json(out / "run.json", {
        "task": TASK,
        "harness": {**export["harness"], "note": "third-party code, see third_party/veriflow-ple-starter/README.md"},
        "model": "claude-opus-5-5 (Anthropic API, local run)", "judge_model": results["judge_model"],
        "dataset": {"repo": results["tasks_repo"], "revision": results["tasks_revision"], "dev_set_sha256": export["dev_set"]},
        "timing": {"trial_started_at": result["started_at"], "trial_finished_at": result["finished_at"],
                   "agent_execution_started_at": ex["started_at"], "agent_execution_finished_at": ex["finished_at"],
                   "agent_seconds": results["tasks"][0]["seconds"]},
        "usage": {"input_tokens": ar["n_input_tokens"], "cached_input_tokens": ar["n_cache_tokens"],
                  "output_tokens": ar["n_output_tokens"], "model_requests": ar["metadata"]["serve_requests"]},
        "trajectory": {"steps": len(steps), "agent_steps": sum(s["source"] == "agent" for s in steps),
                       "tool_call_steps": sum(bool(s.get("tool_calls")) for s in steps), "stop_condition": traj.get("notes")},
        "grading": {"run": GRADE_RUN, "method": "eval/run.py --artifacts on the exported deliverables; the live run's "
                    "verifier container failed to start (evaluator RuntimeError), so the first score was unmeasured",
                    "score": grade["score"], "weighted_points": grade["weighted_points"],
                    "possible_weighted_points": grade["possible_weighted_points"]},
        "final_message": {"first_paragraph": final_msg.replace("# Agent's final message", "").strip().split("\n\n")[0],
                          "sha256": sha256(final_msg.encode())},
    })
    write_json(out / "grade_summary.json", {
        "note": "Per-criterion outcome of the kit's LLM judge (claude-opus-5-5). Labels are ours; rubric text is gated.",
        "criteria": [{"index": g["index"], "label": CRITERIA[g["index"]], "weight": g["weight"], "passed": g["passed"]}
                     for g in grade["grades"]]})
    with (out / "trajectory_steps.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "tool", "command_chars", "observation_chars", *SOURCE_FILES, "memo", "writes_docx", "workspace_digest"])
        for s in steps[1:]:
            call = (s.get("tool_calls") or [{}])[0]
            a = call.get("arguments") or {}
            cmd = a.get("command") or a.get("path") or a.get("url") or ""
            has = lambda needles: int(any(n in cmd for n in ([needles] if isinstance(needles, str) else needles)))  # noqa: E731
            w.writerow([s["step_id"], call.get("function_name", "final_message"), len(cmd), len(json.dumps(s.get("observation"))) if s.get("observation") else 0,
                        *[has(v) for v in SOURCE_FILES.values()], has("Memo_to_Callaway"),
                        int("from docx import" in cmd and "mkdir -p" in cmd), int("sha256sum" in cmd)])
    for f, why in ((trial / "agent/trajectory.json", "agent trajectory: tool outputs contain gated task files"),
                   (memo, "the agent's deliverable: restates gated task content"),
                   (trial / "deliverables/final_message.md", "the agent's full final message"),
                   (graded / "verifier/grade.json", "judge output: quotes the gated rubric")):
        withheld.append({"path": f"veriflow_run/{f.name}", "sha256": sha256(f.read_bytes()), "bytes": f.stat().st_size, "why": why})


def build_template_baseline(work: pathlib.Path, out: pathlib.Path) -> None:
    base = work / "ple-baseline"
    manifest = json.loads((base / "artifacts/manifest.json").read_text())["tasks"]
    meta, cur = {}, None
    for line in (work / "ple-kit/eval/dev_set.yaml").read_text().splitlines():
        m = re.match(r"\s*- id:\s*(\S+)", line)
        if m:
            cur = m.group(1)
            meta[cur] = {}
        elif cur and (m := re.match(r"\s+(domain|grading):\s*(.*)", line)):
            meta[cur][m.group(1)] = m.group(2).strip()
    out.mkdir(parents=True, exist_ok=True)
    with (out / "dev_tasks.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task", "domain", "grading", "reward", "agent_steps", "deliverable_files", "final_message_chars",
                    "prompt_tokens", "cached_tokens", "completion_tokens"])
        for t in sorted(manifest):
            tr = json.loads((base / f"artifacts/{t}/trajectory.json").read_text())
            fm = tr.get("final_metrics") or {}
            msg = (base / f"artifacts/{t}/final_message.md").read_text().replace("# Agent's final message", "").strip()
            w.writerow([t, meta[t]["domain"].split()[0], meta[t]["grading"], manifest[t]["reward"],
                        sum(s.get("source") == "agent" for s in tr["steps"]), len(manifest[t].get("files", {})), len(msg),
                        fm.get("total_prompt_tokens", ""), fm.get("total_cached_tokens", ""), fm.get("total_completion_tokens", "")])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, type=pathlib.Path)
    a = ap.parse_args()
    work = a.work.expanduser().resolve()
    ec, exp, data = work / "evidence-control", REPO / "experiments", REPO / "data"
    withheld: list[dict] = []
    build_cases(ec, exp, withheld)
    n = build_replies(ec, exp / "results", withheld)
    build_veriflow_run(work, data / "veriflow_run", withheld)
    build_template_baseline(work, data / "template_baseline")
    write_json(data / "WITHHELD.json", {
        "note": "Files used in the analysis but not published, with the SHA-256 of each so that a copy shown to the "
                "organizers can be checked against this list. Reasons: docs/DATA_POLICY.md.",
        "files": sorted(withheld, key=lambda r: r["path"])})
    print(f"{n} replies redacted; {len(withheld)} withheld files listed")


if __name__ == "__main__":
    main()
