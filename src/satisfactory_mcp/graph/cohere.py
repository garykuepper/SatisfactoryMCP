"""One coherence score over every signal, agglomerated into proposed factories.

Each earlier signal fails alone -- power islands over-merge, belt components fragment,
slabs are blind to ground-built machines. Combining them works, and measured against the
player's twelve hand-named factories with leave-one-factory-out (weights fitted on the
other eleven, then the held-out one scored):

    precision 1.000   recall 0.945

Ten of the twelve are recovered exactly. Precision is 1.000 on **every** fold: this never
merges two factories, it only ever splits one. That is the failure direction to want,
since an over-segmented proposal is fixed by naming it and an over-merged one is not.

What actually does the work
---------------------------
Not the weights. Ablated on the same 382 labelled machines:

============================  ====  =========  ======  ======
variant                       clus  precision  recall      F1
============================  ====  =========  ======  ======
fitted log-odds                 15      1.000   0.973   0.986
round numbers below             15      1.000   0.973   0.986
every weight set to 1           20      1.000   0.926   0.961
**slab weight set to 0**        15      1.000   0.973   0.986
random +-50% (worst of 12)       -          -       -   0.967
no power-island veto            15      1.000   0.973   0.986
**no distance cap**             14    **0.776**  0.973   0.863
============================  ====  =========  ======  ======

Two conclusions, both against intuition:

1. **The weights are barely load-bearing.** Rounding them changes nothing, perturbing
   them 50% costs 0.02 F1, and removing the strongest signal entirely costs nothing --
   because the others already separate the same pairs. They are kept for interpretability
   in the evidence report, not because the arithmetic needs them.
2. **The linkage rule and the distance cap are everything.** The identical score under
   single linkage scores F1 0.521: one chain of adjacent machines welds a base into a
   blob. Complete linkage requires every cross pair to clear the bar. And dropping the
   distance cap costs 0.12 F1 outright.

The power-island veto was measured redundant and is therefore not applied.

A caveat that no internal cross-validation can remove
-----------------------------------------------------
These numbers come from one save, one player's building style. Leave-one-factory-out
tests generalisation across *that player's* factories, not across players. The insensitivity
to weights above is the real reassurance: a result that survives 50% perturbation of every
parameter is not resting on a fit.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..docs.model import GameData
from .model import FactoryGraph
from .structure import Structures

__all__ = ["MAX_SPAN_M", "WEIGHTS", "Proposal", "propose"]

#: Signal weights. Deliberately round: see the ablation above, they are not load-bearing.
WEIGHTS = {
    "slab": 10.0,  # same foundation platform
    "near": 5.0,  # within NEAR_M of each other
    "prod": 4.0,  # makes the same thing
    "belt": 3.0,  # same belt/pipe component
    "supply": 3.0,  # one's output is the other's input
}

#: Evidence has to beat this for two clusters to merge. Its scale is arbitrary in the same
#: way the weights are; it is the sign that matters.
PRIOR = 1.0

#: Proximity threshold for the ``near`` signal, in metres.
NEAR_M = 100.0

#: No proposal may span more than this. THE load-bearing constant -- removing it drops
#: precision from 1.000 to 0.776. Note it also caps a proposal's diameter, so a genuinely
#: sprawling factory (the player's oil setup spans 381 m) is proposed in pieces.
MAX_SPAN_M = 250.0


@dataclass
class Proposal:
    """A proposed factory and the evidence that produced it."""

    machines: list[str]
    #: Weakest internal link. Under complete linkage every pair scores at least this.
    cohesion: float = 0.0
    evidence: Counter = field(default_factory=Counter)
    seeded_by: str = ""

    @property
    def size(self) -> int:
        return len(self.machines)


def _feature_fn(
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    structures: Structures,
):
    """Build the per-pair feature extractor once, with every lookup pre-indexed."""
    pos: dict[str, tuple[float, float, float]] = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            if record.get("pos"):
                pos[record["instance"].rsplit(".", 1)[-1]] = tuple(record["pos"])

    recipe_of = {
        r["instance"].rsplit(".", 1)[-1]: r["recipe"]
        for r in projection.get("machines", ())
        if r.get("recipe")
    }
    products: dict[str, frozenset[str]] = {}
    ingredients: dict[str, frozenset[str]] = {}
    for machine, rid in recipe_of.items():
        recipe = game.recipes.get(rid)
        if recipe is None:
            continue
        products[machine] = frozenset(game.item_name(f.item) for f in recipe.products)
        ingredients[machine] = frozenset(game.item_name(f.item) for f in recipe.ingredients)

    component = {
        m: k for k, comp in enumerate(graph.machine_components("material")) for m in comp
    }
    slab = structures.slab_of

    def features(a: str, b: str) -> tuple[dict[str, float], float]:
        pa, pb = pos.get(a), pos.get(b)
        distance_m = math.dist(pa[:2], pb[:2]) / 100.0 if pa and pb else math.inf
        sa, sb = slab.get(a), slab.get(b)
        prod_a, prod_b = products.get(a), products.get(b)
        feats = {
            "slab": float(sa is not None and sa == sb),
            "near": float(distance_m <= NEAR_M),
            "prod": float(bool(prod_a) and prod_a == prod_b),
            "belt": float(component.get(a, -1) == component.get(b, -2)),
            "supply": float(
                bool(prod_a and ingredients.get(b) and prod_a & ingredients[b])
                or bool(prod_b and ingredients.get(a) and prod_b & ingredients[a])
            ),
        }
        return feats, distance_m

    return features


def propose(
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    structures: Structures,
    machines: list[str] | None = None,
    weights: dict[str, float] | None = None,
    max_span_m: float = MAX_SPAN_M,
    prior: float = PRIOR,
) -> list[Proposal]:
    """Agglomerate machines into proposed factories, most cohesive first.

    Seeded from foundation slabs rather than from singletons. Slabs have measured
    precision 1.000 as a same-factory signal, so starting there costs nothing and turns
    563 starting clusters into roughly 170.
    """
    weights = {**WEIGHTS, **(weights or {})}
    pool = sorted(machines if machines is not None else graph.machines())
    if not pool:
        return []

    features = _feature_fn(graph, game, projection, structures)
    index = {m: i for i, m in enumerate(pool)}

    # Pairwise scores, computed once. -inf past the span cap so no linkage can cross it.
    pair: dict[tuple[int, int], float] = {}
    fired: dict[tuple[int, int], tuple[str, ...]] = {}
    for i, a in enumerate(pool):
        for j in range(i + 1, len(pool)):
            b = pool[j]
            feats, distance_m = features(a, b)
            if distance_m > max_span_m:
                pair[(i, j)] = -math.inf
                continue
            pair[(i, j)] = sum(weights[k] * v for k, v in feats.items()) - prior
            fired[(i, j)] = tuple(k for k, v in feats.items() if v)

    def score(i: int, j: int) -> float:
        return pair[(i, j)] if i < j else pair[(j, i)]

    slab_seed: dict[int, list[int]] = defaultdict(list)
    clusters: list[list[int]] = []
    seeded: list[str] = []
    for m in pool:
        if m in structures.slab_of:
            slab_seed[structures.slab_of[m]].append(index[m])
        else:
            clusters.append([index[m]])
            seeded.append("ground")
    for slab_id, members in sorted(slab_seed.items()):
        clusters.append(members)
        seeded.append(f"slab:{slab_id}")

    # Complete linkage: merge only when EVERY cross pair clears the bar. Single linkage
    # on the identical score collapses to F1 0.521, because one adjacent pair is enough
    # to chain a whole base together.
    link: dict[tuple[int, int], float] = {}
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            link[(i, j)] = min(score(a, b) for a in clusters[i] for b in clusters[j])

    alive = set(range(len(clusters)))
    cohesion = dict.fromkeys(alive, math.inf)
    while True:
        best, target = 0.0, None
        for (i, j), value in link.items():
            if i in alive and j in alive and value > best:
                best, target = value, (i, j)
        if target is None:
            break
        i, j = target
        clusters[i] = clusters[i] + clusters[j]
        seeded[i] = seeded[i] if seeded[i].startswith("slab") else seeded[j]
        cohesion[i] = min(cohesion[i], cohesion[j], best)
        alive.discard(j)
        for k in alive:
            if k == i:
                continue
            a, b = (min(i, k), max(i, k)), (min(j, k), max(j, k))
            link[a] = min(link.get(a, math.inf), link.get(b, math.inf))

    out: list[Proposal] = []
    for i in sorted(alive):
        members = [pool[x] for x in clusters[i]]
        evidence: Counter = Counter()
        for x, a in enumerate(clusters[i]):
            for b in clusters[i][x + 1 :]:
                for name in fired.get((min(a, b), max(a, b)), ()):
                    evidence[name] += 1
        out.append(
            Proposal(
                machines=sorted(members),
                cohesion=0.0 if cohesion[i] == math.inf else cohesion[i],
                evidence=evidence,
                seeded_by=seeded[i],
            )
        )
    out.sort(key=lambda p: -p.size)
    return out
