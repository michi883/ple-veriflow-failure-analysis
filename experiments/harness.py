"""Evidence-dependence tests for single agent decisions. Model-independent, stdlib only.

Each decision gets three runs of the same prompt shape:
  baseline     original evidence                  -> the responder's own decision
  invariance   presentation-only change           -> decision must stay the same as baseline
  sensitivity  decisive evidence change           -> decision must move as the instruction's rules require

The responder sees only: task instruction + evidence (after ops) + the decision question + answer format.
Expected answers (cases/oracle.json) are used for scoring only.

Usage:
  python3 harness.py --dry-run                                  # build + check every prompt, no responder
  python3 harness.py --responder mock:storage-prior --decision W3-2.44
  python3 harness.py --responder 'cmd:<command reading the prompt on stdin, printing the reply>' --repeat 3
"""

from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import pathlib
import re
import subprocess
import time

HERE = pathlib.Path(__file__).resolve().parent
TESTS = HERE / "cases/tests.json"
ORACLE = HERE / "cases/oracle.json"
EVIDENCE = HERE / "evidence"
RESULTS = HERE / "results"


# ---------- evidence ----------

def load_evidence() -> tuple[str, str]:
    manifest = json.loads((EVIDENCE / "manifest.json").read_text())["outputs"]
    out = []
    for name in ("instruction.txt", "packet.txt"):
        data = (EVIDENCE / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest[name]:
            raise SystemExit(f"{name} differs from evidence/manifest.json: rebuild with build_evidence.py")
        out.append(data.decode())
    return out[0], out[1]


def _span(text: str, marks: list[str]) -> tuple[int, int]:
    start, end = marks
    if text.count(start) != 1:
        raise ValueError(f"span start occurs {text.count(start)} times: {start!r}")
    s = text.index(start)
    e = text.find(end, s + len(start))
    if e < 0:
        raise ValueError(f"span end not found after start: {end!r}")
    return s, e


def apply_ops(text: str, ops: list[dict]) -> str:
    for op in ops:
        if op["op"] == "replace":
            n = text.count(op["old"])
            if n != op.get("count", 1):
                raise ValueError(f"replace expected {op.get('count', 1)} match(es), found {n}: {op['old']!r}")
            text = text.replace(op["old"], op["new"])
        elif op["op"] == "swap":
            (a0, a1), (b0, b1) = sorted([_span(text, op["a"]), _span(text, op["b"])])
            if a1 > b0:
                raise ValueError("swap spans overlap")
            text = text[:a0] + text[b0:b1] + text[a1:b0] + text[a0:a1] + text[b1:]
        elif op["op"] == "regex":
            s, e = _span(text, op["within"]) if "within" in op else (0, len(text))
            new, n = re.subn(op["pattern"], op["repl"], text[s:e])
            if n != op["count"]:
                raise ValueError(f"regex expected {op['count']} match(es), found {n}: {op['pattern']!r}")
            text = text[:s] + new + text[e:]
        else:
            raise ValueError(f"unknown op {op['op']!r}")
    return text


def content_tokens(text: str) -> collections.Counter:
    """Words and numeric values, ignoring order and number formatting."""
    toks = collections.Counter()
    for t in re.findall(r"\d[\d,]*(?:\.\d+)?|[^\W\d_]+", text):
        toks[repr(float(t.replace(",", ""))) if t[0].isdigit() else t.lower()] += 1
    return toks


def evidence_diff(a: str, b: str, limit: int = 40) -> str:
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), "original", "modified", n=0, lineterm=""))
    return "\n".join(lines[:limit] + ([f"... {len(lines) - limit} more diff lines"] if len(lines) > limit else []))


# ---------- prompt / answer ----------

def build_prompt(instruction: str, evidence: str, question: str, answer_format: str) -> str:
    return (
        "You are deciding one line of an engineering cost reconciliation.\n\n"
        f"## Task instruction\n{instruction}\n\n## Project evidence\n{evidence}\n\n"
        f"## Decision\n{question}\n\n## Answer format\n{answer_format}\n"
    )


def parse_answer(raw: str) -> dict | None:
    start = raw.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            depth += {"{": 1, "}": -1}.get(raw[i], 0)
            if depth == 0:
                try:
                    obj = json.loads(raw[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    break
        start = raw.find("{", start + 1)
    return None


def _item(v) -> str | None:
    v = (str(v).strip() if v is not None else "")
    return None if v.lower() in ("", "null", "none") else v


def decision_key(ans: dict | None, compare: str, item: str | None = None):
    if ans is None:
        return None
    try:
        if compare == "quantity":
            other = _item(ans.get("schedule_item"))
            if item and other not in (None, item):
                return 0.0  # quantity carried under a different item: none under the item asked about
            return round(float(ans.get("quantity")), 2)
        return (str(ans.get("treatment")), _item(ans.get("schedule_item")) if ans.get("treatment") == "carry" else None)
    except (TypeError, ValueError):
        return None


def matches(spec: dict, key, compare: str) -> bool:
    if key is None:
        return False
    if compare == "quantity":
        return abs(key - spec["quantity"]) < 0.005
    treatments = spec["treatment"] if isinstance(spec["treatment"], list) else [spec["treatment"]]
    return key[0] in treatments and (spec.get("schedule_item") is None or key[1] == spec["schedule_item"])


def self_consistent(ans: dict | None) -> str:
    """The conventional check VeriFlow ran: does the answer's own arithmetic hold?"""
    try:
        q, p, a = float(ans["quantity"]), float(ans["unit_price"]), float(ans["amount"])
    except (TypeError, ValueError, KeyError):
        return "n/a"
    return "pass" if abs(q * p - a) <= 0.51 else "fail"


# ---------- responders ----------

def make_responder(spec: str):
    kind, _, arg = spec.partition(":")
    if kind == "mock":
        import mocks
        return mocks.MOCKS[arg]
    if kind == "cache":  # replay replies saved by pilot.py, keyed by prompt sha256
        def from_cache(prompt: str) -> str:
            path = HERE / arg / f"{hashlib.sha256(prompt.encode()).hexdigest()}.json"
            return json.loads(path.read_text())["text"]
        return from_cache
    if kind == "cmd":
        def run_cmd(prompt: str) -> str:
            p = subprocess.run(arg, shell=True, input=prompt, capture_output=True, text=True, timeout=900)
            if p.returncode != 0:
                raise RuntimeError(f"responder exited {p.returncode}: {p.stderr[-500:]}")
            return p.stdout
        return run_cmd
    raise SystemExit("responder must be mock:<name> or cmd:<command>")


# ---------- run ----------

def cases(tests: dict, instruction: str, packet: str, forbidden: list[str], only: str | None):
    for d in tests["decisions"]:
        if only and d["id"] != only:
            continue
        for test in ("baseline", "invariance", "sensitivity"):
            spec = d.get(test, {"description": "original evidence", "ops": []})
            evidence = apply_ops(packet, spec["ops"])
            same = content_tokens(evidence) == content_tokens(packet)
            if test == "invariance" and not same:
                raise ValueError(f"{d['id']} invariance ops change content, not just presentation")
            if test == "sensitivity" and same:
                raise ValueError(f"{d['id']} sensitivity ops change no content")
            prompt = build_prompt(instruction, evidence, d["question"], tests["answer_format"])
            leaked = [s for s in forbidden if s in prompt]
            if leaked:
                raise ValueError(f"{d['id']}/{test} prompt contains grading-only text: {leaked}")
            yield d, test, spec, evidence, prompt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--responder", help="mock:<name> or cmd:<command>")
    ap.add_argument("--decision", help="run one decision id only")
    ap.add_argument("--repeat", type=int, default=1, help="samples per test; the majority answer is scored")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tests = json.loads(TESTS.read_text())
    instruction, packet = load_evidence()
    oracle = json.loads(ORACLE.read_text())  # scoring only
    built = list(cases(tests, instruction, packet, oracle["forbidden_in_prompt"], a.decision))

    if a.dry_run or not a.responder:
        total = sum(len(p) for *_, p in built)
        for d, test, spec, evidence, prompt in built:
            print(f"{d['id']:9} {test:11} {len(prompt):>7,} chars  ops={len(spec['ops'])}  {spec['description']}")
        print(f"{len(built)} prompts, {total:,} chars; every op applied, invariance changes no content, "
              f"sensitivity changes content, no grading-only text in any prompt")
        return

    respond = make_responder(a.responder)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^\w.-]+", "_", a.responder)[:40]
    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{run_id}.jsonl"
    baseline_keys: dict[str, object] = {}
    summary: dict[str, dict] = collections.defaultdict(dict)

    with out_path.open("w") as out:
        for d, test, spec, evidence, prompt in built:
            o = oracle["decisions"][d["id"]]
            samples = []
            for _ in range(a.repeat):
                raw = respond(prompt)
                ans = parse_answer(raw)
                samples.append({"raw": raw, "answer": ans, "key": decision_key(ans, d["compare"], d["id"].split("-", 1)[1])})
            key = collections.Counter(json.dumps(s["key"]) for s in samples).most_common(1)[0][0]
            key = json.loads(key)
            key = tuple(key) if isinstance(key, list) else key
            ans = next(s["answer"] for s in samples if json.dumps(s["key"]) == json.dumps(list(key) if isinstance(key, tuple) else key))

            if test == "baseline":
                baseline_keys[d["id"]] = key
                expected = {"reference_answer": o["reference"]}
                passed = matches(o["reference"], key, d["compare"])  # here: is the baseline decision correct?
            elif test == "invariance":
                expected = {"same_as_baseline": baseline_keys[d["id"]]}
                passed = key is not None and key == baseline_keys[d["id"]]
            elif spec.get("criterion") == "delta":
                base = baseline_keys[d["id"]]
                expected = {"change_from_baseline": o["sensitivity"]["delta"]}
                passed = key is not None and base is not None and abs((key - base) - o["sensitivity"]["delta"]) < 0.005
            else:
                expected = {"rule_required_answer": o["sensitivity"]}
                passed = matches(o["sensitivity"], key, d["compare"])

            row = {
                "run_id": run_id, "responder": a.responder, "decision": d["id"], "test": test,
                "original_decision_veriflow": o["veriflow"],
                "evidence_change": {"description": spec["description"], "ops": spec["ops"],
                                    "diff": evidence_diff(packet, evidence),
                                    "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest()},
                "expected": expected, "actual": {"decision": key, "answer": ans},
                "passed": passed, "self_consistency_check": self_consistent(ans),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "prompt_chars": len(prompt),
                "samples": samples,
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            summary[d["id"]][test] = (key, passed, row["self_consistency_check"],
                                      (ans or {}).get("confidence"))

    print(f"results: {out_path.relative_to(HERE)}\n")
    print(f"{'decision':9} {'baseline (correct?)':24} {'conf':>4} {'self-check':10} {'invariance':11} {'sensitivity':11} flagged")
    for did, s in summary.items():
        (bk, bc, sc, conf), (_, ip, _, _), (sk, sp, _, _) = s["baseline"], s["invariance"], s["sensitivity"]
        flagged = not (ip and sp)
        print(f"{did:9} {str(bk) + (' ok' if bc else ' WRONG'):24} {conf if conf is not None else '-':>4} {sc:10} "
              f"{'pass' if ip else 'FAIL':11} {('pass' if sp else 'FAIL') + f' ({sk})':11} {'YES' if flagged else 'no'}")


if __name__ == "__main__":
    main()
