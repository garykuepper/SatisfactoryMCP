"""The grid: what it can make, what it draws, and how much of that is real."""

from __future__ import annotations

from dataclasses import dataclass

from ...core.gamedata.model import GameData

__all__ = ["PowerLedger"]


@dataclass
class PowerLedger:
    """Generation and draw over one save.

    ``paused_count`` is the build census's count, passed in because this class only needs
    the total to report it.
    """

    projection: dict
    game: GameData
    paused_count: int = 0

    def power_report(self) -> dict:
        """Generation capacity, and draw both nameplate and measured.

        Uptime is the projection's 300 s productivity monitor, carried by **524 of 570**
        records on the reference save. Both draws are reported because they answer different
        questions:

        * ``headroom_mw`` (nameplate) is the **safe** figure -- what is free if everything
          currently built ran at once. Energising a block can un-starve idle machines
          downstream, so this is the one not to exceed if you cannot watch it.
        * ``measured_headroom_mw`` is the **current** figure, weighted by how much of the
          factory is actually running.

        They are far apart: on the reference save nameplate draw is **6,901 MW** against a
        measured **2,389 MW**, so 649 MW of headroom nameplate against roughly
        **5,161 MW** actual.

        A machine with no productivity monitor is charged at full nameplate on both sides:
        unknown utilisation must not read as idle. Generators are capacity either way,
        since they burn to meet demand rather than at a rate of their own. Paused buildings
        are excluded from both sides.
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
                # No monitor is NOT evidence of idleness: charged in full, so an unreadable
                # machine can only make the measured figure conservative.
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
            "measured_draw_mw": measured,
            "measured_headroom_mw": total_mw - measured,
            "monitored": monitored,
            "unmonitored": unmonitored,
            "utilisation": (measured / draw) if draw else 1.0,
            "by_generator": gen,
            "unmodellable": sorted(set(variable)),
            "paused_count": self.paused_count,
        }
