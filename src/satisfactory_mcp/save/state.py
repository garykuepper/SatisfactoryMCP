"""Derived views over a save projection, joined against normalized game data."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

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
        out = dict(self.projection.get("building_counts", {}))
        for cls, n in (self.projection.get("lightweight_counts") or {}).items():
            out[cls] = out.get(cls, 0) + n
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

    def spare_hard_drives(self) -> int:
        """Unanalysed drives on hand (depot + inventories)."""
        depot = self.projection.get("depot", {}).get("Desc_HardDrive_C", 0)
        inv = self.projection.get("inventory_totals", {}).get("Desc_HardDrive_C", 0)
        return int(depot) + int(inv)

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
