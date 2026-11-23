"""Deterministic mock responders for decision W3-2.44 (detention basin excavation).

They read only the prompt, like a real model would, and exist to show the test mechanism:
  evidence        uses an excavation quantity if the plans give one, else the quantity already carried
  storage-prior   VeriFlow's actual reasoning: convert the 6.4 ac-ft storage to CY, whatever else the plans say
  brittle         like 'evidence', but reads the carried quantity with a parser that needs thousands separators
"""

from __future__ import annotations

import json
import math
import re


def _section(prompt: str, title: str) -> str:
    i = prompt.index(f"=== FILE: {title}")
    j = prompt.find("=== FILE:", i + 1)
    return prompt[i:j if j > 0 else len(prompt)]


def _row_244(prompt: str) -> str:
    est = _section(prompt, "Cost Estimates/DSR_Ph3_Engineers_Estimate.xlsx")
    return next(line for line in est.splitlines() if line.startswith("2.44 |"))


def _reply(qty: float, evidence: str, confidence: float) -> str:
    return json.dumps({"treatment": "carry", "schedule_item": "2.44", "quantity": qty, "unit": "CY",
                       "unit_price": 9.75, "amount": round(qty * 9.75, 2), "confidence": confidence,
                       "evidence": evidence})


def evidence_controlled(prompt: str) -> str:
    plans = _section(prompt, "Design-Drafting/Phase 3/DSR_Ph3_Improvement_Plans_C1-C9.pdf")
    m = re.search(r"[Bb]asin excavation[^\n]*?(\d[\d,]*)\s*CY", plans)
    if m:
        return _reply(float(m.group(1).replace(",", "")), m.group(0), 0.9)
    carried = _row_244(prompt).split(" | ")[2]
    return _reply(float(carried.replace(",", "")), "plans give no excavation dimension; quantity carried stands", 0.8)


def storage_prior(prompt: str) -> str:
    plans = _section(prompt, "Design-Drafting/Phase 3/DSR_Ph3_Improvement_Plans_C1-C9.pdf")
    acft = float(re.search(r"allocation of ([\d.]+) acre-feet", plans).group(1))
    return _reply(math.ceil(acft * 43560 / 27), f"{acft} acre-feet between basin bottom and 100-year water surface", 0.97)


def brittle(prompt: str) -> str:
    plans = _section(prompt, "Design-Drafting/Phase 3/DSR_Ph3_Improvement_Plans_C1-C9.pdf")
    m = re.search(r"[Bb]asin excavation[^\n]*?(\d[\d,]*)\s*CY", plans)
    if m:
        return _reply(float(m.group(1).replace(",", "")), m.group(0), 0.9)
    row = _row_244(prompt)
    m = re.search(r"\| (\d{1,3}(?:,\d{3})+) \| CY", row) or re.search(r"(\d+(?:\.\d+)?) ac-ft", row)
    return _reply(float(m.group(1).replace(",", "")), row, 0.8)


MOCKS = {"evidence": evidence_controlled, "storage-prior": storage_prior, "brittle": brittle}
