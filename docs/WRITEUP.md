# Scope Guard: Deterministic Enforcement of Explicit Task Restrictions

- Repo (the entry): https://github.com/michi883/ple-baseline
- Commit SHA: 81c4e77ccee7e6eb8832241f5364e75674f9e759
- Primary model_api: google-cloud/claude-opus-5-5
- Other models tested: none
- Dev score (29 tasks): **0.034**. Only 1 of 29 dev tasks has artifacts (*engineers-estimate-check*, 1.0); the other 28 were not run and count 0 | about $8 and 27 min for that task
- Research code and data: https://github.com/michi883/ple-veriflow-failure-analysis

**Headline.** An agent can get every number right and still break the task's rules. On the dev task *engineers-estimate-check*, a public planner-worker-verifier harness finished with "Every number in it matched when I recomputed it". All 9 lines it changed pass an arithmetic check; 5 were changes no instruction rule allowed, costing 21 of 46 rubric points. We built **Scope Guard**, a deterministic check in the harness that flags one such violation, an item taken from outside the section the instruction names, without another model. It passes 20 deterministic tests. The submitted harness scored 1.0 on that task, against 0.543 for the starter. **The guard raised no flag in that run, so the improvement cannot be attributed to it.**

**What is ours, and what is not.**
- **Not ours: the base harness.** The entry is the organizers' [submission template](https://github.com/physical-engineerings-last-exam/submission-template) (Pi 1.1.0), unchanged except for the guard. The 29-task numbers in Figures 2A and 3A are the organizers' run of that template.
- **Not ours: the analyzed harness.** `veriflow-ple-starter` comes from Pablo Diego Pereira's public Kaggle [starter notebook](https://www.kaggle.com/code/pablodiegoo/physical-engineerings-last-exam-starter-notebook) (Apache-2.0), built on Google's `plan-verify` sample. We ran it unmodified. *Correction: an earlier version of this Writeup wrongly called it our own.*
- **Ours:** Scope Guard and its tests, one dev run of the submitted harness, the failure analysis and three decision-level experiments (149 model calls).

## 1. Insights & Failures (25 pts)

**The task in plain terms.** A developer posts a bond sized from an engineer's cost estimate: quantities read off drawings, times unit prices from a county schedule. The agent checks one section of the estimate against three drawing sheets. The instruction says: stay in that section, keep the issued figure unless the plans contradict it, and flag, not price, an item the schedule does not cover.

1. **The confident error was a decision error.** The rubric's subtotal equals the issued subtotal plus exactly four changes. The starter's agent found all four, then made five more (1.1): $8,756.30 too high on a $1.30M subtotal. Every number it read from the drawings was right. Score 0.543.
2. **Its checks were circular.** `verify_claims` re-evaluates the agent's own expression over its own inputs, and the "cold-read" verifier is handed the worker's summary. Two of three passes changed nothing.
3. **It is the model, not one harness.** The organizers' run of the unmodified template made the same ramp-and-path substitution (+$5,721) and scored the same 0.543.
4. **It is not every run.** Our run of the submitted harness made none of the five changes and scored 1.0 with the guard silent. Three runs, one model: 0.543, 0.543, 1.0.
5. **Asking for justification moves errors, and in-sample tuning lies** (1.2).

**Lessons for harness builders.**
- A re-check by the same model over the same inputs only checks consistency.
- Let code check what code can: an explicit restriction against the file the agent wrote.
- Hold out before believing a fix (ours went from 27/27 to 21/25), and repeat runs before crediting one.

### 1.1 Failure Breakdown

**Figure 3A: the organizers' 29-task run of the unmodified template** (their artifacts; our harness ran one of these tasks). 14.8 of 29 points were lost: 2 tasks exported no deliverable, 2 were graded 0, 9 scored below 0.40 (42% of the loss), 9 scored 0.40 to 0.80 and 7 scored higher. These are outcome classes, not root causes.

**Figure 3B: root causes on *engineers-estimate-check* (our run of the public starter).**

| Cause | Unlicensed change | Effect on subtotal | Can code check it? |
|---|---|---|---|
| Substitute item | Ramp and path priced at an item from another section. Rule: flag it | +$5,721 | Yes: Scope Guard |
| Proxy quantity | Basin storage volume used as the excavation quantity. Rule: keep the carried quantity | +$3,179 | No |
| Over-reading | Pipe lengths re-derived from a curve radius. Rule: keep the carried lengths | −$144 | No |

### 1.2 Ablations

Dev-set runs (we ran one task):

| Config | Dev (29) | *estimate-check* | Guard flags | $/task |
|---|---|---|---|---|
| Unmodified template (organizers' run) | 0.491 | 0.543 | no guard | not recorded |
| Submitted: template + Scope Guard (our run) | 0.034 (28 tasks not run) | 1.0 | 0 | about $8 |

The one task we ran went from 0.543 to 1.0, but the guard never fired: the agent flagged the ramp and the path instead of pricing them. This is run-to-run variance, not an ablation result.

Decision-level ablations: one prompt, one decision, the full task evidence, exact-match scoring against a hidden reference. All Claude Opus 5.5.

| Config | In-sample: 9 estimate-check decisions × 3 samples | Held out: 25 decisions, 2 unseen tasks | $ per decision |
|---|---|---|---|
| Baseline | 20/27 | 22/25 | 0.033 |
| + one sentence: justify the change or keep the original | 23/27 | not run | not run |
| + rule protocol: quote the rule and the evidence, classify the link | 24/27 | 22/25 | 0.067 |
| + code gate, tuned in-sample, then frozen | 27/27 (fitted) | **21/25** | 0.067 |

**Ideas that failed.**
- **Evidence-dependence tests.** Change the evidence and see whether the decision moves. No correct decision was falsely flagged, but both wrong ones tracked the evidence too: their errors are misapplied rules.
- **The rule-aware gate.** It made the model justify each decision and overrode it in code. Held out, it fixed 1 decision, introduced 2 errors and took 2.6× as long.

## 2. Architecture & Reasoning (25 pts)

**Figure 1A: the submitted entry.** In the organizers' template, `serve.py` and `taskbridge.py` speak the WebSocket protocol, `harness.py` runs Pi 1.1.0 and `task-tools.ts` runs Pi's tools in the task container. Ours is `scope_guard.py`, a proxy on the relay between those tools and the container.

**How Scope Guard works.**
- It reads the instruction for an explicit confinement to a numbered unit ("Stay in Section 2"). Without one it is inactive.
- After each command it reads the tables in the document files that changed (`.docx`, `.xlsx`, `.md`, `.csv`).
- If a table keys rows under the allowed unit (2.35, 2.38) and others under another (3.05), it appends a flag naming those rows to that command's output, while the model can still act.
- A flag that still stands at the end goes into the final message for the human reader.
- A second check flags modified files when the instruction says nothing else is to change.
- It calls no model and never blocks or edits. Whether a flagged row is wrong stays with the model and the reader.

**Unlike the gate that failed**, it reads the deliverable, not the model's justification; adds no model call; and cannot change an answer.

**Evidence, and its limits.**
- 20 deterministic tests pass: a prohibited row is caught, a legitimate table passes, seven ambiguous cases are not flagged, and the wiring runs through the real `harness.solve`.
- Replayed offline on the organizers' 29 committed outputs, it recognizes a restriction in 2 instructions and flags 1 file: the two rows that carry the +$5,721.
- In the one live run it was active and did not falsely flag a correct memo. **We have not observed it change a model's answer.**
- It covers one of the three causes, and runs only under an explicit confinement: elsewhere, 5 of 15 tables with numbered keys mix units legitimately.

**Figure 1B: the analyzed starter (third-party).** Planner → worker → `verify_claims` → "cold-read" verifier. The red boxes mark where the confident error passed.

**Dev task walkthrough.** A licensed engineer checking an estimate for the surety (the bond insurer) treats the issued estimate as the baseline, changes a line only when a drawing contradicts it, and flags what the price schedule does not cover.

| Step | Licensed engineer | Starter run (0.543) | Submitted harness (1.0) |
|---|---|---|---|
| Manholes | Schedule plus a design note: replace 14 small manholes with 10 large | Same | Same |
| Extra depth | Rim minus invert beyond 10 ft: 22.02 vertical ft | Same | Same |
| Missing outlet pipe | 0.25 ft drop at 0.25% slope = 100 linear ft | Same | Same |
| Ramp and path | The section's ramp item includes asphalt; the plans show gravel → **flag** | Priced at another section's item | Flagged at $0 |
| Basin excavation | Storage volume is not cut volume → **keep** | Converted storage to cubic yards | Kept |
| Pipe lengths | Scheduled reach lengths → **keep** | Re-derived arcs from a curve offset | Kept |

Reasoning held wherever the plans state a count, an elevation or a length. Failures were in three review skills: **scope discipline**, **default preservation** and **quantity identity**.

## 3. Cross-Model Generalizability (15 pts)

**Not done.** Every number we measured is Claude Opus 5.5.

| model_api | Unmodified template | Ours | Delta | Tokens in/out/cached | $/task |
|---|---|---|---|---|---|
| google-cloud/claude-opus-5-5 | 0.491 (organizers, 29 tasks) | 0.034 (1 task run: 1.0) | not comparable | 9.42M / 176k / 8.79M | about $8 |
| second model | not run | not run | not run | not run | not run |

Scope Guard reads the instruction and the files, not the model, so it should transfer; that is untested.

## 4. Efficiency & Scaling (20 pts)

**Figure 2A: score against agent steps.** Dots are the organizers' run of the unmodified template: mean score 0.58 at 65 steps or fewer (10 tasks), 0.54 at 66 to 100 (11), 0.31 above 100 (8). Extra steps did not rescue hard tasks. The star is our run.

One task, three runs: the starter scored 0.543 in 64 model calls for about $18 (three passes, no caching); the unmodified template 0.543 in 38 steps (cost not recorded); the submitted harness 1.0 in 45 calls for about $8 (9.42M input tokens, 93% cached; 27 minutes). The guard added one file-listing command per shell call and no model call.

**Figure 2B: decision-level cost against accuracy.** Held out, the baseline scored 22/25 at $0.033 and 4.8 s per decision; the frozen gate 21/25 at $0.067 and 12.4 s.

We ran no budget sweep, so we cannot mark where accuracy plateaus. On *estimate-check*, 7 of 27 baseline decisions were wrong and a one-sentence prompt recovered 3.

## 5. Reproducibility (15 pts)

**The entry (commit above).**
- `kit check`: every stage passes, "ready to submit". `kit artifacts`: accepted, warning that 28 of 29 dev tasks count 0. `kit check` ran on the commit that holds the artifacts; the submitted commit adds only a README change.
- `artifacts/` holds one task, exported by `eval/run.py` from this harness. The organizers' artifacts for the other 28 are removed: this harness did not produce them.
- Model for the run: `claude-opus-5-5` from a personal Anthropic API key through a local LiteLLM proxy. Local judge: `claude-opus-5-5`.
- `python3 -m unittest test_scope_guard test_harness_wiring`: 20 tests, no model or Docker.

**Our research (separate repository).** `python3 reproduce.py` rechecks 67 numbers in this Writeup from saved results. Task files, prompts, rubric text and raw replies are withheld: the dev set is gated.

**Limits.** One dev task and one run for the submitted harness; one run for the failure analysis; small decision samples; one model. A re-run of the task may score 0.543 again. We read dev-set grading data offline only; the harness never reads it.
