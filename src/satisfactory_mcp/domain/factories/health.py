"""Why a machine is not running, from what the save actually records.

Every buildable that manufactures carries a productivity monitor -- a roughly 300-second
window and the seconds it spent producing inside it -- and that ratio is the only measured
number in this whole MCP; everything else is nameplate. Uptime alone says a machine is
stopped and never why, so the input and output buffers settle it::

    an extractor with no node      -> DEAD NODE, it can never produce
    a required ingredient at zero  -> STARVED of that item, by name
    an output item at a full stack -> BLOCKED, its consumer is not keeping up
    both                           -> BLOCKED wins; a full output stops it regardless

Two neighbouring save fields look usable and are not. ``mCurrentProductivityMeasurement*``
is a partial window still filling, so mixing it with the last complete one compares a
3-minute sample against a 5-minute one; ``mTimeSinceStartStopProducing`` carries FLT_MAX
on roughly half of all carriers as a "never flipped" sentinel, which is not a duration and
poisons any statistic it enters.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ...core.gamedata.constants import STACK_SIZE
from ...core.gamedata.model import GameData

__all__ = ["OK", "STATES", "MachineHealth", "assess", "summarise"]

#: Uptime at or above this counts as running flat out.
SATURATED = 0.999

#: Below this a machine is treated as stopped rather than merely slow.
STOPPED = 0.001

#: A stack this full is treated as backed up. Not 100%: a machine that has just been
#: unblocked sits a few items short, and calling that healthy hides a real bottleneck.
FULL_FRACTION = 0.95

#: States a machine can be in, worst first. Order is the report order.
STATES = (
    "paused",
    "dead node",
    "no recipe",
    "blocked",
    "starved",
    "stalled",
    "intermittent",
    "saturated",
    "unmonitored",
)

#: States that need no action.
OK = frozenset({"saturated", "unmonitored"})


@dataclass
class MachineHealth:
    instance: str
    building: str
    recipe: str
    state: str
    uptime: float | None
    #: What is missing (starved) or backed up (blocked), as item names.
    cause: tuple[str, ...] = ()
    clock: float = 1.0

    @property
    def needs_attention(self) -> bool:
        return self.state not in OK


@dataclass
class HealthReport:
    name: str
    machines: list[MachineHealth] = field(default_factory=list)
    by_state: Counter = field(default_factory=Counter)
    #: item name -> how many machines are blocked on it / starved of it
    blocked_on: Counter = field(default_factory=Counter)
    starved_of: Counter = field(default_factory=Counter)

    @property
    def monitored(self) -> list[MachineHealth]:
        return [m for m in self.machines if m.uptime is not None]

    @property
    def mean_uptime(self) -> float | None:
        seen = [m.uptime for m in self.monitored]
        return sum(seen) / len(seen) if seen else None

    def worst(self, limit: int = 10) -> list[MachineHealth]:
        order = {s: i for i, s in enumerate(STATES)}
        return sorted(
            (m for m in self.machines if m.needs_attention),
            key=lambda m: (order.get(m.state, 99), m.uptime if m.uptime is not None else 0.0),
        )[:limit]


def _stack_limit(game: GameData, item_cls: str) -> int:
    item = game.items.get(item_cls)
    return STACK_SIZE.get(getattr(item, "stack_size", ""), 0)


def _buffer_state(game: GameData, buffers: dict, recipe) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns (items backed up in the output, ingredients missing from the input).

    Starvation is **a required ingredient at zero**, not an empty input: an assembler on
    Black Powder holding 100 Sulfur and no Coal is starved, and naming the missing
    ingredient is the whole value of the report. An ABSENT intake inventory is not an
    empty one either -- a miner draws from its node and has no InputInventory at all, so
    it yields no starvation evidence rather than a false positive.
    """
    backed: list[str] = []
    out = (buffers.get("out") or {}).get("items") or {}
    for item_cls, count in out.items():
        limit = _stack_limit(game, item_cls)
        if limit and count >= limit * FULL_FRACTION:
            backed.append(game.item_name(item_cls))

    intake = buffers.get("in")
    if intake is not None and recipe is not None:
        held = intake.get("items") or {}
        missing = [game.item_name(f.item) for f in recipe.ingredients if not held.get(f.item)]
        return tuple(sorted(backed)), tuple(sorted(missing))

    # No recipe to check against: a generator is starved when its fuel buffer is empty.
    fuel = buffers.get("fuel")
    if fuel is not None:
        empty = not any(v > 0 for v in (fuel.get("items") or {}).values())
        return tuple(sorted(backed)), (("(no fuel)",) if empty else ())
    return tuple(sorted(backed)), ()


def assess(
    name: str,
    machines: list[str],
    game: GameData,
    projection: dict,
) -> HealthReport:
    """Classify every machine in a set. Extractors and generators are included when they
    carry a monitor, since a starved coal plant is exactly what one wants to see."""
    wanted = set(machines)
    report = HealthReport(name=name)

    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            short = record["instance"].rsplit(".", 1)[-1]
            if short not in wanted:
                continue
            recipe = game.recipes.get(record.get("recipe") or "")
            live = record.get("uptime") or {}
            window = live.get("window_s") or 0.0
            # Each record's OWN window, never a hard-coded 300: the game closes the window
            # on a tick boundary, so it reads 300.00, 300.01 or 300.02 across one save. An
            # absent produce_s is a real zero, since UE omits properties equal to their
            # default -- a monitored idle machine is 0.0 uptime, not unmonitored.
            uptime = (live.get("produce_s", 0.0) / window) if window else None
            backed, missing = _buffer_state(game, record.get("buffers") or {}, recipe)

            if record.get("paused"):
                state, cause = "paused", ()
            elif key == "extractors" and not record.get("node"):
                # mExtractableResource ABSENT, not merely unresolvable: the miner is bound
                # to nothing, which happens when a game update removes a resource node
                # under it. A water pump's node IS set but points at an FGWaterVolume that
                # is not a purity-table key, and that one works fine.
                state, cause = "dead node", ("no resource node",)
            elif key == "machines" and not record.get("recipe"):
                state, cause = "no recipe", ()
            elif uptime is None:
                state, cause = "unmonitored", ()
            elif uptime >= SATURATED:
                state, cause = "saturated", ()
            elif uptime > STOPPED:
                # Running, but not flat out: the same evidence, reported without calling
                # the machine stopped.
                state = "intermittent"
                cause = backed or missing
            elif backed:
                # Checked BEFORE starvation: a blocked machine's input backs up too, so
                # testing the input first would misread it as merely well-fed.
                state, cause = "blocked", backed
            elif missing:
                state, cause = "starved", missing
            else:
                # Has input, output not full, still not running: power, or a monitor that
                # has not caught up.
                state, cause = "stalled", ()

            entry = MachineHealth(
                instance=short,
                building=record.get("cls", "?"),
                recipe=recipe.name if recipe else "",
                state=state,
                uptime=uptime,
                cause=tuple(cause),
                clock=float(record.get("clock") or 1.0),
            )
            report.machines.append(entry)
            report.by_state[state] += 1
            if state == "blocked":
                for item in entry.cause:
                    report.blocked_on[item] += 1
            elif state == "starved" and recipe is not None:
                for flow in recipe.ingredients:
                    report.starved_of[game.item_name(flow.item)] += 1

    return report


def summarise(report: HealthReport) -> str:
    """One line, states worst-first."""
    parts = [f"{report.by_state[s]} {s}" for s in STATES if report.by_state[s]]
    mean = report.mean_uptime
    head = f"{len(report.machines)} machines"
    if mean is not None:
        head += f", mean uptime {mean:.0%}"
    return head + (" -- " + ", ".join(parts) if parts else "")
