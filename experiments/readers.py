"""Stdlib-only readers for the PLE task files (no installs).

pdf_text:  ReportLab-style PDFs (Type1/WinAnsi). Rebuilds lines from text positions.
xlsx_rows: cell values (cached) and formulas from an .xlsx, per sheet.
docx_text: paragraph and table text from a .docx.
"""

from __future__ import annotations

import base64
import re
import zipfile
import zlib
import xml.etree.ElementTree as ET

_TOKEN = re.compile(
    rb"\((?:\\.|[^\\)])*\)|\[(?:\((?:\\.|[^\\)])*\)|[^\]])*\]|/[^\s/\[\]()<>]+|[-+]?\d*\.?\d+|[A-Za-z*']+",
    re.S,
)


_SYMBOL = {0xA2: "′", 0xB2: "″", 0xA3: "≤", 0xB3: "≥", 0xBB: "≈", 0xB4: "×", 0xB0: "°", 0xB1: "±",
           0xB7: "•", 0xB9: "≠", 0xAE: "→", 0x44: "Δ", 0x64: "δ", 0x66: "φ", 0x70: "π", 0x6D: "µ", 0x53: "Σ"}


def _decode(b: bytes, base_font: str) -> str:
    if base_font == "Symbol":
        return "".join(_SYMBOL.get(c, chr(c)) for c in b)
    if base_font == "ZapfDingbats":
        return "•" * len(b.strip()) + (" " if b.endswith(b" ") else "")
    # WinAnsiEncoding: unused codes above 040 (e.g. 0177) render as a bullet (PDF spec, Annex D).
    return "".join("•" if c in (0x7F, 0x81, 0x8D, 0x8F, 0x90, 0x9D) else bytes([c]).decode("cp1252") for c in b)


def _pdf_string(raw: bytes, base_font: str = "") -> str:
    s = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):  # backslash
            n = s[i + 1]
            if 0x30 <= n <= 0x37:
                j = i + 1
                while j < len(s) and j < i + 4 and 0x30 <= s[j] <= 0x37:
                    j += 1
                out.append(int(s[i + 1:j], 8) & 0xFF)
                i = j
                continue
            out.append({ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return _decode(bytes(out), base_font)


def pdf_text(path: str) -> str:
    data = open(path, "rb").read()
    fonts = {n.decode(): b.decode() for b, n in re.findall(rb"/BaseFont\s*/([\w-]+)[^>]*?/Name\s*/(\w+)", data)}
    pages: list[str] = []
    for head, stream in re.findall(rb"<<((?:(?!>>).)*?)>>\s*stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            if b"ASCII85Decode" in head:
                stream = base64.a85decode(stream.strip().removeprefix(b"<~").removesuffix(b"~>"))
            content = zlib.decompress(stream)
        except (zlib.error, ValueError):
            continue
        if b"Tj" not in content and b"TJ" not in content:
            continue
        pieces: list[tuple[float, float, str, bool]] = []  # (y, x, text, continues previous piece)
        cont = False
        ctm = [0.0, 0.0]
        stack: list[list[float]] = []
        tx = ty = lx = ly = 0.0
        leading = 0.0
        args: list[bytes] = []
        font = ""
        for tok in _TOKEN.findall(content):
            if tok[:1] in b"(/[" or re.fullmatch(rb"[-+]?\d*\.?\d+", tok):
                args.append(tok)
                continue
            op = tok
            num = lambda k: float(args[k])  # noqa: E731
            if op == b"q":
                stack.append(ctm[:])
            elif op == b"Q" and stack:
                ctm = stack.pop()
            elif op == b"cm" and len(args) >= 6:
                ctm = [ctm[0] + num(-2), ctm[1] + num(-1)]
            elif op == b"BT":
                tx = ty = lx = ly = 0.0
                cont = False
            elif op == b"Tm" and len(args) >= 6:
                tx, ty = num(-2), num(-1)
                lx, ly = tx, ty
                cont = False
            elif op == b"Td" and len(args) >= 2:
                lx, ly = lx + num(-2), ly + num(-1)
                tx, ty = lx, ly
                cont = False
            elif op == b"Tf" and len(args) >= 2:
                font = fonts.get(args[-2].decode().lstrip("/"), "")
            elif op == b"TL" and args:
                leading = num(-1)
            elif op == b"T*":
                ly -= leading
                tx, ty = lx, ly
                cont = False
            elif op in (b"Tj", b"'") and args and args[-1][:1] == b"(":
                if op == b"'":
                    ly -= leading
                    tx, ty = lx, ly
                pieces.append((ctm[1] + ty, ctm[0] + tx, _pdf_string(args[-1], font), cont))
                tx += 0.001  # keep later pieces on the same line ordered after this one
                cont = True
            elif op == b"TJ" and args and args[-1][:1] == b"[":
                text = "".join(_pdf_string(m, font) for m in re.findall(rb"\((?:\\.|[^\\)])*\)", args[-1]))
                pieces.append((ctm[1] + ty, ctm[0] + tx, text, cont))
                tx += 0.001
                cont = True
            args = []
        rows: dict[int, list[tuple[float, str, bool]]] = {}
        for y, x, text, c in pieces:
            rows.setdefault(round(y), []).append((x, text, c))
        lines = []
        for y in sorted(rows, reverse=True):
            cells = sorted(rows[y])
            line = ""
            for x, text, c in cells:
                sep = line and not c and not line.endswith(" ") and not text.startswith(" ")
                line += (" | " if sep else "") + text
            lines.append(line.rstrip())
        pages.append("\n".join(lines))
    return "\n\f\n".join(pages)


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def xlsx_rows(path: str) -> dict[str, list[list[tuple[str, str]]]]:
    """Returns {sheet: [[(cell, value-or-=formula), ...], ...]}."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            for si in _xml(zf, "xl/sharedStrings.xml").findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
        rels = {
            r.get("Id"): r.get("Target")
            for r in _xml(zf, "xl/_rels/workbook.xml.rels")
        }
        out: dict[str, list[list[tuple[str, str]]]] = {}
        for sh in _xml(zf, "xl/workbook.xml").find("m:sheets", ns):
            target = rels[sh.get(rel_ns)].lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            rows = []
            for row in _xml(zf, target).iter("{%s}row" % ns["m"]):
                cells = []
                for c in row.findall("m:c", ns):
                    f, v = c.find("m:f", ns), c.find("m:v", ns)
                    if c.get("t") == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif c.get("t") == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter("{%s}t" % ns["m"]))
                    else:
                        val = v.text if v is not None else ""
                    if f is not None and f.text:
                        val = f"={f.text}" + (f" -> {val}" if val else "")
                    if val != "":
                        cells.append((c.get("r"), val))
                if cells:
                    rows.append(cells)
            out[sh.get("name")] = rows
        return out


def docx_text(path: str) -> str:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        body = _xml(zf, "word/document.xml").find(f"{w}body")
    lines = []
    for el in body:
        if el.tag == f"{w}p":
            lines.append("".join(t.text or "" for t in el.iter(f"{w}t")))
        elif el.tag == f"{w}tbl":
            for tr in el.iter(f"{w}tr"):
                lines.append(" | ".join("".join(t.text or "" for t in tc.iter(f"{w}t")) for tc in tr.iter(f"{w}tc")))
    return "\n".join(lines)
