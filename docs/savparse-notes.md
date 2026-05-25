# Replacing the vendored GPL save parser — the derived format

The `.sav` format as derived, what is implemented against it, and every verification number
with the measurement that produced it. Self-contained: a reader who has seen none of the
conversation should be able to continue from this. The projection is complete; what remains
undecoded is listed under *What is left*, and nothing reads it.

**Every number in this document was re-measured against the tree as it now stands.** They
were first taken while several agents were still editing `savparse/`, which makes them
statements about code that no longer exists; the whole-folder parity run, the property-by-
property comparison and all timings were taken again at the end, on one unchanging tree, and
the figures below are those. Where a re-run disagreed with an earlier note, the earlier note
was corrected rather than kept alongside.

## Status

| layer | module | state |
|---|---|---|
| primitives | `savparse/reader.py` (95 lines) | done |
| one exception type | `savparse/errors.py` (27) | done |
| header | `savparse/header.py` (183) | done |
| chunk decompression | `savparse/chunks.py` (123) | done |
| body, levels, object headers | `savparse/objects.py` (652) | done |
| tagged property serialiser | `savparse/properties.py` (1,139) | done |
| composition + the sidecar switch | `savparse/save.py` (152) | done |
| the lightweight buildables' trailing bytes | `savparse/lightweight.py` (185) | done |
| the other seven classes' trailing bytes | — | skipped by declared length; nothing reads them |

**113 tests** across seven `tests/test_savparse_*.py` files, inside a suite of **899 passing, 1
skipped** — the same count with `SATISFACTORY_SAVPARSE=own` and with `=vendor`. They run
against committed fixtures, so they pass with no game install and will survive the vendored
library's deletion.

**The projection is complete.** All **19** of 19 keys are leaf-identical on all 31 readable
saves, including every one of the 224,530 structure instances. See *Verdict*.

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
| `readFullSaveFile(path)` | `.levels[].actorAndComponentObjectHeaders[]` + `.objects[]` | ✅ `savparse.read_full_save` — all 19 projection fields exact on all 31 readable saves |
| `ParseError` | one `except` at the save boundary | ✅ `savparse.ParseError` |

**All three are wired in**, behind `SATISFACTORY_SAVPARSE=own|vendor`, **defaulting to
`vendor`**. Nothing technical blocks the flip any more — it is a decision for the user, and it
belongs to the deletion rather than preceding it, since the licence exposure is the library's
presence and not which branch runs. Until then the switch's job is to make the parity diff a
measurement.

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

**Verification:** all 31 readable saves inflate, **1,194 MB in 1.39 s**, none fail. Reference
save 2.94 MB → 44.4 MB in 0.047 s. All 9,125 chunks carry `max chunk size` 131072 with both
sizes inside it, which is checked as a self-contradiction rather than as the constant — see
the fuzzing section for why that distinction was made after the fact.

### Body layout and object headers (`savparse/objects.py`) — DONE

`read_body(body)` walks the inflated stream and returns levels, object headers, and each
object's property block as a `(offset, length)` slice. It does not parse property bodies.

**Two layouts.** saveVersion **60** bodies open with an archive version header; saveVersion
**52** bodies (25 of the 31 readable saves) do not, and go straight to the grid table.
They are told apart by the header's `(0, 522, 1017)` signature at the cursor.

```
i64  body size                  len(body) - 8, self-describing
59B  archive version header     60 only: i32 0, 522, 1017, 3; 6 bytes; tag 0x80078F35;
                                str '++FactoryGame+rel-main-1.2.0'
i32  custom version count       60 only, then (16-byte GUID, i32 version) pairs -- 13 on
                                the reference save. A COUNT terminates it, not a sentinel.
i32  grid count                 7 on a 60 body, 6 on a 52 one
     per grid: str name, i32 cell size, u32 checksum, i32 cells,
               then per cell: str 25-char id, u32 checksum
i32  sub-level count            3123 on the reference save
     3123 x level record
     one more level record with NO name string -- the persistent level
i32  closing destroyed-actor table, grouped by level name
```

A level record:

```
str  name                       absent on the persistent level
i64  toc size  ; [ i32 header count ][ headers ][ destroyed-actor list ]
i64  data size ; [ i32 object count ][ object entries ]
i32  version (52 or 60)         \
i32  destroyed count ; refs      |  sub-levels only; the persistent level's place is
i32  archive-follows flag (0/1)  /  taken by the body's closing table
[ archive version header + custom versions, when the flag is 1 ]
```

**The earlier "774 KB partition table" was a misreading.** That figure measured to the
first literal `Persistent_Level\0` at body offset 774709. The grid table ends at **71578**
and is ~71 KB; 774709 is just the first sub-level holding an object whose `rootObject` is
the persistent level. Nothing searches for a literal.

**Headers and objects are two separate blocks**, both length-prefixed — parallel lists,
not interleaved.

Object header, both kinds:

```
i32  kind                       1 = actor, 0 = component
str  class path                 exposed as `typePath` on actors only, see below
str  root object                the owning level's name
str  instance name
u32  object flags               UE EObjectFlags. 0x280008 on actors, 0x2C0008 on
                                components -- the difference is exactly RF_DefaultSubObject
                                (0x40000), which is what identified the field.
actor:      i32 needTransform, f32 rotation[4] (x,y,z,w), position[3], scale[3],
            i32 wasPlacedInLevel
component:  str parent actor name
```

The bytes DO name a component's class. It is exposed as `class_path`, **not** `typePath`,
because `iter_objects` reads `getattr(header, "typePath", "")` and the projection's
class-based branching depends on components resolving to `""`.

Object entry:

```
i32  version                    36, 52 and 60 all occur IN THE SAME FILE -- an untouched
                                world-partition cell keeps the bytes it was written with
i32  flag                       1 on version 36/52, 0 on version 60
i32  size                       the property block's length
     size bytes                 <- the slice handed to the property stage
i32  trailing zero              version 60 only, 0 on all 39,015 of them
```

Two things measured for the property stage:

* a **version-60 payload has one extra byte before its property list** — after the
  reference lists on an actor, at the very start on a component;
* a payload ends with the property-list terminator (`"None"` + an int32), or that plus one
  more int32 (every component), or **keeps going** — 3,209 of 44,634 actors carry
  class-specific binary data after it.

**Verification:** black-box against the vendored parser on all 31 readable saves — identical
level counts, identical per-level header and object counts, identical `typePath` multisets.
Object-by-object on the reference save: `instanceName`, the actor/component split and
`position` all match, 0 differences in 44,634. The only divergence anywhere is the
persistent level's `name`, where the old parser reports `None` and this reports
`"Persistent_Level"`. The same 36 pre-1.0 saves are refused by both.

Re-measured over the whole folder on the final tree: **31 bodies, 86,403 levels and
1,243,288 objects walked in 7.77 s, with zero warnings and zero unparsed bytes** — every byte
of every body is either read or stepped over by a length the file declares. The reference
save's 44 MB walks in **0.231 s**. Skipped-by-declared-length totals **2,414,398 bytes** of
destroyed-actor lists across the 31 saves (97,250 on the reference save), reported as
`SaveBody.skipped_toc_bytes` rather than silently dropped.

### Properties (`savparse/properties.py`) — DONE

`read_object(body, slice, actor=…)` turns one property block into `[[name, value], …]`,
which is what `extract_save.props()` reads. `actor` comes from the header: nothing inside a
payload says which kind of object it is.

**Payload frame.** An actor opens with its parent `ObjectReference` and a counted list of
its component children; a component opens with nothing. Object version 60 then writes one
byte before the property list (an object-reference migration flag, 0 everywhere). After the
list's `"None"` terminator come trailing bytes: 4 or 8 on an ordinary object, and much more
on **3,209** actors — 1,889 conveyor chains, 1,297 power lines, and the lightweight
buildable subsystem with 3.1 MB. All are handed on as `(extra_offset, extra_length)`; the
subsystem's are decoded by `savparse/lightweight.py`, the rest are skipped by that length.

**Two tag layouts**, keyed by the *object's* version, not the save's:

```
version 60 (UE5 FPropertyTag)      version 36/52 (UE4)
str  name                          str  name
...  type-name TREE                str  type
i32  size                          i32  size
u8   flags                         i32  array index
i32  array index   if flags&0x01   ...  type-specific tag data
16B  guid          if flags&0x02   u8   has property guid
                                   16B  guid            if that byte is 1
```

The type-name tree is `str name, i32 paramCount, paramCount × tree`, so a struct carries
both its name and its package as nested parameters:
`ArrayProperty(StructProperty(InventoryStack(/Script/FactoryGame)))`. UE4 keeps the same
information as fixed tag-data fields; both are normalised into one tree so there is one
value reader per type, not two.

**The flags byte is the most useful field in the format.** Three bits, each confirmed
against a value the game shows:

| bit | meaning | how it was pinned |
|---|---|---|
| `0x10` | the BoolProperty's value | size is 0 and there is nowhere else for it — this is the "16 means True" `extract_save.truthy` documents |
| `0x08` | the type serialises itself (raw bytes, not a nested property list) | set on `Box`, `Vector`, `Guid`, `InventoryItem`; clear on `InventoryStack`, `FeetOffset`, `FactoryCustomizationData` |
| `0x01` | a nonzero array index follows | absent tag field on v60, always present on v36/52 |

**The per-property size check is the safety net.** After reading a value the cursor must be
exactly `size` past the payload start, or it raises with the offset. A wrong width fails on
that property instead of shifting everything after it — that check is what found every one
of the format details above, including the two below.

**Two things that cost real objects to find.** The version-60 terminator is a *bare name*
with no type tree after it; reading the tree first turned the 4 trailing bytes into a string
length and broke 16,445 objects. And `InventoryItem` writes one extra int32 on version **36**
only, not on 52 — the single place those two versions disagree, worth 87 pickups.

**Struct bodies.** Decided by NAME (`_NATIVE_STRUCTS`), not by the flag, because the flag is
not reliably per-element: the foliage subsystem's `mSaveData` is one MapProperty with `0x08`
set whose keys are native `IntVector` and whose values are property lists. Nine native
structs are enough for every save: `Vector` (three **doubles**, on version-52 objects too —
the width follows the writer), `Quat`, `Box` (6 doubles + a validity byte), `LinearColor`
(four floats), `Guid`, `IntVector`, `FluidBox` (one float of pipe contents),
`ClientIdentityInfo`, `InventoryItem`. `PlayerInfoHandle` and `UniqueNetIdRepl` are kept as
raw bytes on purpose. Everything else — 37 more struct names — is a nested property list.

**Census on the reference save** — 44,634 objects and **82,660 top-level properties**, which
between them declare **208,548 property tags at every depth**, because a struct's fields and a
container's elements are tags too. Counting every tag, 19 distinct types occur:

```
StructProperty 76214   ArrayProperty  19849   SetProperty     1552   Int8Property     167
ObjectProperty 47670   FloatProperty   9508   UInt32Property  1550   StrProperty       26
IntProperty    41135   BoolProperty    5140   MapProperty      677   TextProperty      26
                       ByteProperty    3784   Int64Property    656   InterfaceProperty 19
                                              EnumProperty     567   DoubleProperty    18
                                                                     NameProperty       4
                                                                     SoftObjectProperty 1
```

The **top-level** 82,660 are a different distribution and only 18 types, and it is the one
that says what the layer spends its time on: `ObjectProperty` 34,139, `StructProperty` 22,301,
`ArrayProperty` 18,260, `IntProperty` 17,091, `FloatProperty` 6,472, `BoolProperty` 1,652,
`ByteProperty` 743, `EnumProperty` 554, `Int8Property` 167, then twelve types in single or
double figures. `DoubleProperty` occurs **only** nested — never as a top-level property.

The object split behind those numbers: **23,821 actors and 20,813 components** in 3,124
levels, the actors covering **184 distinct class paths**.

`SoftObjectProperty` is three strings (package, asset, sub-path), not a two-string
reference — the one occurrence declares 76 bytes and only adds up with the third.

**Verification.** All 31 readable saves compared property by property against the vendored
parser, both parsers in one process, one subprocess per save: **1,243,288 objects, 2,269,824
properties, and zero objects whose property names or order differ.** Values: identical on
2,168,837, differing on 100,987, and **every difference is one of five classes with a stated
cause** — none is unexplained, and none is in a field the projection reads. The classes, with
counts over all 31 saves (the reference save alone has 2,906 + 1 + 1 and no warnings):

| count | difference | why |
|---|---|---|
| 81,358 | `Item[1]`: `None` here vs `1`/`2` there, on a stack with no item state | the bytes there are a 0 flag; the vendored parser writes a constant that tracks the layout (1 on version 52/60, 2 on 36). Only element 0 is read, by `_accumulate_inventory` |
| 19,492 | plain `ByteProperty`'s enum name: `None` vs the literal string `"None"` | UE4 writes `"None"` and UE5 writes no parameter; collapsing both keeps one shape across versions in one file. `_phase_costs` takes `[-1]` |
| 75 | skipped by this parser | the three version-52 unnamed-struct containers below; each is also a `warnings` entry |
| 31 | `Guid` as two uint64s vs 16 raw bytes | the vendored parser is itself inconsistent here — uint64s inside a set, bytes inside a struct array. Nothing reads either |
| 31 | `FSoftObjectPath` sub-path: `""` vs `0` | it is a length-prefixed string that is empty on every occurrence, so the two readings are indistinguishable from data |

The 75 in row three are **the same 75 as the warnings**, one for one: three per saveVersion 52
save, named `mSaveData`, `mDestroyedPickups` and `mLootedDropPods` on
`FoliageRemovalSubsystem` and `ScannableSubsystem`. Nothing is skipped quietly.

**Two shape differences have to be normalised before values can be compared at all**, and
both are the ones `extract_save.struct_fields` already documents: a struct value is
`[values, propertyTypes]` here and sometimes bare `values` in the vendored parser (which
carries no types for a map-element struct), and a `propertyTypes` entry is
`[name, typeName, …]` whose tail past `typeName` is a rendering choice —
`['Item','StructProperty',1,'InventoryItem',1,'/Script/FactoryGame',0,8]` against
`['Item','StructProperty',1,'InventoryItem',['/Script/FactoryGame'],8]`. So the comparison
drops types from the value tree and then compares the `(fieldName, typeName)` sequence
separately, because stripping something and not checking it is how a cosmetic difference
hides a real one. Result: **123 properties of 2,269,824 where the type sequences differ, and
0 where a field both parsers name is given a different type.** Every one of the 123 is the
vendored parser omitting types it does not carry. Getting this wrong in the other direction
is worth recording: a first run of the comparison classified all 21,247 struct-bearing
properties as mismatching, and the second, which compared only the top-level value, buried
2,904 real `Item` differences as "unexplained" — the mismatch lives three lists deep, at
`.0.0.1.1`.

**The end-to-end check that matters:** `extract_save`'s whole projection run through this
instead of the vendored parser is identical in every field except `lightweight_counts` and
`structures`, the two that come from the undecoded trailing bytes.

**Cost, measured in one fresh process** on the reference save (2.94 MB on disk, 44.4 MB
inflated): read + header 0.001 s, inflate 0.047 s, level and header walk 0.231 s, **all
44,634 objects' properties 1.791 s** — **2.07 s** in total, against the vendored parser's
**2.4–2.5 s** for the same work in a process of its own. The earlier note that "the time is
in the property bodies" is confirmed: they are **87%** of it, the walk 11% and inflation 2%.

**Limits, stated.** UE4's tag data for a map or a set names the element's *property* type
and not the struct behind it, so a `StructProperty` element on a version-36/52 object is
genuinely ambiguous — native or property list, no way to tell. Reading it as a property list
and keeping the result only when it lands exactly on the declared end recovers
`mItemsPickedUp`, `mActorsBuiltCount` and `mItemsManuallyCraftedCount`; it correctly refuses
`mSaveData` and the scanner's two `Guid` sets, which are skipped by declared size. That is
**three warnings per saveVersion 52 save and zero on a saveVersion 60 one**, in data nothing
above reads.

#### Adversarial pass over the containers and the escape hatches

The module was then attacked deliberately: hand-built property blocks for the cases no save
on disk contains, plus a census over all 31 saves of every path those blocks reach. **Three
defects, all fixed, all one family** — something unreadable was skipped by the *enclosing*
container's declared length, which is not the element's length.

* An untagged map element with no reader (a container inside a container, a `Bool`, an
  `FText`) was skipped to the **map's** end. With pairs still to read, the next key then came
  out of the bytes after the map and the *next* skip dragged the cursor back onto that end —
  so the declared size balanced and the object parsed. A hand-built
  `map<int, array<Vector>>` came back as two pairs, both values `None`, the second key
  invented out of the terminator's length prefix, **no error**. `unknown()` now refuses to
  move the cursor backwards. Skipping the *last* element of a container still works, because
  there the container's remaining length **is** the element's length — verified on real bytes
  by renaming the value type of the calendar subsystem's one-entry
  `mCalendarsOpenedByPlayers`.
* `FText` had the same hole: a history type other than 0xFF inside an *array* of texts
  consumed the rest of the array and the elements after it were invented
  (`[[0, 3], [5, 78]]`, a "history type" that was really a string length). Its skip now goes
  through `unknown()` and inherits the guard.
* A container's element count is now bounded by the bytes left in its own block instead of a
  flat 10,000,000. The count lives *inside* the payload, so the tag's size check does not
  bound it: a count of 9,000,000 planted in a 12-byte array inside a 40 MB body read
  36,000,004 bytes of the following objects, allocated 79 MB and took **13.4 s** before the
  size check noticed. No element of a container is shorter than one byte, so the bound is
  exact. Same reasoning applied to an actor's child-reference list, where a reference is at
  least eight bytes.

**None of the three changed a value.** The same walk over all 31 saves before and after:
1,243,288 objects, 2,269,824 properties, 75 warnings, and the identical digest of every
property value (`24e7bab3f7c7768a713f1813001d5c0c`). The projection diff is unchanged — 17
of 19 keys exact, `lightweight_counts` and `structures` still the two.

**What the census says the containers actually hold**, which is what makes the guards
correct rather than lucky:

* Map key/value types, all 31 saves: only `ObjectProperty->Int64Property`,
  `ObjectProperty->StructProperty`, `ObjectProperty->IntProperty`,
  `StructProperty->StructProperty` and `IntProperty->IntProperty`. **A map value is never
  itself a container** — `mItemsPickedUp` looks like a map of maps and is not: its value is a
  struct, and the inner map is an ordinary tagged property inside that struct's list. Set
  elements: `UInt32Property`, `StructProperty`, `ObjectProperty`.
* **Empty containers are common and must read as empty, not absent**: 918 empty struct
  arrays (`lastEditedBy`), 862 empty `UInt32` arrays and 862 empty sets
  (`RemovedLocationLookup`), 2,531 empty maps (`BuiltPerPlayer`), 9 empty string arrays. A
  version-36/52 empty struct array still writes its inner struct tag, with size 0 — 56 of
  them — so refusing an array of structs without one is right.
* `FText` history is 0xFF on all **576** of them.
* Every `BoolProperty` declares size **0** on both tag layouts (20,609 on version 60,
  126,747 on 36/52). The value is only ever in the tag.
* **63,318** version-36/52 properties carry a nonzero array index (`Locations` on the foliage
  removal data). They arrive as repeated `[name, value]` pairs — the vendored parser does the
  same — and `props()` keeps the last.
* Version-60 tag flags seen: `0x00`, `0x08`, `0x09` (native + array index), `0x10`. Never
  `0x02`, never `0x04`, never anything above `0x10`.
* Strings: **27,648,606** read, of which **25** are negative-length UTF-16 — and all 25 are
  inside an `attempt()` guess that is then thrown away. No save on this disk holds a UTF-16
  string in a property.

Verified as already correct, and worth not re-deriving: skip-by-declared-length recovers
**in every position** — top level on both tag layouts, inside a nested struct's property
list, an array's element type, a self-serialising struct with no known layout, a struct
array — and every following property still reads. A declared size that is too small or too
large by four bytes fails on that property with its offset, in both directions. Structs
inside arrays inside maps, and arrays inside structs inside arrays, read correctly. A struct
array element whose property list has no terminator fails loudly instead of eating the next
element.

**Two messages fixed by the truncation sweep, both in the commonest failure there is.**
Cutting two real saves at fourteen fractions of their length each put **all 28** failures on
the same read — the last chunk's preamble survives the cut, its compressed blob does not — and
`Reader._take` reported it as `read of 5131 at 974874 runs past end (978615)`: no chunk, no
shortfall, and nothing to say the file was merely mid-write. `chunks.decompress_body` now
checks the blob against the bytes left and says `chunk at 2931690 declares 4106 compressed
bytes but 4105 are left in the file, a shortfall of 1`, matching the vendored parser's
shortfall to the byte on all 28. A **negative** compressed size goes through the same guard,
because `_take` rendered that one as "read of -5 … runs past end", which is not just unhelpful
but false. Separately, a save truncated to exactly its header inflates to an **empty** body —
legitimately, there are no chunks — and `read_body` opened with an int64 read that reported
`read of 8 at 0 runs past end (0)`; it now says the body is 0 bytes and why that happens.

### One exception type (`savparse/errors.py`) — DONE

`ParseError` used to live in `objects.py`, so `header` and `chunks` — written earlier —
raised bare `ValueError`, and so did `Reader._take`, which is where the *commonest* failure
comes from: a walk running off the end of a file the game is rewriting. The sidecar catches
one type at the save boundary, so half of all failures fell through to its bare-`Exception`
handler and were reported as `{"error": "ValueError"}` — a class name with no offset, for a
file that is merely mid-write.

`ParseError` now lives below the reader and every layer raises it. `ValueError` is still the
base class, so nothing that caught the old behaviour broke.

Each layer's messages name the layer without being told to, which is why there is no
stage-wrapping anywhere: `header` prefixes every refusal with `saveHeaderType 14,
saveVersion 60: …`, `chunks` says `chunk at 453 …`, and the walk and the serialiser say
`at body offset 71578: …`. The header prefix is the one that matters to a player rather
than a developer: **36 of the 67 saves on this disk are pre-1.0** (`saveHeaderType` 1, 8, 9
and 10) and fail on a bounds check deep in the reader, where the useful thing to say is
"saveHeaderType 10 (only 14 is understood)" and not "read of 473655 at 138 runs past end".
`KNOWN_HEADER_TYPE = 14` is deliberately **not a gate** — the `PACKAGE_FILE_TAG` check is
still the verdict, because a patch may well bump the type without moving a field.

### Composition and the sidecar switch (`savparse/save.py`) — DONE

`read_full_save(path)` is the one call `extract_save.py` makes. It returns a `ParsedSave`
whose `levels[i].actorAndComponentObjectHeaders` and `levels[i].objects` are the parallel
lists `iter_objects` walks, each object carrying `properties` as `[name, value]` pairs.

Two things in it are decisions rather than glue:

* **The inflated body is retained.** Every `ObjectSlice` and every
  `ParsedObject.extra_offset` is an absolute index into it and nothing is copied, which is
  what keeps a 44 MB body at a fifth of a second. Dropping it would leave the 3,209 actors
  with trailing data pointing at a freed buffer, and the failure would look like a decoding
  bug rather than a lifetime bug.
* **`actorSpecificInfo` is `None` rather than empty when nothing decodes the class.**
  `_lightweight` and `_structures` read it through `getattr(obj, "actorSpecificInfo", None)`.
  It is now populated for `FGLightweightBuildableSubsystem` and `None` for the seven other
  classes carrying trailing bytes — never `[]`, because an empty list is what a decoded blob
  with nothing in it looks like, and "not decoded" must not be able to pass for that. A
  partial decode would turn an undercount into a silent one: a foundation census reporting 40
  slabs where 8,347 pieces are built reads exactly like a real answer.

An unreadable *path* is left as `OSError`, not converted. A missing or locked file is not a
save that cannot be parsed, and the sidecar reports the two differently.

**The switch.** `extract_save.py` resolves `SATISFACTORY_SAVPARSE` **at import**, to `own`
or `vendor`, defaulting to `vendor`. A typo raises `RuntimeError` rather than falling back,
because a silent fallback would make a parity run report perfect agreement between the
vendored parser and itself — the one wrong answer this exercise cannot afford. The
environment is inherited by `projection._run_sidecar`, so setting the variable for the MCP
server switches the parser for every save it reads.

`savparse`'s `warnings` are printed to **stderr** and never added to the projection. They are
worth seeing, but a projection field carrying them would make the two parsers differ for a
reason that is not a disagreement.

### The lightweight buildables (`savparse/lightweight.py`) — DONE

Foundations, walls, ramps, catwalks and pillars are not actors. One
`FGLightweightBuildableSubsystem` actor carries every one of them in the class-specific bytes
trailing its empty property list — 3.10 MB and 8,347 pieces on the reference save, 224,530
across the 31. This is the whole of `structures` and `lightweight_counts`.

```
int32   0                     the object's own trailer
int32   2 or 4                version of what follows
int32   classCount
per class:
    reference   the buildable class: empty level name, then the class path
    int32       instanceCount
    per instance — 162 fixed bytes, 157 at version 2, plus two reference paths:
        4 x double   rotation quaternion
        3 x double   position, world centimetres
        3 x double   scale
        reference    the paint swatch
        reference x3 empty on all 224,530 instances
        2 x 4 float  override colours, primary and secondary
        reference    empty
        uint8        0
        reference    the recipe the piece was built from
        reference    empty
        int32        0
        uint8        version 4 only
        int32        version 4 only
```

**How this was derived, and what that leaves uncertain.** The class count and the first
instance count were read off the front and matched against the oracle's census. The record
length came from the *stride between repetitions of the recipe path*, which every instance
carries: a constant 375 bytes across 4,616 consecutive foundations. Where the fields fall
inside that stride came from a per-byte variability map over those 4,616 records — the two
colour alphas are the only non-zero floats, which places both `FLinearColor`s exactly, and one
`uint8` reads 6 on 33 records where the rest read 0, which places the trailing pair.

What no measurement here can settle is how the **always-zero runs are grouped**. The 24 bytes
after the swatch are read as three empty references; three int32 zeros would be byte-identical.
References are the safer reading — a save that populates one still parses — but that is a
reason to prefer it, not evidence for it. Only the swatch slot is ever populated: 224,357
`SwatchDesc` paths across every save on disk, exactly one per instance, and not one
`PatternDesc`, `MaterialDesc` or `SkinDesc`.

**Two versions, and the trap in them.** saveVersion 52 writes blob version **2**, saveVersion
60 writes version **4**, and the difference is exactly the trailing `(uint8, int32)` — 370
bytes per foundation against 375. Version 2 is **25 of the 31 readable saves**, the common
case rather than the legacy one. Refusing an unknown version rather than reading it as the
nearest one is what caught this: the first pass knew only version 4 and turned all 25 saves
into refusals, which the whole-folder diff reported as 25 files where the two parsers disagreed
about readability. Had it guessed instead, it would have read a 370-byte record as 375 and
desynchronised 4,000 records later.

**Verification.** The walk must consume the blob to its last byte, and does: 3,103,373 of
3,103,373 across 18 classes on the reference save, 2,605,860 of 2,605,860 across 15 on a
version-2 save. Every class path is length-prefixed, so a record read one byte short lands the
next class read on a quaternion — checked before the lengths are consumed, because a
quaternion's second int32 is `0x80000000`, a negative length, and a four-gigabyte UTF-16 read
would otherwise report the buffer size instead of the class the walk was looking for.

## Verification strategy — keep doing this

1. **Black-box parity** against the vendored parser on all 31 readable saves, and not on a
   sample: object counts, per-class census, then **every property of every object**, name by
   name and value by value. A comparison that has to strip a cosmetic rendering difference
   should strip it and then compare the stripped values *and* the rendering separately, so
   that "cosmetic" is a measured claim rather than an excuse.
2. **Golden fixtures.** Committed real bytes, so the tests survive the library's removal and
   pass with no game install:
   * `save_header.bin` — 2 KiB file prefix;
   * `save_body.bin` — 4,539 bytes, a **complete** saveVersion 60 body: archive header, four
     grids, one level per object version (36/52/60), an empty level, the unnamed persistent
     record, the real closing table. Only three int32s are synthesized (body size, grid
     count, sub-level count) because those frame a smaller pick of real records;
   * `save_body_v52.bin` — 3,230 bytes, the same recipe on a saveVersion 52 save, which has
     neither an outer archive header nor per-level ones;
   * `save_properties.bin` — 26,200 bytes, 18 real property blocks verbatim, one per code
     path in the serialiser: both tag layouts, every container, every native struct, and the
     awkward ones (an `InventoryItem` carrying weapon state, `FText`, `FSoftObjectPath`, an
     enum byte beside a plain one, a self-serialising struct with no known layout).
3. **The end-to-end oracle that matters:** run the sidecar both ways and diff the resulting
   projection JSON. It must be byte-identical, or the difference must be explained.

   **Done, over all 67 files in the save folder** — `SATISFACTORY_SAVPARSE=vendor` then
   `=own`, same file, JSON compared leaf by leaf:

   * **31 readable, 36 refused, by both, the same 36.** Every refusal is reported as
     `{"error": "parse_error"}` on both sides; only the message text differs, and it has to,
     because the two parsers hit the wrong layout at different fields. (One of the 36,
     `ServerManager_V2.sav`, is not a game save at all — it opens `MSGF`. Both refuse it.)
   * **Of the 19 projection keys, 17 are identical on all 31 saves.** The two that differ are
     `lightweight_counts` and `structures`, on every save, and only ever by being **empty**
     under the own parser: **488 count keys absent, 62 zero-length lists** (two per save),
     and — the number that matters for the verdict — **224,530 structure instances in 488
     classes** that the vendored parser reports and this one does not. **Zero leaves where the
     two disagree on a value**, on any save, in any key. Both gaps come from the undecoded
     trailing bytes.
   * `n_objects` agrees on every save, from 29,734 to 44,643.
   * Reference figures, both parsers: `Han Solo_270726-215626` → **44,307 objects**, 435
     machines + 69 extractors + 62 generators = **566** records, **11,554** material edges,
     1,287 power edges, 6,111 interned actors, 90 building classes, `schema_version` **10**;
     `structures` = 18 classes / **8,347** instances under vendor, empty under own.
     `Han Solo_280726-230847` (the reference save these notes measure) → 44,634 objects,
     438 + 70 + 62 = 570 records, 11,664 material edges.
   * `--list` over the whole folder: same 31/36 split, and **zero differences in any header
     field** of any readable save.
   * Whole-sidecar wall clock including interpreter start, summed over the 31 readable saves:
     **vendor 75.7 s, own 59.2 s** — 1.28×.

   Both parsers reachable from one process boundary is what makes this a measurement. See
   `savparse/save.py` and `extract_save.ENGINE`.

4. **Attack the awkward files, and synthesize the ones the disk does not have.** Everything
   in step 3 is the *normal* case. The saves that would break a parser are the ones nobody
   has: the whole folder is one session, one player, ASCII, unmodded. Re-run independently
   and all of it held — same 31/36 split, no header field differing anywhere, the same two
   projection keys and no others — and then these, which found the two fixes below:

   * **Truncation, twice over.** A saveVersion 60 and a saveVersion 52 save cut at fourteen
     fractions of their length each. **All 28 land on the same read**: the last chunk's
     49-byte preamble survives the cut and its compressed blob does not. Both parsers refuse
     all 28, and the shortfall each reports agrees to the byte.
   * **Single-byte corruption.** Twelve random flips in the body of each save: all inflate
     failures, all refused by both, and this parser turns zlib's error into a `ParseError`
     where the vendored one lets `zlib.error` escape and the sidecar reports
     `{"error": "error"}`. Every flip in the first 40 header bytes: the two disagree about
     **readability** on 26 of them and it is always the same direction — the vendored parser
     gates on `saveHeaderType`/`saveVersion` being known and decodes strings as strict UTF-8,
     so a damaged cosmetic field loses it the save; this one walks the header anyway and
     decodes with `errors="replace"`, and the `PACKAGE_FILE_TAG` check is still the verdict.
     Deliberate, documented in `reader.string` and `header.KNOWN_HEADER_TYPE`, and it never
     goes the other way: there is no byte whose corruption makes this parser accept a *body*
     the vendored one rejects.
   * **Non-ASCII, synthesized.** Unreal writes an FString as ANSI when every character is
     ≤ 0x7F and as UTF-16LE with a **negative** length otherwise, so a player with an umlaut
     in their session name produces a file no save here has. Headers rebuilt with umlaut,
     Cyrillic, CJK and emoji `saveName`/`sessionName`/`mapOptions` and spliced in front of the
     real body: **both parsers return the same strings** and the same object count, and the
     value survives `--list`'s JSON identically under both engines. In the body, every
     even-length ASCII string re-declared as UTF-16 by negating its length prefix — which
     keeps every declared size in the file correct — and with 400 of them patched both parsers
     agree on every `typePath`, `instanceName`, `rootObject` and `parentActorName`; with all
     273,236 patched both refuse, at the same offset.
   * **A modded save, synthesized.** `isModdedSave`, `isCreativeModeEnabled` and a non-ASCII
     `modMetadata` JSON blob round-trip identically. The real hazard is an unrecognised name,
     synthesized by renaming a name in the body to another of exactly the same byte length so
     that every declared size still holds — see the new limit below for what that found.

5. **Fuzz it against itself, exhaustively, and count what survives.** Step 4 asks whether the
   two parsers *agree* on a damaged file. This asks a different question with no oracle in it:
   does every damaged file produce a `ParseError` with an offset, and does every file that
   still parses produce the *same answer*? Roughly 60,000 mutations of two real saves
   (`Han Solo_280726-230847`, saveVersion 60, and `Han Solo_021125-030302`, 52), each
   classified by outcome rather than eyeballed. **Nothing hung** — with the container-count
   bound from step 4 in place, no case anywhere took longer than one clean parse — and after
   the four fixes below, nothing raises anything but `ParseError`.

   What found nothing, which is the more useful half:

   * **1,750 truncation points** — every byte of the header region and 240 through the body,
     on both saves. All refused, every message carrying an offset.
   * **A torn autosave, which is the realistic shape of the failure.** The game rewrites in
     place, so a file read mid-write is a prefix of the new chunk stream followed by a suffix
     of the old one. Spliced at 14 chunk boundaries between two real autosaves of the same
     world: refused every time, by the body's own `len(body) - 8` size field, at body offset 0.
     Each chunk in such a file is individually valid — right tag, matching size copies, clean
     adler32 — so that one int64 is the only thing standing between this project and a
     confidently-reported chimera of two factories.
   * **600 single-bit flips anywhere in the chunk stream**: 599 refused. The one survivor
     landed in the unused padding bits of a deflate block and inflated to **byte-identical**
     output. Generalise that: everything the property serialiser reads sits behind a per-chunk
     adler32, so silently corrupting the *inflated* body takes a deliberate re-compression and
     not a tear. Sweeps that patch the inflated body — the two below — are therefore testing
     the parser's own arithmetic, not a failure mode a disk can produce.
   * **Every int32 and int64 the body walk reads**, in both committed body fixtures, set to 0,
     1, −1 and both int32 extremes, then the *whole walk* fingerprinted — level names, every
     header field, every slice: 1,488 refused, and of the 688 that parsed only **9** changed
     the answer, all 9 a string length reading as shorter or empty. A length that says "empty"
     is indistinguishable from an empty name; there is nothing to check.
   * **~30,000 mutations inside real property blocks** — every length field of 75 objects
     picked to cover all seven (version, actor?, has-trailing-data?) buckets, plus every byte
     of the small ones. No exception but `ParseError`, no truncated property list, and the
     only silent changes were bytes inside a property *name*, which is untestable for the same
     reason.
   * **`read_info` on all 67 files in the folder**, and every int32 in two real headers set to
     six adversarial values: 31 readable and 36 refused, unchanged, and no exception type but
     `ParseError`.

   What it found, all four now fixed and each with a test in
   `tests/test_savparse_robustness.py`:

   * **A crafted 29 KB payload of nested `StructProperty` tags raised `RecursionError`**, not
     `ParseError`. Written over one real object and run through the sidecar it came out as
     `{"error": "RecursionError", "detail": "maximum recursion depth exceeded"}` with a
     traceback on stderr and **no offset** — exactly the report `errors.py` was written to
     abolish, arriving through the one door it did not cover. `_Decoder.property_list` now
     counts its own depth against `_MAX_NESTING = 32`; the deepest real list in any of the 31
     saves is **4**.
   * **A property list that terminates early was absorbed in silence.** Overwriting one
     property's name with the nine bytes that spell the `"None"` terminator made a
     `Build_ConstructorMk1` read as **2 properties instead of 13**, filing the other 1,352
     bytes as trailing data with no error and no warning; through the sidecar, **exit 0 and a
     complete projection** — 44,634 objects, 438 machines, and one constructor with no recipe
     and no inventories. Nothing looked at the payload as a whole, only one property at a
     time. Now bounded by what is measurable over all **1,243,288** objects of the 31 saves:
     no object leaves a trailer shorter than **4** bytes, and a **component** leaves exactly 4
     (5,300 of them) or 8 (562,556) and never more. Injecting the terminator into every
     component of both saves is now refused 104 of 104 times and was silent 104 of 104 before.
     An actor's trailer stays unchecked — see the limit below.
   * **The chunk preamble's `max chunk size` was read and thrown away.** Setting each byte of
     three chunk preambles to five values — 588 mutations — caught every field except that
     one: **99 undetected changes, all eight of its bytes**. It is 131072 on all 9,125 chunks
     of all 31 saves, but requiring the constant would refuse a save the day the game picks a
     different block size, so what is checked is the contradiction: a maximum smaller than the
     compressed or uncompressed size written beside it.
   * **The `PACKAGE_FILE_TAG` proof was skipped when the file ended at the header.** A save cut
     to exactly its 453-byte header returned a fully populated `SaveInfo`, and the refusal then
     came from two layers down as `read of 8 at 0 runs past end (0)` — an offset into a body
     that does not exist, which reads like a corrupt save rather than one the game has only
     started writing. That string is what a player sees beside the filename in `--list`.
     `decompress_body` also says `no chunk at N` now instead of returning an empty body.

## Opportunities found while doing this

* **Header reads need no subprocess and no decompression** — `read_info` takes a 64 KiB
  prefix. Save discovery and world grouping currently spawn the sidecar per file; they could
  run in-process.
* **`saveDataHash` is in the header** (two u64). Cache validity keys on `mtime_ns` today, so
  a rewritten-but-identical autosave invalidates. A content hash would not.
* **Decompression is not the cost** — confirmed, and now split four ways in a fresh process
  on the reference save: read + header **0.001 s**, inflate 44.4 MB **0.047 s** (2%), level
  and header walk **0.231 s** (11%), all 44,634 objects' properties **1.791 s** (87%). If
  anything here is ever optimised it is the property bodies.
* **Holding every parsed object costs 0.53 s of that 1.791 s.** Parsing each property block
  and *discarding* it takes 1.257 s; keeping all 44,634 takes 1.791 s and about 136 MB. The
  projection makes exactly one pass over the objects, so a streaming `read_full_save` would
  save a quarter of the whole parse and all of that memory. Not done: it would change the
  shape the trailing-bytes work builds on, and this is a measured opportunity rather than a
  guess.
* **The grid table is unused data.** ~71 KB of world-partition cell ids and cell sizes we
  never read (`MainGrid` at 12800 uu, `ExplorationGrid` at 20480). It is a cell→size map
  over the whole world, so it may be worth something to spatial work.
* **`EObjectFlags` is on every header** and distinguishes a default subobject from a spawned
  one. Nothing reads it; it might separate blueprint-placed from hand-placed buildings.

## Limits to keep stated

* 36 of the 67 files are unreadable by both parsers. 35 are pre-1.0 saves (`saveHeaderType`
  10 × 14, 9 × 9, 8 × 12) and one, `ServerManager_V2.sav`, is not a save at all. Not a
  regression, and nothing reads them. The readable 31 split **25 at saveVersion 52 and 6 at
  60**, all `saveHeaderType` 14.
* **The own parser is not the default**, though the projection now agrees on all 19 keys.
  Flipping it changes nothing about the licence, which is what the exercise is for, so the
  flip belongs to the deletion rather than preceding it. There is a test that fails if the
  default moves.
* `session_visibility` and the two unnamed int32s are skipped, not understood. The fields
  either side verify, which is what makes skipping safe.
* The 6-byte block at body offset 20 is unconfirmed.
* The **destroyed-actor lists** are stepped over by declared length, not decoded — 97,250
  bytes on the reference save, reported as `SaveBody.skipped_toc_bytes`. Nothing above reads
  them. The skip is validated by two independent lengths agreeing: the header walk must end
  inside the block, and the next block's size field must be exactly where the block's
  declared end says it is.
* The level trailer's `version` (52/60) and `archive-follows` flag are read and range-checked
  but not *explained*. The flag's correlation with a following archive header is exact on all
  3,123 sub-levels, and the header's own signature is verified when it says one follows.
* The extra int32 after a version-60 payload is **always 0**, so whether it is a trailing
  field or the high half of an int64 size cannot be settled from data. It is read as a
  trailing int32 and required to be 0, which fails loudly the day that changes.
* The **trailing bytes after a property list** are decoded for one class of the eight that
  carry them — the lightweight buildable subsystem, which is every foundation and wall in the
  save. The other seven are skipped by declared length and no projection field reads them.
* **An actor's trailing bytes are not length-checked, so an actor's property list terminating
  early is still silent.** The component half of that check is exact — 562,556 components leave
  exactly 8 bytes and 5,300 leave 4, over all 567,856 of them — and `>= 4` holds for every
  object of every save, but an actor may legitimately carry any amount:
  88,066 of 675,432 do, in exactly **eight classes** — `FGConveyorChainActor` and its
  `RepSizeMedium`/`Large`/`Huge` variants, `Build_PowerLine_C`, and once per save each
  `FGLightweightBuildableSubsystem`, `BP_CircuitSubsystem_C` and `BP_PlayerState_C`. Bounding
  it means whitelisting those eight names, and **that whitelist belongs to whoever decodes
  those bytes**, not to a guard written before them: refusing a whole save because a patch
  taught a ninth class to carry data would be a worse failure than the one being prevented.
  Measured exposure: injecting the `"None"` terminator over a property name silently truncates
  3,968 of 3,968 actors and 0 of 104 components. It is defence in depth rather than urgent —
  every byte involved sits behind a per-chunk adler32, so no torn or bit-rotted file can
  produce it, only a parser misread or a deliberate forgery. `_TRAILER_SIZES` in
  `properties.py` is where to extend the check.
* An object payload's **version-60 leading byte** is read and discarded. It is 0 on every
  object of every save, so what it means is a guess and is not made.
* **`Int8Property` yields raw bytes** and **`BoolProperty` yields the raw flags byte** rather
  than an `int` and a `bool`. Both are compatibility with the vendored parser's shapes, which
  the projection was written against; neither is a claim about the right Python type.
* A struct element inside a **version-36/52 map or set** is genuinely ambiguous — the tag
  names the property type and not the struct — so it is read as a property list and kept only
  if it lands exactly on the declared end. Three per saveVersion 52 save do not and are
  skipped: `mSaveData`, `mDestroyedPickups`, `mLootedDropPods`.
* `PlayerInfoHandle` and `UniqueNetIdRepl` are kept as raw bytes deliberately, so that a
  genuinely new struct still shows up as a warning instead of hiding among them.
* **An untagged element has readers for the five map/set element types that occur and no
  others.** A container, a `BoolProperty` or a `TextProperty` as a map element would raise
  with an offset rather than be skipped, because only the *last* element of a container can
  be skipped honestly. Readers for them were deliberately **not** added: a reader that has
  never been run against real bytes is a worse outcome than a loud failure, which is the same
  reason the 16-bit scalar types are absent.
* The **UTF-16 string branch is not exercised by any save on this disk** — 25 negative
  lengths in 27.6 million strings, every one inside a guess that is discarded. The
  sign-of-the-length rule is right (it is what the game writes for non-Latin text) but it is
  verified by construction in `test_savparse_header.py`, not by data. It has since been
  verified **against the oracle** by construction too: headers rebuilt with umlaut, Cyrillic,
  CJK and emoji strings, and a body with 400 of its strings re-declared UTF-16 by negating
  the length prefix (which leaves every declared size correct), give byte-identical results
  from both parsers. That is as close to data as it gets until somebody plays with an umlaut
  in their session name.
* **An unrecognised struct name loses the whole save on a version-36/52 object, and only the
  property on a version-60 one.** Version 60's flags byte has `0x08` for "this struct
  serialises itself", so an unknown native struct there becomes `struct 'X' serialises itself,
  kept as bytes` — measured by renaming `FluidBox` to `MyModBox` in a v60 save's body:
  44,634 objects still read, 798 warnings, and the vendored parser refuses the file outright.
  UE4's tag has no such bit, so `native_hint` is always false and an unknown struct is read as
  a property list with nothing to referee it; the same rename on a v52 save is a `ParseError`.
  **25 of the 31 readable saves are v52**, so this is the version it matters on. Nothing on
  disk hits it — every struct name in every save is either in `_NATIVE_STRUCTS` or genuinely a
  property list — and the vendored parser is worse here, refusing all of these. The fix is
  known and was left undone on purpose: a top-level `StructProperty` declares its size, so
  routing an *unknown-named* struct on an old-version object through `attempt()` would turn
  the loss into a skip plus a warning. It must apply **only** where `end` is the struct's own
  end. Called from `struct_array` or `element`, `end` is the *container's* end, so `attempt`
  would reject every element but the last and skip real `SplinePointData` arrays that parse
  correctly today. That needs a flag threaded through `struct()`, which is more than a
  one-liner in the module the size check protects.
* Version-60 tag bits **above `0x10` are ignored, while `0x04` refuses the save**. That is
  inconsistent, and deliberately left as it is: no bit above `0x10` is set on any of the
  2,269,824 properties, and inventing a meaning for one — or refusing a save because of one —
  are both guesses. If a patch ever sets one, the property's size check is the net.
* A version-60 `BoolProperty` that declared a payload byte would **refuse the save** rather
  than read the byte, since the value is taken from the tag. Every bool in every save
  declares size 0, so this is a statement of what the parser assumes, not a known risk.

## What is left

**Nothing the projection reads.** What remains undecoded is the trailing class-specific bytes
of **seven** classes; the eighth, the lightweight subsystem, is done. They are handed on as
`(extra_offset, extra_length)` on `ParsedObject`, stepped over by declared length, and no
projection field is built from any of them.

Who carries them, measured over all 31 saves — **88,066 of 675,432 actors, in exactly eight
classes**, and no others (the 31 saves hold 675,432 actors and 567,856 components):

| class | actors, all 31 saves | trailing bytes, all 31 saves |
|---|---|---|
| `FGConveyorChainActor` | 50,660 | 76.6 MB |
| `Build_PowerLine_C` | 36,773 | 8.0 MB |
| `FGConveyorChainActor_RepSizeMedium` / `Large` / `Huge` | 261 / 155 / 124 | 6.9 / 9.4 / 14.1 MB |
| `FGLightweightBuildableSubsystem` — **decoded** | 31 — one per save | 81.9 MB |
| `BP_CircuitSubsystem_C` | 31 — one per save | 4,553 B |
| `BP_PlayerState_C` | 31 — one per save | 558 B |

On the reference save that is **3,209 actors and 7,370,871 bytes**: the lightweight buildable
subsystem alone 3.10 MB, 1,889 conveyor chains 2.75 MB, their 20 rep-size variants 1.24 MB,
1,297 power lines 0.28 MB, and 131 bytes between the circuit subsystem and the player state.
Everything else — 44,634 objects less those 3,209 — leaves exactly 4 or 8 bytes.

The 81.9 MB in that table was the whole gap, and it is closed: **224,530 structure instances
in 488 classes** now come out of `savparse/lightweight.py` rather than the vendored parser.

What the remaining seven would be worth, if anyone wants them:

* **Conveyor chains**, by far the biggest at 76.6 MB plus 30.4 MB in the RepSize variants, hold
  what is physically *on* the belts. The projection derives throughput from recipes and machine
  clocks instead, which is the number a planner wants; belt contents would only say whether a
  line is backed up right now. `factory_health` infers that from machine state already.
* **Power lines**, 8.0 MB, presumably hold their two endpoints. The power graph is already
  built from properties and `graph` agrees with the vendored parser on every save, so this is
  redundant rather than missing.
* The **circuit** and **player-state** subsystems are 4.5 KB and 558 B across all 31 saves.

Decoding them would also close one thing left open on purpose: with all eight classes known, an
**actor's** trailer could be length-checked the way a component's already is, instead of being
accepted as whatever the object declared.

## Verdict

**Is the own parser ready to become the default? Yes — on the evidence, and the decision is
still the user's.** The acceptance test this document set is met: **19 of 19 projection keys
leaf-identical on all 31 readable saves**, no key present under one parser and absent under the
other, the same 36 files refused with the same reasons, `n_objects` equal everywhere, and 1.23×
faster end to end over the folder (vendor 79.0 s, own 64.3 s of sidecar wall). 899 tests pass
under both engines. The one thing that had blocked it — `lightweight_counts` and `structures`
coming out empty, which `graph/structure.py` and `spatial/elevation.py` would have turned into
`factory_sites` returning nothing rather than failing — is gone.

`vendor` remains the default anyway, and `test_the_sidecar_still_defaults_to_the_vendored_parser`
still fails if that moves. **Flipping it buys nothing on its own:** the licence exposure comes
from the library being *in the repository*, not from which branch of an `if` runs, so the
decision that matters is the deletion. The flip is one line, is reversible by an environment
variable, and should happen as part of that deletion rather than before it.

**What would have to be true to delete `sidecar/vendor/`:**

1. ~~The trailing bytes are decoded and the whole-folder diff reads 19 of 19 keys.~~ Done.
2. `SATISFACTORY_SAVPARSE=own` becomes the default, and the tests that pin the default flip
   with it.
3. The projection JSON is re-diffed **after** the flip, over the whole folder, not sampled —
   the same measurement is the acceptance test. Note that after the deletion this diff can no
   longer be run at all, so it is worth banking a copy of the vendored parser's output for
   every save first; a committed projection per save would make the comparison repeatable
   forever, at roughly 130 KB each.
4. Someone decides the licence question separately for `sav_data/`, which is build-time input
   to `tools/gen_*.py` and is not on this path.
5. Every reference to the library outside `sidecar/vendor/` is removed or reworded:
   `sidecar/extract_save.py`'s two-engine switch, `tests/test_savparse_save.py`'s default pin,
   and prose in `README.md`, `DESIGN.md` and this file.
6. The deletion is the user's call. Nothing here should make it for them.

**What is still unknown about the format**, distinct from what is merely not decoded:

* The **contents of those trailing bytes** for the seven classes still skipped. The eighth,
  the lightweight subsystem, is decoded from the bytes — but three things inside it stay
  unknown even so: how the always-zero runs are grouped (three empty references or three
  int32s is undecidable from any save on this disk), what the `(uint8, int32)` pair means that
  reads `6, 0` on 33 of the reference save's 4,617 foundations and `0, -1` on the rest, and
  what the second override colour is for, since every instance measured has both set to
  `[0,0,0,1]`.
* Fields read and discarded because their meaning cannot be derived from data that never
  varies: `session_visibility` and the header's two unnamed int32s, the archive header's
  6-byte block at body offset 20, per-cell grid checksums, `EObjectFlags` beyond the one bit
  that separates actor from component, an object payload's version-60 leading byte (0 on
  every object of every save), and the int32 after a version-60 payload — always 0, so
  whether it is a trailer or the high half of an int64 size is not decidable from any save on
  this disk. Each is read, range-checked where a range exists, and required to be what it has
  always been, so a patch that changes one fails loudly.
* The **destroyed-actor lists**, stepped over by declared length: 2,414,398 bytes across the
  31 saves. Their framing is known (the skip is validated by two independent lengths agreeing);
  their contents are not read.
* Whether **UTF-16 strings** appear where this parser expects them. 25 of 27,648,606 strings
  read have a negative length and all 25 are inside a guess that is discarded, so the branch
  is verified by construction and against the oracle by construction, never by a save a player
  produced.
* What **tag bits above `0x10`** would mean. None is set on any of the 2,269,824 properties.
* Whether a **version-36/52 map or set of structs** can be disambiguated at all. The tag names
  the element's property type and not the struct, so three properties per saveVersion 52 save
  are skipped and warned about. This is a limit of the format as written, not of the reader.
