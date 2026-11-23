"""Builds the frozen evidence packet and VeriFlow's original decisions from existing files.

Reads only (never writes) the original task files and VeriFlow's memo. Writes:
  evidence/packet.txt       the evidence every test starts from (instruction is kept separately)
  evidence/instruction.txt  the task instruction, verbatim
  evidence/manifest.json    sha256 of every source file and output, so drift is detectable
  evidence/veriflow_decisions.json  VeriFlow's memo rows, with an arithmetic re-check

Usage: python3 build_evidence.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import readers

HERE = pathlib.Path(__file__).resolve().parent
PLE = HERE.parent
TASK = PLE / "ple-kit/runs/veriflow-ple-starter-20261122-131754/tasks/engineers-estimate-check"
FILES = TASK / "environment/files"
SOURCES = {
    "instruction": TASK / "instruction.md",
    "estimate": FILES / "Cost Estimates/DSR_Ph3_Engineers_Estimate.xlsx",
    "schedule": FILES / "Cost Estimates/CC_Unit_Cost_Schedule.pdf",
    "plans": FILES / "Design-Drafting/Phase 3/DSR_Ph3_Improvement_Plans_C1-C9.pdf",
    "hgl": FILES / "Reports-Studies/DSR_Ph3_StormDrain_HGL.xlsx",
    "veriflow_memo": PLE / "veriflow-ple-starter/artifacts/engineers-estimate-check/files/filesystem/Correspondence/Client/Memo_to_Callaway_2026-10-31.docx",
}
OUT = HERE / "evidence"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def money(v: float) -> str:
    return f"${v:,.2f}"


def qty(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def estimate_section2(path: pathlib.Path) -> str:
    rows = readers.xlsx_rows(str(path))["Estimate"]
    cells = {c: v for row in rows for c, v in row}
    val = lambda c: cells.get(c, "").split(" -> ")[-1]  # cached value of a formula cell  # noqa: E731
    lines = [cells["A1"], cells["A2"], cells["A3"], "", cells["A18"], "Item | Description | Quantity | Unit | Unit Price | Extended Cost"]
    for r in range(20, 33):
        if f"A{r}" in cells:
            lines.append(" | ".join([
                cells[f"A{r}"], cells[f"B{r}"], qty(float(val(f"C{r}"))), cells[f"D{r}"],
                money(float(val(f"E{r}"))), money(float(val(f"F{r}"))),
            ]))
    lines.append(f"{cells['B33']} | | | | | {money(float(val('F33')))}")
    return "\n".join(lines)


def hgl_pipe_schedule(path: pathlib.Path) -> str:
    rows = readers.xlsx_rows(str(path))["Pipe Schedule"]
    return "\n".join(" | ".join(v for _, v in row) for row in rows)


def plan_sheets(path: pathlib.Path) -> str:
    text = readers.pdf_text(str(path))
    c6, c7, c8 = (text.index(f"SHEET C-{n} —") for n in (6, 7, 8))
    end = text.index("END OF PLAN SET")
    return text[c6:c7].rstrip() + "\n\n" + text[c8:end].rstrip()


def veriflow_rows(path: pathlib.Path) -> list[dict]:
    """Rows of the memo's line-by-line table, with qty x price re-derived (VeriFlow-style check)."""
    out = []
    for line in readers.docx_text(str(path)).splitlines():
        cells = [c.strip() for c in line.split(" | ")]
        if len(cells) == 9 and re.fullmatch(r"\d\.\d\d", cells[0]):
            num = lambda s: float(re.sub(r"[^\d.\-−]", "", s).replace("−", "-") or 0)  # noqa: E731
            carried, corrected, price, new_amt = num(cells[2]), num(cells[3]), num(cells[5]), num(cells[7])
            out.append({
                "item": cells[0], "description": cells[1], "carried_qty": carried, "corrected_qty": corrected,
                "unit": cells[4], "unit_price": price, "corrected_amount": new_amt, "change": cells[8],
                "arithmetic_check": "pass" if abs(corrected * price - new_amt) <= 0.5 else "fail",  # extension rounded to the dollar
            })
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    instruction = SOURCES["instruction"].read_text().strip()
    packet = "\n\n".join([
        "=== FILE: Cost Estimates/DSR_Ph3_Engineers_Estimate.xlsx (sheet Estimate, Section 2) ===\n" + estimate_section2(SOURCES["estimate"]),
        "=== FILE: Cost Estimates/CC_Unit_Cost_Schedule.pdf (full text) ===\n" + readers.pdf_text(str(SOURCES["schedule"])).strip(),
        "=== FILE: Design-Drafting/Phase 3/DSR_Ph3_Improvement_Plans_C1-C9.pdf (Sheets C-6, C-8, C-9) ===\n" + plan_sheets(SOURCES["plans"]),
        "=== FILE: Reports-Studies/DSR_Ph3_StormDrain_HGL.xlsx (sheet Pipe Schedule) ===\n" + hgl_pipe_schedule(SOURCES["hgl"]),
    ]) + "\n"
    (OUT / "instruction.txt").write_text(instruction + "\n")
    (OUT / "packet.txt").write_text(packet)
    rows = veriflow_rows(SOURCES["veriflow_memo"])
    (OUT / "veriflow_decisions.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    manifest = {
        "sources": {k: {"path": str(p.relative_to(PLE)), "sha256": sha(p)} for k, p in SOURCES.items()},
        "outputs": {n: sha(OUT / n) for n in ("instruction.txt", "packet.txt", "veriflow_decisions.json")},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"packet: {len(packet):,} chars; instruction: {len(instruction):,} chars; VeriFlow rows: {len(rows)} "
          f"(arithmetic check pass: {sum(r['arithmetic_check'] == 'pass' for r in rows)}/{len(rows)})")


if __name__ == "__main__":
    main()
