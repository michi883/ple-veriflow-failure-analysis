"""Real-model pilot: sends the harness's 27 prompts to Claude, concurrently, under a hard cost cap.

Each reply is saved as <out>/<prompt sha256>.json the moment it arrives, so a rerun skips finished
prompts. Score afterwards with:  python3 harness.py --responder cache:<out>

Usage: uv run --with anthropic python pilot.py [--limit N] [--workers 5] [--cap 5.00]
The API key comes from ANTHROPIC_API_KEY, else from ../.env (the value is never printed).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import threading
import time

import anthropic

import harness
import rule_gate

MODEL = "claude-opus-5-5"
EFFORT = "medium"        # the model's default; thinking cannot be disabled on Opus 5.5
MAX_TOKENS = 12000       # thinking + a ~150-token JSON answer
PRICE = {"in": 4.00, "out": 20.00, "cache_write": 5.00, "cache_read": 0.20}  # $ per 1M tokens
SPLIT = "\n\n## Decision\n"


def api_key() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None  # the SDK reads it
    env = harness.HERE.parent / ".env"
    for line in env.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip().removeprefix("export ").strip() == "ANTHROPIC_API_KEY":
            return v.strip().strip("'\"")
    raise SystemExit("ANTHROPIC_API_KEY is not set and not in ../.env (no values printed)")


def cost(u: dict) -> float:
    return (u.get("input_tokens", 0) * PRICE["in"] + u.get("output_tokens", 0) * PRICE["out"]
            + u.get("cache_creation_input_tokens", 0) * PRICE["cache_write"]
            + u.get("cache_read_input_tokens", 0) * PRICE["cache_read"]) / 1e6


def worst_case(prompt: str) -> float:
    return (len(prompt) / 2.5 * PRICE["cache_write"] + MAX_TOKENS * PRICE["out"]) / 1e6


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/pilot-opus55")
    ap.add_argument("--limit", type=int, help="stop after this many new calls")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--cap", type=float, default=5.00, help="hard cap on estimated spend, $")
    ap.add_argument("--arm", choices=["baseline", "plain", "gate"], default="baseline",
                    help="baseline: unchanged prompt; plain: + one justify-or-keep sentence; gate: rule_gate protocol")
    ap.add_argument("--tests", default="baseline,invariance,sensitivity")
    ap.add_argument("--budget-root", help="count spend already recorded under this directory against --cap")
    a = ap.parse_args()

    out = harness.HERE / a.out
    out.mkdir(parents=True, exist_ok=True)
    tests = json.loads(harness.TESTS.read_text())
    instruction, packet = harness.load_evidence()
    forbidden = json.loads(harness.ORACLE.read_text())["forbidden_in_prompt"]
    jobs = []
    for d, test, _spec, _ev, prompt in harness.cases(tests, instruction, packet, forbidden, None):
        if test not in a.tests.split(","):
            continue
        prefix, _, rest = prompt.partition(SPLIT)
        if a.arm == "plain":
            prompt = prompt + "\n" + rule_gate.PLAIN + "\n"
        elif a.arm == "gate":
            prompt = prefix + SPLIT + d["question"] + "\n\n" + rule_gate.protocol(instruction) + "\n"
        sha = hashlib.sha256(prompt.encode()).hexdigest()
        if not (out / f"{sha}.json").exists():
            jobs.append((d["id"], test, prompt, sha))
    done = [json.loads(p.read_text()) for p in out.glob("*.json")]
    root = harness.HERE / a.budget_root if a.budget_root else out
    spent = sum(json.loads(p.read_text())["cost_usd"] for p in root.rglob("*.json"))
    # Baselines share the evidence prefix: send one first so the others read it from cache.
    jobs.sort(key=lambda j: j[1] != "baseline")
    if a.limit is not None:
        jobs = jobs[:a.limit]
    print(f"{len(done)} cached (${spent:.3f}), {len(jobs)} to send, workers={a.workers}, cap=${a.cap:.2f}", flush=True)

    client = anthropic.Anthropic(api_key=api_key(), max_retries=4, timeout=600)
    lock = threading.Lock()
    state = {"spent": spent, "reserved": 0.0, "stopped": 0, "rate_limited": 0}

    def call(job):
        did, test, prompt, sha = job
        reserve = worst_case(prompt)
        with lock:
            if state["spent"] + state["reserved"] + reserve > a.cap:
                state["stopped"] += 1
                return None
            state["reserved"] += reserve
        prefix, _, rest = prompt.partition(SPLIT)
        block = {"type": "text", "text": prefix}
        if test == "baseline":
            block["cache_control"] = {"type": "ephemeral"}
        t0 = time.time()
        try:
            r = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, output_config={"effort": EFFORT},
                messages=[{"role": "user", "content": [block, {"type": "text", "text": SPLIT.lstrip("\n") + rest}]}],
            )
        except anthropic.RateLimitError:
            with lock:
                state["reserved"] -= reserve
                state["rate_limited"] += 1
            raise
        except Exception:
            with lock:
                state["reserved"] -= reserve
            raise
        latency = time.time() - t0
        usage = r.usage.to_dict()
        rec = {"decision": did, "test": test, "prompt_sha256": sha, "model": r.model,
               "stop_reason": r.stop_reason, "text": "".join(b.text for b in r.content if b.type == "text"),
               "usage": usage, "cost_usd": cost(usage), "latency_s": round(latency, 1),
               "request_id": r._request_id, "effort": EFFORT, "max_tokens": MAX_TOKENS, "arm": a.arm}
        tmp = out / f"{sha}.tmp"
        tmp.write_text(json.dumps(rec, indent=1))
        tmp.replace(out / f"{sha}.json")
        with lock:
            state["reserved"] -= reserve
            state["spent"] += rec["cost_usd"]
            print(f"{did:9} {test:11} {latency:6.1f}s  in={usage.get('input_tokens')}+cr{usage.get('cache_read_input_tokens')}"
                  f"+cw{usage.get('cache_creation_input_tokens')} out={usage.get('output_tokens')}  ${rec['cost_usd']:.3f}"
                  f"  total ${state['spent']:.3f}  {r.stop_reason}", flush=True)
        return rec

    t_start = time.time()
    results = []
    if jobs and jobs[0][1] == "baseline" and not any(r["test"] == "baseline" for r in done):
        results.append(call(jobs.pop(0)))  # warm the shared cache
    with cf.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for fut in cf.as_completed([pool.submit(call, j) for j in jobs]):
            try:
                results.append(fut.result())
            except Exception as exc:  # recorded, not fatal: rerun resumes
                print(f"call failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
    got = [r for r in results if r]
    print(f"\nsent {len(got)} in {time.time() - t_start:.0f}s; mean latency "
          f"{sum(r['latency_s'] for r in got) / max(1, len(got)):.1f}s; spent ${state['spent']:.3f} of ${a.cap:.2f}; "
          f"skipped for cap: {state['stopped']}; rate-limited calls: {state['rate_limited']}", flush=True)


if __name__ == "__main__":
    main()
