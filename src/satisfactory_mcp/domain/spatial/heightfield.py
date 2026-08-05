"""The 1 m terrain field: the on-disk format, and the loader that is only a loader.

``tools/gen_world_heightmap.py`` cuts the field out of the reader's own installed game and
writes it to the gitignored ``data/local/heightmap/``; this module is the byte format both
sides agree on and a reader over it. **Absent is the normal case** -- the repository ships
no terrain, so ``load_field()`` returns ``None`` on any machine where nobody ran the
generator, and every caller carries on without one. A raster is ``zlib`` over raw bytes,
the two int16 ones row-delta first, and a texel read costs a full decode of its plane, so
each plane is decoded lazily and cached. What each plane means is stated at the constant
that names it; the georeference is in ``meta.json`` and is never assumed.
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
    "DENSITY_NAME",
    "DIR_NAME",
    "HEIGHT_NAME",
    "META_NAME",
    "NODATA",
    "PROV_CLIFF",
    "PROV_CLIFF_DIRECT",
    "PROV_CLIFF_VALUES",
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

#: The planes over one grid, plus the sidecar carrying the georeference. Height and water
#: are int16 DECIMETRES of world Z; provenance, water quality and density are uint8 codes.
HEIGHT_NAME = "height.i16.z"
PROV_NAME = "prov.u8.z"
WATER_NAME = "water.i16.z"
WATER_QUALITY_NAME = "waterq.u8.z"
DENSITY_NAME = "density.u8.z"
META_NAME = "meta.json"

#: The int16 value that means "nothing is known here". Not zero: zero is sea level and a
#: real answer, so a no-data texel read as zero is a flat sea at the map's edge.
NODATA = -32768

#: Which layer answered a texel. The numbers are the file format and must not be
#: renumbered; 2 is unused. 5 splits the cliff province by HOW the texel was answered -- a
#: source vertex landed in it, against 4 where the rasteriser interpolated across a
#: triangle wider than the texel -- and is additive: a reader testing ``== PROV_CLIFF``
#: reads 5 as the whole of what 4 meant before. The split is announced by the presence of
#: ``density.u8.z`` and never inferred from a texel being 4.
PROV_NODATA = 0
PROV_LANDSCAPE = 1
PROV_FILL = 3
PROV_CLIFF = 4
PROV_CLIFF_DIRECT = 5

#: Both values that are the cliff layer. Anything asking "is this texel cliff" means this;
#: testing ``== PROV_CLIFF`` alone misses 5.
PROV_CLIFF_VALUES = (PROV_CLIFF, PROV_CLIFF_DIRECT)

PROV_NAMES = {
    PROV_NODATA: "no data",
    PROV_LANDSCAPE: "landscape",
    PROV_FILL: "fill",
    PROV_CLIFF: "cliff",
    PROV_CLIFF_DIRECT: "cliff, direct",
}

#: What the water channel is called when a reading has one. Not a provenance value: water
#: is a second surface over the same texel, not a different source for the ground.
PROV_WATER_NAME = "water"

#: ``waterq.u8.z``'s values, which are the file format: do not renumber. A water LEVEL
#: comes from a cooked water volume's own box and is good to centimetres wherever there is
#: water; a DEPTH is that level minus the ground, so it exists only where the ground was
#: measured at 1 m -- the landscape and cliff layers. Over the fill layer and over no-data
#: the bed is unknown, and ``WATER_LEVEL_ONLY`` says so rather than subtracting a number
#: it does not have.
WATER_DRY = 0
WATER_MEASURED = 1
WATER_LEVEL_ONLY = 2

WATER_QUALITY_NAMES = {
    WATER_DRY: "dry",
    WATER_MEASURED: "water, depth measured",
    WATER_LEVEL_ONLY: "water, depth unknown",
}

ZLIB_LEVEL = 6

DM_PER_M = 10.0

#: What a caller is told when the sidecar records no measured accuracy for a layer. Only
#: reached by a hand-written or truncated ``meta.json``; the generator always measures.
UNKNOWN_ACCURACY_M = None


# --------------------------------------------------------------------------------------
# The codec. Pure functions over arrays and bytes: one implementation, imported by both
# the generator and the loader, so the two cannot hold different opinions about the format.
# --------------------------------------------------------------------------------------


def encode_i16(grid: np.ndarray) -> bytes:
    """One int16 raster to bytes: row-delta, then zlib.

    ``prepend=0`` makes the first column its own absolute value, so a row decodes from its
    own bytes and a corrupt stream cannot shift the whole field by a constant. The
    subtraction is done in int32 and truncated back: two int16 values can differ by more
    than an int16 holds -- a cliff top beside a no-data texel does -- and the truncation is
    the two's-complement wrap ``decode_i16`` undoes, so the pair is exact for every input.
    """
    if grid.dtype != np.int16:
        raise TypeError(f"expected an int16 raster, got {grid.dtype}")
    delta = np.diff(grid.astype(np.int32), axis=1, prepend=0).astype(np.int16)
    return zlib.compress(delta.tobytes(), ZLIB_LEVEL)


def decode_i16(blob: bytes, height: int, width: int) -> np.ndarray:
    """The inverse of ``encode_i16``, given the shape the sidecar records.

    The shape lives in ``meta.json`` beside the georeference and not in the stream, so a
    raster whose length disagrees with it is a mismatched pair rather than a raster to
    reshape into whatever fits.
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
    """One uint8 raster to bytes: plain zlib, no delta."""
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

    A reading is **not** an ``elevation.Sample``: a sample is a thing somebody's save says
    is at a height, a reading is the game's own cooked terrain looked up. They are kept
    apart all the way out to the page, because one median over both describes neither.
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

        The quality channel decides it, not a comparison here: over the fill layer the
        ground is a 3.9 m-quantised raster that routinely rounds *above* a sea surface 17 m
        down, so ``water_m > z_m`` reads the open ocean as dry. That comparison is the
        fallback only for a field written before the quality plane existed, where it is all
        there is.
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

        ``None`` rather than zero: a texel of open ocean has a real depth this field cannot
        state, and 0.0 would read as "the water is exactly at the ground".
        """
        if not self.submerged or not self.depth_known or self.water_m is None:
            return None
        return max(self.water_m - self.z_m, 0.0)


class Field:
    """A loaded heightmap: its rasters, a georeference, and the accuracy it measured.

    Constructed by ``load_field``. Height and provenance are decoded when the object is
    built, since there is no answer without both; the water and density planes only when
    something asks, because a decoded plane is tens of MB resident.
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
        self._density: np.ndarray | None = None
        self._density_tried = False
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

        Rounded, never floored: the grid is **vertex-aligned**, so a texel's height belongs
        to the point ``x0 + col*spacing`` exactly. Flooring would answer with the vertex up
        to a metre south-west of the question, which on a cliff edge is a different cliff.
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
        """``waterq.u8.z``, or ``None`` for a field written before it existed."""
        if not self._water_quality_tried:
            self._water_quality_tried = True
            path = self.directory / WATER_QUALITY_NAME
            if path.is_file():
                self._water_quality = decode_u8(path.read_bytes(), self.height, self.width)
        return self._water_quality

    def density_raster(self) -> np.ndarray | None:
        """``density.u8.z``, or ``None`` for a field written before it existed.

        How many source vertices landed in each texel, clamped at 255 and zero wherever the
        cliff layer did not answer -- the landscape and the fill are lattices, so "samples
        per texel" is not a question either has. It is the only plane that can tell a
        renderer asking for a sub-metre pixel whether it is reading a measurement or an
        interpolant. ``None`` is not zero: a field predating the plane knows nothing about
        its own density, and that must not be read as "no samples anywhere".
        """
        if not self._density_tried:
            self._density_tried = True
            path = self.directory / DENSITY_NAME
            if path.is_file():
                self._density = decode_u8(path.read_bytes(), self.height, self.width)
        return self._density

    def at(self, x_cm: float, y_cm: float) -> Reading | None:
        """The terrain at one world coordinate, or ``None`` where the field knows nothing.

        ``None`` covers both silences -- off the grid, and a no-data texel inside it --
        because they are one answer to the caller: nothing is known about that spot.
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


#: Loaded fields, keyed by the directory and its sidecar's mtime, so a regenerated field is
#: picked up without a restart while a repeated question costs a dictionary lookup.
_CACHE: dict[tuple[str, int], Field] = {}


def field_dir(local_dir: Path | None = None) -> Path:
    """Where the field lives. Resolved at call time so a test can point it elsewhere."""
    if local_dir is not None:
        return Path(local_dir)
    return config.data_dir() / "local" / DIR_NAME


def load_field(local_dir: Path | None = None) -> Field | None:
    """The terrain field, or ``None`` if this machine has none.

    Every failure mode -- no directory, no sidecar, a sidecar that will not parse, a raster
    whose length disagrees with it -- returns ``None`` rather than raising: the caller asked
    "is there terrain here", and "no" is a complete answer. A broken field is diagnosed by
    the generator, not here.
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
