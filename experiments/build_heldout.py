"""Builds held-out decision sets from two unseen PLE dev tasks (official dataset, external/hf-tasks).

Published form: identical to the script that built the held-out sets on 2026-11-22, except that the nine
landscape decisions (question and accepted-answer patterns) are read from a private file instead of being
written here. With that file and the gated dataset it rebuilds the files whose hashes are in
heldout/*/manifest.json.

Writes heldout/<task>/{instruction.txt, packet.txt, tests.json, oracle.json, manifest.json}.
Evidence is chosen from what each task's own instruction points to, identically for both arms.
References come from the task's grading data and stay in oracle.json (never in a prompt).
"""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import re

import readers

HERE = pathlib.Path(__file__).resolve().parent
TASKS = HERE / "external/hf-tasks/tasks"
OUT = HERE / "heldout"
VALUE_FMT = ('Answer this one decision only. Reply with one JSON object and nothing else:\n'
             '{"value": <the answer, in the form the decision asks for>, "confidence": <0.0 to 1.0>, '
             '"evidence": "<the decisive evidence, quoted verbatim, at most 30 words>"}')
GATE_FIELDS = 'Decision objects have the form {"value": <the answer, in the form the decision asks for>}.'


def text_of(p: pathlib.Path) -> str:
    if p.suffix == ".pdf":
        return readers.pdf_text(str(p))
    if p.suffix == ".docx":
        return readers.docx_text(str(p))
    if p.suffix == ".xlsx":
        return "\n".join(f"[{sh}] " + " | ".join(v for _, v in row) for sh, rows in readers.xlsx_rows(str(p)).items() for row in rows)
    return p.read_text(errors="replace")


def section(name: str, body: str) -> str:
    return f"=== FILE: {name} ===\n{body.strip()}"


def annotations(drawings: pathlib.Path) -> str:
    rows = ["sheet_file | markup_ref | subtype | author | colour_rgb | created | modified | contents"]
    for p in sorted(drawings.rglob("*.pdf")):
        data = p.read_bytes()
        for chunk in data.split(b"endobj"):
            if b"/Type/Annot" not in chunk.replace(b" /", b"/"):
                continue
            def g(key, pat=rb"\((.*?)\)"):
                m = re.search(rb"/" + key + pat, chunk, re.S)
                return m.group(1).decode("latin-1").strip() if m else ""
            rows.append(" | ".join([str(p.relative_to(drawings)), g(b"NM"), g(b"Subtype", rb"/(\w+)"), g(b"T"), g(b"C", rb"\[(.*?)\]"),
                                    g(b"CreationDate"), g(b"M"), g(b"Contents")]))
    return "\n".join(rows)


def write(task: str, instruction: str, packet: str, decisions: list, oracle: dict, sources: list[pathlib.Path]):
    d = OUT / task
    d.mkdir(parents=True, exist_ok=True)
    (d / "instruction.txt").write_text(instruction.strip() + "\n")
    (d / "packet.txt").write_text(packet)
    (d / "tests.json").write_text(json.dumps({"task": task, "answer_format": VALUE_FMT, "gate_fields": GATE_FIELDS,
                                              "decisions": decisions}, indent=1, ensure_ascii=False))
    (d / "oracle.json").write_text(json.dumps(oracle, indent=1, ensure_ascii=False))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    (d / "manifest.json").write_text(json.dumps({
        "sources": {str(p.relative_to(TASKS)): sha(p) for p in sources},
        "outputs": {n: sha(d / n) for n in ("instruction.txt", "packet.txt", "tests.json", "oracle.json")}}, indent=1))
    print(f"{task}: {len(decisions)} decisions, packet {len(packet):,} chars, instruction {len(instruction):,} chars")


def eor() -> None:
    t = TASKS / "eor-review-corrections"
    root = t / "environment/filesystem/dc_structural__eor_revision__blind"
    proj = root / "project"
    brief = (root / "task.md").read_text()
    files = sorted((proj / "review").rglob("*.*")) + [proj / "standards" / n for n in (
        "er_1165_2_217_review_policy.pdf", "er_415_1_11_bcoes_reviews.pdf", "er_1110_1_8159_drchecks.pdf")]
    parts = [section(str(p.relative_to(root)), text_of(p)) for p in files]
    parts.insert(len(parts) - 3, section("project/drawings (annotation objects on every sheet PDF)", annotations(proj / "drawings")))
    truth = json.loads((t / "tests/grading/data/truth.json").read_text())
    comments = truth["packages"]["BP-2"]["branches"]["AMENDMENT"]["comments"]
    decisions, oracle = [], {"decisions": {}}
    for cid, c in comments.items():
        decisions.append({"id": cid, "question": (
            f"Review comment {cid} (markup {c['markup_ref']}): in this issue, is the comment incorporated as a change "
            f'to the drawings? Give value "incorporate" or "do_not_incorporate".')})
        oracle["decisions"][cid] = {"accept": [r"^incorporate"] if c["closure"] == "INCORPORATED" else [r"^do.?not.?incorporate", r"^flag"],
                                    "truth_closure": c["closure"], "void_reason": c.get("void_reason", "")}
    write("eor-review-corrections", brief, "\n\n".join(parts) + "\n", decisions, oracle,
          [root / "task.md", *files, *sorted((proj / "drawings").rglob("*.pdf")), t / "tests/grading/data/truth.json"])


def landscape() -> None:
    t = TASKS / "landscape-sheet-revision"
    fs = t / "environment/filesystem"
    instruction = (t / "instruction.md").read_text()
    files = [p for d in ("Correspondence", "Design-Drafting/Landscape", "Reference", "Meeting Notes")
             for p in sorted((fs / d).rglob("*")) if p.is_file()]
    packet = "\n\n".join(section(str(p.relative_to(fs)), text_of(p)) for p in files) + "\n"
    crit = {c["index"]: c["criteria"] for c in json.loads((t / "tests/grader/criteria.json").read_text())["criteria"]}
    # (rubric index, question, accepted answer patterns) for the nine decisions. Read from a private file: the questions
    # follow the wording of the task's rubric and the patterns are its answers.
    spec = [tuple(x) for x in json.loads((HERE / "heldout_spec.private.json").read_text())]
    decisions = [{"id": f"L{i}-c{idx}", "question": q} for i, (idx, q, _) in enumerate(spec, 1)]
    oracle = {"decisions": {f"L{i}-c{idx}": {"accept": acc, "rubric": crit[idx]} for i, (idx, _, acc) in enumerate(spec, 1)}}
    write("landscape-sheet-revision", instruction, packet, decisions, oracle,
          [t / "instruction.md", *files, t / "tests/grader/criteria.json"])


if __name__ == "__main__":
    eor()
    landscape()
