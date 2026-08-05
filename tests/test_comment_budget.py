"""The comment budget of docs/comments.md, measured: prose:code at most 0.25 in
interfaces/presenters/tools, at most 0.4 in domain/core/pioneersav.

WARN MODE until the cleanup sweep lands: offenders are reported as one pytest warning,
not a failure. Flip ENFORCE to True afterwards.
"""

from __future__ import annotations

import io
import tokenize
import warnings
from pathlib import Path

ENFORCE = False

ROOT = Path(__file__).resolve().parent.parent
BUDGETS = [
    (ROOT / "src" / "satisfactory_mcp" / "interfaces", 0.25),
    (ROOT / "src" / "satisfactory_mcp" / "presenters", 0.25),
    (ROOT / "tools", 0.25),
    (ROOT / "src" / "satisfactory_mcp" / "domain", 0.40),
    (ROOT / "src" / "satisfactory_mcp" / "core", 0.40),
    (ROOT / "src" / "pioneersav", 0.40),
]
MIN_CODE_LINES = 40  # tiny files are all header; the budget is about essays, not stubs


def prose_and_code(path: Path) -> tuple[int, int] | None:
    src = path.read_text(encoding="utf-8")
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return None
    comment_lines: set[int] = set()
    doc_lines: set[int] = set()
    prev = None
    for t in toks:
        if t.type == tokenize.COMMENT:
            comment_lines.update(range(t.start[0], t.end[0] + 1))
        elif t.type == tokenize.STRING and prev in (
            None,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            doc_lines.update(range(t.start[0], t.end[0] + 1))
        if t.type not in (tokenize.COMMENT,):
            prev = t.type
    prose = code = 0
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if (i in comment_lines and s.startswith("#")) or i in doc_lines:
            prose += 1
        else:
            code += 1
    return prose, code


def test_the_prose_stays_inside_its_budget() -> None:
    over: list[str] = []
    for root, budget in BUDGETS:
        for path in root.rglob("*.py"):
            counted = prose_and_code(path)
            if counted is None:
                continue
            prose, code = counted
            if code < MIN_CODE_LINES:
                continue
            ratio = prose / code
            if ratio > budget:
                over.append(f"{ratio:.2f}>{budget} {path.relative_to(ROOT)}")
    if not over:
        return
    over.sort(reverse=True)
    message = f"comment budget: {len(over)} file(s) over (docs/comments.md): " + "; ".join(
        over[:10]
    ) + ("" if len(over) <= 10 else f"; +{len(over) - 10} more")
    if ENFORCE:
        raise AssertionError(message)
    warnings.warn(message, stacklevel=1)
