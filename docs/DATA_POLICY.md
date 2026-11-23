# What is published, what is withheld, and why

## The constraints

- The PLE dev tasks are in a gated dataset. Access requires agreeing not to redistribute or publicly repost the data, not to train on it, and not to expose it to a model under evaluation.
- The Kaggle competition rules (section B.4) allow using competition data only for the competition and forbid publishing or redistributing it to parties outside it.
- The kit's rules say participants must not retain or share task contents.

A public repository is outside the competition, so nothing here reproduces task files, task prompts, graders or rubric text.

## Published

| What | Form |
|---|---|
| Experiment code | as run; `harness.py`, `pilot.py` and `rule_gate.py` are byte-identical to the frozen versions |
| Decision definitions | our questions and our descriptions of each evidence change (`experiments/cases/tests.public.json`) |
| References for `engineers-estimate-check` | the nine decisions (`experiments/cases/oracle.json`); the Writeup itself states them |
| 149 model replies | structured fields only: the decision, confidence, rule ids, booleans, token usage, cost, latency |
| The analyzed run | metrics, per-criterion pass or fail with our labels, a step table with sizes and flags, the first paragraph of the final message |
| The organizers' 29-task run | per-task score, steps and file counts, derived from their public artifacts |
| Hashes | SHA-256 of every withheld file (`data/WITHHELD.json`) and of every reply's full text |

## Withheld

| What | Why |
|---|---|
| Evidence packets and task instructions | text of gated task files |
| Literal anchor strings of the evidence operations | they quote the task files; the operation type and a hash are published |
| Raw model replies | they quote task text and instruction sentences verbatim |
| Reference answers for the two held-out tasks | derived from those tasks' graders and not stated in the Writeup |
| Full question texts of the held-out decisions | they follow the wording of the grading data; ids and our topic labels are published |
| Rubric text and judge output | grader content |
| The run's trajectory and deliverable | tool outputs and the memo restate the task files |

API request ids are removed from the saved replies as well.

## How the scorers still run

- **In-sample arms and experiment 1** score the structured decisions against `oracle.json`. Nothing withheld is needed.
- **The frozen gate** checks that the rule and evidence quotes in a reply are verbatim. That needs the task text. We recorded each check's result when building the public data, and the scorer replays it: every quote is replaced by a sentinel string that `rule_gate.verbatim()` accepts or rejects exactly as it accepted or rejected the real quote. `rule_gate.py` is not modified, and `reproduce.py` checks its hash against the freeze record.
- **Held-out correctness** is published as verdicts: each saved reply carries whether the scorer judged each candidate answer correct against the withheld reference.

`tools/build_public_data.py` is the complete derivation. With the private files present (`PLE_PRIVATE=<dir>`), the same scorers run on the raw replies and real texts. We ran both modes and they agree on every row of all three experiments.

## Checking and rebuilding

- Anyone with dataset access can rebuild the evidence packets with `experiments/build_evidence.py` and `experiments/build_heldout.py` and compare their hashes with the manifests. `build_heldout.py` reads the nine landscape decisions (question and accepted-answer patterns) from a private file; with it, the script reproduces the held-out files byte for byte.
- The competition organizers and judges can have the withheld files on request. Each can be checked against `data/WITHHELD.json`.

## Residual exposure

The published material necessarily describes dev tasks: decision questions, the estimate task's reference decisions, and which held-out answers were scored correct. This is the level of detail a failure analysis needs and the Writeup already contains. If the organizers consider any of it too much, we will remove it.
