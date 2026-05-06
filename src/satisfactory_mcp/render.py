"""All response formatting. Nothing else in the package formats output.

Context budget is the binding design constraint: every response here is read by a
model, and all 291 automatable recipes in the tightest possible TSV is already
~25k characters. Centralising formatting is what stops that budget regressing one
tool at a time.

Rules enforced here:
  * compact TSV, not JSON -- the win is dropping repeated keys and braces
  * names in rows, IDs once in a footer
  * scoped aggregates BEFORE rows (an unscoped total is often actively misleading)
  * truncation envelope counts DATA ROWS ONLY (miscounting misleads the model)
  * coordinates in metres, not raw centimetres
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

__all__ = [
    "bullets",
    "envelope",
    "ids_footer",
    "kv",
    "num",
    "plural",
    "rate",
    "table",
    "where_bands",
]

MAX_ROWS = 25


def num(value: float | None, places: int = 2) -> str:
    """Compact number: no trailing zeros, no scientific notation."""
    if value is None:
        return "-"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.{places}f}".rstrip("0").rstrip(".")


def rate(value: float | None, unit: str = "") -> str:
    if value is None:
        return "-"
    return f"{num(value)}{unit}"


def table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    total: int | None = None,
    offset: int = 0,
    limit: int | None = None,
    hint: str = "",
) -> str:
    """Tab-separated table with an honest truncation envelope.

    ``total`` is the number of MATCHES, not the number of lines emitted. The header
    and any footer are never counted -- reporting "showing 7" for 5 data rows tells
    the model something false about what it has seen.
    """
    body = [list(r) for r in rows]
    shown = len(body)
    lines = ["\t".join(headers)]
    lines += ["\t".join("" if c is None else str(c) for c in r) for r in body]
    out = "\n".join(lines)

    if total is None or total <= shown:
        return out

    remaining = total - offset - shown
    parts = [f"# {total} match(es), showing {shown} from offset {offset}."]
    if remaining > 0:
        nxt = offset + shown
        parts.append(f"{remaining} more: call again with offset={nxt}")
        if hint:
            parts.append(hint)
    return out + "\n" + " ".join(parts)


def envelope(summary: str, body: str = "", notes: Iterable[str] = ()) -> str:
    """Summary first, then notes, then rows.

    Warnings lead because that is where the actionable insight is -- "Blender
    unlocked but 0 built" matters more than the twentieth process row.
    """
    chunks = [summary.rstrip()]
    note_list = [n for n in notes if n]
    if note_list:
        chunks.append("\n".join(f"! {n}" for n in note_list))
    if body.strip():
        chunks.append(body.rstrip())
    return "\n".join(chunks)


def ids_footer(pairs: Iterable[tuple[str, str]], label: str = "ids") -> str:
    """One line mapping display names to class IDs, kept out of the rows."""
    items = [f"{name}={cid}" for name, cid in pairs]
    if not items:
        return ""
    return f"# {label}: " + " ".join(items)


def kv(pairs: Iterable[tuple[str, object]], sep: str = "  ") -> str:
    return sep.join(f"{k}={v}" for k, v in pairs if v not in (None, "", []))


def bullets(lines: Iterable[str], marker: str = "-") -> str:
    return "\n".join(f"{marker} {line}" for line in lines if line)


def flows(items: Iterable[tuple[str, float, bool]]) -> str:
    """Render an ingredient/product list: ``30 Crude Oil + 20 Water``.

    Fluids arrive pre-divided by 1000, so this never shows raw litres.
    """
    return " + ".join(f"{num(amount)} {name}" for name, amount, _ in items) or "-"


def plural(name: str, count: int) -> str:
    """Pluralise a building or item name. "31 Refinerys" reads as a typo, which makes
    the reader distrust the number next to it."""
    if count == 1:
        return name
    if name.endswith("y") and name[-2:-1] not in "aeiou":
        return name[:-1] + "ies"
    return name + ("es" if name.endswith(("s", "x", "z", "ch", "sh")) else "s")


def where_bands(distances_m: Iterable[float], gap_m: float = 200.0, max_bands: int = 3) -> str:
    """Where a set of machines actually is, as ``4@0 13@1 6@2.5`` kilometres.

    A count alone hides the thing that matters: 23 Water Extractors reads as one fleet
    until you see that 4 stand at the plant, 13 at the main base and 6 two and a half
    kilometres away. A mean hides it just as well, so distances are single-linkage
    clustered at the same 200 m the node and site clustering uses, and every group is
    shown with its own count.
    """
    ordered = sorted(distances_m)
    if not ordered:
        return ""
    groups: list[list[float]] = [[ordered[0]]]
    for d in ordered[1:]:
        if d - groups[-1][-1] > gap_m:
            groups.append([])
        groups[-1].append(d)
    if len(groups) > max_bands:
        # Near groups stay separate -- that is where the reusable machines are -- and
        # the tail collapses, since it only ever means "and some far away".
        head, tail = groups[: max_bands - 1], groups[max_bands - 1 :]
        groups = [*head, [d for g in tail for d in g]]
    parts = [f"{len(g)}@{sum(g) / len(g) / 1000:.1f}".rstrip("0").rstrip(".") for g in groups]
    return " ".join(f"{p}0" if p.endswith("@") else p for p in parts)


def clamp(value: int | None, default: int = 10, lo: int = 1, hi: int = MAX_ROWS) -> int:
    """Clamp a caller-supplied limit. Tool schemas also bound this, belt and braces."""
    if value is None:
        return default
    return max(lo, min(hi, int(value)))
