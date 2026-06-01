"""Recipe search, including the reverse direction: what CONSUMES an item.

``search_recipes`` used to match recipe names only, so "what eats Rubber" had no
answer at all. The way it was actually answered was to guess candidate items from
memory and check them one at a time -- eight speculative lookups that still could
not prove the list was complete. Completeness is the whole point of this module,
and it is bought with two rules:

**The census is counted over every recipe in Docs.json, never over the page.**
``kind``, ``include_events``, ``limit`` and ``offset`` decide what is SHOWN; they
never move the counts in the header. So a caller who reads only the first line has
still been told the true total.

**Building recipes are counted even when they are not shown.** A recipe's ``kind``
partitions 872 recipes into 291 part, 547 building and 34 manual, and the build
gun consumes items exactly like a Refinery does. Measured on Rubber: 15 part
recipes, **7 building** (Fuel-Powered Generator, Packager, Resource Well
Pressurizer, Valve, Power Pole Mk.3, Blueprint Designer Mk.2, Fluid Truck Station)
and 4 manual. A part-only answer misses eleven of twenty-six consumers, and the
building ones are the late-tier machines a player is about to place. So the header
always breaks the count down by kind, and a ``kind`` filter that hides rows says
what it hid.

**The units are not comparable and must not be rendered as if they were.** Only
part recipes run in a machine, so only they have a per-minute rate; a building
recipe's ``mManufactoringDuration`` is 1.0 for all 547 of them, which turns
``amount * 60 / duration`` into a rate of 1,200 Iron Ore/min for The HUB. Building
and manual rows therefore carry the per-craft amount and are suffixed ``/build``
and ``/craft`` so no row can be misread as a throughput.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import GameData, Recipe

__all__ = ["KINDS", "Census", "Hit", "search"]

#: Report order. Part recipes first because they are what a factory runs.
KINDS = ("part", "building", "manual")

_KIND_RANK = {kind: i for i, kind in enumerate(KINDS)}


@dataclass(frozen=True)
class Hit:
    recipe: Recipe
    #: How much of the queried item this recipe consumes (or produces), in the
    #: recipe's own unit -- per minute for part, per craft for building/manual.
    qty: float
    #: True HAVE, False LOCKED, None when no save could be read.
    unlocked: bool | None


@dataclass
class Census:
    """Counts over the WHOLE recipe table, so a truncated page is still honest."""

    scanned: int
    total: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    have: dict[str, int] = field(default_factory=dict)
    locked: dict[str, int] = field(default_factory=dict)
    #: FICSMAS recipes matching the query. Hidden by default, never uncounted.
    events: int = 0

    def add(self, recipe: Recipe, unlocked: bool | None) -> None:
        self.total += 1
        self.by_kind[recipe.kind] = self.by_kind.get(recipe.kind, 0) + 1
        if unlocked is True:
            self.have[recipe.kind] = self.have.get(recipe.kind, 0) + 1
        elif unlocked is False:
            self.locked[recipe.kind] = self.locked.get(recipe.kind, 0) + 1
        if recipe.is_event:
            self.events += 1

    def line(self, subject: str) -> str:
        """The completeness claim, and the evidence for it, in one line."""
        if not self.total:
            return f"# no recipe {subject} (searched all {self.scanned} recipes)"
        parts = []
        for kind in KINDS:
            n = self.by_kind.get(kind, 0)
            if not n:
                continue
            gate = ""
            have, locked = self.have.get(kind, 0), self.locked.get(kind, 0)
            if have or locked:
                gate = f" [{have} HAVE, {locked} LOCKED]"
            parts.append(f"{n} {kind}{gate}")
        return (
            f"# {self.total} recipe(s) {subject}: "
            + ", ".join(parts)
            + f". Counted over all {self.scanned} recipes;"
            + " kind/limit change the rows, never these totals."
        )


def _qty(recipe: Recipe, item: str, side: str) -> float:
    flows = recipe.ingredients if side == "consumes" else recipe.products
    return sum((f.per_min if recipe.kind == "part" else f.amount) for f in flows if f.item == item)


def search(
    game: GameData,
    query: str = "",
    consumes: str | None = None,
    produces: str | None = None,
    kind: str = "part",
    only_alternates: bool = False,
    include_events: bool = False,
    unlocked: set[str] | None = None,
) -> tuple[list[Hit], Census]:
    """Every recipe matching the query, plus a census over the whole table.

    ``query``/``consumes``/``produces``/``only_alternates`` define the QUESTION and
    are counted in the census. ``kind`` and ``include_events`` only filter the rows
    that come back, so the header can promise a total the rows do not have to reach.
    """
    q = query.strip().casefold()
    census = Census(scanned=len(game.recipes))
    hits: list[Hit] = []
    wanted = None if kind in ("all", "", None) else kind

    for r in game.recipes.values():
        if q and q not in r.name.casefold():
            continue
        if only_alternates and not r.is_alternate:
            continue
        if consumes and not any(f.item == consumes for f in r.ingredients):
            continue
        if produces and not any(f.item == produces for f in r.products):
            continue
        have = None if unlocked is None else (r.cls in unlocked)
        census.add(r, have)
        if wanted is not None and r.kind != wanted:
            continue
        if r.is_event and not include_events:
            continue
        side, item = ("consumes", consumes) if consumes else ("produces", produces)
        hits.append(Hit(r, _qty(r, item, side) if item else 0.0, have))

    if consumes or produces:
        # Biggest consumer first within a kind: the question is "what eats my
        # Rubber", and the answer is ordered by how much.
        hits.sort(key=lambda h: (_KIND_RANK.get(h.recipe.kind, 9), -h.qty, h.recipe.name))
    else:
        hits.sort(key=lambda h: (not h.recipe.is_alternate, h.recipe.name))
    return hits, census
