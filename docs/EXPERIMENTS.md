# Experiments

Three decision-level experiments followed the failure analysis. All use Claude Opus 5.5 (`claude-opus-5-5`, effort medium) through the Anthropic API, on 2026-11-22. Total spend: $8.56 for 149 calls. The scorers below read the saved replies; they make no model calls.

**Common design.** One prompt asks for one decision. It contains the task instruction, the full evidence the task points to, the decision question and an answer format. The same evidence prefix is shared by every decision of a task and cached. Reference answers are used only for scoring; a check in the harness refuses to build a prompt that contains grading-only text.

The nine in-sample decisions are the nine lines the analyzed run changed on `engineers-estimate-check`: four it got right (D1-D4) and five it got wrong (W1-W5).

## Experiment 1: do evidence-dependence tests catch the wrong decisions?

`python3 experiments/score_pilot.py` (27 calls, $2.33)

Each decision is asked three times: on the original evidence, after a presentation-only change (reordered notes, removed thousands separators), and after a change to the decisive evidence. A decision is flagged if it moves under the first change or fails to move as the rules require under the second.

| | Result |
|---|---|
| Correct on the original evidence | 7 of 9 |
| Errors of the analyzed run fixed when asked in isolation | 3 of 5 |
| Correct decisions falsely flagged | 0 of 7 |
| Wrong decisions flagged | 1 of 2 |
| Wrong decisions that followed the changed evidence correctly | 2 of 2 |
| Wrong decisions that pass the arithmetic check | 2 of 2 |

The one flag came from a rounding difference under a presentation-only change (10,325.33 against 10,325), not from the test doing its job. Both wrong decisions tracked the evidence. Their errors are misapplied rules: a proxy quantity and a substitute item. A test of sensitivity to evidence cannot see those.

One scoring rule was added after seeing the replies: when the model carries the quantity under a different schedule item than the one asked about, the quantity under the asked item counts as zero (`decision_key` in `harness.py`). Without it, one correct decision is scored wrong.

**Verdict: do not scale this design.** No false alarms, but it does not detect the failure we care about.

## Experiment 2: does asking for justification fix them?

`python3 experiments/score_arms.py` (9 decisions × 3 samples per arm; 81 calls, of which 9 baseline samples are reused from experiment 1)

| Arm | Correct samples |
|---|---:|
| Baseline single-decision prompt | 20/27 |
| + one sentence: justify a change or keep the original | 23/27 |
| + rule protocol, the model's own final answer | 24/27 |
| + code gate v1 (every change needs a direct evidence link) | 24/27 |
| + code gate v2 (tuned on these nine decisions afterwards, then frozen) | 27/27 |

The protocol (`rule_gate.py`) numbers the instruction's sentences, then asks for the original value, the governing rule quoted verbatim, the decisive evidence quoted verbatim, whether the link from evidence to value is direct or inferred, and which keep, flag and scope rules apply. The gate is deterministic code over that reply.

- The protocol fixed both errors that survived experiment 1 in 3 of 3 samples each.
- It also turned a correct decision wrong in 3 of 3 samples: the model labelled a correct addition as an inferred link, and the rule blocked it. Self-justification moved the errors.
- Gate v1 scored the same as the model applying the protocol itself. Its verbatim-quote checks never changed an outcome.
- Gate v2 (an inferred link blocks only changes to a value the document already carries) reaches 27/27. It was fitted to these nine decisions after seeing them, so that number is not evidence.

## Experiment 3: does the frozen gate hold up on unseen tasks?

`python3 experiments/score_heldout.py` (25 decisions × 2 arms × 1 sample; 50 calls, $3.74)

Gate v2 was frozen (hashes and timestamps in `experiments/cases/frozen_mechanism.json`) before any held-out task was downloaded. One change followed, after reading the held-out tasks' structure and before any held-out model call: the protocol takes the decision-object field text as a parameter, so it can ask for a `{"value": ...}` answer. The gate logic did not change; `reproduce.py` checks the hash.

Held-out decisions come from two dev tasks we had not used: 16 review-comment decisions from `eor-review-corrections`, referenced against the closures recorded in the task's grading data, and 9 decisions from `landscape-sheet-revision`, referenced against its rubric.

| | Baseline | Frozen gate | Protocol, model's own answer |
|---|---:|---:|---:|
| `eor-review-corrections` (16) | 14 | 13 | 13 |
| `landscape-sheet-revision` (9) | 8 | 8 | 9 |
| **All (25)** | **22** | **21** | **22** |
| $ per decision, evidence prefix cached | 0.033 | 0.067 | |
| Mean latency | 4.8 s | 12.4 s | |

The gate fixed one decision and introduced two new errors. In both new errors the model reported that a keep or flag rule applied, and the gate overrode an answer that was correct. The one fix came from the protocol prompt changing the model's answer; the gate logic left it alone.

**Verdict: no-go.** No accuracy gain, twice the cost, 2.6× the latency. The 27/27 in-sample result was post-hoc fitting, and the held-out test is what showed it.

## Limits

- One model. Three samples in-sample, one sample held out. A difference of one decision in 25 is within noise; the claim is "no evidence of benefit", not "the gate is harmful".
- Decision-level prompts are not agent runs. The model sees the right evidence in one context, which an agent has to find for itself.
- Held-out references for `eor-review-corrections` are the task's recorded closures mapped to incorporate / do not incorporate. That mapping is ours.
- Costs exclude the one-time cache write of each task's evidence prefix, which the two arms share.

## Rerunning the model calls

`pilot.py` and `run_heldout.py` send the prompts. They need the gated dataset files, the evidence packets built by `build_evidence.py` and `build_heldout.py`, and an Anthropic API key. Hashes of the packets we used are in `experiments/evidence/manifest.json` and `experiments/heldout/*/manifest.json`, so a rebuilt packet can be checked.
