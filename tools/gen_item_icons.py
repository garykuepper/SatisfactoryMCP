"""Cut data/local/icons/ -- one PNG per item -- out of the installed game.

Every popup this project draws names its items in words: "Iron Plate, 4,800". The game
names them in pictures, and a reader who plays Satisfactory recognises the icon before they
have finished reading the word. Nothing in this repository could draw one, because an icon
is artwork in the game's own container and the licence posture here is that we ship none of
it -- so this is a LOADER, like ``tools/gen_map_image.py``: it reads the reader's own
install and writes into ``data/local/``, which is gitignored and stays that way.

**Where the pictures are.** ``Docs/en-US.json`` gives every item descriptor an
``mSmallIcon`` and an ``mPersistentBigIcon``, and on all 747 classes that have one the two
are the SAME asset path -- there is one texture per item, at whichever resolution it was
authored, not a pair of variants to choose between. The path resolves to a
``Texture2D`` package in ``FactoryGame-Windows.utoc``, e.g.::

    Texture2D /Game/FactoryGame/Resource/Parts/IronPlate/UI/IconDesc_IronPlates_256...
    -> ../../../FactoryGame/Content/FactoryGame/Resource/Parts/IronPlate/UI/
       IconDesc_IronPlates_256.uasset  (+ .ubulk)

**Case-insensitively**, and that is not defensiveness: five items name a directory the
container spells differently -- ``Mam`` for ``MAM``, ``Medkit`` for ``MedKit``,
``Cyberwagon`` for ``CyberWagon``, ``Golfcart`` twice -- so an exact lookup silently loses
the MAM, the Cyberwagon, the Medkit and both Golf Carts and calls it 742 of 747.

**Two pixel formats, both measured rather than assumed.** 634 of the 747 are ``PF_DXT5``
(BC3, 16 bytes per 4x4 block: an icon is a cut-out and needs interpolated alpha, which BC1
has not got) and 113 are ``PF_B8G8R8A8``, four bytes a texel and no decompression at all.
The format is read off the package's own name table, where the ``PF_`` constant appears
verbatim, and is recorded per icon in the manifest. **No BC7 anywhere**, which is worth
writing down because it is the format one expects of UI art and would have been the reason
to reach for ``texture2ddecoder``'s BC7 path.

Both decoders hand back **BGRA**, and that is the one mistake here that produces a picture
rather than an error: read as ``"RGBA"`` a copper ingot comes out cyan and a candy cane
comes out blue, which looks like a deliberate palette. Verified on both paths after the
first run -- the copper is copper and the candy cane is red.

**The ``.ubulk`` length is the integrity check**, exactly as it is for the map slices. The
bulk chunk holds the mip chain from the texture's own size down to 128 px inclusive -- the
smaller levels are cooked inline and are not here -- so the total is arithmetic over the
format's block size, and exactly one (format, side) pair produces any given length. Four
lengths cover every icon in the game::

    PF_DXT5      256 px -> 81,920 B      512 px -> 344,064 B
    PF_B8G8R8A8  256 px -> 327,680 B     512 px -> 1,376,256 B

A length not in the derived table means the texture was re-cooked at another size or with
another mip tail, i.e. *the game changed*, and that icon is skipped and counted rather than
decoded on a guess.

**Written at 256 px square by default, and the choice is stated because there was one to
make.** The source art is 256 or 512 depending on the item -- 195 classes at 256, 551 at
512, and one 8 px swatch -- so "native" would mean a directory of mixed sizes and a client
that has to ask which. 256 is the smaller of the two real sizes, which makes it the only
choice that never invents a pixel: every source at or under 256 px is written at its own
resolution with no resampling at all, and every 512 px one is halved, which is an exact
2:1 reduction. Nothing is ever upscaled.

**And the size costs real bytes, so the alternatives are measured rather than argued.** On
this build, over a 40-icon sample scaled to the whole set: **256 px is 41.1 MB**, 128 px is
13.8 MB, 96 px is 8.5 MB and 64 px is 4.3 MB. The default stays at 256 because these are
served one file per item, cached immutable behind a build tag, and a popup fetches only the
dozen it is showing -- and because it is the one setting under which a third of the art is
byte-for-byte what the game authored. ``--px`` takes any of the others for a reader who
would rather have the directory small; the manifest records which was used, so a client
never has to guess.

**A texture with no ``.ubulk`` is not a texture with no picture.** Three of the 747
(Liquid Biofuel's pipe glyph, the Explorer's path marker, and an 8 px shared white swatch)
cook their WHOLE mip chain inline in the ``.uasset``, and an earlier cut of this file
skipped them behind "needs the ``FTexturePlatformData`` walk this file deliberately does
not do". The walk turned out to be unnecessary: the Zen header's ``BulkDataMap`` names
every inline level's offset and length outright -- ``packages.bulk_data_entries``, the
same table the Nanite reader streams its pages through -- so mip 0 is a slice of the
package blob at an offset the header states, under the same length check transposed
(see :func:`decode_inline_icon`).

**What it costs, measured on build 495413**: of 750 classes carrying ``mForm``, 747 name
an icon and all 747 decode -- **41.1 MB of PNG in 12 s** -- 195 from 256 px sources, 551
from 512 px ones, and the 8 px swatch as itself; the three inline ones are one of each. The
three that got no picture are named in the manifest under ``unresolved``, each with a
machine-readable ``kind`` -- all three are ``no-icon-in-docs``, the class whose docs entry
names no texture at all, for which the frontend's text tile is the correct rendering
rather than a fallback.

**Staleness is announced, never overwritten.** The manifest records the build it was cut
from, and a run against a different install refuses rather than mixing two builds' art in
one directory -- the rule every generated artifact under ``data/`` follows. ``--force``
replaces it anyway.

Run it::

    uv run --extra gen python tools/gen_item_icons.py

``--extra gen`` is what puts ``ooz`` (container blocks), ``texture2ddecoder`` (BC3) and
Pillow (the PNGs) on the path. None of the three is imported at module scope anywhere in
this repository, here included: they are imported inside ``main`` and handed on to
``core.gameassets.textures``, which is the seam that keeps the server importable without
them.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    install_directory,
    installed_build,
    installed_build_from_exe,
    read_str_path,
)
from satisfactory_mcp.core.gameassets.textures import (
    BC3_BLOCK_BYTES,
    INLINE_BULK_FLAG,
    bc3_mip_sizes,
    decode_bc3_rgba,
    decode_bgra8_rgba,
    inline_chain_side,
    raw_mip_sizes,
)
from satisfactory_mcp.core.gamedata.loader import load_docs
from tools._common import base_parser, require_gen

#: Where the docs dump lives inside an install. Derived from ``--game`` rather than found
#: through ``satisfactory_mcp.config``, and not only because a generator may not import that
#: module: the icons and the class names that point at them MUST come from one install, and
#: a ``SATISFACTORY_DOCS`` pointing at a second one would silently mix two builds' art.
DOCS_SUFFIX = Path("CommunityResources") / "Docs" / "en-US.json"

#: The container the artwork is in, and the prefix its paths carry. Both are
#: ``gen_map_image.py``'s, which reads the map sheet out of the same file.
CONTAINER = "FactoryGame-Windows"
MOUNT = "../../../FactoryGame/Content/"

#: ``Texture2D /Game/Foo/Bar/Icon_256.Icon_256`` -> ``/Game/Foo/Bar/Icon_256``. The asset
#: path is everything up to the first dot after the mount-relative part; what follows it is
#: the object name inside the package and is always the same word again.
ICON_PATH_RE = re.compile(r"Texture2D\s+(/Game/\S+?)\.")

#: The property the icon is read from, and the one that is deliberately not read.
#:
#: ``mSmallIcon`` and ``mPersistentBigIcon`` hold the IDENTICAL string on all 747 classes
#: that have either -- measured, not assumed -- so there is nothing to choose between and
#: reading both would only invite a future reader to wonder which one won.
ICON_FIELD = "mSmallIcon"

#: What makes a docs class an ITEM here: it has a physical form. The same test
#: ``gamedata.normalize._build_items`` applies, and for its reason -- 13 native classes carry
#: ``mForm`` and restricting to ``FGItemDescriptor`` would miss the biomass and the nuclear
#: fuel rods. Building descriptors come through it too, which is wanted: a plan that lists
#: 40 Constructors deserves the Constructor's picture as much as a crate deserves the rod's.
FORM_FIELD = "mForm"

#: The formats the container actually holds for these assets, and the mip arithmetic each
#: one's ``.ubulk`` length is checked against. Measured over all 742 resolvable icons on
#: build 495413: there is no third format, and in particular no BC7.
PIXEL_FORMATS = ("PF_DXT5", "PF_B8G8R8A8")

#: Where the bulk chain STOPS. A cooked ``Texture2D`` keeps its smallest levels inline in
#: the package and streams the rest, and the boundary here is 128 px: every one of the four
#: observed lengths is exactly the chain from the texture's own side down to this one.
#: Stated as the tail rather than as a mip count because the count differs per size.
MIP_TAIL_PX = 128

#: The sides a cooked icon is allowed to be, largest first. Wider than what the game ships
#: (256 and 512) so that a re-cook at another size is READ rather than refused -- the check
#: that matters is that the length is exactly one of these chains, not that it is one of
#: today's two.
CANDIDATE_PX = (2048, 1024, 512, 256, 128)

#: What every icon is written at unless ``--px`` says otherwise. See the module docstring
#: for the measured trade: 256 writes the 196 sources at or under it untouched and halves
#: the 551 larger ones, and is 41.1 MB; 128 is 13.8 MB and resamples everything but the
#: swatch.
ICON_PX = 256

#: The three ways a class ends up under ``unresolved``, as machine-readable kinds. Named
#: because the first cut of this manifest carried six undifferentiated sentences, and "the
#: game genuinely ships no picture for this class" (:data:`KIND_NO_ICON` -- the frontend's
#: text tile is the CORRECT rendering, forever) kept being read in the same breath as the
#: two kinds that mean a picture exists and this reader missed it.
KIND_NO_ICON = "no-icon-in-docs"
KIND_NOT_IN_CONTAINER = "asset-not-in-container"
KIND_UNDECODED = "undecoded"

#: Where they go, and what the sidecar beside them is called. Gitignored, like every other
#: thing cut out of somebody's install.
LOCAL_DIR = ROOT / "data" / "local"
ICONS_DIR_NAME = "icons"
MANIFEST_NAME = "manifest.json"

#: Where the build this directory was cut from is recorded, and therefore where the
#: staleness guard looks. One path, used by the writer and the reader, so the two cannot
#: drift into disagreeing about where the pin lives.
BUILD_PIN_PATH = ("_meta", "source", "game_version_pinned")


def chain_length(px: int, block: bool) -> int:
    """Total bytes of the ``.ubulk`` chain for one square side, in one of the two formats.

    ``block`` picks BC3's 4x4 blocks over raw BGRA's texels; both run from ``px`` down to
    :data:`MIP_TAIL_PX` inclusive, which is what makes this a derivation rather than a table.
    """
    count = max(px, MIP_TAIL_PX).bit_length() - MIP_TAIL_PX.bit_length() + 1
    sizes = bc3_mip_sizes(px, count) if block else raw_mip_sizes(px, count, 4)
    return sum(size for _side, size in sizes)


def bulk_layouts() -> dict[tuple[str, int], int]:
    """``{(pixel format, ubulk length): side}`` -- every chain this reader can name.

    The whole integrity check, derived rather than typed: a length that is not in here is a
    texture cooked at a size or a mip tail this file does not know how to read, and the
    honest answer to one is to skip that icon and say so rather than to decode mip 0 out of
    a layout that is no longer what the reader thinks it is.
    """
    return {
        (fmt, chain_length(px, fmt == "PF_DXT5")): px
        for fmt in PIXEL_FORMATS
        for px in CANDIDATE_PX
    }


def container_stem(icon: str) -> str | None:
    """``Texture2D /Game/Foo/Icon_256.Icon_256`` -> the mount-relative stem, or ``None``.

    ``None`` for the three classes whose ``mSmallIcon`` is literally ``"None"`` and for any
    value this pattern does not recognise -- both are "the dump names no picture", which is
    a coverage number rather than an error.
    """
    match = ICON_PATH_RE.search(icon or "")
    if match is None:
        return None
    return MOUNT + match.group(1).replace("/Game/", "", 1)


def icon_classes(docs_path: Path) -> list[tuple[str, str]]:
    """``[(class name, icon asset path), ...]`` for every item the dump gives a picture.

    Sorted by class name so a run is reproducible and a manifest diff is readable. Classes
    with no icon at all are dropped here and counted by the caller against the total.
    """
    dump = load_docs(docs_path)
    out = []
    for classes in dump.by_native.values():
        for entry in classes:
            if FORM_FIELD not in entry or "ClassName" not in entry:
                continue
            out.append((str(entry["ClassName"]), str(entry.get(ICON_FIELD) or "").strip()))
    return sorted(out)


def path_index(store: IoStore) -> dict[str, str]:
    """Lowercased container path -> the path as the container spells it.

    The five items whose docs path differs from the container's only in the case of a
    directory -- the MAM, the Cyberwagon, the Medkit and both Golf Carts -- are exactly why
    this exists, and skipping them would have looked like five items with no artwork.
    """
    return {path.lower(): path for path in store.paths.values()}


def pixel_format(package_names) -> str | None:
    """The ``PF_`` constant in a package's name table, or ``None`` if it holds none of ours.

    A cooked ``Texture2D`` names its pixel format in the package's own strings, so this is
    the format the asset states rather than one inferred from its length -- which is what
    makes the length an independent check instead of a circular one.
    """
    found = [name for name in package_names if name in PIXEL_FORMATS]
    return found[0] if len(found) == 1 else None


def decode_icon(package_mod, decoder, image_mod, blob: bytes, bulk: bytes, layouts: dict):
    """``((image, source side, pixel format), None)`` for one icon, or ``(None, reason)``.

    Three refusals, and each one is a different thing having gone wrong: the package names
    no format this reader knows, the bulk chain is a length no (format, side) pair produces,
    or the chain is shorter than the level it claims to start with. All three are counted
    and named in the manifest rather than raising, because one re-cooked icon must not cost
    the other 746 -- the posture every guard in the projection extractor takes.
    """
    fmt = pixel_format(package_mod.Package(blob).names)
    if fmt is None:
        return None, "no known PF_ constant in the package name table"
    px = layouts.get((fmt, len(bulk)))
    if px is None:
        return None, f"{fmt} .ubulk is {len(bulk)} B, which is no chain from {CANDIDATE_PX}"
    block = fmt == "PF_DXT5"
    mip0 = (bc3_mip_sizes(px, 1) if block else raw_mip_sizes(px, 1, 4))[0][1]
    if len(bulk) < mip0:
        return None, f"{fmt} .ubulk is shorter than its own mip 0"
    raw = bulk[:mip0]
    image = (
        decode_bc3_rgba(decoder, image_mod, raw, px)
        if block
        else decode_bgra8_rgba(image_mod, raw, px)
    )
    return (image, px, fmt), None


def decode_inline_icon(package_mod, decoder, image_mod, blob: bytes):
    """The same contract as :func:`decode_icon`, for a texture with no ``.ubulk`` at all.

    Three of the 747 icons cook their WHOLE mip chain inline in the ``.uasset`` -- Liquid
    Biofuel's pipe glyph, the Explorer's path marker, and an 8 px shared white swatch --
    and the first cut of this file skipped them behind "needs the ``FTexturePlatformData``
    walk this file deliberately does not do". The walk turned out to be unnecessary: the
    Zen header's ``BulkDataMap`` names every level's offset and length outright, one entry
    per mip, with the offset relative to the export-data segment. So mip 0 is
    ``blob[header_size + offset :][: size]`` of the FIRST entry, and no property tail is
    ever parsed.

    The integrity check transposes rather than disappears: with no file length to test,
    the whole entry list must be exactly the chain :func:`~textures.inline_chain_side`
    re-derives from its largest level -- and every entry must actually SAY it is inline,
    because an entry pointing into a ``.ubulk`` that is not in the container is a cook
    this reader does not know, not a texture with its mips at hand.
    """
    pkg = package_mod.Package(blob)
    fmt = pixel_format(pkg.names)
    if fmt is None:
        return None, "no known PF_ constant in the package name table"
    try:
        entries = pkg.bulk_entries()
    except ValueError as exc:
        return None, f"no .ubulk, and the bulk data map is unreadable ({exc})"
    if not entries:
        return None, "no .ubulk and an empty bulk data map: nowhere the mips could be"
    if not all(entry["flags"] & INLINE_BULK_FLAG for entry in entries):
        return None, "no .ubulk in the container, yet not every bulk entry is inline"
    block = fmt == "PF_DXT5"
    sizes = [entry["size"] for entry in entries]
    px = inline_chain_side(sizes, BC3_BLOCK_BYTES if block else None)
    if px is None:
        return None, f"{fmt} inline entries of {sizes} B are no mip chain this reader knows"
    first = entries[0]
    raw = blob[pkg.header_size + first["offset"] :][: first["size"]]
    if len(raw) != first["size"]:
        return None, "inline mip 0 runs off the end of the package"
    image = (
        decode_bc3_rgba(decoder, image_mod, raw, px)
        if block
        else decode_bgra8_rgba(image_mod, raw, px)
    )
    return (image, px, fmt), None


def to_png(image_mod, image, px: int, want: int) -> bytes:
    """One decoded level as PNG bytes at ``want`` px, resampled only when it has to be.

    ``LANCZOS`` for a reduction of drawn artwork with hard edges and a cut-out alpha; a level
    that is already the wanted size is returned untouched rather than round-tripped through a
    resize that would be the identity with a filter's rounding on top -- which at the default
    is 196 of the 747 icons written exactly as the game authored them.

    Never enlarges. An 8 px source asked for at 256 stays 8 -- which the shared white
    swatch actually is -- because an upscale is a picture this file invented and it would
    sit in the directory looking like the rest.
    """
    import io

    if px > want:
        image = image.resize((want, want), image_mod.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def pinned_build(out_dir: Path) -> str | None:
    """The build the icons already on disk say they were cut from, or ``None``."""
    try:
        existing = json.loads((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return read_str_path(existing, BUILD_PIN_PATH)


def build_manifest(*, pin: str, branch: str | None, docs, entries: dict, unresolved: dict, stats):
    """The sidecar: what a reader needs to know before trusting a directory of pictures.

    Three claims, and the first is the one the staleness guard reads. ``source`` says which
    install these came out of, down to the docs dump's own sha256 -- because the class names
    that key this manifest come from that file and the pixels come from the container beside
    it, and a manifest whose two halves came from two installs is the failure mode worth
    making impossible to reach silently. ``icons`` is the map a client actually uses.
    ``unresolved`` is what did not make it, per class, as ``{"kind", "detail"}`` -- the
    kind machine-readable so "the game ships no picture" and "a picture exists and this
    reader missed it" can never again be conflated, the detail a sentence, because a
    coverage number with no list behind it is a claim rather than a measurement.
    """
    return {
        "_meta": {
            "generator": "tools/gen_item_icons.py",
            "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": {
                "game_version_pinned": pin,
                "game_build_string": branch,
                "container": f"{CONTAINER}.utoc",
                "docs": {"path": str(docs.path), "sha256": docs.sha256, "bytes": docs.size},
                "icon_field": ICON_FIELD,
                "pixel_formats": list(PIXEL_FORMATS),
                "note": (
                    "mip 0 of each Texture2D's .ubulk, decoded from the format the package "
                    "names and written as PNG at the square side under counts.written_px, "
                    "never upscaled. The .ubulk holds the chain from the texture's own side "
                    f"down to {MIP_TAIL_PX} px, so its length is a free check that the layout "
                    "is still the one this reader knows; a length off that table is skipped "
                    "and listed under unresolved. A texture with NO .ubulk keeps its whole "
                    "chain inline in the .uasset instead, one bulk-map entry per level, and "
                    "is read from there under the same check transposed: the entry sizes "
                    "must be exactly the chain re-derived from the largest one. unresolved "
                    "entries carry a machine-readable kind: no-icon-in-docs means the game "
                    "ships no picture and the text tile is correct forever; "
                    "asset-not-in-container and undecoded mean a picture exists and this "
                    "reader missed it."
                ),
            },
            "counts": stats,
            "staleness": (
                "game_version_pinned is the build these pictures were cut from. "
                "tools/gen_item_icons.py refuses to replace this directory from another "
                "build unless --force is passed, because artwork from two builds in one "
                "directory answers questions instead of failing."
            ),
        },
        "icons": entries,
        "unresolved": unresolved,
    }


def main() -> int:
    parser = base_parser("Cut one PNG per item out of the installed game into data/local/icons/.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=LOCAL_DIR / ICONS_DIR_NAME,
        help="where the PNGs and manifest.json go (default: data/local/icons/)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace icons cut from another build instead of refusing",
    )
    parser.add_argument(
        "--px",
        type=int,
        default=ICON_PX,
        help=(
            f"square side to write, in pixels (default {ICON_PX}). Measured on build 495413: "
            "256 is 41.1 MB, 128 is 13.8 MB, 96 is 8.5 MB, 64 is 4.3 MB. Nothing is ever "
            "upscaled, so a source smaller than this stays its own size."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="one line per 100 icons, not per one")
    args = parser.parse_args()
    if args.px < 1:
        print(f"--px is a pixel side, so it has to be at least 1; got {args.px}")
        return 2

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    import texture2ddecoder as decoder
    from PIL import Image as image_mod

    from satisfactory_mcp.core.gameassets import packages as package_mod

    started = time.time()
    try:
        pin, _raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc}\nPass --game <install> if the game is not where this expects it.")
        return 1
    branch = installed_build_from_exe(args.game)
    print(f"install: {args.game}\n  {pin}")

    out_dir: Path = args.out_dir
    if not args.force and (out_dir / MANIFEST_NAME).is_file():
        already = pinned_build(out_dir)
        if already != pin:
            print(
                f"{out_dir} already holds icons and this run cannot show they came from the "
                "install now on disk.\n"
                f"  installed:  {pin}\n"
                f"  those PNGs: {already or 'no manifest.json, or no build recorded in it'}\n"
                "Artwork from two builds in one directory answers questions instead of "
                "failing. Pass --force to replace it anyway."
            )
            return 3

    docs_path = args.game / DOCS_SUFFIX
    if not docs_path.is_file():
        print(f"no docs dump at {docs_path}; the icons are named by the classes in it")
        return 1
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / f"{CONTAINER}.utoc").exists():
        print(f"no {CONTAINER}.utoc under {paks}")
        return 1

    classes = icon_classes(docs_path)
    docs = load_docs(docs_path)
    store = IoStore(paks, CONTAINER, oodle_decompress)
    by_lower = path_index(store)
    layouts = bulk_layouts()
    print(
        f"{len(classes)} item classes carry {FORM_FIELD}; container holds "
        f"{len(store.paths)} paths, {len(layouts)} .ubulk layouts derived"
    )

    payload: dict[str, bytes] = {}
    entries: dict[str, dict] = {}
    unresolved: dict[str, dict] = {}
    sides: dict[int, int] = {}
    for name, icon in classes:
        stem = container_stem(icon)
        if stem is None:
            unresolved[name] = {
                "kind": KIND_NO_ICON,
                "detail": f"the dump names no icon ({icon or 'empty'}); "
                "the frontend's text tile is this class's correct rendering",
            }
            continue
        asset = by_lower.get((stem + ".uasset").lower())
        bulk_path = by_lower.get((stem + ".ubulk").lower())
        if asset is None:
            unresolved[name] = {
                "kind": KIND_NOT_IN_CONTAINER,
                "detail": f"{stem}.uasset is not in the container",
            }
            continue
        if bulk_path is not None:
            decoded, why = decode_icon(
                package_mod,
                decoder,
                image_mod,
                store.read_path(asset),
                store.read_path(bulk_path),
                layouts,
            )
        else:
            # No .ubulk is not a missing picture: the whole mip chain is cooked inline in
            # the .uasset, which is how three of the 747 are cut. See decode_inline_icon.
            decoded, why = decode_inline_icon(
                package_mod, decoder, image_mod, store.read_path(asset)
            )
        if decoded is None:
            unresolved[name] = {"kind": KIND_UNDECODED, "detail": f"{stem}: {why}"}
            continue
        image, px, fmt = decoded
        blob = to_png(image_mod, image, px, args.px)
        payload[f"{name}.png"] = blob
        entries[name] = {
            "file": f"{name}.png",
            "source_px": px,
            "source_format": fmt,
            "bytes": len(blob),
        }
        sides[px] = sides.get(px, 0) + 1
        if not args.quiet and len(entries) % 100 == 0:
            print(f"  {len(entries)} decoded ({time.time() - started:.0f}s)")

    stats = {
        "item_classes": len(classes),
        "icons_written": len(entries),
        "unresolved": len(unresolved),
        "bytes": sum(e["bytes"] for e in entries.values()),
        "written_px": args.px,
        "source_px": {str(px): count for px, count in sorted(sides.items())},
        "seconds": round(time.time() - started, 1),
        "decoders": {name: version for name, version in sorted(versions.items())},
    }
    manifest = build_manifest(
        pin=pin, branch=branch, docs=docs, entries=entries, unresolved=unresolved, stats=stats
    )
    payload[MANIFEST_NAME] = json.dumps(manifest, indent=1).encode("utf-8")
    install_directory(out_dir, payload)

    print(
        f"wrote {out_dir}  {len(entries)} icons at {args.px}x{args.px} "
        f"({stats['bytes'] / 1e6:.1f} MB) from "
        + ", ".join(f"{count}x{px}px" for px, count in sorted(sides.items()))
        + f"  ({stats['seconds']:.0f}s)"
    )
    if unresolved:
        print(f"  {len(unresolved)} class(es) got no picture; manifest.json names each one:")
        for name, entry in sorted(unresolved.items())[:8]:
            print(f"    {name}: [{entry['kind']}] {entry['detail']}")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
