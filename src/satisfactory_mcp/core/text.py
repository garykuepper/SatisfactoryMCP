"""The two text helpers that are NOT presentation.

Everything else that shapes a response -- tables, envelopes, footers, truncation --
lives in ``presenters.text.primitives`` and is forbidden to domain code. These two
are here because a domain result can legitimately carry a number or a building name
inside a sentence it owns: ``DiffRow.note`` reads "31 Refineries busy on other
recipes" and tests assert on it at the dataclass level, so the pluralisation has to
be reachable without importing a presenter.
"""

from __future__ import annotations

__all__ = ["num", "plural"]


def num(value: float | None, places: int = 2) -> str:
    """Compact number: no trailing zeros, no scientific notation."""
    if value is None:
        return "-"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.{places}f}".rstrip("0").rstrip(".")


def plural(name: str, count: int) -> str:
    """Pluralise a building or item name. "31 Refinerys" reads as a typo, which makes
    the reader distrust the number next to it."""
    if count == 1:
        return name
    if name.endswith("y") and name[-2:-1] not in "aeiou":
        return name[:-1] + "ies"
    return name + ("es" if name.endswith(("s", "x", "z", "ch", "sh")) else "s")
