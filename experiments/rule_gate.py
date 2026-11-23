"""Rule-aware change gate. Task-independent: it reads only the task instruction, the evidence and the reply.

1. ledger(instruction)   numbers each instruction sentence (R1..Rn); no model call.
2. protocol(instruction) asks for a justified decision: original value, governing rule quotes,
                         evidence quotes, link type, keep/flag rules and scope rules.
3. apply(reply, ...)     deterministic gate: a change survives only if its quotes are verbatim,
                         its link is direct, no keep/flag rule applies and it stays in scope;
                         otherwise the original is kept, or the item is flagged if there was none.
"""

from __future__ import annotations

import re

PLAIN = ("Before changing anything from what the existing document carries, identify the governing instruction "
         "and the supporting evidence. If you cannot justify the change, keep the original value, or flag the item "
         "if there is no original.")


def ledger(instruction: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", instruction.strip()) if s.strip()]


ESTIMATE_FIELDS = ('Decision objects use the fields treatment ("carry" | "flag_no_schedule_match" | "exclude"), schedule_item, '
                   'quantity (what the reconciled subtotal carries for the item asked about; 0 if none), unit, unit_price, amount.')


def protocol(instruction: str, fields: str = ESTIMATE_FIELDS) -> str:
    rules = "\n".join(f"R{i}: {s}" for i, s in enumerate(ledger(instruction), 1))
    return f"""## Change-justification protocol
Rule ledger (the task instruction, one sentence per line):
{rules}

Before proposing any value that differs from what the existing document already carries for this decision (including adding something it does not carry):
1. State the original: what the existing document carries for this decision, or "absent".
2. Quote, verbatim from the ledger, the rule(s) that require the change.
3. Quote the decisive evidence verbatim from the project evidence.
4. Classify the link from evidence to the new value:
   "direct": the evidence states this item's own value, or it follows by counting, arithmetic or unit conversion of the same measurement of the same thing;
   "inferred": it uses a different quantity as a proxy, substitutes another item or category, chooses between conflicting sources, or rests on an assumption the evidence does not state.
5. List every ledger rule that would keep the original or require a flag instead, and whether its condition is met.
6. List every ledger rule that limits scope, and whether everything the change relies on (including any item or price used) is inside it.
A change is justified only if the link is direct, no keep/flag rule's condition is met, and it stays in scope. Otherwise keep the original, or flag the item if there is no original.

Reply with one JSON object and nothing else. {fields}
{{"original": <decision object or "absent">,
 "proposed": <decision object you would make without this protocol's limits>,
 "changes_original": true | false,
 "governing_rules": [{{"id": "R#", "quote": "..."}}],
 "evidence": ["verbatim quote", ...],
 "link": "direct" | "inferred",
 "keep_or_flag_rules": [{{"id": "R#", "quote": "...", "condition_met": true | false}}],
 "scope_rules": [{{"id": "R#", "quote": "...", "in_scope": true | false}}],
 "final": <decision object after applying this protocol>,
 "confidence": <0.0 to 1.0>}}"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def verbatim(quote: str, source: str) -> bool:
    """Every fragment of the quote (split at ellipses) of 8+ chars must appear in the source."""
    src = _norm(source)
    parts = [p.strip(" \"'") for p in re.split(r"\.\.\.|…", _norm(quote))]
    parts = [p for p in parts if len(p) >= 8]
    return bool(parts) and all(p in src for p in parts)


def _changed(original, proposed: dict) -> bool:
    """Decided from the values, not from the model's own changes_original flag (it proved unreliable)."""
    def q(d):
        try:
            return round(float(d.get("quantity") or 0), 2)
        except (TypeError, ValueError):
            return None
    if "value" in proposed:  # generic decisions: {"value": ...}
        new = _norm(proposed.get("value") or "")
        return bool(new) if not isinstance(original, dict) else _norm(original.get("value") or "") != new
    if not isinstance(original, dict):
        return proposed.get("treatment") == "carry" and (q(proposed) or 0) > 0
    item = lambda d: _norm(d.get("schedule_item") or "")  # noqa: E731
    return (original.get("treatment"), item(original), q(original)) != (proposed.get("treatment"), item(proposed), q(proposed))


def apply(reply: dict | None, instruction: str, evidence: str, require_direct: bool | None = None) -> tuple[dict | None, str]:
    """Returns (decision, reason).

    Frozen v2 (require_direct=None): an inferred link blocks only changes to a value the document already
    carries; an addition is flagged only when a keep/flag rule applies or it is out of scope.
    """
    if not isinstance(reply, dict) or not isinstance(reply.get("proposed"), dict):
        return None, "unparseable"
    proposed, original = reply["proposed"], reply.get("original")
    if require_direct is None:
        require_direct = isinstance(original, dict)
    if not _changed(original, proposed):
        return proposed, "no change"
    problems = []
    if not any(verbatim(r.get("quote", ""), instruction) for r in reply.get("governing_rules") or []):
        problems.append("no verbatim governing rule")
    if not any(verbatim(q, evidence) for q in reply.get("evidence") or []):
        problems.append("no verbatim evidence")
    if require_direct and reply.get("link") != "direct":
        problems.append("inferred link")
    if any(r.get("condition_met") for r in reply.get("keep_or_flag_rules") or []):
        problems.append("keep/flag rule applies")
    if any(r.get("in_scope") is False for r in reply.get("scope_rules") or []):
        problems.append("out of scope")
    if not problems:
        return proposed, "justified change"
    if isinstance(original, dict):
        return original, "kept original: " + "; ".join(problems)
    if "value" in proposed:
        return {"value": "flag: change not justified"}, "flagged: " + "; ".join(problems)
    return {"treatment": "flag_no_schedule_match", "schedule_item": None, "quantity": 0}, "flagged: " + "; ".join(problems)
