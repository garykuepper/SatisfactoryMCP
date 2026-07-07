"""The two files nothing can regenerate, and the three tables everything can.

Opposite failures, one commit, because they are the same mistake about who owns a file.

**The player's own words have to survive a crash.** ``PlanStore`` and ``LabelStore`` write
JSON the reader typed -- factory names and saved plans -- and both used
``Path.write_text``, which truncates and then writes. Die in between and the file on disk is
empty, which is not recoverable from the save, from the docs dump or from anywhere else on
the machine. ``core.atomic`` writes a sibling and renames it over the top, so the original
survives every failure short of the rename itself.

**The generated tables have to NOT survive a regeneration.** ``load_nodes``,
``load_regions`` and ``load_collectibles`` were ``lru_cache(maxsize=1)``, which caches on
nothing: run the generator against a new game build and the running server keeps answering
from the table it read at startup, while the generator prints the new counts to the same
terminal. They are keyed on ``(path, mtime_ns)`` now, exactly as ``spatial.heightfield``
already was.

Every test here writes to ``tmp_path`` and none needs a save or a game install.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core import atomic
from satisfactory_mcp.domain.collectibles import table as collectibles_table
from satisfactory_mcp.domain.factories.labels import LabelStore
from satisfactory_mcp.domain.planning.store import PlanStore
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial import regions as regions_mod

# ------------------------------------------------------------------ atomic write


def test_the_bytes_are_the_ones_write_text_would_have_produced(tmp_path):
    """A drop-in, or the switch is a format change wearing a durability fix's clothes.

    Newline translation included: ``Path.write_text`` opens with the platform default, so on
    Windows a ``\\n`` in the JSON becomes ``\\r\\n`` on disk, and a file written by the old
    code and by this one have to be byte for byte the same.
    """
    text = '{\n "schema": 1,\n "plans": []\n}'
    direct = tmp_path / "direct.json"
    direct.write_text(text, encoding="utf-8")
    replaced = atomic.write_text(tmp_path / "replaced.json", text)
    assert replaced.read_bytes() == direct.read_bytes()


def test_a_crash_at_the_rename_leaves_the_ORIGINAL_intact(tmp_path, monkeypatch):
    """The whole point, simulated at the one instruction that is allowed to fail.

    ``os.replace`` is the last step and everything before it happens off to the side, so
    raising there is exactly "the process died with the new content written and not yet
    installed". The old file must be untouched -- not empty, not half the new document.
    """
    path = tmp_path / "labels.json"
    path.write_text('{"labels": ["the old one"]}', encoding="utf-8")
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("simulated crash between the write and the rename")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        atomic.write_text(path, '{"labels": ["the new one"]}')

    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["labels"] == ["the old one"]


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    """Litter is not harmless here: these directories are the reader's, not the cache's."""
    path = tmp_path / "plans.json"
    path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(atomic.os, "replace", lambda src, dst: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        atomic.write_text(path, "{}")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plans.json"]


def test_a_first_write_to_a_missing_file_still_lands(tmp_path):
    """The ordinary case: there is nothing to protect and it must work anyway."""
    path = atomic.write_text(tmp_path / "new.json", '{"ok": true}')
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.json"]


@pytest.mark.parametrize(
    ("store_dir", "make"),
    [
        ("plans_dir", lambda: PlanStore(world_id="W", session_name="s")),
        ("labels_dir", lambda: LabelStore(world_id="W", session_name="s")),
    ],
)
def test_the_two_stores_survive_a_crash_mid_save(tmp_path, monkeypatch, store_dir, make):
    """Through the real ``save()``, not through the helper it calls.

    Both stores are tested the same way and in one test, because the property is the same
    property and stating it twice by hand invites the second copy to be forgotten when a
    third store is added.
    """
    monkeypatch.setattr(config, store_dir, lambda: tmp_path)
    store = make()
    path = store.save()
    first = path.read_bytes()
    assert json.loads(path.read_text(encoding="utf-8"))["world_id"] == "W"

    monkeypatch.setattr(atomic.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("no")))
    store.session_name = "changed"
    with pytest.raises(OSError):
        store.save()
    assert path.read_bytes() == first, "a crashed save must not cost what was already stored"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


# --------------------------------------------------------------- table reloading


def _write(path: Path, payload: dict, mtime_ns: int) -> None:
    """Write a table and stamp its mtime, so a test does not have to wait for a clock tick."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


_NODES_A = {"nodes": [{"instance": "a", "resource": "Iron", "x": 0, "y": 0}], "_meta": {"v": 1}}
_NODES_B = {
    "nodes": [
        {"instance": "a", "resource": "Iron", "x": 0, "y": 0},
        {"instance": "b", "resource": "Copper", "x": 1, "y": 1},
    ],
    "_meta": {"v": 2},
}

_REGIONS_A = {
    "region_grid": ["ab"],
    "confidence_grid": ["ll"],
    "legend": {"a": "Alpha", "b": "Beta"},
    "regions": {"Alpha": {"centroid": [0, 0]}},
    "grid_meta": {"x0": 0, "y0": 0, "cell": 256, "nx": 2, "ny": 1},
    "_meta": {"accuracy_m": 256},
}

_COLLECTIBLES_A = {
    "collectibles": [
        {"cell": "c", "instance": "x.BP_Crystal_C_1", "category": "slug", "class": "BP_Crystal_C"}
    ],
    "_meta": {},
}


def test_a_regenerated_node_table_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    """The failure this replaces: the generator prints new counts, the server quotes old ones."""
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    nodes_mod._TABLE.clear()
    path = tmp_path / "resource_nodes.json"

    _write(path, _NODES_A, 1_000_000_000)
    first = nodes_mod.load_nodes()
    assert len(first) == 1
    assert nodes_mod.load_nodes() is first, "an unchanged file must still cost one dict lookup"

    _write(path, _NODES_B, 2_000_000_000)
    second = nodes_mod.load_nodes()
    assert second is not first
    assert len(second) == 2
    assert second.meta == {"v": 2}
    # One entry, like the maxsize=1 it replaces: the old table is dead, not retained.
    assert len(nodes_mod._TABLE) == 1


def test_a_regenerated_region_map_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    regions_mod._MAP.clear()
    path = tmp_path / "region_names.json"

    _write(path, _REGIONS_A, 1_000_000_000)
    first = regions_mod.load_regions()
    assert first.label_for(0, 0).name == "Alpha"
    assert regions_mod.load_regions() is first

    renamed = {**_REGIONS_A, "legend": {"a": "Renamed", "b": "Beta"}}
    _write(path, renamed, 2_000_000_000)
    assert regions_mod.load_regions().label_for(0, 0).name == "Renamed"
    assert len(regions_mod._MAP) == 1


def test_a_regenerated_collectible_table_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    collectibles_table._TABLE.clear()
    path = tmp_path / collectibles_table.COLLECTIBLES_FILE

    _write(path, _COLLECTIBLES_A, 1_000_000_000)
    first = collectibles_table.load_collectibles()
    assert len(first) == 1
    assert collectibles_table.load_collectibles() is first

    grown = {
        **_COLLECTIBLES_A,
        "collectibles": [
            *_COLLECTIBLES_A["collectibles"],
            {"cell": "c", "instance": "x.BP_WAT_2", "category": "sloop", "class": "BP_WAT_C"},
        ],
    }
    _write(path, grown, 2_000_000_000)
    assert len(collectibles_table.load_collectibles()) == 2
    assert len(collectibles_table._TABLE) == 1


def test_the_miss_behaviour_of_all_three_loaders_is_unchanged(tmp_path, monkeypatch):
    """Keying on the mtime must not change what happens when there is no file to stat.

    The three disagree on purpose and the disagreement is the point: the node table and the
    region map are committed, so their absence is a broken checkout and says which generator
    to run; the collectible table is untracked, so its absence is the ordinary state of a
    fresh clone and every caller degrades to the save-only census.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "empty")
    nodes_mod._TABLE.clear()
    regions_mod._MAP.clear()
    collectibles_table._TABLE.clear()

    with pytest.raises(FileNotFoundError, match="gen_resource_nodes"):
        nodes_mod.load_nodes()
    with pytest.raises(FileNotFoundError, match="gen_region_names"):
        regions_mod.load_regions()
    assert collectibles_table.load_collectibles() is None


def test_an_unreadable_collectible_table_is_not_cached_as_a_refusal(tmp_path, monkeypatch):
    """``None`` is a state a reader fixes, so the next call has to look again.

    Caching it would key an ABSENCE on an mtime, which is a thing that does not exist: the
    reader runs the generator, the file appears, and a cached no would go on being the
    answer until the process ended.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    collectibles_table._TABLE.clear()
    path = tmp_path / collectibles_table.COLLECTIBLES_FILE

    path.write_text("{ not json", encoding="utf-8")
    assert collectibles_table.load_collectibles() is None

    _write(path, _COLLECTIBLES_A, 3_000_000_000)
    assert len(collectibles_table.load_collectibles()) == 1


def test_a_corrupt_collectible_table_can_be_told_from_a_missing_one(tmp_path, monkeypatch):
    """ "You never ran the generator" and "what it wrote is broken" are different answers.

    Both arrived as ``None``, so a reader who HAD run the generator was told the table did
    not exist and went looking for a run that had already happened. The degrading callers
    still degrade -- that is the default, and it is right for them -- but ``strict`` exists
    so a caller that wants to say which one it is can.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    collectibles_table._TABLE.clear()
    path = tmp_path / collectibles_table.COLLECTIBLES_FILE

    # Not there at all: still ``None``, strict or not. Absence is not a fault.
    assert collectibles_table.load_collectibles(strict=True) is None

    for broken in ("{ not json", "[]", '{"collectibles": []}'):
        path.write_text(broken, encoding="utf-8")
        assert collectibles_table.load_collectibles() is None, broken
        with pytest.raises(collectibles_table.CollectiblesUnreadable, match="exists but"):
            collectibles_table.load_collectibles(strict=True)

    _write(path, _COLLECTIBLES_A, 4_000_000_000)
    assert len(collectibles_table.load_collectibles(strict=True)) == 1
