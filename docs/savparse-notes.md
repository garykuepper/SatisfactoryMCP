# Replacing the vendored GPL save parser — working notes

Everything derived so far, written to be self-contained: a reader who has seen none of the
conversation should be able to continue from this. Doubles as the spec for the remaining
work.

## Why

`sidecar/vendor/sat_sav_parse/` is **GPL-3.0** and its licence reaches the whole project.

**What "cleanroom" can mean here, stated honestly.** A strict cleanroom needs an
implementer who has never seen the original, and the library is vendored in this repo. What
is being done instead is a reimplementation **of the file format** — a fact about what the
game writes, not a creative work — verified black-box: same file in, same values out. That
is the ordinary interoperability route. It reduces exposure; it is not a legal
certification, and nobody on this project should present it as one.

**Rule for anyone continuing:** derive from the bytes and from the game's own displayed
values. Use the vendored library **only** as an oracle — call it, compare outputs — never
read its implementation for structure or naming.

## What we actually depend on

Runtime, in `sidecar/extract_save.py`, is **three entry points**:

| entry point | used for | status |
|---|---|---|
| `readSaveFileInfo(path)` | 9 header fields → projection `header` | ✅ `savparse.read_info` |
| `readFullSaveFile(path)` | `.levels[].actorAndComponentObjectHeaders[]` + `.objects[]` | ⏳ |
| `ParseError` | one `except` at the save boundary | ⏳ |

The adapter surface `extract_save.py` needs from a parsed save:

- `save.levels` → each with `actorAndComponentObjectHeaders` and `objects` (parallel lists)
- header: `.typePath` (absent on components — use `getattr` default), `.instanceName`, `.position`
- object: `.properties` as a list of `[name, value]` pairs (`props()` flattens it)
- struct arrays arrive as `[values, propertyTypes]`; see `struct_fields()` for both shapes

`sav_data/` (6,800 lines of tables: crash sites, Mercer spheres, slugs, resource purity) is
**build-time only** — `tools/gen_region_names.py` and `tools/gen_resource_nodes.py` use it to
produce committed artifacts. Separate licence question, not on this path.

## Format, as derived

### File shape

```
[ uncompressed header ][ chunk ][ chunk ]...   each chunk = 49-byte preamble + zlib blob
```

### Primitives (`savparse/reader.py`)

Little-endian. The one non-guessable rule: **a string is an int32 length then bytes, and the
SIGN of the length is the encoding** — positive is one byte per char (latin-1), negative is
UTF-16LE at two bytes per char. The count **includes** the trailing null, which is stripped.
Zero length means empty with no bytes at all.

Reads past the end raise rather than truncating; a silently short read produces a header
that parses into plausible nonsense.

### Header (`savparse/header.py`) — DONE

Linear, no offsets to seek by, in this order:

```
i32   save_header_type        14
i32   save_version            60   (52 and below = pre-1.0, see limits)
i32   build_version           495413
str   save_name               'Han Solo_280726-230847'
str   map_name                'Persistent_Level'
str   map_options             '?skiponboarding?ClientIdentity=...'
str   session_name            'Han Solo'
i32   play_duration_s         1151711
i64   save_datetime_ticks     639208697277810000
u8    session_visibility      0
i32   editor_object_version   40
str   mod_metadata            ''
i32   is_modded               0
str   save_identifier         'X2faPVKjX06VaRzClNv5KQ'
i32   (unnamed, 1 on every save seen)
i32   (unnamed, 1 on every save seen)
u64   save_data_hash[0]       6096361947348211065
u64   save_data_hash[1]       9325011144171762175
i32   is_creative             0
```

Then `body_offset`, where `u32 == PACKAGE_FILE_TAG (0x9E2A83C1)`. **That assertion is the
proof the whole walk is right** — every field is positional, so one wrong width silently
rereads the rest. A patch inserting a field is caught there.

**Verification:** 67 saves on the author's disk. Exact agreement on all **31** the vendored
parser can read; failure on exactly the same **36**. Those 36 are `saveVersion` 52 and older
(2021–2023) which the old parser also refuses. Same answers, same limits — nothing in this
project has ever read them.

### Chunks (`savparse/chunks.py`) — DONE

49-byte preamble per chunk:

```
i64  tag                  0x222222229E2A83C1  (CHUNK_TAG)
i64  max chunk size       131072
u8   compressor           3 = zlib
i64  compressed size      \
i64  uncompressed size     |  written TWICE, identically
i64  compressed size       |
i64  uncompressed size    /
[compressed size bytes of zlib]
```

Both size copies are read and compared — a free integrity check on a format with no other
one. The tag is checked **per chunk**: autosaves rewrite every few minutes, so a file torn
mid-write is routine and must fail at the tear with an offset.

**Verification:** all 31 readable saves inflate, **1,194 MB in 1.3 s**, none fail. Reference
save 2.9 MB → 44.4 MB in 0.05 s.

### Body — PARTIALLY MAPPED, this is where to continue

After inflating, the flat stream begins:

```
i64   body size            44376203  (= len(body) - 8, self-describing)
i32   0
i32   522
i32   1017
i32   3
6 bytes                    05 00 06 00 01 00   (three i16? unconfirmed)
i32   -2146988235          (0x80078f35)
str   '++FactoryGame+rel-main-1.2.0'
```

then from offset 67, a run of **`(i32, 16-byte GUID)` pairs** — UE's custom-version array.
At least 40 of them; the count/terminator was not established.

After that comes a large **world-partition / level table**: roughly **774 KB** before the
first literal `Persistent_Level\0` on the reference save, shaped as repeated
`(u32 hash, i32 length, 25-char ASCII id)` groups — level or partition identifiers.
`Persistent_Level` first appears at body offset **774709**, again at **1146829**, then a
cluster at 5798128 / 5798308 / 5798512 / 5798737.

**Do not dump this region to stdout** — it is 774 KB and will blow up a tool result. Slice
narrow windows.

### Objects and properties — NOT STARTED

The remaining bulk. Needed:

1. **Level walk** — per level: name, object-header count, headers, object count, object bodies.
2. **Object headers** — two kinds. Actors carry `typePath`, `instanceName`, a transform
   (rotation quaternion, `position`, scale); components carry no `typePath` (hence the
   `getattr` default in `iter_objects`) and reference a parent.
3. **Property serialiser** — the real work. Unreal writes `name`, `type`, `size`, `index`
   then a type-specific body. Types in play: Int, Int64, Int8, UInt32, Float, Double, Str,
   Name, Object, Struct, Array, Map, Set, Byte, Bool, Enum, Text, Interface.
   Struct bodies needed at minimum: `InventoryItem`, `Transform`, `Vector`, `Quat`,
   `LinearColor`, `Box`, `ClientIdentityInfo`, plus the game's own.

**The size field is the escape hatch.** Every property declares its length, so an unknown
type can be **skipped by length** rather than guessed at. That is what makes a useful
parser far smaller than a complete one, and it should be the fallback everywhere.

## Verification strategy — keep doing this

1. **Black-box parity** against the vendored parser on all 31 readable saves: object counts,
   per-class census, then per-property values on a sampled set of actors.
2. **Golden fixtures.** `tests/fixtures/save_header.bin` is a committed 2 KiB prefix so the
   header tests survive the library's removal and pass with no game install. Do the same for
   a small inflated body slice.
3. **The end-to-end oracle that matters:** run the sidecar both ways and diff the resulting
   projection JSON. It must be byte-identical, or the difference must be explained.
   Reference: 44,307 objects, 566 machine/extractor/generator records, 8,347 structure
   instances, 11,481+ material edges, schema_version 10.

## Opportunities found while doing this

* **Header reads need no subprocess and no decompression** — `read_info` takes a 64 KiB
  prefix. Save discovery and world grouping currently spawn the sidecar per file; they could
  run in-process.
* **`saveDataHash` is in the header** (two u64). Cache validity keys on `mtime_ns` today, so
  a rewritten-but-identical autosave invalidates. A content hash would not.
* **Decompression is not the cost** — 44 MB in 0.05 s. Whatever the sidecar spends, it
  spends in the object walk, so that is where optimisation pays.
* **The partition table is unused data.** ~774 KB of level/partition ids we never read;
  possibly relevant to spatial work, unknown value.

## Limits to keep stated

* 36 pre-1.0 saves are unreadable by both parsers. Not a regression, and nothing reads them.
* `session_visibility` and the two unnamed int32s are skipped, not understood. The fields
  either side verify, which is what makes skipping safe.
* The 6-byte block at body offset 20 is unconfirmed.
