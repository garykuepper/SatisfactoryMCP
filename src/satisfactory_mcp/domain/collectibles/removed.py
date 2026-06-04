"""The save's destroyed-actor list, joined against the map's placement table."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

from ..spatial import geo
from .table import CollectibleTable, _class_of_removed, _leaf, _name_stem

__all__ = ["RemovedActors"]


@dataclass
class RemovedActors:
    """What this save has taken off the map, and what that leaves standing.

    Holds the placement table rather than loading it, because the table is a
    process-wide singleton and whether a caller can see one is the caller's
    business: with ``table=None`` every answer here degrades to the save-only
    name-prefix census and says so.
    """

    projection: dict
    table: CollectibleTable | None

    @cached_property
    def destroyed_keys(self) -> frozenset[tuple[str, str]]:
        """``(cell, instance name)`` of every actor this save records as gone.

        The pair, never the bare name, because a bare name is not identity: auto-numbered
        placements reuse names across cells, while ``(cell, name)`` is unique over all
        69,364 map actors. A name-only join both invents matches and misses real ones.
        """
        removed = self.projection.get("removed") or {}
        cells: list[str] = removed.get("cells") or []
        return frozenset(
            (cells[ix] if 0 <= ix < len(cells) else "", leaf)
            for ix, leaf in removed.get("instances") or []
        )

    #: The placement table's ``state`` -> what it means about a placement this save has NOT
    #: collected. Worded so that "3 remain" and "3 I can see" cannot be read as the same
    #: claim: ``never_streamed`` is a placement no save has ever loaded, and the whole point
    #: of naming it is that it must never be presented as standing there.
    OBSERVED: ClassVar[dict[str, str]] = {
        "present": "standing",
        "unknown": "never_streamed",
        "collected": "gone_in_a_later_save",
    }

    def placements(self, category: str | None = None, remaining_only: bool = False) -> list[dict]:
        """Map placements annotated with what THIS save says about each one.

        ``collected`` is exact and is about the loaded save: it either holds a destroyed
        record at that ``(cell, name)`` or it does not. ``observed`` is the one field that is
        not about the loaded save -- it is the placement table's own scan of every save on
        disk, which is what lets a placement nobody has ever visited be reported as such
        instead of as standing there.
        """
        table = self.table
        if table is None:
            return []
        gone = self.destroyed_keys
        source = table.by_category.get(category, []) if category else table.rows
        out: list[dict] = []
        for row in source:
            name = _leaf(row["instance"])
            collected = (row["cell"], name) in gone
            if collected and remaining_only:
                continue
            out.append(
                {
                    "category": row["category"],
                    "cls": row["class"],
                    "name": name,
                    "cell": row["cell"],
                    "pos": (row["x"], row["y"], row["z"]),
                    "collected": collected,
                    #: ``None`` on a state this code does not know, never a nearest guess.
                    "observed": None if collected else self.OBSERVED.get(row.get("state") or ""),
                    "looted": row.get("looted"),
                    "contents": row.get("contents"),
                    "unlock_cost": row.get("unlock_cost"),
                    "hazard": row.get("hazard") or {},
                }
            )
        return out

    def nearest_placements(
        self, origin: tuple[float, float], category: str | None = None
    ) -> list[dict]:
        """Remaining placements, nearest first, each with a planar distance in metres.

        Only remaining ones: "where do I go and get one" is the question, and a placement
        this save has already collected is not an answer to it. Planar because Z spans a
        few hundred metres against a 7 km map, and mixing them would flatter a placement
        directly up a cliff face.
        """
        rows = self.placements(category, remaining_only=True)
        for row in rows:
            row["distance_m"] = geo.distance_m((row["pos"][0], row["pos"][1]), origin)
        rows.sort(key=lambda r: r["distance_m"])
        return rows

    def collectible_census(self) -> list[dict]:
        """Per category: what the map placed, what this save collected, and what is left.

        ``placed`` is the map's own count and ``collected`` is this save's own destroyed
        list, so ``remaining = placed - collected`` is arithmetic between two exact numbers
        -- for every category the game actually records. Where it does not,
        ``remaining`` is ``None`` and not a number: see ``CollectibleTable.state_tracked``.

        ``remaining`` then splits by how much has been *observed*, and the split is the
        honest part. A placement in a cell no save has ever streamed in is counted as
        remaining, because nothing has collected it, and is reported as
        ``never_streamed`` rather than as standing there.

        Counted off ``placements`` rather than off the table again, so the census and the
        listing cannot drift apart: what you can list is what was counted. That invariant
        has been broken here before, by exactly two code paths reading the same thing two
        ways.
        """
        table = self.table
        if table is None:
            return []
        tally: dict[str, dict] = {}
        for placement in self.placements():
            counted = tally.setdefault(
                placement["category"],
                {
                    "collected": 0,
                    #: Standing but already emptied. Only a drop pod can be both.
                    "looted_and_standing": 0,
                    **dict.fromkeys((*self.OBSERVED.values(), "unstated"), 0),
                },
            )
            if placement["collected"]:
                counted["collected"] += 1
                continue
            #: ``unstated`` catches a table newer than this code, rather than a state this
            #: code does not know quietly landing in one it does.
            counted[placement["observed"] or "unstated"] += 1
            if placement["looted"]:
                counted["looted_and_standing"] += 1

        rows: list[dict] = []
        for category in table.categories:
            counted = tally[category]
            placed = len(table.by_category[category])
            tracked = table.state_tracked(category)
            rows.append(
                {
                    "category": category,
                    "cls": table.cls_of(category),
                    "placed": placed,
                    #: None, not placed-minus-zero, when a collection would leave no record.
                    "remaining": (placed - counted["collected"]) if tracked else None,
                    **counted,
                    "state_tracked": tracked,
                    "pedestal_of": table.pedestal_of(category),
                    "note": table.note_for(category),
                }
            )
        return rows

    def removed_actors(self, group: str | None = None) -> dict:
        """What this save records as collected off the map, resolved against the map itself.

        **Why the save alone cannot answer this.** The world is not saved. Every slug,
        mushroom, sphere and drop pod sits where the map put it, and a save never mentions
        the ones still standing -- it records the negative, which actors are gone. So the
        destroyed list *is* the collected list. What it does not carry is a class: an entry
        is a bare ``(cell, name)``, and a name does not decide a class. Joining it to the
        map's placement table by that pair does, exactly, which is the whole difference
        between this and what it replaced -- 40 yellow slugs used to be counted as blue.

        Without the table (it is untracked, so a fresh clone has none) this degrades to the
        name-prefix census under ``source: "save-only"``, and says so rather than raising.
        """
        removed = self.projection.get("removed") or {}
        cells: list[str] = removed.get("cells") or []
        instances: list = removed.get("instances") or []
        counts: dict[str, int] = removed.get("counts") or {}
        table = self.table
        out: dict = {
            #: Every destroyed record, including the classes the map table does not track.
            "total": len(instances) or sum(counts.values()),
            "cells": len(cells),
            "source": "save-only" if table is None else "map",
        }
        if table is None:
            return self._removed_by_name(out, instances, cells, group)

        collected: dict[str, int] = {}
        stems: dict[str, int] = {}
        for ix, leaf in instances:
            row = table.by_key.get((cells[ix] if 0 <= ix < len(cells) else "", leaf))
            if row is None:
                stem = _name_stem(leaf)
                stems[stem] = stems.get(stem, 0) + 1
            else:
                collected[row["category"]] = collected.get(row["category"], 0) + 1
        out["groups"] = dict(sorted(collected.items(), key=lambda kv: -kv[1]))
        out["resolved"] = sum(collected.values())
        out["unresolved"] = sum(stems.values())
        #: Destroyed records the map places nothing at. Two known causes, and they are not
        #: interchangeable: a class the table deliberately excludes (scenery, regrowing
        #: flora, resource nodes -- each with the map's own count in ``_meta.excluded``), or
        #: an actor the map never placed at all, which is what a pickup the player dropped
        #: is. Reported by name stem, which is a label and not a class.
        out["unresolved_stems"] = dict(sorted(stems.items(), key=lambda kv: (-kv[1], kv[0])))
        out["census"] = self.collectible_census()
        if group is None:
            return out
        if group not in table.by_category:
            out["error"] = f"unknown group {group!r}; the map places: {sorted(table.by_category)}"
            return out
        out["group"] = group
        out["actors"] = [p for p in self.placements(group) if p["collected"]]
        return out

    def _removed_by_name(
        self, out: dict, instances: list, cells: list[str], group: str | None
    ) -> dict:
        """The census the save can build on its own: counts by name prefix, and wrong.

        Kept because a fresh clone has no placement table and "collected 889 things" is
        still worth having. Every caller must label it: this cannot say what remains, and
        the counts it does give are known to misfile one actor in fourteen.
        """
        grouped: dict[str, int] = {}
        unmatched: dict[str, int] = {}
        for _ix, leaf in instances:
            label = self.removed_group(leaf)
            if label is None:
                cls = _class_of_removed(leaf)
                unmatched[cls] = unmatched.get(cls, 0) + 1
            else:
                grouped[label] = grouped.get(label, 0) + 1
        out["groups"] = dict(sorted(grouped.items(), key=lambda kv: -kv[1]))
        if unmatched:
            out["other"] = dict(sorted(unmatched.items(), key=lambda kv: -kv[1]))
        if group is None:
            return out
        known = [g for g, _p, _s in self.REMOVED_GROUPS]
        if group not in known:
            out["error"] = f"unknown group {group!r}; known: {known}"
            return out
        out["group"] = group
        out["actors"] = [
            {"name": leaf, "cell": cells[ix] if 0 <= ix < len(cells) else "", "pos": None}
            for ix, leaf in instances
            if self.removed_group(leaf) == group
        ]
        return out

    #: What a name-only rule groups the save's removed-actor list into, used ONLY when the
    #: map's placement table is absent: ``(label, prefixes, strict)``, first match wins, so
    #: ``BP_Crystal_mk2`` must be tried before the ``BP_Crystal`` that is its prefix.
    #:
    #: **This rule is measurably wrong and is kept only as a degraded fallback.** Scored
    #: against the map's own answer for the 713 removed actors the map resolves on the
    #: reference save, it misfiles 51 -- 40 yellow slugs read as blue -- and leaves 65 as
    #: ``artifact_unsplit``. The table's own wider scoring of a name rule over all 4,446
    #: placements is in ``_meta.naming``: 231 wrong, 1,607 unmatched.
    #:
    #: Why it cannot be fixed. These lists carry no class path -- only an instance name -- and
    #: 68% of those are level-placed actors whose placement counter is glued straight onto the
    #: blueprint name with no separator. A somersloop is ``BP_WAT1`` and a Mercer sphere
    #: ``BP_WAT2``, one digit apart exactly where the counter lands, so ``BP_WAT112`` is
    #: undecidable and the ``strict`` groups refuse to guess it. Worse, the map's actors kept
    #: the names of the actors they were copied from: 98 rows the map calls
    #: ``BP_Crystal_mk2_C`` are named ``BP_Crystal_C_<n>``, which spells a class outright and
    #: spells the wrong one. No name rule survives that; only the map does.
    REMOVED_GROUPS: ClassVar[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
        ("slug_purple", ("BP_Crystal_mk3",), False),
        ("slug_yellow", ("BP_Crystal_mk2",), False),
        ("slug_blue", ("BP_Crystal",), False),
        ("somersloop", ("BP_WAT1",), True),
        ("mercer_sphere", ("BP_WAT2",), True),
        ("artifact_unsplit", ("BP_WAT",), False),
        ("mercer_shrine", ("BP_MercerShrine",), False),
        ("crash_site", ("BP_DropPod", "BP_Ship", "BP_CrashSiteDebris"), False),
        ("flora", ("BP_Shroom", "BP_SporeFlower", "BP_NutBush", "BP_BerryBush"), False),
        ("debris", ("BP_DebrisActor", "BP_Rock", "BP_Boulder", "BP_Destructible"), False),
        ("dropped_pickup", ("FGItemPickup_Spawnable",), False),
    )

    def removed_group(self, name: str) -> str | None:
        """Which name-prefix group a removed actor belongs to, for the fallback census only.

        First match wins, which is the whole reason ``REMOVED_GROUPS`` is an ordered tuple:
        ``BP_Crystal_mk3_C_2146`` starts with ``BP_Crystal`` as well as ``BP_Crystal_mk3``, so
        testing the plain slug prefix first would file every purple slug as blue. A ``strict``
        group only accepts a name that spells its class out with ``_C``, because ``BP_WAT112``
        cannot be assigned without guessing.

        Neither guard is enough: see ``REMOVED_GROUPS``. The map is what resolves these.
        """
        for label, prefixes, strict in self.REMOVED_GROUPS:
            if strict:
                if any(name.startswith(f"{p}_C") for p in prefixes):
                    return label
            elif name.startswith(prefixes):
                return label
        return None
