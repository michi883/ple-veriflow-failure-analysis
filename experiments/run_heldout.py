"""Held-out comparison: baseline prompt vs frozen rule gate, identical decisions and evidence.

Usage: uv run --with anthropic python run_heldout.py [--workers 10] [--cap 5.00]
Replies are saved per prompt (results/heldout/<task>/<arm>/<sha>.json) as they arrive; reruns resume.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import pathlib
import threading
import time

import anthropic

import pilot
import rule_gate

HERE = pathlib.Path(__file__).resolve().parent
TASK_IDS = ["eor-review-corrections", "landscape-sheet-revision"]
HEAD = "You are deciding one item of an engineering task.\n\n"


def prompts(task: str):
    d = HERE / "heldout" / task
    ins, packet = (d / "instruction.txt").read_text(), (d / "packet.txt").read_text()
    tests = json.loads((d / "tests.json").read_text())
    forbidden = [o.get("truth_closure", "") for o in json.loads((d / "oracle.json").read_text())["decisions"].values()]
    prefix = f"{HEAD}## Task instruction\n{ins}\n## Project evidence\n{packet}"
    for dec in tests["decisions"]:
        for arm in ("baseline", "gate"):
            suffix = (f"\n## Decision\n{dec['question']}\n\n## Answer format\n{tests['answer_format']}\n" if arm == "baseline"
                      else f"\n## Decision\n{dec['question']}\n\n{rule_gate.protocol(ins, tests['gate_fields'])}\n")
            assert not any(f and f in suffix for f in ("INCORPORATED", "VOID", "NON_CONCUR", "CLOSED_ELEVATED")), "leak"
            yield task, arm, dec["id"], prefix, suffix


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--cap", type=float, default=5.00)
    a = ap.parse_args()
    root = HERE / "results/heldout"
    spent = sum(json.loads(p.read_text())["cost_usd"] for p in root.rglob("*.json")) if root.exists() else 0.0
    client = anthropic.Anthropic(api_key=pilot.api_key(), max_retries=4, timeout=900)
    lock = threading.Lock()
    st = {"spent": spent, "reserved": 0.0, "skipped": 0}
    print(f"already spent ${spent:.3f}; cap ${a.cap:.2f}", flush=True)

    def call(job, warm: bool):
        task, arm, did, prefix, suffix = job
        out = root / task / arm
        sha = hashlib.sha256((prefix + suffix).encode()).hexdigest()
        if (out / f"{sha}.json").exists():
            return None
        est_prefix = len(prefix) / 3.0
        reserve = (est_prefix * (pilot.PRICE["cache_write"] if warm else pilot.PRICE["cache_read"])
                   + len(suffix) / 3.0 * pilot.PRICE["in"] + pilot.MAX_TOKENS * pilot.PRICE["out"]) / 1e6
        with lock:
            if st["spent"] + st["reserved"] + reserve > a.cap:
                st["skipped"] += 1
                return None
            st["reserved"] += reserve
        t0 = time.time()
        try:
            r = client.messages.create(
                model=pilot.MODEL, max_tokens=pilot.MAX_TOKENS, output_config={"effort": pilot.EFFORT},
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": suffix}]}])
        finally:
            with lock:
                st["reserved"] -= reserve
        usage = r.usage.to_dict()
        rec = {"task": task, "arm": arm, "decision": did, "prompt_sha256": sha, "model": r.model, "stop_reason": r.stop_reason,
               "text": "".join(b.text for b in r.content if b.type == "text"), "usage": usage, "cost_usd": pilot.cost(usage),
               "latency_s": round(time.time() - t0, 1), "request_id": r._request_id, "effort": pilot.EFFORT}
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{sha}.tmp").write_text(json.dumps(rec, indent=1))
        (out / f"{sha}.tmp").replace(out / f"{sha}.json")
        with lock:
            st["spent"] += rec["cost_usd"]
            print(f"{task[:9]} {arm:8} {did:10} {rec['latency_s']:5.1f}s out={usage['output_tokens']:5} "
                  f"cr={usage.get('cache_read_input_tokens')} cw={usage.get('cache_creation_input_tokens')} "
                  f"${rec['cost_usd']:.3f} total ${st['spent']:.3f}", flush=True)
        return rec

    t_start = time.time()
    for task in TASK_IDS:
        jobs = list(prompts(task))
        call(jobs[0], warm=True)  # writes the shared evidence prefix to the cache
        with cf.ThreadPoolExecutor(max_workers=a.workers) as pool:
            for f in cf.as_completed([pool.submit(call, j, False) for j in jobs[1:]]):
                try:
                    f.result()
                except Exception as exc:
                    print(f"call failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
    print(f"\nwall time {time.time() - t_start:.0f}s; spent ${st['spent']:.3f}; skipped for cap {st['skipped']}", flush=True)


if __name__ == "__main__":
    main()
