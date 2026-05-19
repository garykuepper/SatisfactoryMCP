"""Derived views over a save projection, joined against normalized game data."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

from ..docs.model import GameData, Recipe, Schematic
from ..spatial import geo
from . import projection as proj

__all__ = ["HardDriveOffer", "WorldState"]


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
        from ..docs.constants import BUILDING_CLASS_ALIASES

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
        """Nameplate generation and machine draw.

        Nameplate, not actual: fuel supply and uptime are not modelled here. Paused
        buildings are excluded from both sides.
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
        for m in self.projection.get("machines", ()):
            if m.get("paused"):
                continue
            r = self.game.recipes.get(m.get("recipe") or "")
            clock = m.get("clock") or 1.0
            if r is not None:
                draw += self.game.recipe_power_mw(r, clock)
            else:
                b = self.game.buildings.get(m["cls"])
                if b:
                    draw += b.power_at(clock)
        for e in self.projection.get("extractors", ()):
            if e.get("paused"):
                continue
            b = self.game.buildings.get(e["cls"])
            if b:
                draw += b.power_at(e.get("clock") or 1.0)

        return {
            "generation_mw": total_mw,
            "draw_mw": draw,
            "headroom_mw": total_mw - draw,
            "by_generator": gen,
            "unmodellable": sorted(set(variable)),
            "paused_count": len(self.paused),
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
        from ..docs.constants import POTENTIAL_SHARD_SLOTS, shards_for_clock

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
