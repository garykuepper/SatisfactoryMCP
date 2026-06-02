"""Derived views over a save projection, joined against normalized game data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from typing import ClassVar

from .. import config
from ..core.gamedata.model import GameData, Recipe, Schematic
from ..spatial import geo
from . import projection as proj

__all__ = ["CollectibleTable", "HardDriveOffer", "WorldState", "load_collectibles"]


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


@lru_cache(maxsize=1)
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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = payload.get("collectibles") or []
    if not rows:
        return None
    return CollectibleTable(rows=rows, meta=payload.get("_meta") or {})


@dataclass
class HardDriveOffer:
    hard_drive_id: int | None
    rerolls_left: int
    options: list[dict]  # {schematic, name, recipes: [Recipe], inventory_slots}


@dataclass
class WorldState:
    """A save projection plus the game data needed to interpret it."""

    projection: dict
    game: GameData

    # ---- identity ------------------------------------------------------

    @property
    def header(self) -> dict:
        return self.projection.get("header", {})

    @property
    def world_id(self) -> str:
        """Stable per-world key. Labels hang off this, so it must survive autosave
        rotation and renaming -- which saveIdentifier does and the filename does not."""
        h = self.header
        return h.get("save_identifier") or f"session:{h.get('session_name') or '?'}"

    @cached_property
    def graph(self):
        """The factory graph. Built once per state, since identity, health and layout
        all want it."""
        from ..graph.build import build_graph

        return build_graph(self.projection)

    @cached_property
    def structures(self):
        """Foundation slabs -- what was physically built as one platform."""
        from ..graph.structure import build_structures

        return build_structures(self.projection)

    @cached_property
    def proposals(self):
        """Coherence-scored factory proposals. ~0.3 s, so built once per state."""
        from ..graph.cohere import propose

        return propose(self.graph, self.game, self.projection, self.structures)

    @cached_property
    def plans(self):
        """Named plans saved for this world."""
        from ..planning.store import PlanStore

        return PlanStore.load(self.world_id, self.header.get("session_name") or "")

    @cached_property
    def labels(self):
        """Persisted factory names for this world."""
        from ..graph.labels import LabelStore

        return LabelStore.load(self.world_id, self.header.get("session_name") or "")

    @property
    def age_note(self) -> str:
        """Human-readable provenance. Always shown: autosaves rotate every ~5 min
        and can catch the factory mid-restructure."""
        h = self.header
        kind = "autosave" if "autosave" in h.get("filename", "").lower() else "manual save"
        hours = (h.get("play_duration_s") or 0) / 3600
        return (
            f"{h.get('filename', '?')} ({kind}, world {h.get('session_name', '?')!r}, "
            f"{hours:.0f}h played, saveVersion {h.get('save_version')})"
        )

    # ---- unlocks -------------------------------------------------------

    @cached_property
    def available_recipe_ids(self) -> set[str]:
        """FGRecipeManager.mAvailableRecipes -- the authoritative unlock gate.

        Deliberately NOT reconstructed from purchased schematics: that over-reports
        by 8 on the reference save, because recipes can arrive via more than one
        schematic (Charcoal/Biocoal come from Compacted Coal, whose own alternate
        schematics are unpurchased).
        """
        return set(self.projection.get("progression", {}).get("available_recipes", ()))

    @cached_property
    def purchased_schematic_ids(self) -> set[str]:
        return set(self.projection.get("progression", {}).get("purchased_schematics", ()))

    @cached_property
    def unresolved_recipe_ids(self) -> set[str]:
        """Available recipes with no FGRecipe in Docs.json.

        31 on the reference save: 24 Recipe_Swatch_*, 5 Recipe_Material_*, 2 skins.
        Filtered out rather than surfaced as broken IDs.
        """
        return {r for r in self.available_recipe_ids if r not in self.game.recipes}

    def unlocked_recipes(self, kind: str = "part") -> list[Recipe]:
        return [
            self.game.recipes[r]
            for r in sorted(self.available_recipe_ids)
            if r in self.game.recipes and self.game.recipes[r].kind == kind
        ]

    def has_recipe(self, recipe_id: str) -> bool:
        return recipe_id in self.available_recipe_ids

    @cached_property
    def unlocked_alternates(self) -> list[Recipe]:
        return [r for r in self.unlocked_recipes("part") if r.is_alternate]

    @cached_property
    def locked_alternates(self) -> list[Recipe]:
        return [r for r in self.game.alternates() if r.cls not in self.available_recipe_ids]

    @cached_property
    def unlocked_building_ids(self) -> set[str]:
        """Buildings the player can construct.

        Derived from unlocked BUILDING recipes via their product descriptor, because
        recipe naming is unreliable: Recipe_SmelterMk1_C builds the FOUNDRY.
        """
        desc_to_build = {
            b.descriptor: cls for cls, b in self.game.buildings.items() if b.descriptor
        }
        out: set[str] = set()
        for rid in self.available_recipe_ids:
            r = self.game.recipes.get(rid)
            if r is None or r.kind != "building":
                continue
            for f in r.products:
                hit = desc_to_build.get(f.item)
                if hit:
                    out.add(hit)
        return out

    def can_build(self, building_id: str) -> bool:
        return building_id in self.unlocked_building_ids

    # ---- what is actually built ----------------------------------------

    @cached_property
    def built_counts(self) -> dict[str, int]:
        """Header census plus the lightweight subsystem.

        FGLightweightBuildableSubsystem holds Build_* classes that appear in no actor
        header, so a header-only count understates what exists.
        """
        from ..core.gamedata.constants import BUILDING_CLASS_ALIASES

        out: dict[str, int] = {}
        for source in (
            self.projection.get("building_counts", {}),
            self.projection.get("lightweight_counts") or {},
        ):
            for cls, n in source.items():
                # The save and the dump disagree on a few names. Folding the save's name
                # onto the dump's is what stops "unlocked but never built: Biomass
                # Burner" appearing while eight of them are running.
                key = BUILDING_CLASS_ALIASES.get(cls, cls)
                out[key] = out.get(key, 0) + n
        return out

    def built(self, building_id: str) -> int:
        return self.built_counts.get(building_id, 0)

    def unlocked_but_unbuilt(self) -> list[str]:
        """Capability the player has and is not using -- often the actionable gap."""
        interesting = {
            cls
            for cls in self.unlocked_building_ids
            if (b := self.game.buildings.get(cls))
            and (b.is_manufacturer or b.is_generator or b.is_extractor)
        }
        return sorted(cls for cls in interesting if self.built(cls) == 0)

    @cached_property
    def paused(self) -> list[dict]:
        return [r for r in self._all_records() if r.get("paused")]

    @cached_property
    def misconfigured(self) -> list[dict]:
        """Manufacturers with no recipe selected -- they produce nothing."""
        return [m for m in self.projection.get("machines", ()) if not m.get("recipe")]

    @cached_property
    def overclocked(self) -> list[dict]:
        return [
            r
            for r in self._all_records()
            if r.get("clock") is not None and abs(r["clock"] - 1.0) > 1e-6
        ]

    def _all_records(self) -> list[dict]:
        p = self.projection
        return [*p.get("machines", ()), *p.get("extractors", ()), *p.get("generators", ())]

    # ---- power ---------------------------------------------------------

    def power_report(self) -> dict:
        """Generation capacity, and draw both nameplate and measured.

        This used to report nameplate only, saying uptime "is not modelled here". The
        uptime was in the projection all along -- the 300 s productivity monitor, on 520 of
        566 records -- and the difference is not a rounding detail. On the reference save
        nameplate draw is **6,839 MW** while utilisation-weighted draw over the last
        complete window is **1,516 MW**, because most of the factory is idle. Headroom
        therefore reads 711 MW nameplate against roughly **6,034 MW** actual: an 8.5x
        error in the number `commission_plan` sizes a startup against.

        Both are reported because both are true and they answer different questions:

        * ``headroom_mw`` (nameplate) is the **safe** figure -- what is free if everything
          currently built ran at once. Energising a block can un-starve idle machines
          downstream, so this is the one not to exceed if you cannot watch it.
        * ``measured_headroom_mw`` is the **current** figure -- what is free right now,
          given how much of the factory is actually running.

        A machine with no productivity monitor is charged at full nameplate on both sides:
        unknown utilisation must not read as idle. Generators are capacity either way,
        since they burn to meet demand rather than at a rate of their own.

        Paused buildings are excluded from both sides.
        """
        gen: dict[str, dict] = {}
        total_mw = 0.0
        variable: list[str] = []
        for g in self.projection.get("generators", ()):
            if g.get("paused"):
                continue
            b = self.game.buildings.get(g["cls"])
            if b is None:
                variable.append(g["cls"])  # e.g. the two biomass classes absent from Docs
                continue
            clock = g.get("clock") or 1.0
            mw = b.power_production_mw * clock
            if not b.power_production_mw and b.variable_power_factor:
                mw = b.variable_power_factor * clock  # geothermal: normal-geyser average
            entry = gen.setdefault(g["cls"], {"name": b.name, "count": 0, "mw": 0.0})
            entry["count"] += 1
            entry["mw"] += mw
            total_mw += mw

        draw = 0.0
        measured = 0.0
        monitored = 0
        unmonitored = 0

        def _charge(rated: float, record: dict) -> None:
            """Add one machine to both totals, weighting the measured one by uptime."""
            nonlocal draw, measured, monitored, unmonitored
            draw += rated
            uptime = record.get("uptime") or {}
            window = uptime.get("window_s") or 0.0
            produced = uptime.get("produce_s") or 0.0
            if window > 0:
                monitored += 1
                measured += rated * (produced / window)
            else:
                # No monitor is NOT evidence of idleness. Charged in full, so an
                # unreadable machine can only make the measured figure conservative.
                unmonitored += 1
                measured += rated

        for m in self.projection.get("machines", ()):
            if m.get("paused"):
                continue
            r = self.game.recipes.get(m.get("recipe") or "")
            clock = m.get("clock") or 1.0
            if r is not None:
                _charge(self.game.recipe_power_mw(r, clock), m)
            else:
                b = self.game.buildings.get(m["cls"])
                if b:
                    _charge(b.power_at(clock), m)
        for e in self.projection.get("extractors", ()):
            if e.get("paused"):
                continue
            b = self.game.buildings.get(e["cls"])
            if b:
                _charge(b.power_at(e.get("clock") or 1.0), e)

        return {
            "generation_mw": total_mw,
            "draw_mw": draw,
            "headroom_mw": total_mw - draw,
            #: Utilisation-weighted over the last complete 300 s window.
            "measured_draw_mw": measured,
            "measured_headroom_mw": total_mw - measured,
            "monitored": monitored,
            "unmonitored": unmonitored,
            "utilisation": (measured / draw) if draw else 1.0,
            "by_generator": gen,
            "unmodellable": sorted(set(variable)),
            "paused_count": len(self.paused),
        }

    #: Native classes that actually carry ITEMS between machines. Deliberately narrow.
    #: `items_per_min` alone is not the test: a Personnel Elevator reports 400/min and
    #: moves people, and a Conveyor Lift duplicates a belt tier's rate, so a naive
    #: "fastest thing with a rate" pick can name something that is not a belt at all.
    BELT_NATIVE: ClassVar[str] = "FGBuildableConveyorBelt"
    PIPE_NATIVE: ClassVar[str] = "FGBuildablePipeline"

    def best_carrier(self, native: str, rate: str) -> tuple[str, float] | None:
        """The fastest tier of one carrier the player can actually build.

        Planning against a tier you have not unlocked is silent-wrong-by-default, which is
        the worst failure a planner has: every pipe count halves or doubles and nothing
        says so. The belt and pipe rates were hardcoded at Mk5/Mk2 -- correct on this save
        and unverified for a year.
        """
        best: tuple[str, float] | None = None
        for cls, b in self.game.buildings.items():
            if b.native != native or cls not in self.unlocked_building_ids:
                continue
            value = getattr(b, rate, 0.0)
            if value and (best is None or value > best[1]):
                best = (cls, value)
        return best

    def best_belt(self) -> tuple[str, float] | None:
        return self.best_carrier(self.BELT_NATIVE, "items_per_min")

    def best_pipe(self) -> tuple[str, float] | None:
        return self.best_carrier(self.PIPE_NATIVE, "flow_m3_min")

    def water_volumes(self) -> dict:
        """Water Extractors grouped by the body of water they draw from, plus sea level.

        OQ5 said water pumps "carry no node, purity or geometry", and concluded they could
        not be matched to anything. Two thirds of that is right and the conclusion was not:
        `mExtractableResource` points at a named `FGWaterVolume`, the sidecar has been
        storing it in ``node`` the whole time, and it groups this save's 23 pumps into
        three distinct bodies (13 / 6 / 4). The volume OBJECT is level geometry and is not
        in the save, so its shape and capacity really are unknowable -- but its identity
        is not, and identity is enough to say how many separate shorelines are already in
        use.

        Sea level falls out of the same rows. Every pump on this save sits at -17.3 or
        -17.5 m, which turns "water must be drawn at sea level" from a rule of thumb into
        a measured number that deck ordering can be checked against.
        """
        groups: dict[str, list[dict]] = {}
        zs: list[float] = []
        for e in self.projection.get("extractors", ()):
            if e["cls"] != "Build_WaterPump_C":
                continue
            groups.setdefault(e.get("node") or "(unresolved)", []).append(e)
            if e.get("pos"):
                zs.append(e["pos"][2] / 100.0)
        return {
            "volumes": {
                k.rsplit(".", 1)[-1]: len(v)
                for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))
            },
            "pumps": sum(len(v) for v in groups.values()),
            "sea_level_m": (sum(zs) / len(zs)) if zs else None,
            "sea_level_span_m": (max(zs) - min(zs)) if zs else None,
        }

    # ---- progression ---------------------------------------------------

    def progression(self) -> dict:
        p = self.projection.get("progression", {})
        tiers: dict[int, list[str]] = {}
        for sid in self.purchased_schematic_ids:
            s = self.game.schematics.get(sid)
            if s and s.type == "EST_Milestone":
                tiers.setdefault(s.tier, []).append(sid)
        all_tiers: dict[int, int] = {}
        for s in self.game.schematics.values():
            if s.type == "EST_Milestone":
                all_tiers[s.tier] = all_tiers.get(s.tier, 0) + 1
        # Highest FULLY complete tier. The highest tier with ANY milestone done is a
        # different and misleading number.
        complete = [t for t, total in sorted(all_tiers.items()) if len(tiers.get(t, ())) == total]
        return {
            "game_phase": p.get("game_phase"),
            "target_phase": p.get("target_phase"),
            "phase_costs_remaining": {
                k: {i: a for i, a in v.items() if a}
                for k, v in (p.get("phase_costs_remaining") or {}).items()
                if any(v.values())
            },
            "milestones_by_tier": {
                t: f"{len(tiers.get(t, ()))}/{all_tiers[t]}" for t in sorted(all_tiers)
            },
            "highest_complete_tier": max(complete) if complete else None,
            "purchased_schematics": len(self.purchased_schematic_ids),
            "available_recipes": len(self.available_recipe_ids),
        }

    #: EGamePhase -> GP_Project_Assembly_Phase_N.
    #:
    #: The keys of mGamePhaseCosts are the DEPRECATED EGamePhase enum
    #: (FGGamePhaseManager.h: "The old enum that defined the phases of the game.
    #: Replaced by UFGGamePhase. DEPRECATED Only kept for save compatibility"), while
    #: mCurrentGamePhase / mTargetGamePhase point at UFGGamePhase assets named
    #: GP_Project_Assembly_Phase_N. Nothing in Docs.json joins them: the assets do not
    #: ship there at all (0 occurrences of "GP_Project" in the 10 MB dump; the only
    #: "EGP_" string in it is an EGP_Victory schematic dependency), and the field that
    #: WOULD join them, UFGGamePhase::mGamePhase, lives on those unshipped assets.
    #: The save cannot join them directly either -- the manager's own legacy scalar
    #: mGamePhase is absent, i.e. UE-default EGP_NA, whose declaration comment reads
    #: "Added N/A to have a state that indicates we have migrated the save".
    #:
    #: EGP_EndGame -> Phase_3 is nonetheless MEASURED, from two save epochs of the same
    #: world. At 180-244 h the save reads mTargetGamePhase = Phase_3 with
    #: mTargetGamePhasePaidOffCosts = {SpaceElevatorPart_2: 2500} -- exactly one item
    #: paid. The EGP_EndGame entry of the deprecated array at the same instant reads
    #: {Part_2: 0 remaining, Part_4: 500, Part_5: 100}: the same three items, with the
    #: same single one settled. No other key mentions Part_2 as outstanding, and
    #: nothing can have been paid into a phase that was never the target.
    #:
    #: The other three follow by enum order (EarlyGame 0 < MidGame 1 < LateGame 2 <
    #: EndGame 3 < FoodCourt 4, declared in the shipped header) anchored on that pin,
    #: the four stored keys being contiguous in it. Corroborated but NOT relied on: the
    #: vendored wiki-derived PROJECT_ASSEMBLY_COSTS table lists Phase 1-4 item sets that
    #: match these four keys exactly and in order.
    #: ClassVar, not a field: a bare dict annotation on a dataclass is a mutable
    #: default and raises at class-creation time.
    EGP_TO_PHASE: ClassVar[dict[str, str]] = {
        "EGP_MidGame": "GP_Project_Assembly_Phase_1",
        "EGP_LateGame": "GP_Project_Assembly_Phase_2",
        "EGP_EndGame": "GP_Project_Assembly_Phase_3",
        "EGP_FoodCourt": "GP_Project_Assembly_Phase_4",
    }

    def phase_requirements(self) -> dict:
        """Space Elevator deliveries, live record first and deprecated record labelled.

        Two sources disagree and only one is alive:

        * ``mCurrentGamePhase`` / ``mTargetGamePhase`` / ``mTargetGamePhasePaidOffCosts``
          are the live ones. Deliveries go to the TARGET phase
          (``PayOffOnTargetGamePhase``, ``GetTargetGamePhaseCosts``), so "what do I owe"
          is the target's cost minus what is paid off.
        * ``mGamePhaseCosts`` is deprecated and **frozen**. Byte-identical across all 29
          parseable saves of the reference world, 180 h to 316 h, spanning the session
          where the player finished Phase 3 -- it still bills them 500 Modular Engine
          and 100 Adaptive Control Unit for it.

        The frozen table is still the only source of per-phase item lists, because the
        UFGGamePhase assets that hold ``mCosts`` do not ship in Docs.json. It is
        trustworthy for exactly one row: the phase that has never been targeted, whose
        untouched snapshot still equals its full cost. Every row is returned with a
        ``stale`` flag saying which case it is, rather than being silently filtered.
        """
        p = self.projection.get("progression", {}) or {}
        current = p.get("game_phase") or ""
        target = p.get("target_phase") or ""
        # Absent means empty, not missing: UE omits empty SaveGame TArrays. Empty is
        # the informative answer here -- nothing has been delivered to the target yet.
        paid = {k: v for k, v in (p.get("paid_off_target") or {}).items() if v}

        rows = []
        for egp, costs in (p.get("phase_costs_remaining") or {}).items():
            phase = self.EGP_TO_PHASE.get(egp)
            outstanding = {i: a for i, a in costs.items() if a}
            done = sorted(i for i, a in costs.items() if not a)
            if phase is None:
                stale = "unmapped"
            elif phase == target and not paid:
                # Never delivered into, so the frozen snapshot is still the true cost.
                stale = "usable"
            elif not outstanding:
                # All zeros. Frozen or not, "nothing outstanding" is what the live
                # pointers say too for any phase at or below the current one.
                stale = "complete"
            else:
                stale = "stale"
            rows.append(
                {
                    "egp": egp,
                    "phase": phase,
                    "outstanding": outstanding,
                    "complete": done,
                    "stale": stale,
                }
            )
        rows.sort(key=lambda r: r["phase"] or "~")
        return {
            "current_phase": current,
            "target_phase": target,
            "paid_off_target": paid,
            "phases": rows,
        }

    # ---- overclocking ----------------------------------------------------

    def shard_budget(self) -> dict:
        """Power Shards held, committed and free.

        Committed shards are READ, never derived. Every buildable carries an
        ``InventoryPotential`` component holding the shards actually slotted into it,
        and that is the only faithful count: a shard raises the maximum clock, it does
        not set it, so a building may hold more shards than its current clock needs. On
        the reference save 39 of 41 overclocked buildings hold exactly
        ``shards_for_clock``, and two hold 3 while running at 2.0 -- deriving from clock
        would report 95 spent where 97 are.

        "Free" excludes machine inventories on purpose (see ``stock``): the 97 in
        InventoryPotential components are all inside machines, so counting the raw
        ``inventories["machine"]`` total as shards on hand overstates the free pool by
        more than 4x on this save.
        """
        from ..core.gamedata.constants import POTENTIAL_SHARD_SLOTS, shards_for_clock

        shard_items = self.game.clock_shards()
        per_shard = max(shard_items.values()) if shard_items else 0.0
        stock = self.stock()
        free = sum(stock.get(item, 0.0) for item in shard_items)

        # Uncrafted slugs are latent shards. They sit wherever stock() looks -- carried,
        # in crates, or in the Dimensional Depot -- and on the reference save the depot
        # alone holds 93 Blue, 58 Yellow and 39 Purple, worth 404 shards against 22
        # already crafted. Reporting only the crafted pool understates what the player
        # can overclock with by ~19x, which is the same class of error as counting
        # machine buffers as stock.
        # Where they physically are. "free" pools carried + crates + Depot, which is
        # correct for spending but hides the answer to "is that the Depot?" -- a question
        # worth not making the player ask.
        wanted = set(shard_items) | set(self.game.slug_yields())
        by_place: dict[str, dict[str, float]] = {}
        for place, source in (
            ("carried", self._inventories.get("player", {})),
            ("crates", self._inventories.get("storage", {})),
            ("depot", self.projection.get("depot", {})),
        ):
            held = {self.game.item_name(k): v for k, v in source.items() if k in wanted and v}
            if held:
                by_place[place] = held

        slugs = []
        craftable = 0.0
        for item, yield_each in sorted(self.game.slug_yields().items(), key=lambda kv: -kv[1]):
            held = stock.get(item, 0.0)
            if not held:
                continue
            slugs.append(
                {
                    "item": item,
                    "name": self.game.item_name(item),
                    "held": held,
                    "each": yield_each,
                    "shards": held * yield_each,
                }
            )
            craftable += held * yield_each

        committed = 0
        holders: list[dict] = []
        for record in self._all_records():
            slotted = sum(
                n
                for item, n in (record.get("potential_slots") or {}).items()
                if item in shard_items
            )
            clock = float(record.get("clock") or 1.0)
            needed = shards_for_clock(clock, per_shard)
            if not slotted and not needed:
                continue
            committed += slotted
            holders.append(
                {
                    "instance": record["instance"].rsplit(".", 1)[-1],
                    "cls": record.get("cls", "?"),
                    "clock": clock,
                    "slotted": slotted,
                    "needed": needed,
                    #: A slot filled but not being used by the current clock.
                    "idle": max(0, slotted - needed),
                }
            )
        holders.sort(key=lambda h: (-h["slotted"], h["cls"]))
        return {
            "shard_items": shard_items,
            "slugs": slugs,
            "by_place": by_place,
            #: Shards these slugs would yield once crafted. NOT free: crafting is a
            #: manual step, so this is potential, never availability.
            "craftable": craftable,
            "potential": free + craftable,
            "free": free,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "slots_per_building": POTENTIAL_SHARD_SLOTS,
            #: Buildings whose InventoryPotential is unreadable get no entry at all, so
            #: a projection predating schema 9 reports 0 committed rather than a wrong
            #: number. Flagged so a caller can tell the two apart.
            "measured": any("potential_slots" in r for r in self._all_records()),
        }

    #: Capability -> the unlock flag that records it, where one exists. Present only
    #: once true: UE omits a SaveGame property at its default, so absent means false.
    CAPABILITY_FLAGS: ClassVar[dict[str, str]] = {
        "production_boost": "mIsBuildingProductionBoostUnlocked",
    }

    def has_capability(self, name: str) -> bool:
        """Whether a MAM-gated capability is researched.

        The unlock flag wins when the projection carries one, because it is what the game
        itself checks. The purchased-schematic set is the fallback, and it is not merely
        belt-and-braces: the flag is absent from a save taken before the research AND from
        any projection written before schema 10 extracted it, and those two look identical
        from here. Falling back keeps an older projection answering correctly instead of
        reporting every capability locked.
        """
        from ..core.gamedata.constants import CAPABILITY_SCHEMATICS

        flag = self.CAPABILITY_FLAGS.get(name)
        if flag and flag in self._unlock_flags:
            return bool(self._unlock_flags[flag])
        gate = CAPABILITY_SCHEMATICS.get(name)
        return bool(gate) and gate in self.purchased_schematic_ids

    @property
    def _unlock_flags(self) -> dict:
        return self.projection.get("unlock_flags", {}) or {}

    def research_gate(self, name: str) -> dict | None:
        """The schematic that unlocks ``name``, its cost, and what the player holds.

        ``None`` when the capability is already researched, so a caller can treat a
        truthy result as "here is what is still in the way".
        """
        from ..core.gamedata.constants import CAPABILITY_SCHEMATICS

        gate = CAPABILITY_SCHEMATICS.get(name)
        schematic = self.game.schematics.get(gate or "")
        if schematic is None or self.has_capability(name):
            return None
        stock = self.stock()
        rows = [
            {
                "item": f.item,
                "name": self.game.item_name(f.item),
                "need": f.amount,
                "have": stock.get(f.item, 0.0),
            }
            for f in schematic.cost
        ]
        return {
            "capability": name,
            "schematic": gate,
            "schematic_name": schematic.name,
            "kind": schematic.type,
            "cost": rows,
            "short": [r for r in rows if r["have"] < r["need"]],
            "affordable": all(r["have"] >= r["need"] for r in rows),
            #: Prerequisite schematics not yet purchased. Empty on an unblocked node.
            "blocked_by": [
                self.game.schematics[d].name
                for d in schematic.dependencies
                if d in self.game.schematics and d not in self.purchased_schematic_ids
            ],
        }

    def sloop_budget(self) -> dict:
        """Somersloops on hand and in machines.

        Free ones are read the same way as shards: ``stock`` pools carried, crates and
        the Dimensional Depot, which is exactly the set that can be spent.

        **Committed ones are read too, and this module used to claim they could not be.**
        That was wrong, and wrong in an instructive way: a save taken before Production
        Amplifier was researched contains no somersloop anywhere, so probing it found
        nothing and the absence was read as "the game does not record this". It does --
        in ``InventoryPotential``, the *same component* that holds Power Shards, which the
        sidecar had been reading into ``potential_slots`` the whole time. The component
        carries both, distinguished only by item class, and its ``mArbitrarySlotSizes``
        shows the shape: ``[1, 1, 1, 2]`` on an Assembler is three shard slots plus one
        somersloop slot holding up to two.

        So the number is exact, like the shard one, and for the same reason: it is the
        slot contents, not something derived from the boost. ``mPendingProductionBoost``
        does record the resulting MULTIPLIER (1.5 on an Assembler with one of two slots
        filled), which is a useful cross-check but a worse source -- inverting a
        multiplier to a count needs the building's base and step, and rounds.

        Mercer Spheres are counted separately and never added in. They share the WAT
        prefix and the same alien-artifact feel, and they do nothing for production.
        """
        stock = self.stock()
        free = float(stock.get(self.SLOOP_ITEM, 0.0))
        by_place: dict[str, float] = {}
        for place, source in (
            ("carried", self._inventories.get("player", {})),
            ("crates", self._inventories.get("storage", {})),
            ("depot", self.projection.get("depot", {})),
        ):
            held = float(source.get(self.SLOOP_ITEM, 0.0))
            if held:
                by_place[place] = held

        committed = 0.0
        holders: list[dict] = []
        for record in self._all_records():
            slotted = float((record.get("potential_slots") or {}).get(self.SLOOP_ITEM, 0.0))
            if not slotted:
                continue
            committed += slotted
            building = self.game.buildings.get(record.get("cls", ""))
            holders.append(
                {
                    "instance": record["instance"].rsplit(".", 1)[-1],
                    "cls": record.get("cls", "?"),
                    "name": building.name if building else record.get("cls", "?"),
                    "sloops": slotted,
                    #: What the plan model says that many slots are worth, so a caller can
                    #: check it against the boost the save reports.
                    "boost": building.boost_for(int(slotted)) if building else None,
                    "boost_in_save": record.get("production_boost"),
                }
            )
        holders.sort(key=lambda h: (-h["sloops"], h["cls"]))
        return {
            "item": self.SLOOP_ITEM,
            "free": free,
            "by_place": by_place,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "mercer_spheres": float(stock.get(self.MERCER_ITEM, 0.0)),
            #: False only on a projection too old to carry InventoryPotential at all, in
            #: which case `committed` is unknown rather than zero. Same flag, and the same
            #: reason, as the shard budget's.
            "committed_measured": any("potential_slots" in r for r in self._all_records()),
        }

    #: The Somersloop and Mercer Sphere item classes. Named here rather than resolved by
    #: display name because both are stable class ids and a name lookup would silently
    #: match nothing in a localised dump.
    SLOOP_ITEM: ClassVar[str] = "Desc_WAT1_C"
    MERCER_ITEM: ClassVar[str] = "Desc_WAT2_C"

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

    # ---- what the map placed, and what is left of it ----------------------

    @cached_property
    def collectibles(self) -> CollectibleTable | None:
        """The map's placement table, or ``None`` when it has not been generated."""
        return load_collectibles()

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
        table = self.collectibles
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
        table = self.collectibles
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
        table = self.collectibles
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

    # ---- MAM / hard drives ---------------------------------------------

    def _schematic_recipes(self, s: Schematic) -> list[Recipe]:
        """Recipes a schematic grants that the player does not already have.

        Follows BP_UnlockSchematic_C exactly one level, which is needed for
        Quartz Purification -> Silica Distilled. Deeper recursion is wrong: it drags
        in 23 customization schematics.
        """
        ids = list(s.unlocks_recipes)
        for chained in s.unlocks_schematics:
            child = self.game.schematics.get(chained)
            if child is not None:
                ids.extend(child.unlocks_recipes)
        out = []
        for rid in dict.fromkeys(ids):
            r = self.game.recipes.get(rid)
            if r is not None and rid not in self.available_recipe_ids:
                out.append(r)
        return out

    def dependencies_met(self, schematic_id: str) -> tuple[bool, list[str]]:
        """Gate on mSchematicDependencies, never on mTechTier.

        24 of the 109 alternates are blocked behind milestone schematics on the
        reference save. mTechTier is 0 for 71 of them, so it cannot be the gate.
        """
        s = self.game.schematics.get(schematic_id)
        if s is None:
            return False, [f"unknown schematic {schematic_id}"]
        missing = [d for d in s.dependencies if d not in self.purchased_schematic_ids]
        names = [self.game.schematics[m].name if m in self.game.schematics else m for m in missing]
        return (not missing), names

    @cached_property
    def hard_drive_offers(self) -> list[HardDriveOffer]:
        """The player's live pending choices, straight from the save."""
        out: list[HardDriveOffer] = []
        for entry in self.projection.get("research", {}).get("unclaimed_hard_drives", ()):
            options = []
            for sid in entry.get("options", ()):
                s = self.game.schematics.get(sid)
                if s is None:
                    options.append({"schematic": sid, "name": sid, "recipes": [], "slots": 0})
                    continue
                options.append(
                    {
                        "schematic": sid,
                        "name": s.name,
                        "recipes": self._schematic_recipes(s),
                        "slots": s.grants_inventory_slots,
                    }
                )
            executed = entry.get("rerolls_executed") or 0
            out.append(
                HardDriveOffer(
                    hard_drive_id=entry.get("hard_drive_id"),
                    rerolls_left=max(0, 1 - executed),
                    options=options,
                )
            )
        out.sort(key=lambda o: (o.hard_drive_id is None, o.hard_drive_id))
        return out

    # ---- where the player is -------------------------------------------

    @cached_property
    def players(self) -> list[dict]:
        """Player pawns with positions.

        Read from Char_Player_C, never BP_PlayerState_C: the state actor sits at the
        world origin, so using it would place every player at (0, 0).
        """
        return [p for p in self.projection.get("players", ()) if p.get("pos")]

    def player_position(self) -> tuple[float, float, float] | None:
        """Where the player is, in centimetres. None if the save has no pawn.

        With several pawns (co-op, or a stale disconnected one) the one holding a
        build gun wins, since that is the one actually being played.
        """
        if not self.players:
            return None
        armed = [p for p in self.players if p.get("has_build_gun")]
        pick = (armed or self.players)[0]
        x, y, z = pick["pos"]
        return (float(x), float(y), float(z))

    def spare_hard_drives(self) -> int:
        """Unanalysed drives on hand."""
        return int(self.stock().get("Desc_HardDrive_C", 0))

    @cached_property
    def _inventories(self) -> dict[str, dict[str, float]]:
        return self.projection.get("inventories", {}) or {}

    def stock(self) -> dict[str, float]:
        """What the player can actually spend: carried + storage + Dimensional Depot.

        Deliberately EXCLUDES machine buffers. Summing every stack in the world gives
        Water 5,556,375 and Fuel 1,048,762 -- pipe and machine contents in litres --
        so a build-cost check against that would say anything is affordable. Fluids
        are scaled to m3 here; the sidecar reports raw litres.
        """
        out: dict[str, float] = {}
        sources = [
            self._inventories.get("player", {}),
            self._inventories.get("storage", {}),
            self.projection.get("depot", {}),
        ]
        for source in sources:
            for item, amount in source.items():
                out[item] = out.get(item, 0.0) + amount
        for item in list(out):
            it = self.game.items.get(item)
            if it is not None and it.is_fluid:
                out[item] /= 1000.0
        return out

    def machine_buffers(self) -> dict[str, float]:
        """Material sitting in machine inputs, outputs and pipes. Not spendable."""
        out = dict(self._inventories.get("machine", {}))
        for item in list(out):
            it = self.game.items.get(item)
            if it is not None and it.is_fluid:
                out[item] /= 1000.0
        return out

    # ---- sites ---------------------------------------------------------

    def infra_points(self) -> list[tuple[float, float]]:
        """XY of every built production building, for distance-to-infrastructure."""
        return [(r["pos"][0], r["pos"][1]) for r in self._all_records() if r.get("pos")]

    def consumer_z(self, building_ids: tuple[str, ...] = ("Build_OilRefinery_C",)) -> float | None:
        """Mean altitude of a consumer class, for pipe head-lift sign.

        Defaults to refineries because that is what a fluid field usually feeds.
        """
        zs = [r["pos"][2] for r in self._all_records() if r.get("pos") and r["cls"] in building_ids]
        return sum(zs) / len(zs) if zs else None

    def sites(self, link_m: float = 300.0) -> list[dict]:
        """Cluster built production buildings into named-by-content sites."""
        records = [r for r in self._all_records() if r.get("pos")]
        points = [
            {"x": r["pos"][0], "y": r["pos"][1], "z": r["pos"][2], "kind": r["cls"], "rec": r}
            for r in records
        ]
        out = []
        for c in geo.cluster(points, link_m=link_m):
            counts: dict[str, int] = {}
            for m in c.members:
                counts[m["kind"]] = counts.get(m["kind"], 0) + 1
            cx, cy, cz = c.centroid
            out.append(
                {
                    "centroid": (round(cx), round(cy), round(cz)),
                    "grid": geo.grid_cell(cx, cy),
                    "direction": geo.direction_of(cx, cy),
                    "buildings": counts,
                    "count": c.size,
                    "diameter_m": round(c.diameter_m),
                }
            )
        out.sort(key=lambda s: -s["count"])
        return out


def load_state(
    game: GameData,
    path: str | None = None,
    world: str | None = None,
    prefer_manual: bool = False,
    refresh: bool = False,
) -> WorldState:
    return WorldState(
        projection=proj.load_projection(path, world, prefer_manual, refresh), game=game
    )
