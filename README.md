# Self-checks verify arithmetic, not decisions

Research materials for our Kaggle Writeup in the hackathon *Google - Agent Harness on Physical-Engineering's Last Exam (PLE)*:
<https://www.kaggle.com/competitions/physical-engineerings-last-exam/writeups/self-checks-verify-arithmetic-not-decisions-a-pl>

**This repository is not the competition entry.** The entry is the organizers' official submission template, unchanged: [michi883/ple-baseline](https://github.com/michi883/ple-baseline) at commit `d988c45fcb8b860268588de379cf5436f1c70467`. This repository holds what we contributed: a failure analysis of one confidently wrong agent run, three follow-up experiments, their saved results, and a script that recomputes every number in the Writeup.

## Findings

1. **A confident error that was a decision error.** We ran a public planner-worker-verifier starter harness on the dev task `engineers-estimate-check` (Claude Opus 5.5, one run). It scored 0.543 (25 of 46 rubric points) and ended with "Every number in it matched when I recomputed it". It found all 4 changes the rubric requires. It also made 5 changes that no instruction rule allowed, which put a $1.30M subtotal $8,756.30 too high and failed the two 10-point criteria.
2. **Its checks could only confirm arithmetic.** All 9 changed lines pass the arithmetic check (quantity × unit price = amount). The harness's claim checker evaluates the agent's own expression over the agent's own inputs. Its verifier is given the worker's summary. In the run, three passes read the same four source files: the first wrote the memo, the next two changed nothing.
3. **The error is not specific to that harness.** The organizers' own run of the official template on the same task (Pi, same model) made the same substitution, +$5,721, and scored the same 0.543.
4. **Asked one decision at a time, the model fixed 3 of the 5 errors** (7 of 9 decisions correct). Both remaining wrong decisions still passed the arithmetic check.
5. **Two fixes did not hold up.**
   - *Evidence-dependence tests* (change the evidence, see whether the decision moves): 0 of 7 correct decisions were falsely flagged, but both wrong decisions followed the changed evidence correctly. Their errors are misapplied rules, which these tests cannot see.
   - *A rule-aware change gate*: 27/27 on the nine decisions it was tuned on, then 21/25 against a baseline of 22/25 on 25 held-out decisions from two unseen tasks, at twice the cost and 2.6× the latency.

Details: [docs/FAILURE_ANALYSIS.md](docs/FAILURE_ANALYSIS.md), [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md).

## Reproduce

```bash
python3 reproduce.py
```

Python 3.10+ standard library only. No API key, no network, no Docker, no gated files; it takes about a second. It recomputes 56 values from the files here and compares each with the value the Writeup states.

Individual pieces:

```bash
python3 analysis/overstatement.py        # the $8,756.30 split by cause
python3 analysis/trajectory_passes.py    # the three passes over the same sources
python3 experiments/score_pilot.py       # experiment 1: evidence-dependence tests
python3 experiments/score_arms.py        # experiment 2: in-sample arms
python3 experiments/score_heldout.py     # experiment 3: held-out test of the frozen gate
uv run --with matplotlib python figures/make_figures.py   # the three Writeup figures
```

## What is ours, and what is not

| Component | Author | License | Where |
|---|---|---|---|
| Submitted harness and its 29-task dev artifacts | PLE organizers | Apache-2.0 | [michi883/ple-baseline](https://github.com/michi883/ple-baseline), an unchanged copy of [physical-engineerings-last-exam/submission-template](https://github.com/physical-engineerings-last-exam/submission-template) |
| `veriflow-ple-starter`, the harness whose run we analyze | Pablo Diego Pereira, exported by the Kaggle notebook [Physical Engineerings Last Exam: Starter Notebook](https://www.kaggle.com/code/pablodiegoo/physical-engineerings-last-exam-starter-notebook); built on Google's `sample_submissions/plan-verify` | Apache-2.0 | [third_party/veriflow-ple-starter](third_party/veriflow-ple-starter), byte-identical except one line |
| PLE benchmark, dev tasks, graders, competition kit | Han et al. 2026; Google | dev tasks gated; kit Apache-2.0 | not redistributed here |
| Failure analysis, experiment code, saved results, figures | us | Apache-2.0 (code), CC BY 4.0 (text and figures) | this repository |

We did not write either harness. How we identified the source of `veriflow-ple-starter`: [docs/PROVENANCE.md](docs/PROVENANCE.md).

## Layout

```
reproduce.py                 recomputes and checks every number in the Writeup
analysis/                    overstatement split; three-pass analysis
experiments/                 experiment code, decision definitions, redacted model replies
  harness.py pilot.py rule_gate.py     frozen before the held-out run (hashes in cases/frozen_mechanism.json)
  score_pilot.py score_arms.py score_heldout.py   scorers; run on the redacted replies
  results/                   149 saved replies (structured fields only)
data/veriflow_run/           run metrics, per-criterion grades, content-free step table
data/template_baseline/      per-task outcomes of the organizers' 29-task dev run
data/WITHHELD.json           SHA-256 of every file used but not published
figures/                     the three Writeup figures and the script that draws them
third_party/                 the public starter harness, as run
tools/build_public_data.py   how the public data was derived from the private files
docs/                        failure analysis, experiments, data policy, provenance, the Writeup text
```

## What is not published

The PLE dev tasks are gated, and the competition rules restrict publishing competition data. This repository therefore contains no task files, task prompts, grader or rubric text, raw trajectories, deliverables, or raw model replies (they quote task text). It contains structured decisions, recorded check results, scores, and hashes of everything withheld. The frozen gate code runs unmodified on the redacted replies; we verified that redacted and raw scoring give identical results. See [docs/DATA_POLICY.md](docs/DATA_POLICY.md).

## Limits

- One task and one run for the failure analysis. The three experiments use Claude Opus 5.5 only.
- 9 in-sample decisions × 3 samples; 25 held-out decisions × 1 sample from two tasks.
- We ran no 29-task dev evaluation ourselves. Every dev-set score here is the organizers'.
- Role boundaries in the analyzed run are inferred from the harness's control flow; the trajectory does not label them.

## Citation

PLE benchmark: Han et al., *PLE: Physical-Engineering's Last Exam*, 2026, [arXiv:2610.30537](https://arxiv.org/abs/2610.30537).
