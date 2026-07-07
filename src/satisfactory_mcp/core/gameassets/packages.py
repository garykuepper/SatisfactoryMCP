"""Cooked (zen, IoStore-resident) packages: their headers, their exports, their properties.

What a ``.umap`` or ``.uasset`` looks like once the cooker has been at it. The container next
door hands over bytes; this turns them into exports with names, classes, outers and tagged
properties, and composes the transform chain that says where a placed actor actually is.

Nothing here opens a file or decides what is interesting. ``PackageView`` is handed a blob,
``ScriptObjects`` and the two class-side caches are handed an ``IoStore``, and which packages
to read is entirely the caller's business -- which is why one generator can use this to find
collectibles, another to find landscape components and a third to read the biome table.
"""

from __future__ import annotations

import collections
import math
import struct
from pathlib import Path

from .iostore import ContainerError, Decompressor, IoStore

#: The class of the export every map actor hangs off. This, not a path prefix, is what makes
#: an export an actor: a native class is a 62-bit hash rather than a path, so a ``/Game/``
#: prefix test silently drops every natively-classed actor there is.
LEVEL_CLASS = "/Script/Engine.Level"

_D2R = math.pi / 180.0
_UNIT_SCALE = (1.0, 1.0, 1.0)
_MASK62 = (1 << 62) - 1


def _name_batch(blob: bytes, pos: int) -> tuple[list[str], int]:
    """An ``FNameBatch``: count, byte length, hash version, hashes, headers, then strings."""
    count = struct.unpack_from("<I", blob, pos)[0]
    pos += 8 + 8 + 8 * count
    headers = blob[pos : pos + 2 * count]
    pos += 2 * count
    names = []
    for i in range(count):
        header = struct.unpack_from(">H", headers, i * 2)[0]
        length = header & 0x7FFF
        if header & 0x8000:  # UTF-16
            names.append(blob[pos : pos + length * 2].decode("utf-16-le", "replace"))
            pos += length * 2
        else:
            names.append(blob[pos : pos + length].decode("utf-8", "replace"))
            pos += length
    return names, pos


class ScriptObjects:
    """``/Script/...`` object paths, out of ``global.utoc``'s ScriptObjects chunk.

    A cooked package refers to a native class by an ``FPackageObjectIndex`` of kind
    ``ScriptImport``, which is a 62-bit hash of the lowercased object path and carries no
    text. The only way back is this table: a name batch, an ``int32`` count, then one
    32-byte ``FScriptObjectEntry`` per object holding its name, its own hash and its
    Outer's. Walking ``OuterIndex`` composes the full path -- package, then ``.`` for a
    top-level object and ``:`` for anything nested inside one.

    Without this every natively-classed actor in the map is unidentifiable, which is
    exactly how 703 loot caches went missing.
    """

    CHUNK_TYPE = 5
    NONE = 0xFFFFFFFFFFFFFFFF

    def __init__(self, paks: Path, decompress: Decompressor) -> None:
        store = IoStore(paks, "global", decompress)
        chunks = store.chunks_of_type(self.CHUNK_TYPE)
        if not chunks:
            raise ContainerError("global.utoc holds no ScriptObjects chunk")
        blob = store.read(chunks[0])
        names, pos = _name_batch(blob, 0)
        count = struct.unpack_from("<i", blob, pos)[0]
        pos += 4
        entries: dict[int, tuple[str, int]] = {}
        for i in range(count):
            name_index, number, own, outer, _cdo = struct.unpack_from("<IIQQQ", blob, pos + i * 32)
            slot = name_index & 0x3FFFFFFF
            base = names[slot] if slot < len(names) else f"<oob{slot}>"
            entries[own] = (base if number == 0 else f"{base}_{number - 1}", outer)
        self.entries = entries
        self.paths: dict[int, str] = {}
        for own in entries:
            self.paths[own] = self._path(own)
        self.object_count = count
        self.package_count = sum(1 for _n, outer in entries.values() if outer == self.NONE)
        self.chunk_bytes = len(blob)

    def _path(self, own: int, depth: int = 0) -> str:
        cached = self.paths.get(own)
        if cached is not None:
            return cached
        if own == self.NONE or depth > 32:
            return ""
        entry = self.entries.get(own)
        if entry is None:
            return f"<unresolved:{own:016x}>"
        name, outer = entry
        above = self._path(outer, depth + 1)
        if not above:
            path = name
        elif above.startswith("/") and "." not in above:
            path = f"{above}.{name}"  # package -> top-level object
        else:
            path = f"{above}:{name}"  # nested subobject
        self.paths[own] = path
        return path

    def get(self, own: int) -> str:
        return self.paths.get(own) or f"/Script/<unresolved:{own:016x}>"


class Package:
    """``FZenPackageSummary`` plus the import and export maps. 15 uint32 of summary here."""

    EXPORT_SIZE = 72

    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        words = struct.unpack_from("<15I", blob, 0)
        self.header_size = words[1]
        import_offset, export_offset = words[7], words[8]
        self.export_offset = export_offset
        self.names, _ = _name_batch(blob, 60)
        # The summary carries no export count: the map ends where the next section starts.
        after = min([w for w in words[9:] if w > export_offset] or [self.header_size])
        self.export_count = (after - export_offset) // self.EXPORT_SIZE
        # The import map is an FPackageObjectIndex[] filling the gap to the export map.
        self.imports: list[int] = []
        if import_offset and export_offset > import_offset:
            count = (export_offset - import_offset) // 8
            self.imports = list(struct.unpack_from(f"<{count}Q", blob, import_offset))
        self.imported_packages: list[str] = []
        offset = words[12]
        if offset and offset < self.header_size:
            try:
                self.imported_packages, _ = _name_batch(blob, offset)
            except (struct.error, IndexError):
                self.imported_packages = []

    def name(self, index: int, number: int) -> str:
        kind, slot = index >> 30, index & 0x3FFFFFFF
        if kind == 0:
            base = self.names[slot] if slot < len(self.names) else f"<oob{slot}>"
        else:
            # Kind 2 is the global name map, which lives in global.utoc. Nothing this
            # generator reads -- actor names, RelativeLocation -- is ever global.
            base = f"<kind{kind}:{slot}>"
        return base if number == 0 else f"{base}_{number - 1}"

    def exports(self) -> list[dict]:
        out = []
        for slot in range(self.export_count):
            pos = self.export_offset + slot * self.EXPORT_SIZE
            offset, size = struct.unpack_from("<QQ", self.blob, pos)
            name_index, name_number = struct.unpack_from("<II", self.blob, pos + 16)
            outer, class_index = struct.unpack_from("<2Q", self.blob, pos + 24)
            out.append(
                {
                    "slot": slot,
                    "offset": offset,
                    "size": size,
                    "name": self.name(name_index, name_number),
                    "outer": outer,
                    "class": class_index,
                }
            )
        return out

    def body(self, export: dict) -> bytes:
        start = self.header_size + export["offset"]
        return self.blob[start : start + export["size"]]


def property_tags(
    body: bytes, names: list[str], pos: int = 1
) -> tuple[list[tuple[str | None, str | None, bytes, int]], int]:
    """Walk a tagged-property stream, yielding ``(name, type, payload, value byte)``.

    The value byte is the one that follows ``Size`` in the tag. It is dead weight for every
    type except ``BoolProperty``, whose payload is empty and whose value lives there and
    nowhere else -- which is how ``mIsPassiveCreature`` is read.

    An export body starts one byte in; a nested struct payload starts at 0, which is what
    *pos* is for. The end offset comes back so a ``TArray<FStruct>`` can walk element by
    element. A malformed run stops the walk rather than raising: a truncated tail costs one
    actor's transform, and a raise costs the whole package.
    """
    out: list[tuple[str | None, str | None, bytes, int]] = []
    limit = len(body)
    while pos + 8 <= limit:
        name_index, _number = struct.unpack_from("<II", body, pos)
        if name_index == 0 and _number == 0:  # the None that terminates the stream
            pos += 8
            break
        slot = name_index & 0x3FFFFFFF
        name = names[slot] if (name_index >> 30) == 0 and slot < len(names) else None
        pos += 8

        kind: str | None = None

        def skip_type(pos: int, top: bool) -> int:
            nonlocal kind
            index, _num = struct.unpack_from("<II", body, pos)
            inner = struct.unpack_from("<i", body, pos + 8)[0]
            if top:
                slot = index & 0x3FFFFFFF
                kind = names[slot] if (index >> 30) == 0 and slot < len(names) else None
            pos += 12
            for _ in range(inner):
                pos = skip_type(pos, False)
            return pos

        try:
            pos = skip_type(pos, True)
        except (struct.error, IndexError, RecursionError):
            break
        if pos + 5 > limit:
            break
        size = struct.unpack_from("<I", body, pos)[0]
        value_byte = body[pos + 4]
        pos += 5  # uint32 size, then one byte that is the value of a bool
        if size > limit - pos + 1:
            break
        out.append((name, kind, body[pos : pos + size], value_byte))
        pos += size
    return out, pos


def _triple(payload: bytes) -> tuple[float, float, float] | None:
    if len(payload) == 24:
        return struct.unpack("<3d", payload)
    if len(payload) == 12:
        return struct.unpack("<3f", payload)
    return None


def _float(payload: bytes) -> float | None:
    if len(payload) == 4:
        return struct.unpack("<f", payload)[0]
    if len(payload) == 8:
        return struct.unpack("<d", payload)[0]
    return None


def _int32(payload: bytes) -> int | None:
    return struct.unpack("<i", payload)[0] if len(payload) == 4 else None


def _vector_array(payload: bytes | None) -> list[tuple[float, float, float]]:
    """A ``TArray<FVector>``: uint32 count, then that many triples of double or float."""
    if not payload or len(payload) < 4:
        return []
    count = struct.unpack_from("<I", payload, 0)[0]
    for width, fmt in ((24, "<3d"), (12, "<3f")):
        if len(payload) == 4 + count * width:
            return [struct.unpack_from(fmt, payload, 4 + i * width) for i in range(count)]
    return []


def class_name_of(class_path: str | None) -> str:
    """The class name this table keys on, from either kind of class path.

    A blueprint class is named by its *package* -- ``/Game/.../BP_Crystal`` is the class
    ``BP_Crystal_C`` -- while a native one is a full object path, so the two need different
    tails. Anything else, including the one actor in the map whose class is an export of
    its own package, comes back as-is so it is visible rather than silently binned.
    """
    if not class_path:
        return "<null class>"
    if class_path.startswith("/Game/"):
        return class_path.rsplit("/", 1)[-1] + "_C"
    if class_path.startswith("/Script/"):
        return class_path.rsplit(".", 1)[-1]
    return class_path


class PackageView:
    """One package's exports with lazily parsed properties and an outer -> children map."""

    def __init__(self, blob: bytes, scripts: ScriptObjects | None = None) -> None:
        self.pkg = Package(blob)
        self.scripts = scripts
        self.exports = self.pkg.exports()
        self.class_of: dict[int, str | None] = {}
        self.outer_of: dict[int, int | None] = {}
        self.children: dict[int, list[int]] = collections.defaultdict(list)
        self.level_slots: set[int] = set()
        self._props: dict[int, dict[str, bytes]] = {}
        self._kinds: dict[int, dict[str, str | None]] = {}
        self._bools: dict[int, dict[str, int]] = {}
        for export in self.exports:
            slot = export["slot"]
            path = self.object_path(export["class"])
            self.class_of[slot] = path
            if path == LEVEL_CLASS:
                self.level_slots.add(slot)
            outer = export["outer"]
            if outer >> 62 == 0:
                parent = outer & _MASK62
                self.outer_of[slot] = parent
                self.children[parent].append(slot)
            else:
                self.outer_of[slot] = None

    def object_path(self, packed: int) -> str | None:
        """An ``FPackageObjectIndex`` as a readable path.

        Four kinds. ``Null`` is nothing; ``Export`` points inside this package, which no
        class normally does and one map actor's does; ``ScriptImport`` is the 62-bit hash a
        ``ScriptObjects`` table turns back into ``/Script/FactoryGame.Whatever``;
        ``PackageImport`` is ``(imported package index, export hash)`` with the package
        NAME right there in the header, because this build is UE 5.2 or later. On the older
        layout that was a CityHash64 of the whole path with no way back, which is why an
        earlier attempt at this built a hash reverser it turned out not to need.
        """
        kind = packed >> 62
        if kind == 3:
            return None
        if kind == 0:
            return f"export:{packed & _MASK62}"
        if kind == 1:
            return self.scripts.get(packed) if self.scripts else f"/Script/<hash:{packed:016x}>"
        slot = (packed & _MASK62) >> 32
        if slot >= len(self.pkg.imported_packages):
            return None
        return self.pkg.imported_packages[slot]

    def _parse(self, slot: int) -> None:
        props: dict[str, bytes] = {}
        kinds: dict[str, str | None] = {}
        bools: dict[str, int] = {}
        entries, _end = property_tags(self.pkg.body(self.exports[slot]), self.pkg.names)
        for name, kind, payload, value in entries:
            if name is None or name in props:
                continue
            props[name] = payload
            kinds[name] = kind
            bools[name] = value
        self._props[slot] = props
        self._kinds[slot] = kinds
        self._bools[slot] = bools

    def props(self, slot: int) -> dict[str, bytes]:
        if slot not in self._props:
            self._parse(slot)
        return self._props[slot]

    def kinds(self, slot: int) -> dict[str, str | None]:
        if slot not in self._kinds:
            self._parse(slot)
        return self._kinds[slot]

    def flag(self, slot: int, name: str) -> bool | None:
        """A ``BoolProperty``'s value, which lives in the tag rather than the payload."""
        if slot not in self._bools:
            self._parse(slot)
        if name not in self._bools[slot]:
            return None
        return bool(self._bools[slot][name])

    def export_ref(self, payload: bytes) -> int | None:
        """An ``FPackageIndex`` pointing at an export in this same package, or None."""
        if len(payload) != 4:
            return None
        value = struct.unpack("<i", payload)[0]
        return value - 1 if value > 0 else None

    def import_path(self, payload: bytes) -> str | None:
        """An ``FPackageIndex`` pointing OUT of this package, as a path.

        The negative half of an ``FPackageIndex`` indexes the import map, whose entries are
        ``FPackageObjectIndex``. Without this every cross-package reference -- a spawner's
        ``mCreatureClass``, a deposit's ``mOverrideResourceClass``, a cache's item class --
        reads as None, which is how the hazard channels came to look underivable.
        """
        if len(payload) != 4:
            return None
        value = struct.unpack("<i", payload)[0]
        if value >= 0:
            return None
        index = -value - 1
        if index >= len(self.pkg.imports):
            return None
        return self.object_path(self.pkg.imports[index])

    def decode_struct(self, payload: bytes) -> dict:
        """A nested tagged struct as plain values, one level of types deep.

        Anything this does not know is kept as ``{"_type": ..., "_raw": hex}`` rather than
        dropped, which is what made ``FInventoryItem`` legible: it has no tagged members at
        all, so its bytes had to survive to be read as an ``FPackageIndex``.
        """
        entries, _end = property_tags(payload, self.pkg.names, 0)
        out: dict = {}
        for name, kind, raw, value in entries:
            if name is None:
                continue
            if kind == "StructProperty":
                inner = self.decode_struct(raw)
                out[name] = inner if inner else {"_type": kind, "_raw": raw.hex()}
            elif kind in ("ObjectProperty", "ClassProperty", "SoftClassProperty"):
                out[name] = self.import_path(raw) or (
                    f"export:{self.export_ref(raw)}" if self.export_ref(raw) is not None else None
                )
            elif kind in ("EnumProperty", "NameProperty"):
                out[name] = self._fname(raw)
            elif kind == "IntProperty":
                out[name] = _int32(raw)
            elif kind in ("FloatProperty", "DoubleProperty"):
                out[name] = _float(raw)
            elif kind == "BoolProperty":
                out[name] = bool(value)
            else:
                out[name] = {"_type": kind, "_raw": raw[:32].hex()}
        return out

    def _fname(self, payload: bytes) -> str | None:
        if len(payload) < 8:
            return None
        index, number = struct.unpack_from("<II", payload, 0)
        slot = index & 0x3FFFFFFF
        if (index >> 30) != 0 or slot >= len(self.pkg.names):
            return None
        base = self.pkg.names[slot]
        return base if number == 0 else f"{base}_{number - 1}"


class AssetIndex:
    """Container paths for ``.uasset`` classes, by leaf name.

    Every class-side fact a generator wants -- a component template, a creature's
    ``mIsPassiveCreature``, a spore flower's damage radius, an ore's radioactivity -- is
    read out of the class asset, and all of them need the same lookup.
    """

    def __init__(self, store: IoStore) -> None:
        self.store = store
        self._by_leaf: dict[str, str] = {}
        for path in store.by_path:
            if path.endswith(".uasset"):
                self._by_leaf.setdefault(path[: -len(".uasset")].rsplit("/", 1)[-1], path)

    def path_for(self, class_package: str) -> str | None:
        """The container path of a ``/Game/`` class package, guarded against namesakes."""
        path = self._by_leaf.get(class_package.rsplit("/", 1)[-1])
        if not path:
            return None
        directory = class_package.split("/Game/", 1)[-1].rsplit("/", 1)[0]
        return path if directory in path.replace("\\", "/") else None


class ClassFacts:
    """Class-default values read from blueprint ``.uasset`` files, cached per class.

    Two shapes are wanted and both come from the same read: the class DEFAULT OBJECT, whose
    properties are the class's own defaults, and the ``<Name>_GEN_VARIABLE`` component
    templates. The templates are not an optimisation -- a placed instance serialises only
    the parts of a component transform that differ from its template, so without them the
    composed world transform of anything attached under ``BP_WAT2`` is 73 cm out.
    """

    SUFFIX = "_GEN_VARIABLE"

    def __init__(self, store: IoStore, index: AssetIndex) -> None:
        self.store = store
        self.index = index
        self._templates: dict[str, dict[str, tuple]] = {}
        self._defaults: dict[str, dict[str, bytes]] = {}
        self._flags: dict[str, dict[str, bool | None]] = {}
        self._components: dict[str, dict[str, dict[str, bytes]]] = {}
        self._views: dict[str, PackageView | None] = {}
        self._failures: dict[str, str] = {}

    @property
    def resolved(self) -> int:
        return sum(1 for value in self._templates.values() if value)

    @property
    def looked_up(self) -> int:
        return len(self._templates)

    @property
    def failed(self) -> int:
        """Classes whose package is on disk and would not parse.

        Beside ``resolved`` and ``looked_up`` because it is the third of three and the
        one the other two hide. A class with no package at all and a class whose package
        raised both answer ``None`` from ``_view`` and both come out of ``templates`` as
        ``{}``, so a container this build cannot read at all -- a format change, a bad
        decompressor -- reads as a world where nothing happens to have a template, which
        is a normal-looking number. This one is zero on a healthy install and is not.
        """
        return len(self._failures)

    @property
    def failures(self) -> dict[str, str]:
        """``{class package: exception type}`` for every one of the above, for a report."""
        return dict(self._failures)

    def _view(self, class_package: str) -> PackageView | None:
        if class_package in self._views:
            return self._views[class_package]
        view: PackageView | None = None
        path = self.index.path_for(class_package)
        if path:
            try:
                view = PackageView(self.store.read_path(path))
            except Exception as exc:
                # Still cached, because `_load` asks once per class and re-reading a
                # package that just raised buys nothing -- but recorded, so that "no
                # template" and "could not read the package that holds the template" stop
                # being the same answer. They were, and a run that could not open a single
                # asset reported the same shape of result as one where every asset was fine
                # and simply carried no component templates.
                self._failures[class_package] = type(exc).__name__
        self._views[class_package] = view
        return view

    def _load(self, class_package: str) -> None:
        templates: dict[str, tuple] = {}
        defaults: dict[str, bytes] = {}
        flags: dict[str, bool | None] = {}
        components: dict[str, dict[str, bytes]] = {}
        view = self._view(class_package)
        if view is not None:
            for export in view.exports:
                name = export["name"]
                if name.endswith(self.SUFFIX):
                    stem = name[: -len(self.SUFFIX)]
                    props = view.props(export["slot"])
                    components[stem] = props
                    templates[stem] = (
                        _triple(props["RelativeLocation"]) if "RelativeLocation" in props else None,
                        _triple(props["RelativeRotation"]) if "RelativeRotation" in props else None,
                        _triple(props["RelativeScale3D"]) if "RelativeScale3D" in props else None,
                    )
                elif name.startswith("Default__") and not defaults:
                    defaults = view.props(export["slot"])
                    flags = {
                        key: view.flag(export["slot"], key) for key in view.kinds(export["slot"])
                    }
        self._templates[class_package] = templates
        self._defaults[class_package] = defaults
        self._flags[class_package] = flags
        self._components[class_package] = components

    def templates(self, class_package: str) -> dict[str, tuple]:
        if class_package not in self._templates:
            self._load(class_package)
        return self._templates[class_package]

    def defaults(self, class_package: str) -> dict[str, bytes]:
        if class_package not in self._defaults:
            self._load(class_package)
        return self._defaults[class_package]

    def flag(self, class_package: str, name: str) -> bool | None:
        if class_package not in self._flags:
            self._load(class_package)
        return self._flags[class_package].get(name)

    def component(self, class_package: str, stem: str) -> dict[str, bytes]:
        if class_package not in self._components:
            self._load(class_package)
        return self._components[class_package].get(stem, {})

    def component_float(self, class_package: str, stem: str, name: str) -> float | None:
        """A float on one of a class's component templates, e.g. a sphere's radius."""
        if class_package not in self._components:
            self._load(class_package)
        for candidate, props in self._components[class_package].items():
            if candidate == stem or candidate.startswith(stem):
                value = props.get(name)
                if value is not None:
                    return _float(value)
        return None


# --------------------------------------------------------------------------------------
# Transforms. UE composes parent * child, and rotators are pitch/yaw/roll in degrees.
# --------------------------------------------------------------------------------------


def rotator_to_quat(pitch: float, yaw: float, roll: float) -> tuple[float, float, float, float]:
    sp, cp = math.sin(pitch * _D2R * 0.5), math.cos(pitch * _D2R * 0.5)
    sy, cy = math.sin(yaw * _D2R * 0.5), math.cos(yaw * _D2R * 0.5)
    sr, cr = math.sin(roll * _D2R * 0.5), math.cos(roll * _D2R * 0.5)
    return (
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def quat_mul(a: tuple, b: tuple) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_rotate(q: tuple, v: tuple) -> tuple[float, float, float]:
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def compose(parent: tuple, child: tuple) -> tuple:
    ploc, pquat, pscale = parent
    cloc, cquat, cscale = child
    scaled = (cloc[0] * pscale[0], cloc[1] * pscale[1], cloc[2] * pscale[2])
    rotated = quat_rotate(pquat, scaled)
    return (
        (ploc[0] + rotated[0], ploc[1] + rotated[1], ploc[2] + rotated[2]),
        quat_mul(pquat, cquat),
        (pscale[0] * cscale[0], pscale[1] * cscale[1], pscale[2] * cscale[2]),
    )


def _owner_class(view: PackageView, component: int) -> str | None:
    owner = view.outer_of.get(component)
    if owner is None:
        return None
    path = view.class_of.get(owner)
    return path if path and path.startswith("/Game/") else None


def local_transform(view: PackageView, slot: int, classes: ClassFacts) -> tuple:
    props = view.props(slot)
    default_loc = default_rot = default_scale = None
    owner = _owner_class(view, slot)
    if owner:
        template = classes.templates(owner).get(view.exports[slot]["name"])
        if template:
            default_loc, default_rot, default_scale = template
    loc = _triple(props["RelativeLocation"]) if "RelativeLocation" in props else default_loc
    rot = _triple(props["RelativeRotation"]) if "RelativeRotation" in props else default_rot
    scale = _triple(props["RelativeScale3D"]) if "RelativeScale3D" in props else default_scale
    loc = loc or (0.0, 0.0, 0.0)
    rot = rot or (0.0, 0.0, 0.0)
    return (loc, rotator_to_quat(*rot), scale or _UNIT_SCALE)


def world_transform(
    view: PackageView,
    slot: int,
    classes: ClassFacts,
    seen: set[int] | None = None,
) -> tuple[tuple | None, int | None]:
    """Compose up the ``AttachParent`` chain. Returns the transform and the parent actor.

    The parent actor is the export the immediate ``AttachParent`` belongs to when that is a
    different actor from this one -- which is how a Mercer shrine names the sphere it is
    the pedestal of, exactly, without a distance heuristic.
    """
    if seen is None:
        seen = set()
    if slot in seen or len(seen) > 24:
        return None, None
    seen.add(slot)
    here = local_transform(view, slot, classes)
    payload = view.props(slot).get("AttachParent")
    if payload is None:
        return here, None
    parent = view.export_ref(payload)
    if parent is None or not 0 <= parent < len(view.exports):
        return here, None
    my_actor = view.outer_of.get(slot)
    parent_actor = view.outer_of.get(parent)
    attached_to = parent_actor if parent_actor is not None and parent_actor != my_actor else None
    above, deeper = world_transform(view, parent, classes, seen)
    if above is None:
        return None, attached_to or deeper
    return compose(above, here), attached_to if attached_to is not None else deeper


def root_component(view: PackageView, actor: int) -> int | None:
    """The actor's root ``SceneComponent``, by property if it has one and by shape if not."""
    payload = view.props(actor).get("RootComponent")
    if payload is not None:
        slot = view.export_ref(payload)
        if slot is not None and 0 <= slot < len(view.exports):
            return slot
    candidates = [c for c in view.children.get(actor, []) if "RelativeLocation" in view.props(c)]
    unattached = [c for c in candidates if "AttachParent" not in view.props(c)]
    if len(unattached) == 1:
        return unattached[0]
    if len(candidates) == 1:
        return candidates[0]
    return None
