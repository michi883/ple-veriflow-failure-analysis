# veriflow-ple-starter (third-party)

Not our code. Kept here unmodified so the failure analysis can point at exact lines.

- **Author and source:** Pablo Diego Pereira, exported by Section 4 of the Kaggle notebook *Physical Engineerings Last Exam: Starter Notebook* (version 13): <https://www.kaggle.com/code/pablodiegoo/physical-engineerings-last-exam-starter-notebook>
- **Upstream:** built on `sample_submissions/plan-verify` from the PLE competition kit, <https://github.com/google-research/ple> (Copyright 2026 Google LLC).
- **License:** Apache License 2.0, for both the notebook and the kit. A copy is in [`LICENSE`](../../LICENSE) at the repository root.
- **Changes:** `serve.py`, `taskbridge.py` and `requirements.txt` are byte-identical to the notebook's sources. In `submission.yaml` we changed `model_api` to `anthropic/claude-opus-5-5` for a local run. Nothing else.
- **Not for submission:** this copy declares a non-sponsored `model_api` and has no `artifacts/`.

Hashes and how the source was identified: [docs/PROVENANCE.md](../../docs/PROVENANCE.md).

Lines the failure analysis refers to, in `serve.py`:

| Lines | What |
|---|---|
| 65-92 | planner, worker and verifier system prompts |
| 94-108 | units `verify_claims` accepts; no CY, SY, LF, EA, VF or LS |
| 177-240 | `verify_claims`: evaluates each claim's own `expr` over its own `inputs` |
| 663-665 | "read-only" tool set: everything except `write_file` and `edit_file`, so the shell stays |
| 896-908 | planner call, then worker call |
| 910-956 | verify loop: workspace digest, verifier, workspace digest, optional fix round |
| 929-931 | verifier input: task, plan, harness telemetry and the worker's summary |
