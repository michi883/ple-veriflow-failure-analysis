# Provenance of the analyzed harness

The harness whose run we analyze is `veriflow-ple-starter`. **It is third-party code. We did not write it.** An earlier version of our Writeup called it "our own earlier harness"; that was wrong, and the Writeup was corrected on 2026-11-22.

## Source

`veriflow-ple-starter` is the harness bundle exported by Section 4 of the public Kaggle notebook

> Pablo Diego Pereira, *Physical Engineerings Last Exam: Starter Notebook*, version 13, Apache 2.0
> <https://www.kaggle.com/code/pablodiegoo/physical-engineerings-last-exam-starter-notebook>

The notebook states that it audits Google's `sample_submissions/plan-verify/serve.py` and exports a standalone `veriflow-ple-starter/` bundle. The files we ran match the notebook's embedded sources byte for byte:

| File | Bytes | SHA-256 | Match with the notebook |
|---|---:|---|---|
| `serve.py` | 42,488 | `3d5b8e2b558cf39d145d40fe2d061fd04507053806687b3aa9720b310f6a5157` | identical |
| `taskbridge.py` | 18,892 | `d307c6dd260d61982690c8d8e14c7de500c3dcd606afd8bd0e53f2c3f0cf45a9` | identical |
| `requirements.txt` | 32 | `396bec429a8dd6de344403f66049d42685ced99d5bc3d6dd728df00b9c0e199c` | identical |
| `submission.yaml` | 356 | `11dccb019395db69c9d88e6ec5c361675f3c4a7d6d1f9ca3eaa099d3aa5f4e8a` | differs by 3 bytes (notebook: 359 bytes) |

The one difference is ours: `model_api` set to `anthropic/claude-opus-5-5` for a local run on the Anthropic API. The copy in `third_party/veriflow-ple-starter/` is the exact tree the run used (git blob hashes recorded by the kit in `data/veriflow_run/run.json`; `reproduce.py` checks them).

## Upstream: Google's `plan-verify` sample

The starter's structure comes from `sample_submissions/plan-verify/serve.py` in the competition kit ([google-research/ple](https://github.com/google-research/ple), Apache-2.0):

- 15 of that sample's 22 module-level constants appear by name in the starter (`PLAN_SHARE`, `CHECK_SHARE`, `MAX_FIX_ROUNDS`, `WRAP_UP_SEC`, `CONTEXT_FULL`, `MAX_EFFORT_MODELS`, ...).
- The same planner, worker and verifier roles, with the verifier started in a fresh conversation that contains the task, the plan and the worker's summary.
- The planner and verifier lose only the file-writing tools and are told not to modify files.

About 7% of the starter's code lines match the sample line for line, so it is a rewrite and extension, not a copy. The starter adds the `verify_claims` and `verify_deliverables` tools, MCP tool discovery, context compaction and engineering-specific prompts.

Two of the design points our failure analysis relies on are therefore inherited from the official sample: the verifier receives the worker's summary, and "read-only" roles keep a shell.

## Checked and ruled out: PE-VeriFlow

The name resembles *PE-VeriFlow* by Robina Mirbahar ([repository](https://github.com/RobinaMirbahar/ple-veriflow-harness), [notebook](https://www.kaggle.com/code/robinamirbahar/pe-veriflow-hard-gate-agent-harness-ple), Apache-2.0). It is a different project and not the source of the files we ran:

- None of its 21 commits contains a file with the git blob hash of our `serve.py` or `taskbridge.py`.
- Its harness wraps the Pi agent; ours is a custom OpenAI-compatible loop. About 3% of our code lines match its `harness.py`; no module constants or long string literals are shared.

Nothing in this repository is a statement about PE-VeriFlow.

## What this means for our claims

- We ran the starter once, on one dev task, unmodified. Our analysis describes that run and the code paths it exercised. It is not an evaluation of the starter in general, and the notebook's author made no claim about this task.
- Our contribution is the failure analysis and the three follow-up experiments. The experiment code in `experiments/` is ours and does not use the starter.
