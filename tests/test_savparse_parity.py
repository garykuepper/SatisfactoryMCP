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
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "vendor_parity.json"
SIDECAR = Path(__file__).resolve().parents[1] / "sidecar" / "extract_save.py"
REPO = SIDECAR.parents[1]

#: Header keys that describe the FILE rather than the world, so they are excluded from the
#: digest: a save copied to another path or re-read after a touch is the same world.
VOLATILE = {"path", "filename", "mtime_ns", "size"}


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

    A truncated or half-written bank would make every comparison below vacuously pass.
    """
    assert banked["_meta"]["saves"] == len(banked["saves"]) == 31
    assert banked["_meta"]["schema_version"] == 11
    for name, entry in banked["saves"].items():
        assert "header" in entry, name
        assert entry["n_objects_value"] > 0, name
        # 20 projection keys plus the readable count beside them.
        assert len(entry) == 21, (name, len(entry))


@pytest.mark.integration
def test_this_parser_still_produces_what_the_two_agreed_on(banked, saves_root):
    """The replayed acceptance test, and the reason the bank exists.

    Every key of every save that both parsers once read must still digest to the value the
    vendored one produced. A difference here is this parser having drifted from the only
    independent check it ever had.
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
