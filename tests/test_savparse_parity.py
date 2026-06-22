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
``belts`` key (schema 12) and a ``pipes`` key (schema 13). None of those fields existed while
the oracle did, so it never had an opinion about them, and re-recording the bank against this
parser would replace an independent measurement with this parser's own output -- the one thing
that would make every test here vacuous. So the comparison runs on a projection FILTERED BACK
to the schema-11 shape, by the explicit list in ``POST_11_ADDITIONS`` below. Additive fields are
legitimately outside the deleted oracle's scope; a *changed* schema-11 field is exactly what
this still catches.

Every later schema adds its own entry to that list rather than re-banking, which is why the
list is keyed by what was added and annotated with which schema added it.
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
    #: Whole new top-level keys: per-belt spline polylines (12), per-pipe ones (13), and the
    #: splitters and mergers those belt runs pass through (13).
    #:
    #: Schema 14 added a FOURTH COLUMN to a pipe segment -- the index of its own actor in
    #: ``graph["actors"]``, which joins a drawn pipe to the connection graph -- and needs no
    #: entry of its own for one reason worth writing down rather than leaving to be rederived:
    #: the change is confined inside ``pipes``, and ``pipes`` is already dropped whole. Had it
    #: widened a schema-11 row instead, it would have owed ``row_width`` an entry, exactly as
    #: ``structures`` does below.
    "keys": ("belts", "pipes", "attachments"),
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
}


def as_schema_11(projection: dict) -> dict:
    """The projection with every post-11 addition removed, and nothing else touched.

    Not a general downgrade: it undoes exactly ``POST_11_ADDITIONS`` and leaves every other
    difference -- which is the point, because every other difference is drift.
    """
    out = {k: v for k, v in projection.items() if k not in POST_11_ADDITIONS["keys"]}
    if "schema_version" in out:
        out["schema_version"] = POST_11_ADDITIONS["schema_version"]
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
        "warnings": [],
    }
    thirteen = {
        "schema_version": 13,
        "machines": [{"cls": "Build_SmelterMk1_C", "pos": [1.0, 2.0, 3.0], "yaw": -20.0}],
        "extractors": [{"cls": "Build_MinerMk2_C", "pos": [4.0, 5.0, 6.0], "yaw": 90.0}],
        "generators": [{"cls": "Build_GeneratorCoal_C", "pos": [7.0, 8.0, 9.0], "yaw": 0.0}],
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [[0, 10, 20, 30, -20.0]],
        },
        "belts": {"classes": ["Build_ConveyorBeltMk3_C"], "segments": [[0, 0, [[1, 2, 3]]]]},
        "attachments": [
            {"cls": "Build_ConveyorAttachmentSplitter_C", "pos": [1.0, 2.0, 3.0], "yaw": 90.0}
        ],
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 3, "fluid": "Desc_Water_C"}],
            "segments": [[0, 0, [[1, 2, 3], [4, 5, 6]]]],
        },
        "warnings": [],
    }
    filtered = as_schema_11(thirteen)
    assert filtered == eleven, "the filter did not land back on the schema-11 shape"
    assert {k: _digest(v) for k, v in filtered.items()} == {
        k: _digest(v) for k, v in eleven.items()
    }

    moved = dict(thirteen)
    moved["machines"] = [{**thirteen["machines"][0], "pos": [1.0, 2.0, 99.0]}]
    assert _digest(as_schema_11(moved)["machines"]) != _digest(eleven["machines"]), (
        "the filter hides a changed schema-11 field, which is the drift the bank exists to catch"
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
        assert proj["schema_version"] == 14, (name, "unexpected schema for the filter")
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
