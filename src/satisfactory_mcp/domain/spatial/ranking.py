"""Score candidate build sites.

The score is a weighted sum of min-max normalised terms, and **every raw component is
returned alongside it** so a caller can re-weight for its own priorities rather than
trusting one opaque number.

Throughput counts **untapped, reachable** capacity only. Total capacity is the wrong
measure for "where should I build": a field whose nodes all have miners on them offers
nothing, and a field of well satellites offers nothing until the Pressurizer is
unlocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.constants import PURITY_MULT
from . import geo

__all__ = ["WEIGHTS", "SiteScore", "rank_sites"]

#: Default weights. Throughput dominates; spread and distance are real but secondary
#: costs; purity is a tiebreak because a pure node needs fewer machines for the same
#: output.
WEIGHTS = {
    "throughput": 1.00,
    "spread": -0.35,
    "distance": -0.25,
    "purity": 0.20,
}


@dataclass
class SiteScore:
    cluster: geo.Cluster
    score: float
    raw: dict[str, float] = field(default_factory=dict)
    normalised: dict[str, float] = field(default_factory=dict)

    @property
    def centroid(self) -> tuple[float, float, float]:
        return self.cluster.centroid


def _normalise(values: list[float]) -> list[float]:
    """Min-max to 0..1. A degenerate spread maps everything to 0.5, which keeps the
    term neutral instead of arbitrarily favouring one candidate."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _purity_quality(cluster: geo.Cluster) -> float:
    """Mean purity multiplier scaled to 0..1 (impure 0.25, normal 0.5, pure 1.0)."""
    members = cluster.members
    if not members:
        return 0.0
    total = sum(PURITY_MULT.get(m.get("purity", "normal"), 1.0) for m in members)
    return (total / len(members)) / 2.0


def _untapped_rate(cluster: geo.Cluster) -> float:
    return sum(
        m.get("rate", 0.0)
        for m in cluster.members
        if not m.get("tapped") and m.get("reachable", True)
    )


def _distance_to_infra_m(cluster: geo.Cluster, infra: list[tuple[float, float]]) -> float | None:
    if not infra:
        return None
    cx, cy, _ = cluster.centroid
    return min(geo.distance_m((cx, cy), p) for p in infra)


def _altitude_delta_m(cluster: geo.Cluster, consumer_z: float | None) -> float | None:
    """Height of the field above the consumer, in metres.

    The sign matters and is easy to get backwards: a field 268 m ABOVE the refineries
    feeds them downhill and needs no pipeline pumps, so a positive delta is an
    advantage for fluids, not a cost.
    """
    if consumer_z is None:
        return None
    _, _, cz = cluster.centroid
    return (cz - consumer_z) / 100.0


def rank_sites(
    clusters: list[geo.Cluster],
    infra: list[tuple[float, float]] | None = None,
    consumer_z: float | None = None,
    weights: dict[str, float] | None = None,
    require_untapped: bool = True,
) -> list[SiteScore]:
    """Rank candidate fields, best first."""
    w = {**WEIGHTS, **(weights or {})}
    infra = infra or []

    pool = list(clusters)
    if require_untapped:
        pool = [c for c in pool if _untapped_rate(c) > 0]
    if not pool:
        return []

    throughput = [_untapped_rate(c) for c in pool]
    spread = [c.diameter_m for c in pool]
    distance = [_distance_to_infra_m(c, infra) for c in pool]
    purity = [_purity_quality(c) for c in pool]

    # A missing distance (no infrastructure known) must not silently score as 0 km.
    known = [d for d in distance if d is not None]
    fallback = max(known) if known else 0.0
    distance_filled = [fallback if d is None else d for d in distance]

    n_through = _normalise(throughput)
    n_spread = _normalise(spread)
    n_distance = _normalise(distance_filled)
    n_purity = _normalise(purity)

    out: list[SiteScore] = []
    for i, cluster in enumerate(pool):
        normalised = {
            "throughput": n_through[i],
            "spread": n_spread[i],
            "distance": n_distance[i],
            "purity": n_purity[i],
        }
        score = sum(w[k] * normalised[k] for k in normalised)
        out.append(
            SiteScore(
                cluster=cluster,
                score=round(score, 4),
                raw={
                    "untapped_rate": round(throughput[i], 2),
                    "total_rate": round(sum(m.get("rate", 0.0) for m in cluster.members), 2),
                    "nodes": cluster.size,
                    "spread_m": round(spread[i], 1),
                    "distance_to_infra_m": (None if distance[i] is None else round(distance[i], 1)),
                    "purity_quality": round(purity[i], 3),
                    "altitude_vs_consumer_m": _altitude_delta_m(cluster, consumer_z),
                },
                normalised={k: round(v, 3) for k, v in normalised.items()},
            )
        )
    out.sort(key=lambda s: -s.score)
    return out
