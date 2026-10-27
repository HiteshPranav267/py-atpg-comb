"""Generate docs/changelog/changelog.tex from docs/changelog/changelog.md.

Handles the subset of Markdown the changelog uses: #/##/### headings,
paragraphs, "- " bullets (with indented continuation lines), **bold**,
*italic* and `code`. Run from the repository root:

    python docs/changelog/changelog_to_tex.py
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC, DST = HERE / "changelog.md", HERE / "changelog.tex"

PREAMBLE = r"""% Generated from changelog.md by changelog_to_tex.py -- do not edit by hand.
\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{parskip}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\setlist{nosep,leftmargin=1.4em}
\begin{document}
"""

_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
            "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}"}
_UNICODE = {"→": r"$\rightarrow$", "≥": r"$\geq$", "≤": r"$\leq$", "×": r"$\times$",
            "–": "--", "—": "---", "¬": r"$\neg$"}


def _escape(text: str) -> str:
    out = "".join(_SPECIAL.get(c, c) for c in text)
    for k, v in _UNICODE.items():
        out = out.replace(k, v)
    return out


def inline(text: str) -> str:
    """Escape text and convert `code`, **bold** and *italic*."""
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(r"\texttt{" + _escape(part[1:-1]) + "}")
            continue
        esc = _escape(part)
        esc = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", esc)
        esc = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\\emph{\1}", esc)
        out.append(esc)
    return "".join(out)


def convert(md: str) -> str:
    lines = md.splitlines()
    body: list[str] = []
    para: list[str] = []
    items: list[str] = []
    title = "Development log"

    def flush_para():
        if para:
            body.append(inline(" ".join(para)) + "\n")
            para.clear()

    def flush_items():
        if items:
            body.append("\\begin{itemize}")
            body.extend(r"  \item " + inline(i) for i in items)
            body.append("\\end{itemize}\n")
            items.clear()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            flush_items()
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush_para()
            flush_items()
            level, text = len(m.group(1)), m.group(2)
            if level == 1:
                title = text
            else:
                cmd = "section" if level == 2 else "subsection"
                body.append(f"\\{cmd}*{{{inline(text)}}}")
            continue
        if line.startswith("- "):
            flush_para()
            items.append(line[2:].strip())
            continue
        if items and raw.startswith("  "):
            items[-1] += " " + line.strip()
            continue
        para.append(line.strip())
    flush_para()
    flush_items()
    head = f"\\title{{{inline(title)}}}\n\\author{{}}\n\\date{{}}\n\\maketitle\n"
    return PREAMBLE + head + "\n".join(body) + "\n\\end{document}\n"


if __name__ == "__main__":
    DST.write_text(convert(SRC.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {DST}")
