"""The 1 m terrain field: its on-disk format, and the loader that is only a loader.

``tools/gen_world_heightmap.py`` cuts a real heightmap of this world out of the reader's
own installed game and writes it to ``data/local/heightmap/``, which is gitignored. This
module is the other half: the byte format both sides agree on, and a reader that answers
"how high is the ground at this coordinate" from it -- or says the field is absent, which
is the default, because the repository ships no terrain and never will.

**Absent is the normal case.** Nothing here raises when ``data/local/heightmap/`` is not
there. ``load_field()`` returns ``None`` and every caller is expected to carry on without
it, the same loader-or-nothing shape ``/api/mapimage`` has had since the map image was
made optional. A field only exists on a machine where somebody ran the generator against
their own install.

The format, and why it is this one
----------------------------------
Four rasters over one grid, plus a JSON sidecar that carries the georeference so the
arrays never have to be interpreted from constants a caller typed in themselves.

* ``height.i16.z`` -- terrain top surface, **decimetres**, ``-32768`` for no data. int16
  costs 0.028 m RMS of quantisation, which is an eighth of the field's own measured
  accuracy and therefore free; int32 would double 16.5 MB for nothing.
* ``prov.u8.z`` -- which layer answered each texel, so an answer can carry its own
  uncertainty rather than one number being quoted for a field that is a metre good in the
  middle and four metres good at the edge.
* ``water.i16.z`` -- water surface Z on the same grid, same no-data. Information only; see
  the generator's docstring for the measurement that says it must not gate terrain.
* ``waterq.u8.z`` -- whether the water's **depth** is knowable at that texel, which is a
  different question from whether its level is. The level comes from a cooked water
  volume's own bounding box and is good to centimetres everywhere; the depth is that level
  minus the ground, and over the fill layer the ground is a 3.9 m-quantised raster whose
  own no-data value the water surface frequently *is*. So a texel says one of three
  things: dry, water whose depth was measured against 1 m terrain, or water whose level is
  known and whose depth is not. Printing the third as a depth of zero is the one mistake
  this byte exists to prevent.

Every raster is ``zlib`` over the raw bytes, and the two int16 ones are **row-delta**
first: neighbouring texels of a 1 m heightfield differ by a few decimetres, so the deltas
are small and repetitive where the raw values are neither. Measured on the shipped field,
that is 112 MB of int16 down to **16.45 MB**, against 26.72 MB for plain zlib of the same
array -- so the delta is worth 38% of the artifact and is not decoration.

The delta runs along a row and the arithmetic is deliberately allowed to wrap: a delta
between two int16 values need not fit in an int16, and both sides do the sum in int32 and
truncate back, so the round trip is exact for every input without the format needing a
wider type for the rare texel where a cliff face meets no-data.

Why the whole raster is decoded at once
---------------------------------------
zlib over the whole array is one stream, so a texel read costs a full decode: about 170 MB
resident for height and provenance together and a fraction of a second, once, on the first
question anybody asks. That is the price of the format that measured smallest, and it is
paid lazily and cached, so a server that is never asked about terrain never pays it. Water
and its quality byte are decoded separately and only if something asks for them.

**A field written before the quality byte existed still reads.** ``waterq.u8.z`` is absent
from those, and a reading then falls back to the only test that field supports -- a water
surface standing above the ground -- which is exactly what it meant before. Missing is not
the same as dry, and it is not read as dry.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ... import config

__all__ = [
    "DIR_NAME",
    "HEIGHT_NAME",
    "META_NAME",
    "NODATA",
    "PROV_CLIFF",
    "PROV_FILL",
    "PROV_LANDSCAPE",
    "PROV_NAME",
    "PROV_NAMES",
    "PROV_NODATA",
    "PROV_WATER_NAME",
    "WATER_DRY",
    "WATER_LEVEL_ONLY",
    "WATER_MEASURED",
    "WATER_NAME",
    "WATER_QUALITY_NAME",
    "WATER_QUALITY_NAMES",
    "Field",
    "Reading",
    "decode_i16",
    "decode_u8",
    "encode_i16",
    "encode_u8",
    "field_dir",
    "load_field",
]

#: The directory the generator writes and this module reads, under ``data/local/``.
DIR_NAME = "heightmap"

HEIGHT_NAME = "height.i16.z"
PROV_NAME = "prov.u8.z"
WATER_NAME = "water.i16.z"
WATER_QUALITY_NAME = "waterq.u8.z"
META_NAME = "meta.json"

#: The int16 value that means "nothing is known here". Not zero: zero is sea level and a
#: real answer, and a no-data raster silently read as a flat sea at the map's edge is
#: exactly the kind of invented number this project refuses elsewhere.
NODATA = -32768

#: Which layer answered a texel. The numbers are the file format and must not be
#: renumbered; 2 is deliberately absent, because the prototype that used it -- baseline
#: values standing in for cliffs over the landscape -- was superseded by real geometry.
PROV_NODATA = 0
PROV_LANDSCAPE = 1
PROV_FILL = 3
PROV_CLIFF = 4

PROV_NAMES = {
    PROV_NODATA: "no data",
    PROV_LANDSCAPE: "landscape",
    PROV_FILL: "fill",
    PROV_CLIFF: "cliff",
}

#: What the water channel is called when a reading has one. Not a provenance value: water
#: is a second surface over the same texel, not a different source for the ground.
PROV_WATER_NAME = "water"

#: ``waterq.u8.z``'s three values, and they are the file format: do not renumber. The
#: distinction is between what the channel measured and what it only located. A level
#: comes from a cooked water volume's own box and is good to centimetres wherever there is
#: water at all; a DEPTH is that level minus the ground, and it exists only where the
#: ground under the water was measured at 1 m -- the landscape and cliff layers. Over the
#: fill layer, and over no-data, the ground beneath a water surface is unknown, so the
#: depth is unknown, and ``WATER_LEVEL_ONLY`` is the channel saying so out loud instead of
#: subtracting two numbers one of which it does not have.
WATER_DRY = 0
WATER_MEASURED = 1
WATER_LEVEL_ONLY = 2

WATER_QUALITY_NAMES = {
    WATER_DRY: "dry",
    WATER_MEASURED: "water, depth measured",
    WATER_LEVEL_ONLY: "water, depth unknown",
}

#: zlib level 6. Measured against 9 on the shipped field: 9 costs 4.4x the compression
#: time and saves 1.6% of 16.5 MB, which is not a trade worth making in a tool that runs
#: for two minutes and writes to a gitignored directory.
ZLIB_LEVEL = 6

#: Decimetres to metres. The container's step, and the whole of its rounding error.
DM_PER_M = 10.0

#: What a caller is told when the sidecar records no measured accuracy for a layer. Only
#: reached by a hand-written or truncated ``meta.json``; the generator always measures.
UNKNOWN_ACCURACY_M = None


# --------------------------------------------------------------------------------------
# The codec. Pure functions over arrays and bytes, so the generator and the loader cannot
# hold two opinions about the format: there is one implementation and both import it.
# --------------------------------------------------------------------------------------


def encode_i16(grid: np.ndarray) -> bytes:
    """One int16 raster to bytes: row-delta, then zlib.

    ``prepend=0`` makes the first column its own absolute value, so a row decodes from its
    own bytes with nothing carried in from the row above -- which is what keeps a corrupt
    stream a corrupt stream rather than a whole field shifted by a constant.

    The subtraction is done in int32 and truncated back to int16 on purpose. Two int16
    values can differ by more than an int16 holds -- a cliff top beside a no-data texel
    does exactly that -- and the truncation is the same two's-complement wrap ``decode_i16``
    undoes, so the pair is exact rather than merely usually right.
    """
    if grid.dtype != np.int16:
        raise TypeError(f"expected an int16 raster, got {grid.dtype}")
    delta = np.diff(grid.astype(np.int32), axis=1, prepend=0).astype(np.int16)
    return zlib.compress(delta.tobytes(), ZLIB_LEVEL)


def decode_i16(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_i16``, given the shape the sidecar records.

    The shape is not in the stream, deliberately: it is in ``meta.json`` beside the
    georeference it belongs with, and a raster whose length does not match what the sidecar
    says is a mismatched pair rather than a raster to reshape into whatever fits.
    """
    delta = np.frombuffer(zlib.decompress(blob), dtype="<i2")
    if delta.size != height * width:
        raise ValueError(
            f"height raster is {delta.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    running = np.cumsum(delta.reshape(height, width).astype(np.int32), axis=1)
    return running.astype(np.int16)


def encode_u8(grid: np.ndarray) -> bytes:
    """One uint8 raster to bytes: plain zlib, no delta.

    The provenance byte takes four distinct values in large flat runs, which zlib's own
    match finder handles better than a delta would: measured at 0.9 MB either way, so the
    simpler one wins.
    """
    if grid.dtype != np.uint8:
        raise TypeError(f"expected a uint8 raster, got {grid.dtype}")
    return zlib.compress(grid.tobytes(), ZLIB_LEVEL)


def decode_u8(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_u8``."""
    flat = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
    if flat.size != height * width:
        raise ValueError(
            f"provenance raster is {flat.size} texels, but meta.json says {height}x{width} "
            f"= {height * width} -- the sidecar and the raster are not from one run"
        )
    return flat.reshape(height, width)


# --------------------------------------------------------------------------------------
# Reading the field.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One texel of the field, with the uncertainty that belongs to that texel.

    A reading is **not** a ``Sample``. A sample is a thing somebody's save says is at a
    height; a reading is the cooked terrain the game itself ships, looked up. They are kept
    apart all the way out to the page for the same reason nodes and foundations are: mixing
    two kinds of evidence into one median produces a number that describes neither.
    """

    z_m: float
    provenance: int
    accuracy_m: float | None
    water_m: float | None = None
    water_quality: int = WATER_DRY

    @property
    def source(self) -> str:
        return PROV_NAMES.get(self.provenance, f"layer {self.provenance}")

    @property
    def submerged(self) -> bool:
        """Whether water stands over this ground. Never a terrain correction.

        The channel decides this, not a comparison here. Over the fill layer the ground is
        a 3.9 m-quantised raster that routinely rounds *above* a sea surface 17 m down, so
        ``water_m > z_m`` reads the open ocean as dry -- which is precisely the arithmetic
        the quality byte was added to stop being the answer. It is still the answer for a
        field written before that byte existed, because there it is all such a field has.
        """
        if self.water_m is None:
            return False
        if self.water_quality != WATER_DRY:
            return True
        return self.water_m > self.z_m

    @property
    def depth_known(self) -> bool:
        """Whether the ground under the water was measured well enough to subtract."""
        return self.water_quality == WATER_MEASURED or (
            self.water_quality == WATER_DRY and self.submerged
        )

    @property
    def water_depth_m(self) -> float | None:
        """How deep the water is, or ``None`` where the bed is not known well enough.

        ``None`` rather than zero, and the difference is the whole point: a texel of open
        ocean has a real depth this field cannot state, and reporting 0.0 there would read
        as "the water is exactly at the ground", which is a measurement nobody made.
        """
        if not self.submerged or not self.depth_known or self.water_m is None:
            return None
        return max(self.water_m - self.z_m, 0.0)


class Field:
    """A loaded heightmap: three rasters, a georeference, and the accuracy it measured.

    Constructed by ``load_field``. The height and provenance rasters are decoded when the
    object is built -- there is no answer without both -- and the two water rasters only
    when something asks, because most questions are about ground and the channel is a
    fraction of a MB compressed against 112 MB decoded.
    """

    def __init__(self, meta: dict[str, Any], directory: Path) -> None:
        self.meta = meta
        self.directory = directory
        grid = meta["grid"]
        self.width = int(grid["width"])
        self.height = int(grid["height"])
        self.x0_cm = float(grid["x0_cm"])
        self.y0_cm = float(grid["y0_cm"])
        self.spacing_cm = float(grid["spacing_cm"])
        self._height_dm = decode_i16(
            (directory / HEIGHT_NAME).read_bytes(), self.height, self.width
        )
        self._prov = decode_u8((directory / PROV_NAME).read_bytes(), self.height, self.width)
        self._water_dm: np.ndarray | None = None
        self._water_tried = False
        self._water_quality: np.ndarray | None = None
        self._water_quality_tried = False
        self._accuracy = {
            int(key): value.get("accuracy_m")
            for key, value in (meta.get("provenance") or {}).items()
            if str(key).lstrip("-").isdigit()
        }

    # -- geometry ----------------------------------------------------------------------

    @property
    def build(self) -> str | None:
        """The game build this field was cut from, for the staleness the project announces."""
        source = (self.meta.get("sources") or {}).get("game") or {}
        pinned = source.get("game_version_pinned")
        return pinned if isinstance(pinned, str) else None

    def texel(self, x_cm: float, y_cm: float) -> tuple[int, int] | None:
        """``(row, col)`` for a world coordinate, or ``None`` if it is off the grid.

        Rounded rather than floored: the grid is **vertex-aligned**, so a texel's recorded
        height belongs to the point ``x0 + col*spacing`` exactly, and the nearest vertex is
        the nearest measurement. Flooring would answer with the vertex up to a metre
        south-west of the question, which on a cliff edge is a different cliff.
        """
        col = round((x_cm - self.x0_cm) / self.spacing_cm)
        row = round((y_cm - self.y0_cm) / self.spacing_cm)
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        return row, col

    def _water_raster(self) -> np.ndarray | None:
        if not self._water_tried:
            self._water_tried = True
            path = self.directory / WATER_NAME
            if path.is_file():
                self._water_dm = decode_i16(path.read_bytes(), self.height, self.width)
        return self._water_dm

    def _water_quality_raster(self) -> np.ndarray | None:
        """``waterq.u8.z``, or ``None`` for a field written before it existed.

        Public enough to be read by name: ``tools/gen_map_renders.py`` draws the channel
        straight off the rasters rather than one texel at a time, and it needs this one for
        the same reason a reading does -- to stop asking whether the surface stands above a
        ground it has no measurement of.
        """
        if not self._water_quality_tried:
            self._water_quality_tried = True
            path = self.directory / WATER_QUALITY_NAME
            if path.is_file():
                self._water_quality = decode_u8(path.read_bytes(), self.height, self.width)
        return self._water_quality

    def at(self, x_cm: float, y_cm: float) -> Reading | None:
        """The terrain at one world coordinate, or ``None`` where the field knows nothing.

        ``None`` is returned for both reasons a field can be silent -- off the grid, and a
        no-data texel inside it -- because they are the same answer to the caller: this
        module has nothing to say about that spot, and a caller that invents something from
        the silence would be doing what the sampled populations exist to avoid.
        """
        where = self.texel(x_cm, y_cm)
        if where is None:
            return None
        row, col = where
        raw = int(self._height_dm[row, col])
        if raw == NODATA:
            return None
        provenance = int(self._prov[row, col])
        water = self._water_raster()
        water_m = None
        quality = WATER_DRY
        if water is not None:
            wet = int(water[row, col])
            if wet != NODATA:
                water_m = wet / DM_PER_M
                grades = self._water_quality_raster()
                if grades is not None:
                    quality = int(grades[row, col])
        return Reading(
            z_m=raw / DM_PER_M,
            provenance=provenance,
            accuracy_m=self._accuracy.get(provenance, UNKNOWN_ACCURACY_M),
            water_m=water_m,
            water_quality=quality,
        )


#: Loaded fields, keyed by the directory and its sidecar's mtime, so a regenerated field
#: is picked up without a restart while a repeated question costs one dictionary lookup
#: rather than 170 MB of zlib.
_CACHE: dict[tuple[str, int], Field] = {}


def field_dir(local_dir: Path | None = None) -> Path:
    """Where the field lives. Resolved at call time so a test can point it elsewhere."""
    if local_dir is not None:
        return Path(local_dir)
    return config.data_dir() / "local" / DIR_NAME


def load_field(local_dir: Path | None = None) -> Field | None:
    """The terrain field, or ``None`` if this machine has none.

    A loader and only a loader. Every failure mode -- no directory, no sidecar, a sidecar
    that will not parse, a raster whose length disagrees with it -- returns ``None`` rather
    than raising, because a caller asked "is there terrain here" and "no" is a complete
    answer to that. The generator is where a broken field is diagnosed; the server's job is
    to carry on with the sampled populations it had before the field existed.
    """
    directory = field_dir(local_dir)
    meta_path = directory / META_NAME
    try:
        stamp = meta_path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(directory), stamp)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return None
        field = Field(meta, directory)
    except (OSError, ValueError, TypeError, KeyError, zlib.error):
        return None
    _CACHE.clear()
    _CACHE[key] = field
    return field
