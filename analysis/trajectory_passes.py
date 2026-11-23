"""Shows the three passes in the harness's run on engineers-estimate-check, from content-free step metadata.

data/veriflow_run/trajectory_steps.csv has one row per tool call: the tool, the sizes of the command and of
its output, and whether the command names each of the four source files or the memo. The trajectory does not
label roles. The split below uses three observable markers:
  - the step that writes the .docx deliverable,
  - the two workspace-digest commands, which the harness runs immediately before and after its verifier
    (third_party/veriflow-ple-starter/serve.py, lines 914 and 938).
"""

from __future__ import annotations

import csv
import json
import pathlib

DATA = pathlib.Path(__file__).resolve().parent.parent / "data/veriflow_run"
SOURCES = ("estimate", "plans", "unit_cost_schedule", "hgl_pipe_schedule")


def passes() -> dict:
    rows = [{k: (v if k == "tool" else int(v)) for k, v in r.items()} for r in csv.DictReader((DATA / "trajectory_steps.csv").open())]
    write = next(r["step"] for r in rows if r["writes_docx"])
    digests = [r["step"] for r in rows if r["workspace_digest"]]
    first_tool = next(r["step"] for r in rows if r["tool"] != "http")
    spans = {"pass 1 (writes the memo)": (first_tool, write + 1), "pass 2 (re-check)": (write + 2, digests[0] - 1),
             "pass 3 (verifier, between the two digests)": (digests[0] + 1, digests[1] - 1)}
    out = {"memo_written_at_step": write, "workspace_digest_steps": digests, "passes": {}}
    for name, (a, b) in spans.items():
        sel = [r for r in rows if a <= r["step"] <= b]
        out["passes"][name] = {"steps": f"{a}-{b}", "tool_calls": len(sel),
                               "calls_naming_each_source": {s: sum(r[s] for r in sel) for s in SOURCES},
                               "reads_all_four_sources": all(sum(r[s] for r in sel) > 0 for s in SOURCES),
                               "memo_writes": sum(r["writes_docx"] for r in sel),
                               "output_chars": sum(r["observation_chars"] for r in sel)}
    return out


def main() -> None:
    p = passes()
    run = json.loads((DATA / "run.json").read_text())
    print(f"memo written at step {p['memo_written_at_step']}; workspace digests at steps {p['workspace_digest_steps']}")
    for name, v in p["passes"].items():
        print(f"{name:44} steps {v['steps']:6} calls={v['tool_calls']:2} sources={v['calls_naming_each_source']} "
              f"all four re-read: {v['reads_all_four_sources']}  memo writes: {v['memo_writes']}")
    u = run["usage"]
    print(f"model requests {u['model_requests']}, input tokens {u['input_tokens']:,} (cached {u['cached_input_tokens']}), "
          f"output tokens {u['output_tokens']:,}, agent time {run['timing']['agent_seconds']} s")
    print(f"final message: \"{run['final_message']['first_paragraph'][:150]}...\"")


if __name__ == "__main__":
    main()
