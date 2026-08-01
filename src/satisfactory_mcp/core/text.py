"""The text helpers that are NOT presentation.

Everything else that shapes a response -- tables, envelopes, footers, truncation --
lives in ``presenters.text.primitives`` and is forbidden to domain code. These are
here because a domain result can legitimately carry a number or a building name
inside a sentence it owns: ``DiffRow.note`` reads "31 Refineries busy on other
recipes" and tests assert on it at the dataclass level, so the pluralisation has to
be reachable without importing a presenter.

``stamp`` and ``ago`` are here on the same terms: the save's age belongs to the
sentence the domain owns (``SaveIdentity.age_note``) and to a refusal ``core.saveio``
raises, and neither of those layers may import a presenter.
"""

from __future__ import annotations

import time

__all__ = ["ago", "num", "plural", "stamp"]


def num(value: float | None, places: int = 2) -> str:
    """Compact number: no trailing zeros, no scientific notation."""
    if value is None:
        return "-"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.{places}f}".rstrip("0").rstrip(".")


def stamp(mtime_ns: int | None) -> str | None:
    """A file write as local wall-clock time, to the minute, or ``None`` for no mtime.

    Local time on purpose: the reader is the person whose disk this is, and "08:28"
    must be the same "08:28" their game's save dialog shows.
    """
    if not mtime_ns:
        return None
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime_ns / 1e9))


def ago(mtime_ns: int | None, now_s: float | None = None) -> str | None:
    """How long ago a file was written, as one coarse human unit, or ``None`` for no mtime.

    Coarse by design -- "3h ago" answers "is this file the live world?" and false
    precision ("187 minutes") makes the reader do the division the function exists to
    do. Clamped at zero because an autosave can land between ``stat`` and ``now`` and
    "-1 min ago" reads as a bug rather than as a fresh file.
    """
    if not mtime_ns:
        return None
    secs = max(0.0, (time.time() if now_s is None else now_s) - mtime_ns / 1e9)
    if secs < 60:
        return "under a minute ago"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 48 * 3600:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)} days ago"


def plural(name: str, count: int) -> str:
    """Pluralise a building or item name. "31 Refinerys" reads as a typo, which makes
    the reader distrust the number next to it."""
    if count == 1:
        return name
    if name.endswith("y") and name[-2:-1] not in "aeiou":
        return name[:-1] + "ies"
    return name + ("es" if name.endswith(("s", "x", "z", "ch", "sh")) else "s")
