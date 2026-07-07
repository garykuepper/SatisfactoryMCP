"""The map's own collectible placements, and the names a save can offer instead."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ... import config

__all__ = ["COLLECTIBLES_FILE", "CollectibleTable", "_name_stem", "load_collectibles"]


def _leaf(instance: str) -> str:
    """The instance name without its level path."""
    return str(instance).rsplit(".", 1)[-1]


def _class_of_removed(leaf: str) -> str:
    """Class of a removed actor from its instance name, for the `other` bucket only.

    Mirrors the sidecar's `_removed_class`: strip a trailing index, a `_UAID_<hex>` if present,
    then a trailing `_C`. Duplicated rather than imported because the sidecar runs as a separate
    process and importing across that boundary is what the boundary exists to prevent -- and it
    is only ever used to LABEL an unmatched class, never to decide a group.
    """
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    if len(parts) >= 2 and parts[-2] == "UAID":
        parts = parts[:-2]
    if parts and parts[-1] == "C":
        parts.pop()
    return "_".join(parts) or leaf


#: A placement counter glued straight onto a blueprint name with no separator, e.g. the
#: ``369`` of ``BP_SporeFlower369``. Only stripped after a LETTER, so ``BP_DebrisActor_02``
#: -- where the digits are a real part of the class name -- survives intact.
_GLUED_INDEX = re.compile(r"(?<=[A-Za-z])\d+$")


def _name_stem(leaf: str) -> str:
    """A label for a removed actor the map table has no row for. NOT a class.

    Both halves of that matter. It is a label because the map is the only thing that can
    name a class -- ``BP_WAT133`` is a somersloop and ``BP_Crystal_C_15`` can be a yellow
    slug -- so anything derived from a name is a display string and never a decision. It is
    still worth computing because the alternative is 89 spore flowers appearing as 40
    one-row entries with the counter still attached.
    """
    return _GLUED_INDEX.sub("", _class_of_removed(leaf))


@dataclass
class CollectibleTable:
    """The map's own collectible placements: ``data/world_collectibles.json``.

    Read from the installed game's cooked packages, so ``placed`` is the map's own count
    rather than a count of sightings. The save is never a source of position here and this
    table is never a source of state -- that split is what makes both halves honest.
    """

    rows: list[dict]
    meta: dict
    by_key: dict[tuple[str, str], dict] = field(default_factory=dict, repr=False)
    by_category: dict[str, list[dict]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for row in self.rows:
            #: ``(cell, name)``, never the bare name. All 14,367 UAID names are globally
            #: unique but auto-numbered ones are not: the pair is unique over all 69,364
            #: map actors and a bare name is not, so a name-only index would both invent
            #: matches and miss real ones.
            self.by_key[(row["cell"], _leaf(row["instance"]))] = row
            self.by_category.setdefault(row["category"], []).append(row)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def categories(self) -> list[str]:
        """Category names, most-placed first."""
        return sorted(self.by_category, key=lambda c: (-len(self.by_category[c]), c))

    def info(self, category: str) -> dict:
        return ((self.meta.get("totals") or {}).get("by_category") or {}).get(category, {})

    def cls_of(self, category: str) -> str:
        rows = self.by_category.get(category) or []
        return rows[0]["class"] if rows else str(self.info(category).get("class") or "?")

    def note_for(self, category: str) -> str:
        return str(self.info(category).get("note") or "")

    def state_tracked(self, category: str) -> bool:
        """Whether a save records anything at all about this class.

        ``rows_any_save_mentions`` counts the placements some save on disk names, live or
        gone. Where it is 0 the class is not save-serialised, and the table says so: it
        "can be located and never state-tracked". That is the difference between a
        ``remaining`` figure and a fabricated one -- with no record of a collection,
        ``placed - collected`` equals ``placed`` whether or not the player took every one.
        """
        return bool(self.info(category).get("rows_any_save_mentions"))

    def pedestal_of(self, category: str) -> str | None:
        """The category this one is the base of, where it is one.

        A shrine is a second row about one find, not a second find: the map's own
        AttachParent pairs all 298 Mercer shrines 1:1 with a sphere. Summing categories
        therefore over-counts artifacts by the number of shrines.
        """
        pedestals = (self.meta.get("totals") or {}).get("pedestals") or {}
        parents = (pedestals.get(category) or {}).get("parent_category") or {}
        return next(iter(parents), None)

    def excluded_reason(self, stem: str) -> str | None:
        """Why the map table has no row for a class, in the table's own words.

        Falls back to naming the excluded classes a stem could belong to, without picking
        one: ``BP_DebrisActor`` is the stem of three, and the counter glued onto a name is
        not evidence about which. Naming all three still answers "is this a collectible".
        """
        excluded = self.meta.get("excluded") or {}
        entry = excluded.get(f"{stem}_C")
        if isinstance(entry, dict):
            return str(entry.get("why"))
        siblings = sorted(k for k in excluded if k.startswith(stem))
        if siblings:
            return "the map excludes " + ", ".join(siblings) + " -- a name does not say which"
        return None

    @property
    def build(self) -> str:
        return str(
            ((self.meta.get("source") or {}).get("placements") or {}).get("game_build") or "?"
        )


COLLECTIBLES_FILE = "world_collectibles.json"


#: Keyed by the file and its mtime, for the argument ``spatial.nodes._TABLE`` makes. This
#: table is the one of the three that moves MOST -- it is untracked, it is regenerated
#: whenever the reader re-derives placements from a new game build, and a stale copy is
#: exactly the "pinned tables drift every map update" failure the loaders are warned about.
#:
#: A miss is not cached. ``None`` here means the file is absent or unreadable, which is a
#: state a reader fixes by running the generator, and there would be no mtime to key the
#: absence on anyway -- so the next call looks again rather than answering from a cached no.
_TABLE: dict[tuple[str, int], CollectibleTable] = {}


def load_collectibles() -> CollectibleTable | None:
    """The map's placement table, or ``None`` when it has not been generated.

    ``None`` rather than an exception: the file is untracked, so a fresh clone does not
    have one, and every caller degrades to the save-only census instead of failing. What
    is lost without it is everything the save cannot know by itself -- how many of each
    kind exist, where they are, and therefore what remains.
    """
    path = config.data_dir() / COLLECTIBLES_FILE
    if not path.is_file():
        return None
    try:
        key = (str(path), path.stat().st_mtime_ns)
        hit = _TABLE.get(key)
        if hit is not None:
            return hit
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = payload.get("collectibles") or []
    if not rows:
        return None
    table = CollectibleTable(rows=rows, meta=payload.get("_meta") or {})
    _TABLE.clear()
    _TABLE[key] = table
    return table
