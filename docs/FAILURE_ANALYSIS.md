# Failure analysis: a confident, wrong estimate review

One run of a public planner-worker-verifier starter harness on the PLE dev task `engineers-estimate-check`. Every number below is recomputed by `python3 reproduce.py`.

## 1. What was run

| | |
|---|---|
| Harness | `veriflow-ple-starter`, third-party, unmodified ([provenance](PROVENANCE.md)) |
| Model | Claude Opus 5.5 through the Anthropic API (local run, 2026-11-22) |
| Task | `engineers-estimate-check`, dataset revision `e0c21fa5` |
| Usage | 64 model requests, 3,547,663 input tokens (0 cached), 199,967 output tokens, 1,946 s of agent time |
| Grading | the kit's LLM judge set to `claude-opus-5-5`, run with `eval/run.py --artifacts` on the exported deliverables |
| Score | 0.543 = 25 of 46 weighted rubric points |

The live run's verifier container failed to start (an evaluator-side `RuntimeError`), so that run's score was unmeasured. We graded the deliverables it exported; their hashes match (`data/WITHHELD.json`).

## 2. The task, for readers outside civil engineering

A developer must post a bond that covers the cost of the public improvements in a subdivision. The bond is computed from an engineer's cost estimate: quantities taken from the drawings, multiplied by unit prices from a schedule the county publishes. The task is to check one section of that estimate (storm drain and detention) against three drawing sheets before it goes out, and to report the corrected section subtotal, a line-by-line table of changes, and the revised bond.

The instruction sets explicit default rules. In our words:

- Stay inside the section, but add anything the plans show that the estimate leaves out.
- Price from the published schedule.
- Flag an item only when no item in the schedule matches the plans. A flagged item keeps whatever amount the estimate already carries.
- Where the plans give no dimension for a line already in the estimate, the carried quantity stands.

So the baseline is the estimate as issued, and each change needs a specific licence from the plans.

## 3. What the agent did

The rubric's expected subtotal is reproduced to the cent by the carried subtotal plus exactly four changes. The agent made all four, and five more (`python3 analysis/overstatement.py`):

| Line | Agent's change | Reference | Excess | Rule the change needed |
|---|---:|---:|---:|---|
| Manholes, wrong size deleted | −$89,600 | −$89,600 | 0 | licensed |
| Manholes, correct size added | +$87,500 | +$87,500 | 0 | licensed |
| Manhole depth beyond the schedule limit | +$15,084 | +$15,083.70 | +$0.30 | licensed (rounded to the dollar) |
| Outlet pipe missing from the estimate | +$13,800 | +$13,800 | 0 | licensed |
| Access ramp, priced at an item from another section | +$2,107 | 0 | +$2,107 | no matching item in the section: flag, add nothing |
| Maintenance path, same substitute item | +$3,614 | 0 | +$3,614 | same |
| Basin excavation, recomputed from the storage volume | +$3,179 | 0 | +$3,179 | storage is not an excavation dimension: carried quantity stands |
| 48-inch pipe, length re-derived from a curve radius | −$516 | 0 | −$516 | carried length already matches the scheduled reaches |
| 60-inch pipe, same re-derivation | +$372 | 0 | +$372 | same |

- Agent subtotal $1,312,474; reference $1,303,717.70; **overstatement $8,756.30**.
- By cause: substitute item +$5,721; proxy quantity +$3,179; over-reading −$144; rounding +$0.30.
- The bond rounds up to the next $1,000: $5,393,000 against $5,384,000.

## 4. Why that cost 21 of 46 points

The judge failed three criteria (`data/veriflow_run/grade_summary.json`): the corrected subtotal (10 points), the bond computed from it (10 points), and the ramp, which should have been reported as having no matching schedule item (1 point). The seven other criteria passed. Every number the agent extracted from the drawings was right. Every lost point came from a decision about whether a change should exist.

## 5. Why the agent was confident

Its final message: "Every number in it matched when I recomputed it from the estimate, the plans, the HGL schedule and the County unit-cost schedule." That statement is true and beside the point.

**All nine changed lines pass an arithmetic check.** Quantity × unit price equals the amount on every line, including the five that should not exist.

**The claim checker is circular by construction.** `verify_claims` evaluates the `expr` the agent supplies over the `inputs` the agent supplies and compares the result with the value the agent claims (`serve.py` lines 209-226). It can show that the arithmetic is consistent. It cannot show that an input was read correctly or that the claim should be made. Its unit list (lines 94-108) has no CY, SY, LF, EA, VF or LS, the units of a quantity take-off, so a claim in the estimate's own units is reported as an unrecognized unit. The harness does not log calls to this tool, so we do not know whether it was called in this run.

**The verifier starts from the worker's account.** Its input is the task, the plan, a telemetry line and the worker's summary (lines 929-931). Its prompt asks it to recompute hard safety and code ratios and to confirm that the required files exist (lines 85-92). Nothing asks whether each change is allowed by the instruction's rules. This input design comes from the kit's `plan-verify` sample.

**Three passes read the same sources and reached the same numbers** (`python3 analysis/trajectory_passes.py`):

| Pass | Steps | Tool calls | Re-read all four source files | Wrote the memo |
|---|---|---:|---|---|
| 1 | 5-31 | 27 | yes | yes, at step 30 |
| 2 | 32-43 | 12 | yes | no |
| 3 | 45-64 | 20 | yes | no |

Pass 3 is the verifier: the harness runs a workspace digest immediately before and after it (lines 914 and 938), and those digests are steps 44 and 65. The trajectory does not label the other roles. Two facts suggest that pass 1 was the planner and pass 2 the worker: the final summary says the memo "was already saved" and that it "didn't change it", and the planner's tool set still includes the shell (lines 663-665 remove only `write_file` and `edit_file`). If so, the role that was told not to create files wrote the deliverable, and the worker and the verifier both only re-checked it. We present this as an inference.

Whatever the labels, the run spent 3.55M input tokens to produce one memo and re-read it twice. Three readings of the same four files by the same model are not three independent checks.

## 6. The same error without this harness

The organizers' run of the official template on this task (Pi 1.1.0, same model, 38 agent steps; public artifacts in [michi883/ple-baseline](https://github.com/michi883/ple-baseline/tree/d988c45fcb8b860268588de379cf5436f1c70467/artifacts/engineers-estimate-check)) also priced the ramp and the path at the substitute item, +$5,721, and reported a subtotal of $1,309,439. It scored the same 0.543. It did not make the excavation or pipe-length changes.

Two harnesses, one model, the same substitution. The tendency to price a near match instead of flagging is the model's. The starter's extra passes did not catch it, and the run added two more unlicensed changes.

## 7. What this does and does not show

- One task, one run. We have no estimate of how often this happens.
- The reference decisions for the five unlicensed changes are inferred from the rubric's subtotal, which they reproduce to the cent. One (the ramp) is also stated by a rubric criterion.
- The analysis describes this run and the code paths it used. It is not an evaluation of the starter overall.
- The general point is narrower than "verification does not work": checks that recompute what the agent already computed confirm arithmetic. On a review task, the errors that cost points were decisions, and nothing checked those.
