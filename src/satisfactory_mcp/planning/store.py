"""Named, persisted plans.

**The request is stored, never the solution.** A solve depends on the world it was run
against -- the unlocked recipe set, which nodes are free, which buildings exist -- and all
three change as the game is played. A stored solution would keep answering with a world
that no longer exists, and would do it silently. Storing the arguments and re-solving on
recall always answers about the world as it is now.

That makes ``plan_id`` do real work. It already hashes the arguments together with the
save-derived solve inputs, so recording it at save time and comparing it on recall detects
exactly the case that matters: *the plan did not change, the world did*. Recall reports
the drift instead of pretending the old answer still holds.

Stored per world under ``saveIdentifier``, beside the factory labels and for the same
reason: a plan for one world is meaningless in another, and neither belongs in the cache
that ``cache_prune`` wipes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config

__all__ = ["SCHEMA", "Plan", "PlanStore"]

SCHEMA = 1

#: Argument names a plan captures. Everything build_scenario takes that changes the
#: answer -- deliberately NOT `limit` (presentation) or `save`/`world` (which save was
#: read, not what was asked for).
PLAN_ARGS = (
    "objective",
    "target_item",
    "sources",
    "exports",
    "export_minimums",
    "only_free_nodes",
    "allow_sinks",
    "clocks",
    "extractor_clocks",
    "machine_cost_mw",
    "exclude_recipes",
    "only_recipes",
    "water_extractors",
    "sloops",
    "belt_ipm",
    "pipe_m3min",
    "recycle_once",
    "supplied",
)


@dataclass
class Plan:
    name: str
    args: dict = field(default_factory=dict)
    notes: str = ""
    #: plan_id at the moment it was saved. A different id on recall means the WORLD
    #: moved, not the plan.
    plan_id: str = ""
    #: Optional factory label this plan is for, so a diff can be scoped to it.
    factory: str = ""
    created: str = ""

    def kwargs(self) -> dict:
        """Stored arguments, filtered to those a planning call still accepts."""
        return {k: v for k, v in self.args.items() if k in PLAN_ARGS}


@dataclass
class PlanStore:
    world_id: str
    session_name: str = ""
    plans: list[Plan] = field(default_factory=list)

    @staticmethod
    def path_for(world_id: str) -> Path:
        safe = "".join(c for c in world_id if c.isalnum() or c in "-_") or "world"
        return config.plans_dir() / f"{safe}.json"

    @classmethod
    def load(cls, world_id: str, session_name: str = "") -> PlanStore:
        path = cls.path_for(world_id)
        if not path.is_file():
            return cls(world_id=world_id, session_name=session_name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            world_id=raw.get("world_id", world_id),
            session_name=raw.get("session_name", session_name),
            plans=[Plan(**p) for p in raw.get("plans", ())],
        )

    def save(self) -> Path:
        path = self.path_for(self.world_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "world_id": self.world_id,
                    "session_name": self.session_name,
                    "plans": [asdict(p) for p in self.plans],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        return path

    def find(self, name: str) -> Plan | None:
        needle = name.strip().casefold()
        for plan in self.plans:
            if plan.name.casefold() == needle:
                return plan
        hits = [p for p in self.plans if needle in p.name.casefold()]
        return hits[0] if len(hits) == 1 else None

    def put(
        self,
        name: str,
        args: dict,
        plan_id: str,
        notes: str = "",
        factory: str = "",
        when: str = "",
    ) -> Plan:
        existing = self.find(name)
        if existing is None:
            existing = Plan(name=name.strip(), created=when)
            self.plans.append(existing)
        # Only what actually shapes the solve, and only non-defaults, so a stored plan
        # reads as the request that was made rather than a dump of every parameter.
        existing.args = {
            k: v for k, v in args.items() if k in PLAN_ARGS and v not in (None, [], {})
        }
        existing.plan_id = plan_id
        if notes:
            existing.notes = notes
        if factory:
            existing.factory = factory
        return existing

    def remove(self, name: str) -> bool:
        plan = self.find(name)
        if plan is None:
            return False
        self.plans.remove(plan)
        return True
