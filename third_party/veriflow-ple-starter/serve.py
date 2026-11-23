"""VeriFlow-PLE WebSocket harness entrypoint (`docs/protocol.md` v1).

Pairs `taskbridge.Session` + `taskbridge.MCPClient` with:
  1. Automatic discovery and full schema injection for Streamable HTTP MCP tools.
  2. Runtime `claude-opus-5-5` / `gemini-4-argon` parameter sanitization and prompt caching.
  3. Context compaction (`COMPACT_AT_TOKENS`) and bounded transient error retries.
  4. Planner -> Worker -> Cold-Read Verifier loop where empty verifier timeouts never count
     as `PASS` and post-fix mutations always trigger a fresh verification pass before `done`.
"""

from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import io
import json
import math
import operator
import os
import re
from typing import Any
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

try:
    import openai
except ImportError:  # Allows offline protocol testing with an injected client or local OSS proxy
    openai = None  # type: ignore[assignment]

import taskbridge

MODEL_API = os.environ.get("MODEL_API", "")
MODEL = (
    os.environ.get("MODEL_NAME")
    or (MODEL_API.split("/", 1)[1] if "/" in MODEL_API else MODEL_API)
    or "claude-opus-5-5"
)
MAX_EFFORT_MODELS = ("claude", "glm", "gpt-6", "deepseek-v4")
EFFORT = os.environ.get("REASONING_EFFORT") or (
    "omit"
    if MODEL_API.lower().startswith("openai-compatible/")
    else ("max" if any(m in MODEL.lower() for m in MAX_EFFORT_MODELS) else "high")
)
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "32000"))
CACHE_MARK = "claude" in MODEL.lower()
WRAP_UP_SEC = 240
PLAN_SHARE = 0.10
CHECK_SHARE = 0.25
MIN_STEP_SEC = 120
MAX_FIX_ROUNDS = 2
MAX_NUDGES = 3
RESULT_CHARS = 20_000
COMPACT_AT_TOKENS = 150_000
CUT_KEEP = 1_200
RETRY_WAITS = (3, 10, 25, 60)
CONTEXT_FULL = re.compile(
    r"context.?(length|window)|prompt is too long|maximum prompt length|exceed[^.]{0,40}(token|context)",
    re.I,
)

PLANNER_SYSTEM = (
    "You are the Lead Engineering Planner inside an offline Linux container (workdir {workdir}). "
    "Inspect all project drawings, schedules, addenda, RFIs, and any MCP tools, without creating or "
    "modifying files. Output an authoritative execution plan listing: (1) every required deliverable "
    "path and format, (2) governing document revisions (reconciling RFIs/Addenda that override preliminary "
    "NOAA ambient temperatures, soil thermal resistivity, or footing datums) and unit systems, (3) every "
    "hard safety or code feasibility gate (including cumulative leaf-to-root constraints and hard spatial "
    "clearances overriding soft prompt preferences), and (4) deterministic Python verification scripts to "
    "run before finishing."
)

WORKER_SYSTEM = (
    "You are the Responsible Physical Engineer working in {workdir}. Execute all calculations via "
    "deterministic Python scripts (never mental arithmetic), apply governing RFI ambient/soil derating "
    "and lug-capped cumulative constraints, use `math.ceil` for integer package/carton quantities, log "
    "key numeric deliverables with `verify_claims` (value, unit, expr, inputs, clauses, evidence), write "
    "deliverables to the exact required paths, and run `verify_deliverables` before finishing to confirm "
    "archive, spreadsheet, JSON, Python, and CAD/BIM structural integrity."
)

VERIFIER_SYSTEM = (
    "You are an independent Cold-Read Peer Reviewer in {workdir}. Do NOT create or modify any file. "
    "Re-open the written deliverables from disk, recompute hard safety/code ratios independently in "
    "Python (including RFI thermal derating, 2D duct-bank mutual heating, cumulative voltage drop, and "
    "3D code clearances), run `verify_claims` on any claim ledger and `verify_deliverables` on output "
    "files, check unit conversions, and verify that every required path exists and is non-empty. Reply "
    "with the single word PASS only if every hard gate holds; otherwise return a numbered defect list."
)

_CLAIM_KNOWN_UNITS = frozenset({
    "1", "", "dimensionless", "unitless", "ratio", "rad", "deg", "%",
    "m", "meter", "meters", "cm", "mm", "in", "inch", "inches", "ft", "foot", "feet", "yd",
    "m2", "cm2", "mm2", "in2", "ft2", "kcmil",
    "m3", "l", "liter", "in3", "ft3", "gal",
    "kg", "g", "lb", "lbm", "slug",
    "s", "sec", "min", "hr", "hour", "yr",
    "n", "kn", "lbf", "kip", "kips",
    "pa", "kpa", "mpa", "gpa", "psi", "ksi", "psf", "ksf", "bar",
    "j", "kj", "kwh", "mwh", "btu", "n*m", "kn*m", "lbf*ft", "lbf*in", "kip*ft", "kip*in",
    "w", "kw", "hp", "btu/hr", "kva", "mva",
    "a", "ma", "ka", "v", "kv", "mv", "ohm", "mohm", "hz",
    "m3/s", "l/s", "gpm", "cfs", "pa*s", "poise", "cp",
    "m/s", "ft/s", "fps", "c", "degc", "k", "k*m/w", "usd",
})
_CLAIM_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CLAIM_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_CLAIM_FUNCS: dict[str, Any] = {
    "abs": abs, "min": min, "max": max, "round": round, "sum": sum, "pow": pow,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "ceil": math.ceil, "floor": math.floor,
    "hypot": math.hypot,
}


def _eval_claim_ast(node: ast.AST, env: dict[str, float]) -> tuple[float, bool]:
    if isinstance(node, ast.Expression):
        return _eval_claim_ast(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"unsupported constant {node.value!r}")
        return float(node.value), False
    if isinstance(node, ast.Name):
        if node.id in env:
            return float(env[node.id]), True
        if node.id == "pi":
            return math.pi, False
        if node.id == "e":
            return math.e, False
        raise KeyError(f"undefined input {node.id!r}")
    if isinstance(node, ast.UnaryOp):
        fn = _CLAIM_UNARY_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"unsupported unary op {type(node.op).__name__}")
        val, computed = _eval_claim_ast(node.operand, env)
        return float(fn(val)), computed
    if isinstance(node, ast.BinOp):
        fn = _CLAIM_BIN_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"unsupported binary op {type(node.op).__name__}")
        lv, _ = _eval_claim_ast(node.left, env)
        rv, _ = _eval_claim_ast(node.right, env)
        return float(fn(lv, rv)), True
    if isinstance(node, ast.Call):
        if node.keywords:
            raise ValueError("keyword args disallowed in claim expr")
        if isinstance(node.func, ast.Name) and node.func.id in _CLAIM_FUNCS:
            fn = _CLAIM_FUNCS[node.func.id]
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "math"
            and node.func.attr in _CLAIM_FUNCS
        ):
            fn = _CLAIM_FUNCS[node.func.attr]
        else:
            raise ValueError("disallowed function call in claim expr")
        args = [_eval_claim_ast(a, env)[0] for a in node.args]
        return float(fn(*args)), True
    raise ValueError(f"disallowed AST node {type(node).__name__}")


def _verify_claims_list(claims: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(claims, list) or not claims:
        return {"claims": 0, "passed": 0, "failed": 1, "detail": [{"id": None, "errors": ["empty claim ledger"]}]}
    seen_ids: set[str] = set()
    failures: list[dict[str, Any]] = []
    for idx, c in enumerate(claims):
        cid = str(c.get("id") or c.get("claim_id") or f"claim-{idx + 1}")
        errs: list[str] = []
        if cid in seen_ids:
            errs.append(f"duplicate claim id {cid!r}")
        seen_ids.add(cid)
        unit = str(c.get("unit") or "").strip().lower().replace(" ", "")
        if unit and unit not in _CLAIM_KNOWN_UNITS:
            errs.append(f"unrecognized unit {c.get('unit')!r}")
        raw_val = c.get("value")
        val: float | None = None
        if raw_val is None or isinstance(raw_val, bool):
            errs.append("missing numeric value")
        else:
            try:
                val = float(raw_val)
                if not math.isfinite(val):
                    errs.append("non-finite value")
            except (TypeError, ValueError):
                errs.append(f"non-numeric value {raw_val!r}")
        lo = c.get("min") if "min" in c else c.get("min_value")
        hi = c.get("max") if "max" in c else c.get("max_value")
        if val is not None and math.isfinite(val):
            if lo is not None and val < float(lo):
                errs.append(f"value {val:.6g} below min {float(lo):.6g}")
            if hi is not None and val > float(hi):
                errs.append(f"value {val:.6g} above max {float(hi):.6g}")
        expr = str(c.get("expr") or "").strip()
        inputs = {str(k): float(v) for k, v in (c.get("inputs") or {}).items()}
        is_const = bool(c.get("is_input_constant", False))
        if not expr:
            errs.append("missing expr for re-derivation")
        else:
            try:
                tree = ast.parse(expr, mode="eval")
                rederived, has_comp = _eval_claim_ast(tree, inputs)
                if not math.isfinite(rederived):
                    errs.append("re-derived value is non-finite")
                elif not has_comp and not is_const:
                    errs.append("non-derived literal echo (provide inputs/expr or set is_input_constant=true)")
                elif val is not None and math.isfinite(val):
                    tol = c.get("tolerance")
                    tol_f = float(tol) if tol is not None else max(1e-9, abs(val) * 1e-6)
                    if abs(rederived - val) > tol_f:
                        errs.append(f"re-derived {rederived:.6g} != claimed {val:.6g} (tol {tol_f:.3g})")
            except Exception as exc:  # noqa: BLE001
                errs.append(f"expr error: {exc}")
        if not (c.get("clauses") or []):
            errs.append("no code clause cited")
        if not (c.get("evidence") or []):
            errs.append("no evidence handle")
        if errs:
            failures.append({"id": cid, "errors": errs})
    return {
        "claims": len(claims),
        "passed": len(claims) - len(failures),
        "failed": len(failures),
        "detail": failures[:8],
    }


_EXCEL_ERROR_TOKENS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")
_ABORTED_REPLY_EXACT = frozenset({"", "[tool call]", "..."})


def _is_aborted_reply(text: str | None) -> bool:
    stripped = (text or "").strip()
    return stripped in _ABORTED_REPLY_EXACT or stripped.endswith("...")


def _reject_nonfinite_json(tok: str) -> None:
    raise ValueError(f"non-finite JSON constant {tok!r}")


def _audit_deliverable_bytes(label: str, data: bytes, depth: int = 0) -> list[str]:
    defects: list[str] = []
    if len(data) == 0:
        defects.append(f"{label}: empty 0-byte deliverable")
        return defects
    inner_name = label.rsplit("!", 1)[-1]
    dot = inner_name.rfind(".")
    ext = inner_name[dot:].lower() if dot >= 0 else ""
    if ext == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                entries = [i for i in zf.infolist() if not i.is_dir()]
                if not entries:
                    defects.append(f"{label}: archive has zero files")
                    return defects
                bad = zf.testzip()
                if bad is not None:
                    defects.append(f"{label}: corrupt archive entry {bad}")
                    return defects
                if depth < 2:
                    total_read = 0
                    for info in entries[:128]:
                        if info.file_size > (16 << 20) or total_read + max(0, info.file_size) > (64 << 20):
                            continue
                        raw_inner = zf.read(info.filename)
                        total_read += len(raw_inner)
                        defects.extend(_audit_deliverable_bytes(f"{label}!{info.filename}", raw_inner, depth + 1))
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            defects.append(f"{label}: corrupt ZIP archive ({type(exc).__name__})")
    elif ext in (".xlsx", ".xlsm"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as xz:
                names = set(xz.namelist())
                if "xl/workbook.xml" not in names:
                    defects.append(f"{label}: missing xl/workbook.xml")
                    return defects
                for nm in sorted(names):
                    if not (nm.startswith("xl/worksheets/") and nm.endswith(".xml")):
                        continue
                    try:
                        root = ET.fromstring(xz.read(nm))
                    except ET.ParseError as exc:
                        defects.append(f"{label} ({nm}): malformed worksheet XML ({exc})")
                        return defects
                    for elem in root.iter():
                        tag_local = elem.tag.rsplit("}", 1)[-1]
                        if tag_local == "c" and elem.attrib.get("t") == "e":
                            defects.append(f"{label} ({nm}): Excel error cell (t='e')")
                            return defects
                        if tag_local == "v" and (elem.text or "").strip() in _EXCEL_ERROR_TOKENS:
                            defects.append(f"{label} ({nm}): formula error {(elem.text or '').strip()}")
                            return defects
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            defects.append(f"{label}: corrupt OOXML workbook ({type(exc).__name__})")
    elif ext == ".json":
        try:
            json.loads(data.decode("utf-8"), parse_constant=_reject_nonfinite_json)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            defects.append(f"{label}: invalid JSON ({exc})")
    elif ext == ".py":
        try:
            ast.parse(data.decode("utf-8"), filename=label)
        except (UnicodeDecodeError, SyntaxError) as exc:
            defects.append(f"{label}: invalid Python syntax ({exc})")
    elif ext == ".ifc":
        txt = data.decode("utf-8", errors="replace")
        s_pos = txt.find("ISO-10303-21;")
        e_pos = txt.find("END-ISO-10303-21;")
        if s_pos < 0 or e_pos < 0 or e_pos < s_pos:
            defects.append(f"{label}: incomplete IFC (missing ISO-10303-21/END-ISO-10303-21 wrapper)")
    elif ext == ".dxf":
        txt = data.decode("utf-8", errors="replace")
        if "SECTION" not in txt or "EOF" not in txt:
            defects.append(f"{label}: incomplete DXF (missing SECTION/EOF)")
    elif ext == ".csv":
        try:
            rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
            if not any(any(cell.strip() for cell in row) for row in rows):
                defects.append(f"{label}: CSV contains no non-empty rows")
            else:
                for row in rows:
                    tok = next((c.strip() for c in row if c.strip() in _EXCEL_ERROR_TOKENS), None)
                    if tok is not None:
                        defects.append(f"{label}: CSV contains spreadsheet error token {tok}")
                        break
        except (UnicodeDecodeError, csv.Error) as exc:
            defects.append(f"{label}: invalid CSV ({exc})")
    return defects


_DELIV_EXTS_FIND_ARGS = (
    "-name '*.zip' -o -name '*.xlsx' -o -name '*.xlsm' -o -name '*.json' "
    "-o -name '*.py' -o -name '*.ifc' -o -name '*.dxf' -o -name '*.csv'"
)


def _clip(text: str) -> str:
    if len(text) <= RESULT_CHARS:
        return text
    half = RESULT_CHARS // 2
    return f"{text[:half]}\n[... {len(text) - RESULT_CHARS} chars omitted ...]\n{text[-half:]}"


def _compact_older_tool_outputs(messages: list[dict[str, Any]], keep_recent: int = 6) -> int:
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    compacted = 0
    for idx in tool_indices[:-keep_recent]:
        content = messages[idx].get("content")
        if isinstance(content, str) and len(content) > 2 * CUT_KEEP + 200:
            messages[idx] = {
                **messages[idx],
                "content": (
                    f"{content[:CUT_KEEP]}\n"
                    f"[... compacted {len(content) - 2 * CUT_KEEP} chars ...]\n"
                    f"{content[-CUT_KEEP:]}"
                ),
            }
            compacted += 1
    return compacted


def _marked(messages: list[dict[str, Any]], *, cache_mark: bool | None = None) -> list[dict[str, Any]]:
    use_cache = CACHE_MARK if cache_mark is None else cache_mark
    if not use_cache or not messages:
        return messages
    out = list(messages)
    for idx in {0, len(out) - 1}:
        msg = out[idx]
        content = msg.get("content")
        if isinstance(content, str) and content:
            out[idx] = {
                **msg,
                "content": [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}],
            }
        else:
            out[idx] = {**msg, "cache_control": {"type": "ephemeral"}}
    return out


def _sanitize_kwargs(model_name: str, kwargs: dict[str, Any], *, model_api: str = "") -> dict[str, Any]:
    clean = dict(kwargs)
    low = model_name.lower()
    api_low = (model_api or os.environ.get("MODEL_API", MODEL_API)).lower()
    clean.pop("web_search_options", None)
    if "claude" in low:
        for bad_key in ("temperature", "top_p", "top_k"):
            clean.pop(bad_key, None)
        if clean.get("tool_choice") not in (None, "auto", "none"):
            clean["tool_choice"] = "auto"
    if "gemini-4-argon" in low:
        clean.pop("thinkingBudget", None)
        if clean.get("reasoning_effort") not in (None, "low", "medium", "high"):
            clean["reasoning_effort"] = "high"
    eff = str(clean.get("reasoning_effort") or "").strip().lower()
    if (
        api_low.startswith("openai-compatible/")
        or eff in ("", "none", "off", "omit")
        or os.environ.get("PLE_OMIT_REASONING_EFFORT") == "1"
    ):
        clean.pop("reasoning_effort", None)
    return clean


class _StdlibOpenAICompatibleClient:
    """Minimal stdlib async OpenAI Chat Completions HTTP client for local OSS proxies."""

    def __init__(self, *, base_url: str, api_key: str, timeout: float = 1800.0) -> None:
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kwargs: Any) -> Any:
        body = json.dumps(kwargs).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        def _post() -> dict[str, Any]:
            req = urllib.request.Request(self._endpoint, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        raw = await asyncio.to_thread(_post)
        first_choice = (raw.get("choices") or [{}])[0]
        msg_raw = first_choice.get("message") or {}
        finish_reason = first_choice.get("finish_reason") or "stop"
        tool_calls = msg_raw.get("tool_calls")
        content = msg_raw.get("content")

        class _CompatMsg:
            def __init__(self, c: Any, tc: Any) -> None:
                self.content = c
                self.tool_calls = tc

            def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
                out: dict[str, Any] = {"role": "assistant"}
                if self.content is not None or not exclude_none:
                    out["content"] = self.content
                if self.tool_calls:
                    out["tool_calls"] = self.tool_calls
                return out

        usage_raw = raw.get("usage")
        usage_obj = None
        if isinstance(usage_raw, dict):
            details_raw = usage_raw.get("prompt_tokens_details") or {}
            details_obj = type(
                "D", (), {"cached_tokens": int(details_raw.get("cached_tokens") or 0)}
            )()
            usage_obj = type(
                "U",
                (),
                {
                    "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
                    "prompt_tokens_details": details_obj,
                },
            )()

        choice_obj = type(
            "C", (), {"message": _CompatMsg(content, tool_calls), "finish_reason": finish_reason}
        )()
        return type("R", (), {"choices": [choice_obj], "usage": usage_obj})()


async def _workspace_digest(session: taskbridge.Session) -> str:
    res = await session.exec(
        "find /app /filesystem -maxdepth 4 -type f -printf '%p %s %T@\n' 2>/dev/null | sort | sha256sum"
    )
    return hashlib.sha256(res.get("output", "").encode("utf-8")).hexdigest()


async def solve(session: taskbridge.Session, *, client: Any = None) -> str:
    runtime_model_api = os.environ.get("MODEL_API", MODEL_API)
    runtime_model = (
        os.environ.get("MODEL_NAME")
        or (runtime_model_api.split("/", 1)[1] if "/" in runtime_model_api else runtime_model_api)
        or MODEL
    )
    runtime_effort = os.environ.get("REASONING_EFFORT") or (
        "omit"
        if runtime_model_api.lower().startswith("openai-compatible/")
        else ("max" if any(m in runtime_model.lower() for m in MAX_EFFORT_MODELS) else "high")
    )
    runtime_cache_mark = "claude" in runtime_model.lower()

    if client is None:
        base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("MODEL_BASE_URL") or None
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("MODEL_API_KEY")
            or ("local-oss" if base_url else None)
        )
        if openai is not None:
            client_kw: dict[str, Any] = {"max_retries": 3, "timeout": 1800}
            if base_url:
                client_kw["base_url"] = base_url
            if api_key:
                client_kw["api_key"] = api_key
            client = openai.AsyncOpenAI(**client_kw)
        elif base_url:
            client = _StdlibOpenAICompatibleClient(base_url=base_url, api_key=api_key or "local-oss")
        else:
            raise RuntimeError("openai>=1.68.0 is required when no client or MODEL_BASE_URL is configured")

    mcp_clients: dict[str, taskbridge.MCPClient] = {}
    mcp_summary_lines: list[str] = []

    for srv in session.mcp_servers:
        name = str(srv.get("name") or "apps")
        url = str(srv.get("url") or "")
        if not url:
            continue
        try:
            mcp = taskbridge.MCPClient(session, url)
            await mcp.initialize()
            tools = await mcp.list_tools()
            mcp_clients[name] = mcp
            mcp_summary_lines.append(f"### MCP server `{name}` ({len(tools)} tools)")
            for t in tools[:40]:
                schema_str = json.dumps(t.get("inputSchema") or {}, separators=(",", ":"))
                desc = str(t.get("description") or "").strip()
                mcp_summary_lines.append(f"- `{t.get('name')}`: {desc} | schema: `{schema_str[:400]}`")
        except Exception as exc:  # noqa: BLE001
            await session.log(f"MCP init warning for {name}: {exc}")

    base_tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute bash inside the task container.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_sec": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a UTF-8 file from the task container (optionally bounded by 1-indexed start_line/end_line).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a UTF-8 file inside the task container.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace a unique exact UTF-8 snippet inside an existing container file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                    },
                    "required": ["path", "old_str", "new_str"],
                },
            },
        },
    ]
    base_tools.extend(
        [
            {
                "type": "function",
                "function": {
                    "name": "verify_claims",
                    "description": (
                        "Deterministically re-derive and audit structured engineering claims "
                        "(value, unit, expr, inputs, clauses, evidence) from claims_path or claims_json."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "claims_path": {"type": "string"},
                            "claims_json": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "verify_deliverables",
                    "description": (
                        "Parse staged deliverable files (.zip, .xlsx, .xlsm, .json, .py, .ifc, .dxf, .csv) "
                        "to verify structural integrity and catch formula/syntax/truncation defects."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "paths": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        ]
    )
    if mcp_clients:
        base_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "mcp_call",
                    "description": "Invoke a tool on a connected task-local MCP server.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "server": {"type": "string"},
                            "tool": {"type": "string"},
                            "arguments_json": {"type": "string"},
                        },
                        "required": ["server", "tool", "arguments_json"],
                    },
                },
            }
        )
    read_only_tools = [
        t for t in base_tools if t["function"]["name"] not in ("write_file", "edit_file")
    ]
    exec_stats: dict[str, Any] = {
        "file_writes": 0,
        "verify_claims_calls": 0,
        "last_claims_failed": None,
        "verify_deliverables_calls": 0,
        "last_deliverable_defects": None,
    }

    async def _exec_tool(fn: dict[str, Any]) -> str:
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise TypeError("arguments must be a JSON object")
        except (TypeError, ValueError) as exc:
            fn["arguments"] = "{}"
            return f"error: invalid JSON arguments ({exc})"
        fn["arguments"] = json.dumps(args, allow_nan=False)
        try:
            if name == "bash":
                out = await session.exec(
                    str(args["command"]),
                    timeout_sec=int(args.get("timeout_sec") or 120),
                )
                return _clip(f"exit code {out['exit_code']}\n{out['output']}")
            if name == "read_file":
                raw = await session.read_file(str(args["path"]))
                text = raw.decode("utf-8", "replace")
                if args.get("start_line") is not None or args.get("end_line") is not None:
                    lines = text.splitlines()
                    s_idx = max(1, int(args.get("start_line") or 1)) - 1
                    e_raw = args.get("end_line")
                    e_idx = len(lines) if e_raw is None else max(s_idx, int(e_raw))
                    text = "\n".join(lines[s_idx:e_idx])
                return _clip(text)
            if name == "write_file":
                nbytes = await session.write_file(str(args["path"]), str(args["content"]))
                exec_stats["file_writes"] += 1
                return f"wrote {nbytes} bytes to {args['path']}"
            if name == "edit_file":
                path = str(args["path"])
                old_s = str(args["old_str"])
                new_s = str(args["new_str"])
                cur = (await session.read_file(path)).decode("utf-8")
                count = cur.count(old_s)
                if count != 1:
                    return f"error: old_str matched {count} times in {path}; must match exactly once"
                nbytes = await session.write_file(path, cur.replace(old_s, new_s, 1))
                exec_stats["file_writes"] += 1
                return f"edited {path} ({nbytes} bytes)"
            if name == "verify_claims":
                if args.get("claims_path"):
                    raw_claims = (await session.read_file(str(args["claims_path"]))).decode("utf-8")
                    claims_list = json.loads(raw_claims)
                else:
                    raw_c = args.get("claims_json") or "[]"
                    claims_list = raw_c if isinstance(raw_c, list) else json.loads(str(raw_c))
                rep = _verify_claims_list(claims_list)
                exec_stats["verify_claims_calls"] += 1
                exec_stats["last_claims_failed"] = rep.get("failed")
                return _clip(json.dumps(rep, ensure_ascii=False))
            if name == "verify_deliverables":
                raw_paths = args.get("paths")
                if isinstance(raw_paths, list) and raw_paths:
                    target_paths = [str(p) for p in raw_paths[:64]]
                else:
                    found = await session.exec(
                        rf"find /app /filesystem -maxdepth 5 -type f \( {_DELIV_EXTS_FIND_ARGS} \) 2>/dev/null | sort"
                    )
                    target_paths = [
                        ln.strip()
                        for ln in found.get("output", "").splitlines()
                        if ln.strip().startswith(("/app/", "/filesystem/"))
                    ][:64]
                defects: list[str] = []
                checked = 0
                for tp in target_paths:
                    checked += 1
                    try:
                        payload = await session.read_file(tp)
                        defects.extend(_audit_deliverable_bytes(tp, payload))
                    except Exception as exc:  # noqa: BLE001
                        defects.append(f"{tp}: unreadable deliverable ({exc})")
                exec_stats["verify_deliverables_calls"] += 1
                exec_stats["last_deliverable_defects"] = len(defects)
                rep = {
                    "files_checked": checked,
                    "defects_count": len(defects),
                    "defects": defects[:16],
                }
                return _clip(json.dumps(rep, ensure_ascii=False))
            if name == "mcp_call":
                srv_name = str(args["server"])
                if srv_name not in mcp_clients:
                    return f"error: unknown MCP server {srv_name!r} (available: {sorted(mcp_clients)})"
                raw_arg = args.get("arguments_json")
                call_args = raw_arg if isinstance(raw_arg, dict) else json.loads(str(raw_arg or "{}"))
                res = await mcp_clients[srv_name].call_tool(str(args["tool"]), call_args)
                return _clip(json.dumps(res, ensure_ascii=False))
            return f"error: unknown tool {name}"
        except taskbridge.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {exc}"

    retryable_exc: tuple[type[BaseException], ...] = (
        (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        )
        if openai is not None
        else ()
    ) + (urllib.error.URLError,)

    def _is_retryable_model_error(exc: BaseException) -> bool:
        if isinstance(exc, retryable_exc):
            return True
        status = getattr(exc, "status_code", getattr(exc, "code", None))
        return isinstance(status, int) and (status in (408, 409, 429) or status >= 500)

    async def _call_model(req_kwargs: dict[str, Any], timeout: float | None, messages: list[dict[str, Any]]) -> Any:
        sanitized = _sanitize_kwargs(runtime_model, req_kwargs, model_api=runtime_model_api)
        for attempt, wait_s in enumerate((*RETRY_WAITS, 0)):
            try:
                return await asyncio.wait_for(
                    client.chat.completions.create(**sanitized),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise
            except Exception as exc:  # noqa: BLE001
                msg_s = str(exc)
                if CONTEXT_FULL.search(msg_s) and _compact_older_tool_outputs(messages, keep_recent=2) > 0:
                    sanitized["messages"] = _marked(messages, cache_mark=runtime_cache_mark)
                    continue
                if not _is_retryable_model_error(exc):
                    raise
                if wait_s == 0 or attempt >= len(RETRY_WAITS):
                    raise
                left = session.time_left()
                if left is not None and left < wait_s + 30:
                    raise
                await asyncio.sleep(wait_s)

    wrap_msg = {"role": "user", "content": "Time budget reached for this phase; summarize immediately."}

    async def _run_role(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        until_sec: float,
        final: bool = False,
    ) -> str:
        nudges, head = 0, ""
        while True:
            left = session.time_left()
            wrap = left is not None and left < until_sec
            if wrap and messages[-1:] != [wrap_msg]:
                messages.append(dict(wrap_msg))
            timeout = (
                None
                if left is None
                else (min(WRAP_UP_SEC, max(1.0, left)) if wrap else max(1.0, left - until_sec))
            )
            req_kwargs = {
                "model": runtime_model,
                "messages": _marked(messages, cache_mark=runtime_cache_mark),
                "tools": tools,
                "tool_choice": "none" if wrap else "auto",
                "reasoning_effort": runtime_effort,
                "max_completion_tokens": MAX_OUTPUT_TOKENS,
            }
            try:
                reply = await _call_model(req_kwargs, timeout, messages)
            except asyncio.TimeoutError:
                return head
            if getattr(reply, "usage", None):
                details = getattr(reply.usage, "prompt_tokens_details", None)
                await session.usage(
                    runtime_model,
                    reply.usage.prompt_tokens,
                    reply.usage.completion_tokens,
                    getattr(details, "cached_tokens", 0) or 0,
                )
                if reply.usage.prompt_tokens > COMPACT_AT_TOKENS:
                    _compact_older_tool_outputs(messages)
            choice = reply.choices[0]
            msg = choice.message
            cut = getattr(choice, "finish_reason", None) == "length"
            raw_content = msg.content or ""
            aborted_reply = _is_aborted_reply(raw_content) and not getattr(msg, "tool_calls", None)
            text = head + ("" if aborted_reply and head else raw_content)
            if final and text and not _is_aborted_reply(text):
                session.final_message = text
            turn = msg.model_dump(exclude_none=True) if hasattr(msg, "model_dump") else dict(msg)
            if wrap or not getattr(msg, "tool_calls", None):
                turn.pop("tool_calls", None)
                if raw_content and not aborted_reply:
                    messages.append(turn)
                if wrap or (not aborted_reply and not cut) or nudges >= MAX_NUDGES:
                    return "" if _is_aborted_reply(text) else text
                nudges = nudges + 1
                head = head if aborted_reply else text
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your reply was cut off; continue where it stopped."
                            if cut
                            else (
                                "Your previous reply returned an empty or aborted placeholder; finish writing and verifying all deliverables before answering."
                                if aborted_reply
                                else "Continue, and finish with your answer."
                            )
                        ),
                    }
                )
                continue
            nudges, head = 0, ""
            messages.append(turn)
            for tc in turn["tool_calls"]:
                result_str = await _exec_tool(tc["function"])
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})

    total = session.time_left()
    until = lambda share: 0.0 if total is None else total * share
    mcp_note = ("\n\n## Connected MCP Servers\n" + "\n".join(mcp_summary_lines)) if mcp_summary_lines else ""
    task_prompt = session.instruction + mcp_note

    plan = await _run_role(
        [
            {"role": "system", "content": PLANNER_SYSTEM.format(workdir=session.workdir)},
            {"role": "user", "content": task_prompt},
        ],
        read_only_tools,
        until(1.0 - PLAN_SHARE),
    )
    work_msgs = [
        {"role": "system", "content": WORKER_SYSTEM.format(workdir=session.workdir)},
        {"role": "user", "content": f"{task_prompt}\n\n## Execution Plan\n{plan}"},
    ]
    summary = await _run_role(work_msgs, base_tools, until(CHECK_SHARE), final=True)

    for round_idx in range(MAX_FIX_ROUNDS + 1):
        left = session.time_left()
        if left is not None and left < WRAP_UP_SEC + MIN_STEP_SEC:
            break
        pre_hash = await _workspace_digest(session)
        telemetry_note = (
            f"file_writes={exec_stats['file_writes']}, "
            f"verify_claims_calls={exec_stats['verify_claims_calls']}, "
            f"last_claims_failed={exec_stats['last_claims_failed']}, "
            f"verify_deliverables_calls={exec_stats['verify_deliverables_calls']}, "
            f"last_deliverable_defects={exec_stats['last_deliverable_defects']}, "
            f"tree_digest={pre_hash[:16]}"
        )
        verdict = await _run_role(
            [
                {"role": "system", "content": VERIFIER_SYSTEM.format(workdir=session.workdir)},
                {
                    "role": "user",
                    "content": (
                        f"## Task\n{task_prompt}\n\n## Plan\n{plan}"
                        f"\n\n## Deterministic Harness Telemetry\n{telemetry_note}"
                        f"\n\n## Summary\n{summary}"
                    ),
                },
            ],
            read_only_tools,
            until(CHECK_SHARE / 2.0),
        )
        post_hash = await _workspace_digest(session)
        cleaned = verdict.strip("*_. \r\n\t").upper()
        # Strict cold-read check: empty timeout string "" is NEVER accepted as PASS, and verifier must not mutate files.
        if cleaned == "PASS" and pre_hash == post_hash:
            break
        if round_idx == MAX_FIX_ROUNDS:
            break
        gap_msg = (
            verdict
            if verdict.strip()
            else "Verifier timed out or returned empty output; re-verify all deliverable files and non-zero archive sizes directly."
        )
        work_msgs.append(
            {
                "role": "user",
                "content": f"Cold-read verification flagged issues:\n{gap_msg}\n\nFix every defect and re-check all output paths.",
            }
        )
        summary = await _run_role(work_msgs, base_tools, WRAP_UP_SEC, final=True) or summary

    for cand in (summary, session.final_message):
        if cand and not _is_aborted_reply(cand):
            return cand
    return "Completed engineering deliverables."


if __name__ == "__main__":
    taskbridge.run(solve)
