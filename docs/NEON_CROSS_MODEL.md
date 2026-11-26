# Supplementary note: Clash Flag with Gemini 4 Neon

2026-11-26. A small cross-model check of Clash Flag, the deterministic IFC clash check in our competition entry
([michi883/ple-baseline](https://github.com/michi883/ple-baseline), commit `ff27d9d47b45a343ebadb9aa5447907171271757`).
It is supplementary: it is not part of the entry, its artifacts, or the 29-task dev score (0.560, Claude Opus 5.5).

## Question

Clash Flag reads IFC files and the task instruction, not the model. Does a model from another family also act on it?

## Result

Two MEP routing dev tasks, one run each, `google-cloud/gemini-4-neon`, no prompt from us after the task instruction.

| | `data-hall-mep-routing` | `data-hall-mep-routing-offset` |
|---|---|---|
| IFC model built | yes | yes |
| Clash Flag checked it | yes (4 checks) | yes (4 checks) |
| First build | 0 clashing pairs | 18 clashing pairs |
| Flag shown to the agent | no (nothing to report) | yes, once |
| After the flag | not applicable | 0 at the next rebuild ("no clashing pairs now (was 18)") |
| Task verifier | 0.940: valid, 0 clashes, connectivity passed | 0.920: valid, 0 clashes, connectivity passed |
| Model calls | 41, plus 4 attempts that timed out | 40 |
| Agent time | 44 min | 23 min |
| Tokens in / out (share read from cache) | 2.78M / 128k (93%) | 2.88M / 135k (93%) |
| Cost at list price | $2.87 (at most $7.48, see Cost) | $3.07 |

- **The repair (`-offset`).** The 18 pairs were all between elements of the same IFC class: 6 `IfcPipeSegment`, 4 `IfcCableCarrierSegment`,
  4 `IfcCableSegment` and 4 `IfcBuildingElementProxy` pairs. The notice arrived in the output of the command that built the model.
  The agent's next action edited its own build script. The rebuild had 0 clashing pairs, and two later checks (a second copy, then the final model) also found 0.
- **No intervention (`data-hall-mep-routing`).** Neon's first model was already clash-free. Clash Flag checked it and added nothing
  to the agent's output, as designed. This run shows that the check stays silent on a clean model. It is not evidence that the flag helps.

Claude Opus 5.5 on the same tasks (from the Writeup; submitted harness):

| | `data-hall-mep-routing` | `data-hall-mep-routing-offset` |
|---|---|---|
| Clashing pairs, first build to final | 23 → 0 | 18 → 6 → 4 → 0 |
| Task verifier | 0.904 | 0.960 |
| Cost at list price, agent time | $18.92, 66 min | $20.28, 73 min |

## What this does and does not show

- A second model family received the same deterministic notice and repaired every flagged clash in one iteration.
- It does **not** establish that the flag caused the repair or improved the score. There is no run with the flag switched off,
  and each task was run once. Neon's clean first model on the other task shows it can deliver a clash-free model without any notice.
- The scores of the two models should not be ranked against each other: one run each, and the conditions below differ.

## Method, and how it differs from the submitted runs

| | Submitted entry (Opus 5.5) | This check (Neon) |
|---|---|---|
| Harness | commit `217269f` code (as in `ff27d9d`) | the same code with two changed lines (below) |
| Clash Flag, Scope Guard, relay code | as submitted | byte-identical to the submitted files |
| Model route | Anthropic API (`data-hall-mep-routing`), Vertex AI (`-offset`) | Vertex AI, through the kit's LiteLLM proxy configuration |
| Reasoning level | the harness asks Pi for its highest level (`max`) | the same harness setting; no parameter was tuned. For a Gemini request that names no level, the kit proxy applies `high`. We did not log which level reached the model |
| Kit | commit `8320f04` (`-offset`; the other run used that commit or its predecessor) | the next upstream commit, `5cf736f`, which adds Neon to the kit's model list and proxy |
| Dev-set revision | `e0c21fa` | `9ca4de8`: same instruction and input files; the grader now runs the build script as an unprivileged user. We saw no change to scoring terms in the grader's diff, and did not re-grade either side on the other revision |
| Runs | one per task, `--trials 1 --attempts 1` | the same; no rerun, no selection |

The two changed harness lines:

```diff
--- a/submission.yaml
-model_api: google-cloud/claude-opus-5-5
+model_api: google-cloud/gemini-4-neon
--- a/harness.py
-UNKNOWN_MODEL = {"input": ["text"], "contextWindow": 128000, "maxTokens": 16384}
+UNKNOWN_MODEL = {"input": ["text", "image"], "contextWindow": 1048576, "maxTokens": 65536}
```

The second line exists because Pi 1.1.0's model catalog does not list Gemini 4 Neon, so the harness would otherwise give it
generic limits (128K context, 16,384 output tokens). The Opus runs used Pi's catalog limits for Opus. The Neon values are
taken from third-party model listings, not from Google's documentation. In these runs the largest prompt was about 170K tokens.

Before the two task runs, `kit check` passed every stage it ran on the modified entry (model, listen, smoke, MCP).

## Cost

List prices used: $3 per million input tokens, $15 output, $0.15 cached input.

| Item | Cost |
|---|---|
| Tool-call probe and two `kit check` runs | $0.13 |
| `data-hall-mep-routing` | $2.87 |
| `data-hall-mep-routing-offset` | $3.07 |
| Total, settled calls | $6.07 |
| Four attempts that timed out in `data-hall-mep-routing` | $0 to $4.61 (upper bound: full prompt and maximum output for each) |
| Compute | $0 (the runs used a local machine) |

The four timed-out attempts were consecutive attempts at one model turn, about five minutes apart; the agent framework's
own retry then succeeded. Whether they were billed is not known, so the total is between $6.07 and $10.68.

## Not published

Trajectories, harness logs, verifier output and the generated models hold content from the gated dev set and stay private.
The numbers above were checked against them. `data/neon_cross_model/summary.json` holds the same aggregates in machine-readable form.
