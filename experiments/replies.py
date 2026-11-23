"""Loads saved model replies in either form, so the scorers run with or without the gated task files.

private  the raw record written by pilot.py / run_heldout.py: {"text": <full reply>, ...}
public   the redacted record in this repository: {"redacted": true, "answer": {...}, ...}

A redacted record keeps every structured field the scorers read. Free text that quotes the gated task
files is removed. The frozen gate's two verbatim-quote checks cannot be recomputed without that text, so
their recorded results are replayed: each quote is replaced by a sentinel that rule_gate.verbatim()
accepts or rejects exactly as it accepted or rejected the real quote. rule_gate.py runs unmodified.

Set PLE_PRIVATE=<directory with the original evidence-control files> to score the raw records instead.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

import harness

PUBLIC = pathlib.Path(__file__).resolve().parent
PASSED, FAILED = "verbatim-check-passed", "verbatim-check-failed"
FLAG_VALUE = "flag: change not justified"


def root() -> pathlib.Path:
    return pathlib.Path(os.environ["PLE_PRIVATE"]).expanduser().resolve() if os.environ.get("PLE_PRIVATE") else PUBLIC


def records(directory: pathlib.Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def answer(rec: dict) -> dict | None:
    """The reply as the scorers see it. Redacted gate replies get sentinel quotes."""
    if not rec.get("redacted"):
        return harness.parse_answer(rec["text"])
    ans = rec["answer"]
    if ans is None or "governing_rules" not in ans:
        return ans
    out = dict(ans)
    out["governing_rules"] = [{"id": r["id"], "quote": PASSED if r["verbatim"] else FAILED} for r in ans["governing_rules"]]
    out["evidence"] = [PASSED if ok else FAILED for ok in ans["evidence_verbatim"]]
    return out


def sources(rec: dict, instruction: str | None, evidence: str | None) -> tuple[str, str]:
    """Texts the gate checks quotes against: the real ones for raw records, the sentinel for redacted ones."""
    return (PASSED, PASSED) if rec.get("redacted") else (instruction, evidence)


def evidence_texts(directory: pathlib.Path) -> tuple[str | None, str | None]:
    ins, packet = directory / "instruction.txt", directory / "packet.txt"
    return (ins.read_text(), packet.read_text()) if ins.exists() and packet.exists() else (None, None)


def tests() -> dict:
    """Decision definitions: the full file when present, else the public one (no evidence-operation anchors)."""
    full = root() / "cases/tests.json"
    return json.loads((full if full.exists() else PUBLIC / "cases/tests.public.json").read_text())


def value(ans) -> str:
    return re.sub(r"\s+", " ", str((ans or {}).get("value", ""))).strip().lower()


def heldout_verdict(rec: dict, which: str, val: str, accept: list[str] | None) -> bool:
    """Is this candidate answer correct? From the private reference if present, else from the published verdict."""
    if accept is not None:
        return any(re.search(p, val) for p in accept)
    return bool(rec["verdicts"][which])
