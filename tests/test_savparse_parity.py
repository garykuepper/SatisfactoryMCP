"""The agreement with the deleted parser, kept falsifiable after it was deleted.

For the length of this reimplementation the acceptance test was a diff: run the same save through
the vendored GPL-3.0 parser and through ours, and compare the projection JSON leaf for leaf. That
diff was the whole argument, and **deleting the library destroyed the ability to re-run it.**

So it was banked first. ``fixtures/vendor_parity.json`` holds, per save and per projection key, a
digest of the value the *vendored* parser produced, recorded in the last minutes before its
deletion, when the two agreed on **all 20 keys on all 31 saves it could read**. This file replays
that comparison against the surviving parser.

**What this can and cannot catch.** It catches our parser drifting away from what the two agreed
on -- which is the regression that matters, because every claim in ``docs/savparse-notes.md``
rests on that agreement. It cannot catch a fault they *shared*: if both parsers misread the same
field the same way, the digests agree and always will. That limit is inherent in an oracle, was
inherent while the oracle was still here, and is the reason the notes also record predicates
measured against the bytes rather than against the other parser.

These tests need real saves and skip without them. That is deliberate: the rest of the suite runs
on committed fixtures with no game install, and this one is the exception, because a digest of a
projection is only meaningful against the save it came from.

**Why the bank is not re-banked, ever.** The projection has since grown a placement yaw and a
``belts`` key (schema 12), a ``pipes`` key (schema 13) and a ``storage`` key (schema 15). None
of those fields existed while
the oracle did, so it never had an opinion about them, and re-recording the bank against this
parser would replace an independent measurement with this parser's own output -- the one thing
that would make every test here vacuous. So the comparison runs on a projection FILTERED BACK
to the schema-11 shape, by the explicit list in ``POST_11_ADDITIONS`` below. Additive fields are
legitimately outside the deleted oracle's scope; a *changed* schema-11 field is exactly what
this still catches.

Every later schema adds its own entry to that list rather than re-banking, which is why the
list is keyed by what was added and annotated with which schema added it.

**Schema 16 is the first entry that is not an addition**, and it needed a decision rather than
a line: it CORRECTED ``inventories``, which is one of the banked keys. The choice made, and
the choice rejected, are argued in ``_unfix_16``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "vendor_parity.json"
SIDECAR = REPO / "src" / "satisfactory_mcp" / "core" / "saveio" / "extract.py"

#: Header keys that describe the FILE rather than the world, so they are excluded from the
#: digest: a save copied to another path or re-read after a touch is the same world.
VOLATILE = {"path", "filename", "mtime_ns", "size"}

#: Everything every schema after 11 added, named one by one rather than detected. Guessing
#: structurally -- "drop keys the bank has never seen", "drop record fields the bank cannot know
#: about" -- would also silently absorb a field this parser started emitting BY MISTAKE, which is
#: the class of regression the bank exists to catch. Written out, adding to this list is a
#: decision somebody has to make and a reviewer can see.
POST_11_ADDITIONS = {
    #: Whole new top-level keys: per-belt spline polylines (12), per-pipe ones (13), the
    #: splitters and mergers those belt runs pass through (13), and the containers and fluid
    #: buffers with their contents (15).
    #:
    #: Schema 14 added a FOURTH COLUMN to a pipe segment -- the index of its own actor in
    #: ``graph["actors"]``, which joins a drawn pipe to the connection graph -- and needs no
    #: entry of its own for one reason worth writing down rather than leaving to be rederived:
    #: the change is confined inside ``pipes``, and ``pipes`` is already dropped whole. Had it
    #: widened a schema-11 row instead, it would have owed ``row_width`` an entry, exactly as
    #: ``structures`` does below.
    #:
    #: **Schema 15's spline tangents are the same case, and it is worth saying so out loud
    #: because they land in two keys rather than one.** A belt segment gained a fourth column
    #: and a pipe segment a fifth -- the curve through the points either side of each span --
    #: and both are inside ``belts`` and ``pipes``, which are dropped whole here. So the
    #: tangents need no entry, and the reason is not "they are new" (everything in this list is
    #: new) but "the key that carries them was already outside the oracle's scope". The
    #: ``storage`` key beside them is a genuinely new top-level name and IS listed.
    "keys": ("belts", "pipes", "attachments", "storage"),
    #: The version label is itself one of the 20 banked keys, and it is the one key that is
    #: SUPPOSED to differ. A projection filtered back to the schema-11 shape claims the
    #: schema-11 number; leaving the current number here would report drift on every save on
    #: the grounds that the schema changed, which is the thing being announced rather than a
    #: fault.
    "schema_version": 11,
    #: Schema 12. A new field on every record of these keys: top-down placement yaw in degrees.
    "record_fields": {"machines": "yaw", "extractors": "yaw", "generators": "yaw"},
    #: Schema 12. ``structures.instances`` rows were ``[classIndex, x, y, z]`` and gained a
    #: fifth column, the same yaw. A row is positional, so the addition is a length, not a name.
    "row_width": {"structures": 4},
    #: Schema 16, and the first entry here that is not an ADDITION. See ``_unfix_16`` below for
    #: what it undoes and why it is undone this way; these are the three container classes the
    #: schema-11 bucket rule could not see, which is the whole of the difference.
    "storage_bucket_fix": (
        "Build_StoragePlayer_C",
        "Build_StorageIntegrated_C",
        "Build_StorageBlueprint_C",
    ),
}


def _unfix_16(projection: dict, inventories: dict) -> dict:
    """``inventories`` with schema 16's storage-bucket fix put back, for comparison only.

    **The one place this file compares a CHANGED schema-11 key rather than dropping a new
    one, so the choice is argued rather than made.** Schema 16 corrected
    ``inventories["storage"]``: the bucket rule matched three substrings where it meant
    membership of STORAGE_CLASSES, so the Personal Storage Boxes, the HUB's own container and
    the Blueprint Designer's were bucketed as machine buffers -- 10,667 units over 31 item
    classes on the reference save, moved out of ``machine`` and into ``storage``. ``header``
    aside, ``inventories`` is one of the twenty keys the vendored parser and this one were
    shown to agree on, and that bank is never re-recorded. So the fix moves a banked value on
    every save, and something has to give.

    The alternative was a per-field exception: declare ``inventories`` outside the oracle's
    scope from schema 16 on, and stop comparing it. That is one line and it is what the phrase
    "documented exception" would have bought -- at the price of retiring the key whole. The
    two parsers agreed about the PLAYER bucket, about the machine bucket's other 6,500 stacks
    and about all 52 item classes; none of that is affected by this fix, and none of it would
    be checked again.

    So the split is reconstructed instead. Only three classes moved, ``storage`` carries those
    same containers' contents per instance, and it is dropped whole here anyway -- so
    subtracting them from ``storage`` and adding them back to ``machine`` lands exactly on the
    numbers the old rule produced, in integers, with nothing rounded. What the bank goes on
    checking is everything else in the key.

    **What this does cost, stated rather than left to be found.** The reconstruction is
    computed from this parser's own ``storage`` rows, so if this parser started misreading one
    of those eight containers, ``storage`` and ``inventories`` would move together and cancel:
    that one drift is now invisible here. It is real, it is confined to eight containers of
    one key, and it is smaller than the exception's cost, which is the whole key on every
    save for ever. It is also not a new KIND of blindness -- an oracle can never catch a
    fault the two sides share, which the module docstring says at the top.
    """
    moved: dict[str, float] = {}
    for row in projection.get("storage") or ():
        if isinstance(row, dict) and row.get("cls") in POST_11_ADDITIONS["storage_bucket_fix"]:
            for item, amount in row.get("items") or ():
                moved[item] = moved.get(item, 0) + amount
    out = {bucket: dict(stacks) for bucket, stacks in inventories.items()}
    for item, amount in moved.items():
        rest = out.get("storage", {}).get(item, 0) - amount
        # Removed rather than left at zero: the old rule never wrote a key it had counted
        # nothing into, so a lingering ``item: 0`` would digest differently and read as drift.
        if rest:
            out["storage"][item] = rest
        else:
            out.get("storage", {}).pop(item, None)
        out["machine"][item] = out.get("machine", {}).get(item, 0) + amount
    return out


def as_schema_11(projection: dict) -> dict:
    """The projection with every post-11 addition removed, and one correction put back.

    Not a general downgrade: it undoes exactly ``POST_11_ADDITIONS`` and leaves every other
    difference -- which is the point, because every other difference is drift.
    """
    out = {k: v for k, v in projection.items() if k not in POST_11_ADDITIONS["keys"]}
    if "schema_version" in out:
        out["schema_version"] = POST_11_ADDITIONS["schema_version"]
    if isinstance(out.get("inventories"), dict):
        out["inventories"] = _unfix_16(projection, out["inventories"])
    for key, field in POST_11_ADDITIONS["record_fields"].items():
        if isinstance(out.get(key), list):
            out[key] = [
                {k: v for k, v in record.items() if k != field}
                if isinstance(record, dict)
                else record
                for record in out[key]
            ]
    for key, width in POST_11_ADDITIONS["row_width"].items():
        payload = out.get(key)
        if isinstance(payload, dict) and isinstance(payload.get("instances"), list):
            out[key] = {
                **payload,
                "instances": [
                    row[:width] if isinstance(row, list) else row for row in payload["instances"]
                ],
            }
    return out


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]


@pytest.fixture(scope="module")
def banked() -> dict:
    if not FIXTURE.is_file():
        pytest.skip("vendor parity fixture not committed")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def saves_root() -> Path:
    # `Path("")` is `Path(".")` and IS a directory, so an unset variable must be rejected before
    # the is_dir() check -- otherwise this silently searches the repo root, finds no saves, and
    # reports "none of the banked saves is on this machine" on a machine that has all 31.
    env = os.environ.get("SATISFACTORY_SAVES")
    candidates = [Path(env)] if env else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "FactoryGame" / "Saved" / "SaveGames")
    for root in candidates:
        if root.is_dir():
            return root
    pytest.skip("no save directory on this machine")
    raise AssertionError("unreachable")


def _projection(path: Path) -> dict:
    out = subprocess.run(
        ["uv", "run", "python", str(SIDECAR), str(path)],
        cwd=str(REPO),
        capture_output=True,
        check=False,  # a refusal is data here: the caller asserts on the payload, not the code
    )
    return json.loads(out.stdout.decode("utf-8", "replace"))


def test_the_banked_reference_is_what_it_claims(banked):
    """The fixture is evidence, so its own shape is worth pinning.

    A truncated or half-written bank would make every comparison below vacuously pass. The
    schema stays 11 for ever: it records what the oracle emitted, not what this parser emits.
    """
    assert banked["_meta"]["saves"] == len(banked["saves"]) == 31
    assert banked["_meta"]["schema_version"] == 11
    for name, entry in banked["saves"].items():
        assert "header" in entry, name
        assert entry["n_objects_value"] > 0, name
        # 20 projection keys plus the readable count beside them.
        assert len(entry) == 21, (name, len(entry))


def test_the_schema_11_filter_removes_the_new_fields_and_only_those():
    """The mechanism the comparison below now depends on, pinned without a save.

    Two halves, and the second is the one that matters. A filter that removed too much -- or
    that simply returned a constant -- would make every digest agree for ever, so it is not
    enough to show that adding the later schemas' fields leaves the digests alone: changing a
    schema-11 field must still move them.
    """
    eleven = {
        "schema_version": 11,
        "machines": [{"cls": "Build_SmelterMk1_C", "pos": [1.0, 2.0, 3.0]}],
        "extractors": [{"cls": "Build_MinerMk2_C", "pos": [4.0, 5.0, 6.0]}],
        "generators": [{"cls": "Build_GeneratorCoal_C", "pos": [7.0, 8.0, 9.0]}],
        "structures": {"classes": ["Build_Foundation_8x1_01_C"], "instances": [[0, 10, 20, 30]]},
        # The bucketing the deleted parser was compared against: a Personal Storage Box's
        # contents counted as a machine buffer, and 60 of the 100 Iron Plate -- the box's
        # share -- missing from what the player can spend. Wrong, and what the bank holds.
        "inventories": {
            "player": {"Desc_Wire_C": 7},
            "storage": {"Desc_IronPlate_C": 40},
            "machine": {"Desc_IronPlate_C": 60, "Desc_Rubber_C": 5},
        },
        "warnings": [],
    }
    sixteen = {
        "schema_version": 16,
        "machines": [{"cls": "Build_SmelterMk1_C", "pos": [1.0, 2.0, 3.0], "yaw": -20.0}],
        "extractors": [{"cls": "Build_MinerMk2_C", "pos": [4.0, 5.0, 6.0], "yaw": 90.0}],
        "generators": [{"cls": "Build_GeneratorCoal_C", "pos": [7.0, 8.0, 9.0], "yaw": 0.0}],
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [[0, 10, 20, 30, -20.0]],
        },
        # A curved belt, so the schema-15 tangent column is actually present and not just
        # declared absent: a filter that only ever saw three-column rows would pass this test
        # while dropping nothing.
        "belts": {
            "classes": ["Build_ConveyorBeltMk3_C"],
            "segments": [[0, 0, [[1, 2, 3]]], [0, 0, [[1, 2, 3], [4, 5, 6]], [[7, 8, 9, 1, 2, 3]]]],
        },
        "attachments": [
            {"cls": "Build_ConveyorAttachmentSplitter_C", "pos": [1.0, 2.0, 3.0], "yaw": 90.0}
        ],
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 3, "fluid": "Desc_Water_C"}],
            "segments": [[0, 0, [[1, 2, 3], [4, 5, 6]], 4, [[7, 8, 9, 1, 2, 3]]]],
        },
        # Two containers, and the second is the one schema 16 moved. Its 60 Iron Plate are in
        # ``storage`` below and were in ``machine`` before, which is exactly what _unfix_16
        # has to undo -- and the Rubber beside them is a real machine buffer that must not be
        # touched by the undoing.
        "storage": [
            {
                "cls": "Build_StorageContainerMk1_C",
                "instance": "x.Build_StorageContainerMk1_C_1",
                "pos": [1.0, 2.0, 3.0],
                "yaw": 90.0,
                "items": [["Desc_IronPlate_C", 40]],
                "slots": 24,
            },
            {
                "cls": "Build_StoragePlayer_C",
                "instance": "x.Build_StoragePlayer_C_2",
                "pos": [4.0, 5.0, 6.0],
                "yaw": None,
                "items": [["Desc_IronPlate_C", 60]],
                "slots": 10,
            },
        ],
        "inventories": {
            "player": {"Desc_Wire_C": 7},
            "storage": {"Desc_IronPlate_C": 100},
            "machine": {"Desc_Rubber_C": 5},
        },
        "warnings": [],
    }
    filtered = as_schema_11(sixteen)
    assert filtered == eleven, "the filter did not land back on the schema-11 shape"
    assert {k: _digest(v) for k, v in filtered.items()} == {
        k: _digest(v) for k, v in eleven.items()
    }

    moved = dict(sixteen)
    moved["machines"] = [{**sixteen["machines"][0], "pos": [1.0, 2.0, 99.0]}]
    assert _digest(as_schema_11(moved)["machines"]) != _digest(eleven["machines"]), (
        "the filter hides a changed schema-11 field, which is the drift the bank exists to catch"
    )

    # And the same demand of the schema-16 undo specifically, because it is the one step here
    # that RESTORES a value rather than dropping one: a reconstruction that simply copied the
    # bank's shape would pass the equality above and hide every stack in the key for ever. A
    # container the two parsers would have read differently still has to move the digest.
    misread = dict(sixteen)
    misread["storage"] = [
        {**sixteen["storage"][0], "items": [["Desc_IronPlate_C", 41]]},
        sixteen["storage"][1],
    ]
    misread["inventories"] = {
        **sixteen["inventories"],
        "storage": {"Desc_IronPlate_C": 101},
    }
    assert _digest(as_schema_11(misread)["inventories"]) != _digest(eleven["inventories"]), (
        "a miscounted container reads as agreement, which makes the whole key vacuous"
    )


@pytest.mark.integration
def test_this_parser_still_produces_what_the_two_agreed_on(banked, saves_root):
    """The replayed acceptance test, and the reason the bank exists.

    Every key of every save that both parsers once read must still digest to the value the
    vendored one produced. A difference here is this parser having drifted from the only
    independent check it ever had.

    Compared through ``as_schema_11``: what the oracle never saw cannot be part of an
    agreement with it.
    """
    by_name = {p.name: p for p in saves_root.rglob("*.sav")}
    checked = 0
    drift: list[tuple[str, str]] = []
    for name, entry in banked["saves"].items():
        path = by_name.get(name)
        if path is None:
            continue
        proj = _projection(path)
        assert "error" not in proj, (name, proj.get("detail"))
        assert proj["schema_version"] == 16, (name, "unexpected schema for the filter")
        proj = as_schema_11(proj)
        for key, want in entry.items():
            if key == "n_objects_value":
                assert proj["n_objects"] == want, (name, key)
                continue
            value = (
                {k: v for k, v in proj["header"].items() if k not in VOLATILE}
                if key == "header"
                else proj[key]
            )
            if _digest(value) != want:
                drift.append((name, key))
        checked += 1
    if not checked:
        pytest.skip("none of the banked saves is on this machine")
    assert not drift, f"drifted from the banked agreement on {len(drift)} key(s): {drift[:8]}"
