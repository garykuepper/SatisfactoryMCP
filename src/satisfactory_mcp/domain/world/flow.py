"""Which way the fluid goes, inferred from the plumbing rather than read off a pipe.

The game does not store a pipe's flow direction and this module does not claim it does. What
it does claim is that the direction is usually *implied*, and that refusing to say so on a
pipe hanging off a water extractor reads as obtuseness rather than honesty.

**What the save actually hands over** -- measured in ``extract._pipes``, not assumed:

* Every fluid connection is a component carrying ``mConnectedComponent``, folded into
  ``graph["material"]``. On the reference save that is **1,560 directed couplings, total and
  symmetric, none crossing a network id**. The plumbing graph is not inferred; it is read.
* The component's NAME types the port at a machine. ``PipeInputFactory`` is a consumer,
  ``PipeOutputFactory`` a producer, ``ConnectionAny0``/``1`` an explicit "either way" on a
  buffer -- the game's own Consumer/Producer/Any surviving into the file. Where a building
  has one port and a generic ``FGPipeConnectionFactory``, the building settles it: an
  extractor cannot consume its own resource and a generator cannot produce its fuel.
* ``PipelineConnection0`` is the spline's first point and ``PipelineConnection1`` its last,
  so an answer about connections converts into an answer about the drawn line.

So the only guessing left is *propagation*, and the two models below are both conservative:
each declines wherever more than one ordering is consistent.

**Model 1, the cut.** Fluid is conserved, so on an edge whose removal splits the network in
two, everything crossing goes one way, and which way is fixed by which side has the
producers. Formally: if side A holds a producer and no consumer while side B holds a
consumer, flow is A to B. Both halves matter -- the "and B has a consumer" clause is what
stops a full, stagnant dead-end branch being drawn as if it ran. An edge inside a cycle
splits nothing, so it is left alone, which is the honest answer: with two routes available
the game really can send fluid down either.

**Model 2, the one-way device.** A pump and a valve move fluid one way by construction, from
``Connection0`` to ``Connection1``. That orientation is measured twice over, never assumed
from the name. Against model 1: of the reference save's 23 pumps and valves, every one whose
neighbours model 1 had already settled reads ``Connection0`` as the inlet -- **13 of 13, no
counterexample**. And against gravity, which knows nothing about either model: this world
holds an unfinished 696 m crude-oil lift, 45 pipes and 9 Mk2 pumps climbing 240 m with both
ends not yet plumbed to anything, and a pump pushes UP. Reading ``Connection0`` as the inlet
sends the oil **191 m uphill, 22 pipes climbing against 3 descending**. The other reading is
the same number with the sign flipped, i.e. nine pumps arranged to help oil fall.

**Then conservation propagates both**, to a fixpoint: at a junction with no port of its own,
if every settled edge but one points inward, the last must point out. That step carries a
guard, added because a hold-out caught it wrong without one -- an edge is only settled toward
a side holding something able to take the fluid, a consumer or a pump's intake, so the rule
can never invent flow into a stub that consumes nothing.

**How well it does, on the reference save's 503 pipes.** 365 resolved, 138 left unknown. What
stays unknown stays unknown for a reason worth saying out loud: a trunk with producers and
consumers on both sides genuinely has no fixed direction without the RATES, and a pipe in a
loop has two legal answers.

**And it is checked four ways, none of which is the inference marking its own homework:**

* The two models are independent, so where both speak they check each other: **118 pipes,
  118 agreements, no disagreement.**
* A conservation audit over the finished answer: **293 nodes, none where fluid appears from
  nowhere or vanishes into nothing.**
* Following the inferred flow to its end from each of the 365: **not one arrives at a
  producer going downstream or leaves a consumer going upstream**, which a single flipped
  sign would cause. 297 of them trace upstream to an actual producer.
* Water extractor to coal generator is known a priori. Of the water pipes touching either,
  **39 are oriented correctly and 0 point into an extractor or out of a generator.**
"""

from __future__ import annotations

from collections import defaultdict

from ...core.saveio import rows as saverows

__all__ = ["FORWARD", "REVERSE", "UNKNOWN", "pipe_flow"]

#: Along the segment's own point order, against it, and "we will not say".
FORWARD = "forward"
REVERSE = "reverse"
UNKNOWN = "unknown"

#: The connector role names that belong to FLUID plumbing. Listed rather than pattern-matched
#: for the same reason ``PIPE_CLASSES`` is: ``PipeHyperConnection0`` is a hypertube, which
#: moves a player and no fluid, and a substring match on ``Pipe`` would drag it in. The belt
#: side of ``graph["material"]`` spells its roles ``Input*`` / ``Output*`` / ``ConveyorAny*``
#: and so cannot collide with any of these.
_FLUID_ROLES = frozenset(
    {
        "PipelineConnection0",
        "PipelineConnection1",
        "FGPipeConnectionFactory",
        "PipeInputFactory",
        "PipeOutputFactory",
        "ConnectionAny0",
        "ConnectionAny1",
        "Connection0",
        "Connection1",
        "Connection2",
        "Connection3",
    }
)

#: A junction and a buffer are ONE volume of fluid: what arrives at any port can leave by any
#: other, so their ports collapse into a single node. A pump and a valve emphatically are not,
#: which is the whole of their contribution below.
_BODIES = (
    "Build_PipelineJunction_Cross_C",
    "Build_IndustrialTank_C",
    "Build_PipeStorageTank_C",
)

#: One-way by construction, inlet first. See the module docstring for the measurement.
_ONE_WAY = (
    "Build_PipelinePump_C",
    "Build_PipelinePumpMk2_C",
    "Build_Valve_C",
)

#: Basis labels, most local evidence first -- a popup should say which of these it is.
BASIS_PORT = "machine port"
BASIS_DEVICE = "pump"
BASIS_NETWORK = "propagated"
BASIS_NONE = "unresolved"


def _class_of(short: str) -> str:
    """``Build_OilRefinery_C_2147245036`` -> ``Build_OilRefinery_C``."""
    head, sep, _tail = short.rpartition("_C_")
    return head + "_C" if sep else short


class _Union:
    """Union-find over ``(actorIndex, roleIndex)``, which is what a coupling joins."""

    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, x: tuple[int, int]) -> tuple[int, int]:
        parent = self._parent
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _build(projection: dict) -> tuple[list, list, list, dict, set]:
    """The plumbing as (pipe edges, one-way edges, terminals, adjacency, buffer nodes).

    A node is a place fluid can be: a coupling between two connectors, or a junction or
    buffer body whose ports have been merged into one.

    The buffer nodes come out separately because a tank is a peculiar thing: it holds fluid,
    so it can supply and it can accept, but it PRODUCES nothing and CONSUMES nothing, so it
    can never orient a pipe. It is not a terminal, and it is not a dead end either.
    """
    graph = projection.get("graph") or {}
    actors = list(graph.get("actors") or ())
    roles = list(graph.get("roles") or ())
    fluid_role = {i for i, name in enumerate(roles) if name in _FLUID_ROLES}
    role_name = {i: name for i, name in enumerate(roles)}

    joins = _Union()
    ports_of: dict[int, set[int]] = defaultdict(set)
    for edge in graph.get("material") or ():
        if not isinstance(edge, (list, tuple)) or len(edge) < 4:
            continue
        a, b, ra, rb = edge[0], edge[1], edge[2], edge[3]
        if ra not in fluid_role or rb not in fluid_role:
            continue
        joins.union((a, ra), (b, rb))
        ports_of[a].add(ra)
        ports_of[b].add(rb)

    for actor, ports in ports_of.items():
        if 0 <= actor < len(actors) and _class_of(actors[actor]) in _BODIES:
            first = min(ports)
            for role in ports:
                joins.union((actor, first), (actor, role))

    # Decoded once and held, because the segments are walked twice here: for the actors that
    # ARE pipes, and then for the node pair each of those pipes joins.
    segments = list(saverows.iter_pipe_segments(projection))
    pipe_actors = {seg.actor_index for seg in segments if seg.actor_index >= 0}

    # An extractor cannot consume what it pulls out of the ground and a generator cannot
    # produce its fuel, so the projection's own sorting of the world settles the buildings
    # whose single port carries the generic name.
    producers = {r.get("cls") for r in projection.get("extractors") or () if isinstance(r, dict)}
    consumers = {r.get("cls") for r in projection.get("generators") or () if isinstance(r, dict)}

    devices: list[tuple[tuple, tuple]] = []
    terminals: list[tuple[tuple, str, int]] = []

    role_ix = {name: i for i, name in enumerate(roles)}
    stores: set = set()
    for actor, ports in ports_of.items():
        cls = _class_of(actors[actor]) if 0 <= actor < len(actors) else ""
        if actor in pipe_actors:
            continue  # emitted below, in the segments' own order
        if cls in _BODIES:
            # One merged node, no port of its own and no direction to give. A tank is also a
            # STORE, which the guard below needs; a junction is not, and holds nothing.
            if cls != "Build_PipelineJunction_Cross_C":
                stores.add(joins.find((actor, min(ports))))
            continue
        if cls in _ONE_WAY:
            c0, c1 = role_ix.get("Connection0"), role_ix.get("Connection1")
            if c0 in ports and c1 in ports:
                devices.append((joins.find((actor, c0)), joins.find((actor, c1))))
            continue
        for role in ports:
            name = role_name.get(role, "")
            if name.startswith("PipeInputFactory"):
                kind = "sink"
            elif name.startswith("PipeOutputFactory"):
                kind = "source"
            elif name.startswith("ConnectionAny"):
                kind = "any"
            elif cls in producers:
                kind = "source"
            elif cls in consumers:
                kind = "sink"
            else:
                kind = "any"
            if kind != "any":
                terminals.append((joins.find((actor, role)), kind, actor))

    # One entry per ROW of the table, not per row that decoded: ``/api/pipes`` joins to this
    # list by a segment's position, so a torn row owes it a slot that says "no idea" rather
    # than shifting every pipe after it up by one. `saverows.iter_pipe_segments` reports each
    # segment's own ordinal for exactly this, and `pipe_segment_count` sizes the list.
    c0, c1 = role_ix.get("PipelineConnection0"), role_ix.get("PipelineConnection1")
    pipes: list[tuple[tuple | None, tuple | None]] = [(None, None)] * saverows.pipe_segment_count(
        projection
    )
    for seg in segments:
        actor = seg.actor_index
        ports = ports_of.get(actor, ()) if actor >= 0 else ()
        pipes[seg.index] = (
            joins.find((actor, c0)) if c0 in ports else None,
            joins.find((actor, c1)) if c1 in ports else None,
        )

    adjacency: dict[tuple, list] = defaultdict(list)
    for i, (n0, n1) in enumerate(pipes):
        if n0 is None or n1 is None:
            continue
        adjacency[n0].append((n1, ("pipe", i)))
        adjacency[n1].append((n0, ("pipe", i)))
    for j, (n0, n1) in enumerate(devices):
        adjacency[n0].append((n1, ("device", j)))
        adjacency[n1].append((n0, ("device", j)))
    return pipes, devices, terminals, adjacency, stores


def _reach(adjacency: dict, start, without) -> set:
    """Everything fluid could get to from ``start`` without using edge ``without``."""
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for peer, tag in adjacency.get(node, ()):
            if tag == without or peer in seen:
                continue
            seen.add(peer)
            stack.append(peer)
    return seen


def _solve(
    pipes,
    devices,
    terminals,
    adjacency,
    stores=frozenset(),
    *,
    drop_actor=None,
    cuts=True,
    one_way=True,
):
    """Direction per pipe as +1 (points[0] to points[-1]), -1 (the reverse) or 0."""
    source: dict = defaultdict(int)
    sink: dict = defaultdict(int)
    for node, kind, actor in terminals:
        if actor == drop_actor:
            continue
        (source if kind == "source" else sink)[node] += 1
    total_source, total_sink = sum(source.values()), sum(sink.values())

    settled = [0] * len(pipes)
    if cuts:
        for i, (n0, n1) in enumerate(pipes):
            if n0 is None or n1 is None or n0 == n1:
                continue
            near = _reach(adjacency, n0, ("pipe", i))
            if n1 in near:
                continue  # a cycle: both orderings are consistent, so neither is claimed
            near_source = sum(source[n] for n in near)
            near_sink = sum(sink[n] for n in near)
            if near_source and not near_sink and total_sink - near_sink:
                settled[i] = 1
            elif near_sink and not near_source and total_source - near_source:
                settled[i] = -1

    if not one_way:
        devices = ()

    # Conservation, to a fixpoint. At a node that is nothing but plumbing, what arrives has
    # to leave, so a single unsettled edge among same-facing settled ones is forced.
    incident: dict[tuple, list] = defaultdict(list)
    for i, (n0, n1) in enumerate(pipes):
        if n0 is None or n1 is None:
            continue
        incident[n0].append(("pipe", i, True))
        incident[n1].append(("pipe", i, False))
    for j, (n0, n1) in enumerate(devices):
        incident[n0].append(("device", j, True))
        incident[n1].append(("device", j, False))
    inlets = {n0 for n0, _n1 in devices}
    outlets = {n1 for _n0, n1 in devices}

    changed = True
    while changed:
        changed = False
        for node, rows in incident.items():
            if source[node] or sink[node]:
                continue  # it has a port of its own, so nothing here is forced
            arriving = leaving = 0
            open_edge = None
            for kind, index, at_first in rows:
                if kind == "device":
                    # The device draws fluid out of its inlet node and into its outlet node.
                    leaving += 1 if at_first else 0
                    arriving += 0 if at_first else 1
                elif settled[index] == 0:
                    if open_edge is not None:
                        open_edge = False
                        break
                    open_edge = (index, at_first)
                elif (settled[index] == 1) == at_first:
                    leaving += 1
                else:
                    arriving += 1
            if not open_edge:
                continue
            index, at_first = open_edge
            if arriving and not leaving:
                direction = 1 if at_first else -1
            elif leaving and not arriving:
                direction = -1 if at_first else 1
            else:
                continue
            # The guard. Settling this edge says fluid crosses it, so the side it would be
            # sent to has to hold something able to take it -- otherwise the far end is a
            # stub, nothing flows, and the honest answer is still nothing.
            #
            # Two things count besides a machine port. A PUMP, which pulls at its inlet and
            # pushes at its outlet, and is why this world's unfinished 696 m oil lift -- 45
            # pipes and 9 pumps climbing 240 m, neither end plumbed to anything -- still gets
            # an answer. And a TANK, which holds fluid, so it can supply and it can accept
            # even though it produces and consumes nothing. What is left for the guard to
            # veto is the case it was written for: a bare stub of pipe ending in nothing.
            n0, n1 = pipes[index]
            far = n1 if at_first else n0
            outbound = (direction == 1) == at_first
            wanted, wanted_ports = (sink, inlets) if outbound else (source, outlets)
            beyond = _reach(adjacency, far, ("pipe", index))
            if (
                not any(wanted[n] for n in beyond)
                and not (beyond & wanted_ports)
                and not (beyond & stores)
            ):
                continue
            settled[index] = direction
            changed = True
    return settled


def pipe_flow(projection: dict) -> list[dict]:
    """One ``{"direction", "basis"}`` per pipe segment, in the segments' own order.

    ``direction`` is ``forward`` along the segment's stored points, ``reverse`` against them,
    or ``unknown``. ``basis`` names the evidence, from the most local outwards: a typed
    ``machine port`` at one end of this very pipe, a one-way ``pump`` at one end of it, or
    ``propagated`` when only the shape of the wider network settles it.
    """
    pipes, devices, terminals, adjacency, stores = _build(projection)
    settled = _solve(pipes, devices, terminals, adjacency, stores)

    port_nodes = {node for node, _kind, _actor in terminals}
    device_nodes = {node for edge in devices for node in edge}
    out = []
    for i, (n0, n1) in enumerate(pipes):
        if not settled[i]:
            out.append({"direction": UNKNOWN, "basis": BASIS_NONE})
            continue
        if n0 in port_nodes or n1 in port_nodes:
            basis = BASIS_PORT
        elif n0 in device_nodes or n1 in device_nodes:
            basis = BASIS_DEVICE
        else:
            basis = BASIS_NETWORK
        out.append({"direction": FORWARD if settled[i] == 1 else REVERSE, "basis": basis})
    return out
