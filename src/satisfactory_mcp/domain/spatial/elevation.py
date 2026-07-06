"""How high the ground is: measured where the terrain has been extracted, sampled elsewhere.

This module opened with "because there is no heightmap" for as long as that was true.
**It is not true any more.** ``tools/gen_world_heightmap.py`` decodes the cooked UE
Landscape heightfield and the Chaos collision geometry of 20,227 placed rocks out of the
reader's own installed game and writes a 1 m field to ``data/local/heightmap/``, measured
at 0.21 m median error against the 626 static resource nodes. The premise this file was
built on is gone, and saying so is the first thing it has to do.

What has **not** changed is everything the module argued for, because the field is a fourth
kind of evidence rather than a replacement for the other three. It is:

* **optional** -- the raster is derived from Coffee Stain's cooked assets, so the
  repository ships none of it and never will. Most machines have no field, every caller
  gets ``None``, and the sampled populations below are the whole answer there.
* **not a sample** -- a texel read is the game's own terrain looked up, not somebody's
  save reporting where a thing stands. It is reported beside the populations and is never
  averaged into one. See ``Elevation.terrain``.

The sampled populations, unchanged
----------------------------------
Nothing in Docs.json or the save carries terrain. What both carry is a large number of
things whose Z is exact, scattered across the whole map:

* **608 resource nodes**, from the generated node table. These sit ON the ground, so
  their Z *is* terrain, and they are available with no save at all.
* **8,347 foundation and wall pieces**, from ``FGLightweightBuildableSubsystem`` as
  ``[class, x, y, z]``.
* **566 production buildings**, from their actor transforms.

So without a field the honest answer to "how high is it here" is still a *sample*, with its
count and its spread, and never an interpolated surface. A single number invented from
three points 40 m apart reads as measured and is not, and ``MIN_GROUND_SAMPLES`` still
refuses to quote a fill depth below three of them. A heightmap arriving on the machine does
not turn three nodes into a ground level; it answers a different question beside them.

Nodes and structures are kept apart on purpose
----------------------------------------------
A resource node is on the terrain. A foundation is wherever the player put it -- often
deliberately level across a slope, sometimes on stilts over a cliff -- so a foundation's Z
is **built elevation, not ground**. Averaging the two produces a number that is neither,
and near a big platform the structures outnumber the nodes hundreds to one, so the average
would silently become "the height of that platform" while still being labelled ground.

They are therefore reported as separate populations, and the caller is told which is which.
Where they disagree, that disagreement is the interesting part: it is the fill depth. The
field makes a third kind of disagreement visible -- terrain against ground samples -- and
it is left visible for the same reason.

The field answers with its own uncertainty
------------------------------------------
A texel is not uniformly good. ``provenance`` says which layer answered, and the accuracy
the generator *measured* for that layer rides along with the number:

* **landscape** -- the cooked 1 m heightfield, 0.205 m measured over 430 nodes.
* **cliff** -- rasterised collision geometry of a real rock, 0.210 m over 187 nodes.
* **fill** -- the 2048 px interface raster outside the landscape frame, 3.897 m per
  quantisation step and the coarsest thing in the field.
* **no data** -- open ocean and two cave mouths, about a fifth of the box. ``terrain`` is
  ``None`` there, and the right answer is to say nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.saveio import rows as saverows
from . import geo, heightfield

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
    """Known elevations near a point, kept as populations rather than one number.

    ``terrain`` is the fourth source and stands apart from ``samples`` deliberately: it is
    one texel of the extracted heightfield read at exactly this coordinate, not a thing
    somebody's save says is standing somewhere near it. Folding it into the populations
    would put a reading with 0.2 m accuracy into a median with points 40 m away, and the
    caller would lose both the precision and the ability to tell the two apart.

    ``None`` whenever there is no field on this machine -- which is most of them -- or when
    the field has no data at this coordinate.
    """

    x: float
    y: float
    radius_m: float
    samples: list[Sample] = field(default_factory=list)
    terrain: heightfield.Reading | None = None

    @property
    def terrain_m(self) -> float | None:
        """The field's own answer in metres, or ``None`` if it has none here."""
        return None if self.terrain is None else self.terrain.z_m

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
    # save. Decoded through ``core.saveio.rows``, which is where the guard that used to be
    # written out here now lives, once, for all ten readers of these three tables: a
    # malformed row still costs one sample rather than the whole probe.
    for piece in saverows.iter_structures(state.projection):
        out.append(Sample("structure", piece.x, piece.y, piece.z))
    return out


def probe(
    x: float,
    y: float,
    samples: list[Sample],
    radius_m: float = 200.0,
    terrain_field: heightfield.Field | None = None,
) -> Elevation:
    """Elevation samples within ``radius_m`` of a point. Coordinates in centimetres.

    ``terrain_field`` is passed in rather than loaded here, and defaulting it to ``None``
    means "no field was consulted" rather than "look one up". A domain function that
    reached for ``data/local/`` on its own would make every caller's answer depend on
    whether somebody had run a generator, silently and without the caller having asked --
    which is the same posture ``/api/mapimage`` takes about the map image, for the same
    reason. The interface layer decides whether to offer one.
    """
    out = Elevation(x=x, y=y, radius_m=radius_m)
    if terrain_field is not None:
        out.terrain = terrain_field.at(x, y)
    for s in samples:
        d = geo.distance_m((x, y), (s.x, s.y))
        if d <= radius_m:
            out.samples.append(Sample(s.source, s.x, s.y, s.z, d))
    out.samples.sort(key=lambda s: s.dist_m)
    return out
