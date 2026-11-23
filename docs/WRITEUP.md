<!-- Text of the Kaggle Writeup as updated on 2026-11-22 (CC BY 4.0). Figures: ../figures/. -->

# Self-checks verify arithmetic, not decisions: a PLE failure analysis

- Repo (the entry): https://github.com/michi883/ple-baseline
- Commit SHA: d988c45fcb8b860268588de379cf5436f1c70467
- Primary model_api: google-cloud/claude-opus-5-5
- Other models tested: none (Section 3)
- Dev score (29 tasks): 0.491, **produced by the organizers** | $/task and min/task: not recorded in their artifacts
- Research code and data (ours, not the entry): https://github.com/michi883/ple-veriflow-failure-analysis

**Headline.** An agent can check its own work three times and still be confidently wrong, because its checks confirm arithmetic and its error is a decision. On the dev task *engineers-estimate-check*, a public planner-worker-verifier harness finished with "Every number in it matched when I recomputed it". All 9 lines it changed pass an arithmetic check. Five of them are changes no instruction rule allowed. They put a $1.30M subtotal $8,756.30 too high and cost 21 of 46 rubric points. Two fixes we then built did not survive a held-out test.

**What is ours, and what is not.**
- **Not ours: the entry.** The submitted commit is the organizers' official [submission template](https://github.com/physical-engineerings-last-exam/submission-template), unchanged (Pi 1.1.0 on `google-cloud/claude-opus-5-5`). Its harness, prompt and every dev score are the organizers'.
- **Not ours: the analyzed harness.** `veriflow-ple-starter` comes from Pablo Diego Pereira's public Kaggle [starter notebook](https://www.kaggle.com/code/pablodiegoo/physical-engineerings-last-exam-starter-notebook) (Apache-2.0), built on Google's `plan-verify` sample. We ran it unmodified. *Correction: an earlier version of this Writeup wrongly called it our own.*
- **Ours:** the failure analysis of that run, three decision-level experiments (149 model calls) and the lessons below.

## 1. Insights & Failures (25 pts)

**The task in plain terms.** A developer posts a bond sized from an engineer's cost estimate: quantities read off the drawings, times unit prices from a county schedule. The agent must check one section of the estimate against three drawing sheets. The instruction sets defaults: the issued estimate stands unless the plans contradict it, prices come only from the schedule, and an item the schedule does not cover is flagged, not priced.

1. **The confident error was a decision error.** The rubric's subtotal equals the issued subtotal plus exactly four changes. The agent found all four, then made five more (1.1). Every number it read from the drawings was right. The wrong subtotal failed two 10-point criteria (the subtotal and the bond computed from it); the ramp failed a 1-point criterion. Score 0.543, judged by `claude-opus-5-5`.
2. **Its checks were circular.**
   - `verify_claims` evaluates the agent's own expression over its own inputs. It confirms consistency, not whether a change should exist.
   - The "cold-read" verifier is handed the worker's summary (inherited from `plan-verify`).
   - Three passes read the same four source files. The first wrote the memo at step 30; the next two (steps 32-43 and 45-64) changed nothing.
   - "Read-only" roles keep a shell. The final summary says the memo "was already saved", so the planner pass appears to have written it. That is an inference: roles are not logged.
3. **It is the model, not one harness.** The organizers' Pi run on this task made the same ramp-and-path substitution (+$5,721) and scored the same 0.543.
4. **Short context helps.** Asked one decision at a time with the same evidence, the model fixed 3 of the 5 errors. Both remaining wrong answers still passed the arithmetic check.
5. **Asking for justification moves errors, and in-sample tuning lies** (1.2).

**Lessons for harness builders.**
- A re-check by the same model over the same inputs is a consistency check. Here two extra passes caught nothing.
- Give the verifier the deliverable and the instruction, not the worker's account. Enforce read-only roles with tools, not prompts.
- On review tasks, check the list of changes against the instruction's rules. That is where the points went.
- Hold out before believing a fix. Ours went from 27/27 to 21/25; the held-out set cost $3.74.

### 1.1 Failure Breakdown

**Figure 3A: the submitted template's 29-task run (organizers' artifacts).** 14.8 of 29 points were lost.

| Outcome | Tasks | Points lost |
|---|---|---|
| No deliverable exported | 2 | 2.0 (14%) |
| Deliverable graded 0 | 2 | 2.0 (14%) |
| Score below 0.40 | 9 | 6.3 (42%) |
| Score 0.40 to 0.80 | 9 | 3.8 (26%) |
| Score 0.80 or higher | 7 | 0.7 (5%) |

These are outcome classes read from the artifacts, not root causes; we hold rubric-level evidence for one task only. Six of the nine tasks below 0.40 are graded by automated constraint checks; seven of the nine in the next band, *engineers-estimate-check* included, by rubric.

**Figure 3B: root causes on *engineers-estimate-check* (our run of the public starter).**

| Cause | Unlicensed change | Effect on subtotal |
|---|---|---|
| Substitute item | Ramp and path priced at an item from another section. Rule: flag it | +$5,721 |
| Proxy quantity | Basin storage volume used as the excavation quantity. Rule: keep the carried quantity | +$3,179 |
| Over-reading | Pipe lengths re-derived from a curve radius. Rule: keep the carried lengths | −$144 |

### 1.2 Ablations

We ran no dev-set ablation. The only dev-set numbers are the organizers':

| Config | Dev (29) | Str (4) | Civ (9) | Elec (9) | Mech (7) | $/task |
|---|---|---|---|---|---|---|
| Submitted = official Pi template (organizers' run) | 0.491 | 0.305 | 0.617 | 0.482 | 0.448 | not recorded |

Our ablations are decision-level: one prompt, one decision, the full task evidence, exact-match scoring against a reference the model never sees. All Claude Opus 5.5.

| Config | In-sample: 9 estimate-check decisions × 3 samples | Held out: 25 decisions, 2 unseen tasks | $ per decision |
|---|---|---|---|
| Baseline | 20/27 | 22/25 | 0.033 |
| + one sentence: justify the change or keep the original | 23/27 | not run | not run |
| + rule protocol: quote the rule and the evidence, classify the link | 24/27 | 22/25 | 0.067 |
| + code gate, tuned in-sample, then frozen | 27/27 (fitted) | **21/25** | 0.067 |

**Ideas that failed.**
- **Evidence-dependence tests.** Change the evidence and see whether the decision moves: in presentation only (it must not), then decisively (it must). 0 of 7 correct decisions were falsely flagged, but both wrong decisions followed the changed evidence correctly. Their errors are misapplied rules, which this test cannot see.
- **The rule-aware gate.** In-sample, the protocol fixed both remaining errors in 3 of 3 samples and broke a correct decision in 3 of 3. The code gate added nothing over the model applying the protocol itself. Held out (16 decisions from *eor-review-corrections*, 9 from *landscape-sheet-revision*), the frozen gate fixed 1 decision, introduced 2 errors and took 2.6× as long.

## 2. Architecture & Reasoning (25 pts)

**Figure 1.**

**A. Submitted entry (organizers' template, unchanged).** `serve.py` and `taskbridge.py` speak the WebSocket protocol; `harness.py` runs Pi 1.1.0 with `--thinking max`; `task-tools.ts` runs Pi's file and shell tools inside the task container. Compaction, time limits and cancel recovery are Pi's and the template's. We changed nothing.

**B. The analyzed starter (third-party, not submitted).** Planner → worker → `verify_claims` → "cold-read" verifier, with up to two fix rounds. The red boxes mark where the confident error passed.

**Dev task walkthrough.** We use *engineers-estimate-check* because it is the only task where we hold trajectory-level and rubric-level evidence. A licensed engineer checking an estimate before it goes to the surety (the bond insurer) treats the issued estimate as the baseline, changes a line only when a drawing dimension, count or station contradicts it, and flags what the price schedule does not cover. A bond must be defensible line by line.

| Step | Licensed engineer | Agent |
|---|---|---|
| Manholes | Structure schedule plus a design note: replace 14 small manholes with 10 large | Same (correct) |
| Extra depth | Rim minus invert beyond 10 ft, summed: 22.02 vertical ft | Same (correct) |
| Missing outlet pipe | 0.25 ft drop at 0.25% slope = 100 linear ft | Same (correct) |
| Ramp and path | The section's only ramp item includes asphalt; the plans show gravel only → **flag** | Priced at another section's gravel item (scope shortcut) |
| Basin excavation | Storage volume is not cut volume; no excavation dimension → **keep** | Converted storage to cubic yards (proxy quantity) |
| Pipe lengths | Scheduled reach lengths, rounded up → **keep** | Re-derived arcs from a curve offset (over-reading) |

The agent's reasoning held wherever the plans state a count, an elevation or a length. It failed on three review skills: **scope discipline**, **default preservation** and **quantity identity**. No check in the harness covered them.

## 3. Cross-Model Generalizability (15 pts)

**Not done.** We ran no 29-task dev evaluation with any model. Every number we measured is Claude Opus 5.5.

| model_api | Baseline | Yours | Delta | Tokens in/out/cached | $/task |
|---|---|---|---|---|---|
| google-cloud/claude-opus-5-5 | 0.491 (organizers' run) | same, unchanged template | 0 | not recorded | not recorded |
| second model | not run | not run | not run | not run | not run |

What we can report is cross-harness, on one model and one task: Pi and the starter made the same substitution. Whether other model families share it is untested.

## 4. Efficiency & Scaling (20 pts)

**Figure 2A: organizers' run, score against agent steps per dev task.** Tokens are recorded for only 3 of 29 trajectories, so steps are the compute measure. Mean score falls as steps rise: 0.58 at 65 steps or fewer (10 tasks), 0.54 at 66 to 100 (11 tasks), 0.31 above 100 (8 tasks). Long runs are the hard tasks, and extra steps did not rescue them: both MEP-routing runs (123 and 159 steps) scored 0.

**Figure 2B: our measured cost against decision accuracy.**

| | $ per decision | Latency | Correct |
|---|---|---|---|
| Held-out baseline | 0.033 | 4.8 s | 22/25 |
| Held-out frozen gate | 0.067 | 12.4 s | 21/25 |

The gate doubled the cost (evidence prefix cached in both arms) with no accuracy gain.

**Re-reading everything is the expensive kind of verification.** The starter's run used 64 model calls, 3.55M input tokens (none cached) and 32 minutes of agent time, about $18 at list price, for the same 0.543 the template reached in 38 steps. Two of its three passes changed nothing.

We ran no budget sweep, so we cannot mark where dev-set accuracy plateaus. The headroom we can point to is decision-level: on *estimate-check*, 7 of 27 baseline answers were wrong and a one-sentence prompt recovered 3.

## 5. Reproducibility (15 pts)

**The entry (commit above).**
- `python -m kit validate`: ok.
- `python -m kit artifacts`: accepted. 28 of 29 tasks are usable; *foundation-strategy* has no gradable file. It warns that the artifacts were made with an earlier `dev_set.yaml`.
- We did **not** run `kit check`: it needs Docker and a model call. The commit is the organizers' reference template, byte-identical.

**Our research (separate repository, linked at the top and under Project Links).**
- `python3 reproduce.py` recomputes 56 values in this Writeup from saved results and checks each. Standard library only: no API key, no network, no Docker.
- Included: experiment code, 149 saved model replies as structured fields, run metrics, the starter exactly as run, figure code.
- Not included: task files, prompts, rubric text and raw replies, because the dev set is gated and competition data may not be republished. Hashes of everything withheld are listed; the organizers can have the files.
- The gate code was hash-frozen before the held-out run.

**Limits.** One task and one run for the failure analysis; 9 in-sample decisions × 3 samples; 25 held-out decisions × 1 sample; one model; $8.56 of API spend. We read dev-set grading data offline only, to analyze failures and set references. The submitted harness never reads it.
