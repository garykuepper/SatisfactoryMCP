"""How high the ground is, sampled -- because there is no heightmap.

Nothing in Docs.json or the save carries terrain. What both carry is a large number of
things whose Z is exact, scattered across the whole map:

* **608 resource nodes**, from the generated node table. These sit ON the ground, so
  their Z *is* terrain, and they are available with no save at all.
* **8,347 foundation and wall pieces**, from ``FGLightweightBuildableSubsystem`` as
  ``[class, x, y, z]``.
* **566 production buildings**, from their actor transforms.

So the honest answer to "how high is it here" is a *sample*, with its count and its
spread, and never an interpolated surface. A single number invented from three points
40 m apart reads as measured and is not.

Nodes and structures are kept apart on purpose
----------------------------------------------
A resource node is on the terrain. A foundation is wherever the player put it -- often
deliberately level across a slope, sometimes on stilts over a cliff -- so a foundation's Z
is **built elevation, not ground**. Averaging the two produces a number that is neither,
and near a big platform the structures outnumber the nodes hundreds to one, so the average
would silently become "the height of that platform" while still being labelled ground.

They are therefore reported as separate populations, and the caller is told which is which.
Where they disagree, that disagreement is the interesting part: it is the fill depth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = ["Elevation", "Sample", "probe", "sample_points"]

#: Terrain-truth sources. A node rests on the ground; anything built does not have to.
GROUND_SOURCES = frozenset({"node"})

#: Ground samples needed before a fill depth is quoted at all. Three is not a statistical
#: threshold, it is a refusal: one node is a point, and a point is not a ground level.
MIN_GROUND_SAMPLES = 3


@dataclass
class Sample:
    source: str
    x: float
    y: float
    z: float
    dist_m: float = 0.0


@dataclass
class Elevation:
    """Known elevations near a point, kept as populations rather than one number."""

    x: float
    y: float
    radius_m: float
    samples: list[Sample] = field(default_factory=list)

    def of(self, *sources: str) -> list[float]:
        keep = set(sources) if sources else None
        return sorted(s.z / 100.0 for s in self.samples if keep is None or s.source in keep)

    @property
    def ground(self) -> list[float]:
        """Metres, from sources that genuinely rest on terrain."""
        return self.of(*GROUND_SOURCES)

    @property
    def built(self) -> list[float]:
        return sorted(s.z / 100.0 for s in self.samples if s.source not in GROUND_SOURCES)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.samples:
            out[s.source] = out.get(s.source, 0) + 1
        return out

    @staticmethod
    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0

    def median(self, *sources: str) -> float | None:
        return self._median(self.of(*sources))

    def spread(self, *sources: str) -> float | None:
        vals = self.of(*sources)
        return (max(vals) - min(vals)) if vals else None

    @property
    def nearest(self) -> Sample | None:
        return min(self.samples, key=lambda s: s.dist_m) if self.samples else None

    @property
    def fill_m(self) -> float | None:
        """How far the built surface sits above the ground samples, in metres.

        ``None`` unless BOTH populations are present, because it is a difference and one
        side alone cannot produce it. A large positive number is the depth of foundation
        already stacked here; a negative one means the nodes nearby are above the
        platform, which is worth seeing before planning a gravity feed.

        Also ``None`` below ``MIN_GROUND_SAMPLES``. On a developed site the structures
        outnumber the nodes by hundreds to one -- measured at 805 against 1 on this
        world's main platform -- and one node's height is not a ground level. Quoting a
        25 m fill from a single sample would be an invented number wearing a measurement's
        clothes, which is the failure this whole module exists to avoid.
        """
        ground, built = self.ground, self.built
        if len(ground) < MIN_GROUND_SAMPLES or not built:
            return None
        return self._median(built) - self._median(ground)


def sample_points(node_table=None, state=None) -> list[Sample]:
    """Every point whose elevation is known, from whatever sources are available.

    ``state`` is optional. Without a save only the node table contributes, which still
    covers the whole map -- so this answers for unexplored ground as well as for a site
    that has been built on.
    """
    out: list[Sample] = []
    for n in getattr(node_table, "nodes", ()) or ():
        z = n.get("z")
        if z is not None:
            out.append(Sample("node", n["x"], n["y"], float(z)))
    if state is None:
        return out

    for record in state._all_records():
        pos = record.get("pos")
        if pos and len(pos) >= 3:
            out.append(Sample("building", float(pos[0]), float(pos[1]), float(pos[2])))

    # Foundations arrive as flat [class_index, x, y, z] rows rather than as records, and
    # they are by far the densest source -- 8,347 against 566 buildings on the reference
    # save. Guarded field-by-field because this is raw projection data, and a malformed
    # row should cost one sample rather than the whole probe.
    raw = (state.projection.get("structures") or {}).get("instances") or ()
    for inst in raw:
        if isinstance(inst, (list, tuple)) and len(inst) >= 4:
            try:
                out.append(Sample("structure", float(inst[1]), float(inst[2]), float(inst[3])))
            except (TypeError, ValueError):
                continue
    return out


def probe(x: float, y: float, samples: list[Sample], radius_m: float = 200.0) -> Elevation:
    """Elevation samples within ``radius_m`` of a point. Coordinates in centimetres."""
    out = Elevation(x=x, y=y, radius_m=radius_m)
    limit = radius_m * 100.0
    for s in samples:
        d = math.dist((x, y), (s.x, s.y))
        if d <= limit:
            out.samples.append(Sample(s.source, s.x, s.y, s.z, d / 100.0))
    out.samples.sort(key=lambda s: s.dist_m)
    return out
