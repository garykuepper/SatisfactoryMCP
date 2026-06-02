"""Milestones by tier, and what the Space Elevator is still owed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from ...core.gamedata.model import GameData

if TYPE_CHECKING:
    from .unlocks import UnlockSet

__all__ = ["PhaseLedger"]


@dataclass
class PhaseLedger:
    """Where the run stands: tiers completed and phase deliveries outstanding."""

    projection: dict
    game: GameData
    unlocks: UnlockSet

    def progression(self) -> dict:
        p = self.projection.get("progression", {})
        tiers: dict[int, list[str]] = {}
        for sid in self.unlocks.purchased_schematic_ids:
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
            "purchased_schematics": len(self.unlocks.purchased_schematic_ids),
            "available_recipes": len(self.unlocks.available_recipe_ids),
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
