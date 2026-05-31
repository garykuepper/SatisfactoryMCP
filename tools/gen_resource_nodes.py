"""Generate data/resource_nodes.json by merging two independent node datasets.

    uv run python tools/gen_resource_nodes.py

Node resource type and purity are NOT serialized in save files -- node actors carry
only ``mResourcesLeft`` and a transform -- so this static table is the only source.
It is map data, not save data, which is why it lives with the server rather than
coming out of the sidecar.

Two sources, because neither alone is sufficient:

* **MIT** (``data/world_resource_nodes.mit.json``) -- authoritative node SET. Derived
  from an FModel dump of ``Persistent_Level.umap``, i.e. from the game's own packaged
  assets, and MIT-licensed. Complete, but carries no satellite -> fracking-core link:
  a row is ``{id, resource, purity, classPath, nodeType, displayName, x, y, z,
  rotation}`` and nothing in it names a parent.
* **``_WELL_CORES`` below** -- supplies the satellite -> core grouping, which the
  well-rate calculation needs. Read out of the game's own packaged assets: every
  ``BP_FrackingSatellite`` export in ``Persistent_Level.umap`` carries an
  ``mCore`` ObjectProperty pointing at its ``BP_FrackingCore`` export. This is a
  reference in the shipped data, not a guess -- see ``_WELL_CORES`` for how it was
  read and what it was checked against.

The grouping used to come from SCIM via a vendored GPL save parser. That parser is
gone and nothing copyleft reaches this repository any more, so the link is now taken
from the game files directly.

``main`` re-derives the grouping geometrically from the MIT positions on every run and
fails loudly if the two disagree, so the embedded table cannot rot silently: satellites
sit in a tight ring around their core (median 34 m, max 66 m) while the next-nearest
core is at least 3.8x further, which makes the assignment unambiguous.

Geysers are labelled ``Desc_Geyser_C`` here. Neither that nor the MIT dataset's
``Desc_GeothermalEnergy_C`` exists in Docs.json -- a geyser is not an item, it is a
placement target for the Geothermal Generator -- so the name is a synthetic label on
both sides and the difference is cosmetic.

The MIT set is cut from an *older* build than the one installed, so "how far off is a
position" has two answers and the artifact records both -- see ``_VS_SOURCE_BUILD`` and
``_INSTALLED_BUILD_POS``. Against the build it was cut from the table is exact to within
whole-centimetre rounding; against the installed build 25 rows are up to 80 cm stale in
z and one row was renamed. Recording only the first would flatter the table and recording
only the second would read as an error in it, so both are named and the rows that predate
the installed build are listed individually.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Save actors are keyed by this prefix; the MIT dataset stores bare ids.
INSTANCE_PREFIX = "Persistent_Level:PersistentLevel."

#: When everything below that had to be read out of the game rather than computed was read.
MEASURED = "2026-07-30"

#: classPath -> our node kind.
#:
#: A well satellite yields half a plain node's rate AND needs a Pressurizer on its
#: parent core, so conflating the two overstates a field by 2x.
_KINDS = {
    "BP_ResourceNode_C": "node",
    "BP_FrackingSatellite_C": "well_sat",
    "BP_ResourceNodeGeyser_C": "geyser",
}

#: Excluded on purpose: a resource DEPOSIT is hand-mineable only -- no extractor can
#: be placed on it -- so including it would advertise capacity that cannot be built.
_EXCLUDED = {"BP_ResourceDeposit_C", "BP_FrackingCore_C"}

#: Geysers have no real item class; keep the label stable for downstream code.
_GEYSER_RESOURCE = "Desc_Geyser_C"

#: Purity vocabulary, asserted against the MIT rows so a renamed value cannot pass.
_PURITIES = {"impure", "normal", "pure"}

#: The MIT rows store whole centimetres -- every x/y/z in the file is an integer, which
#: ``main`` asserts -- so a comparison against anything that keeps sub-centimetre floats
#: can never read below half a centimetre per axis. Any delta at or under this is that
#: rounding and nothing else; measured deltas cluster at 0.49 cm and stop at 0.83 cm,
#: then jump to 9.54 cm, so the threshold separates rounding from movement cleanly.
ROUNDING_FLOOR_CM = math.sqrt(3) / 2

#: World position, in the INSTALLED build, of every shipped row whose position moved.
#:
#: Measured 2026-07-30 against ``buildVersion 495413`` (engine branch
#: ``++FactoryGame+rel-main-1.2.0``) by reading
#: ``FactoryGame/Content/FactoryGame/Map/GameLevel01/Persistent_Level.umap`` out of the
#: IoStore container and composing the root component's transform for every
#: ``BP_ResourceNode`` / ``BP_ResourceNodeGeyser`` / ``BP_FrackingSatellite`` /
#: ``BP_FrackingCore`` export -- the same read that produced ``_WELL_CORES``.
#:
#: 25 of the 607 shipped rows the installed build still has under the same name are past
#: ``ROUNDING_FLOOR_CM``; the other 582 agree to within rounding. Every one of the 25 moved
#: vertically: |dz| is 9.5-80.4 cm while the horizontal component is at most 0.69 cm over
#: all 607 rows, i.e. rounding. Confirmed independently against save actors: the same 25
#: rows and the same 80.38 cm maximum come out of a ``saveVersion`` 60 save, and the asset
#: extraction agrees with those save actors to 0.015 cm.
#:
#: ``main`` recomputes every delta from these positions, so the artifact never carries a
#: hand-typed number, and drops any row that a refreshed MIT file has caught up with.
_INSTALLED_BUILD_POS: dict[str, tuple[float, float, float]] = {
    "BP_ResourceNode143_1543": (-129599.0, -96956.0, 4129.6216),
    "BP_ResourceNode124_5785": (-90457.8672, -152279.4844, -1700.314),
    "BP_ResourceNode137_2248": (34968.0703, 118464.1562, 13410.2461),
    "BP_ResourceNode586": (-43321.6914, 239553.4688, -3810.8611),
    "BP_ResourceNode566": (-5633.5938, 44274.0625, 21065.998),
    "BP_ResourceNode553": (-41301.4062, 288558.0312, -2545.5698),
    "BP_ResourceNode556": (-110521.7656, 249721.6719, -5349.6841),
    "BP_ResourceNode40": (178061.9062, -120503.6328, 2250.7485),
    "BP_ResourceNode469": (138805.3594, -23952.6699, 9410.7686),
    "BP_ResourceNode12_91": (248481.4375, -146160.3438, 3561.8936),
    "BP_ResourceNode466": (168496.5, -108029.6016, 1587.0562),
    "BP_ResourceNode229": (385953.0, -254364.75, 3420.0154),
    "BP_ResourceNode545": (-44922.0469, 302273.5625, -2543.1182),
    "BP_ResourceNode550": (16091.8359, 264153.125, -3824.1799),
    "BP_ResourceNode464_UAID_40B076DF2F7914E201_2026233335": (92494.6876, -132749.0604, 700.6762),
    "BP_ResourceNode486": (2570.9895, 9834.4873, 24001.5195),
    "BP_ResourceNode53_510": (-21758.373, -145311.75, 10021.6787),
    "BP_ResourceNode442": (60592.707, -77890.8281, 9437.1377),
    "BP_ResourceNode85": (-151152.1719, 184381.0, 998.07),
    "BP_ResourceNode144_1644": (-231390.2969, -89529.7266, 823.9837),
    "BP_ResourceNode620": (406197.0312, -252989.9688, 3920.4285),
    "BP_ResourceNode573_UAID_40B076DF2F7983E001_1840982787": (239775.0249, 148305.2755, 6662.4583),
    "BP_ResourceNode464_UAID_40B076DF2F790EE201_1850696287": (15240.2536, -158578.6127, -1397.1209),
    "BP_ResourceNode441": (67878.3984, -95777.4297, 9362.0342),
    "BP_ResourceNode554": (-37142.0742, 201538.1562, -3056.4609),
}

#: The one row the installed build does not have under the name this table uses. Same read
#: as ``_INSTALLED_BUILD_POS``: a pure Limestone node 150.12 cm away carries the ``installed``
#: name instead. Which name a save uses tracks the build exactly -- all 25 ``saveVersion`` 52
#: saves on this machine carry ``shipped``, all 6 ``saveVersion`` 60 saves carry ``installed``
#: -- so this row cannot be joined against a current save.
_INSTALLED_BUILD_RENAME = {
    "shipped": "BP_ResourceNode11",
    "installed": "BP_ResourceNode20_UAID_04D9F5D42711A7C902_1245462149",
    "moved_cm": 150.12,
}

#: Everything about the installed build that is NOT a position, from the same read. Purity
#: came out of the ``mPurity`` ByteProperty (absent means the class default, ``normal``;
#: ``RP_Pure``/``RP_Inpure`` otherwise) and resource out of the ``mResourceClass``
#: ObjectProperty. Both agree with this table on every row it was possible to compare.
_VS_INSTALLED_BUILD = {
    "build": (
        "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    ),
    "method": (
        "mPurity, mResourceClass and the composed root-component transform of every node "
        "export in the installed build's Persistent_Level.umap, read out of the IoStore "
        "container"
    ),
    "purity_mismatches": [],
    "resource_mismatches": [],
    "resource_rows_compared": 576,
    "resource_not_compared": (
        "31 geysers: BP_ResourceNodeGeyser carries no mResourceClass, and the resource is a "
        "synthetic label on both sides -- Desc_Geyser_C here, Desc_GeothermalEnergy_C in the "
        "MIT rows"
    ),
}

#: The other half of the position question: how far off is this table from the build it was
#: actually cut from? Measured 2026-07-30 over the 66 saves on this machine, grouped by
#: ``saveVersion`` -- a node actor's transform is written by whatever build made the level,
#: so the save version dates the table. Nothing here is derivable from the MIT file alone,
#: which is why it is transcribed rather than recomputed.
_VS_SOURCE_BUILD = {
    "build": "saveVersion 52 -- 25 of the 66 saves on this machine",
    "method": (
        "node actor transforms in every .sav on disk, grouped by saveVersion; a save's "
        "node transforms come from the build that made the level"
    ),
    "rows_missing_from_the_save": [],
    "median_position_delta_cm": 0.48,
    "max_position_delta_cm": 0.83,
    "rows_past_the_rounding_floor": [],
    "note": (
        "Every shipped row is present in those saves and every one is inside the "
        "whole-centimetre rounding floor, so this table is that build exactly. The same rows "
        "against a saveVersion 60 save instead give max 80.38 cm over 25 rows, which is the "
        "figure under against_the_installed_build reached a second, independent way."
    ),
}

#: fracking core -> the satellites that draw from it, transcribed from the game's own
#: packaged assets.
#:
#: Extraction: read ``FactoryGame/Content/FactoryGame/Map/GameLevel01/Persistent_Level.umap``
#: out of the IoStore container, and for each ``BP_FrackingSatellite`` export take the
#: ``mCore`` ObjectProperty, which resolves to a ``BP_FrackingCore`` export in the same
#: package. All 118 satellites carry it and all 17 cores are referenced, so the grouping
#: is total on both sides and needs no fallback.
#:
#: Checked three ways when transcribed (2026-07-30, save version 60 build):
#:   * nearest core by MIT position agrees with ``mCore`` for 118 of 118 satellites;
#:   * every core's satellites agree with the core on ``mResourceClass``;
#:   * the same extraction reproduces all 625 live save node actors -- identical
#:     instance names, position delta median 0.0001 cm, max 0.015 cm.
#: The first of those three is recomputed on every run by ``check_geometry``.
_WELL_CORES: dict[str, tuple[str, ...]] = {
    # Desc_NitrogenGas_C, 8 satellites
    "BP_FrackingCore2": (
        "BP_FrackingSatellite6",
        "BP_FrackingSatellite7",
        "BP_FrackingSatellite8",
        "BP_FrackingSatellite9",
        "BP_FrackingSatellite10",
        "BP_FrackingSatellite11",
        "BP_FrackingSatellite12",
        "BP_FrackingSatellite13",
    ),
    # Desc_NitrogenGas_C, 7 satellites
    "BP_FrackingCore3": (
        "BP_FrackingSatellite14",
        "BP_FrackingSatellite15",
        "BP_FrackingSatellite16",
        "BP_FrackingSatellite17",
        "BP_FrackingSatellite19",
        "BP_FrackingSatellite20",
        "BP_FrackingSatellite23_5",
    ),
    # Desc_NitrogenGas_C, 7 satellites
    "BP_FrackingCore4": (
        "BP_FrackingSatellite21",
        "BP_FrackingSatellite22",
        "BP_FrackingSatellite24",
        "BP_FrackingSatellite25",
        "BP_FrackingSatellite26",
        "BP_FrackingSatellite27",
        "BP_FrackingSatellite51_1",
    ),
    # Desc_NitrogenGas_C, 7 satellites
    "BP_FrackingCore5": (
        "BP_FrackingSatellite28",
        "BP_FrackingSatellite29",
        "BP_FrackingSatellite30",
        "BP_FrackingSatellite31",
        "BP_FrackingSatellite32",
        "BP_FrackingSatellite33",
        "BP_FrackingSatellite34",
    ),
    # Desc_NitrogenGas_C, 10 satellites
    "BP_FrackingCore6": (
        "BP_FrackingSatellite35",
        "BP_FrackingSatellite36",
        "BP_FrackingSatellite37",
        "BP_FrackingSatellite38",
        "BP_FrackingSatellite39",
        "BP_FrackingSatellite40",
        "BP_FrackingSatellite41",
        "BP_FrackingSatellite42",
        "BP_FrackingSatellite43",
        "BP_FrackingSatellite44",
    ),
    # Desc_LiquidOil_C, 6 satellites
    "BP_FrackingCore6_UAID_40B076DF2F79D3DF01_1961476789": (
        "BP_FrackingSatellite61_UAID_40B076DF2F79D7DF01_1933830510",
        "BP_FrackingSatellite61_UAID_40B076DF2F79D7DF01_2053713511",
        "BP_FrackingSatellite61_UAID_40B076DF2F79D8DF01_1587984689",
        "BP_FrackingSatellite61_UAID_40B076DF2F79D8DF01_1704280690",
        "BP_FrackingSatellite61_UAID_40B076DF2F79D8DF01_1999134691",
        "BP_FrackingSatellite61_UAID_40B076DF2F79D9DF01_1230935868",
    ),
    # Desc_LiquidOil_C, 6 satellites
    "BP_FrackingCore7": (
        "BP_FrackingSatellite45",
        "BP_FrackingSatellite46",
        "BP_FrackingSatellite47",
        "BP_FrackingSatellite48",
        "BP_FrackingSatellite49",
        "BP_FrackingSatellite50",
    ),
    # Desc_LiquidOil_C, 6 satellites
    "BP_FrackingCore9": (
        "BP_FrackingSatellite57",
        "BP_FrackingSatellite59",
        "BP_FrackingSatellite60",
        "BP_FrackingSatellite61",
        "BP_FrackingSatellite62",
        "BP_FrackingSatellite63",
    ),
    # Desc_Water_C, 7 satellites
    "BP_FrackingCore10": (
        "BP_FrackingSatellite58",
        "BP_FrackingSatellite64",
        "BP_FrackingSatellite65",
        "BP_FrackingSatellite66",
        "BP_FrackingSatellite67",
        "BP_FrackingSatellite68",
        "BP_FrackingSatellite103_8",
    ),
    # Desc_Water_C, 7 satellites
    "BP_FrackingCore11": (
        "BP_FrackingSatellite69",
        "BP_FrackingSatellite70",
        "BP_FrackingSatellite71",
        "BP_FrackingSatellite72",
        "BP_FrackingSatellite73",
        "BP_FrackingSatellite74",
        "BP_FrackingSatellite102",
    ),
    # Desc_Water_C, 6 satellites
    "BP_FrackingCore12": (
        "BP_FrackingSatellite75",
        "BP_FrackingSatellite76",
        "BP_FrackingSatellite77",
        "BP_FrackingSatellite78",
        "BP_FrackingSatellite79",
        "BP_FrackingSatellite80",
    ),
    # Desc_Water_C, 6 satellites
    "BP_FrackingCore13": (
        "BP_FrackingSatellite81",
        "BP_FrackingSatellite82",
        "BP_FrackingSatellite83",
        "BP_FrackingSatellite84",
        "BP_FrackingSatellite85",
        "BP_FrackingSatellite86",
    ),
    # Desc_Water_C, 8 satellites
    "BP_FrackingCore14": (
        "BP_FrackingSatellite87",
        "BP_FrackingSatellite88",
        "BP_FrackingSatellite89",
        "BP_FrackingSatellite90",
        "BP_FrackingSatellite91",
        "BP_FrackingSatellite92",
        "BP_FrackingSatellite93",
        "BP_FrackingSatellite94",
    ),
    # Desc_Water_C, 7 satellites
    "BP_FrackingCore15": (
        "BP_FrackingSatellite95",
        "BP_FrackingSatellite96",
        "BP_FrackingSatellite97",
        "BP_FrackingSatellite98",
        "BP_FrackingSatellite99",
        "BP_FrackingSatellite100",
        "BP_FrackingSatellite101",
    ),
    # Desc_Water_C, 7 satellites
    "BP_FrackingCore17": (
        "BP_FrackingSatellite108",
        "BP_FrackingSatellite109",
        "BP_FrackingSatellite110",
        "BP_FrackingSatellite111",
        "BP_FrackingSatellite112",
        "BP_FrackingSatellite113",
        "BP_FrackingSatellite121",
    ),
    # Desc_Water_C, 7 satellites
    "BP_FrackingCore18": (
        "BP_FrackingSatellite114_7",
        "BP_FrackingSatellite115",
        "BP_FrackingSatellite116",
        "BP_FrackingSatellite117",
        "BP_FrackingSatellite118",
        "BP_FrackingSatellite119",
        "BP_FrackingSatellite120",
    ),
    # Desc_NitrogenGas_C, 6 satellites
    "BP_FrackingCore_8": (
        "BP_FrackingSatellite2",
        "BP_FrackingSatellite3",
        "BP_FrackingSatellite4",
        "BP_FrackingSatellite5",
        "BP_FrackingSatellite18_3",
        "BP_FrackingSatellite_2",
    ),
}


def load_mit() -> list[dict]:
    path = ROOT / "data" / "world_resource_nodes.mit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["nodes"]


def satellite_cores() -> dict[str, str]:
    """Invert _WELL_CORES, refusing a satellite that two cores both claim."""
    out: dict[str, str] = {}
    for core, sats in _WELL_CORES.items():
        for sat in sats:
            if sat in out:
                raise SystemExit(f"satellite {sat} claimed by {out[sat]} and {core}")
            out[sat] = core
    return out


def check_geometry(mit: list[dict], sat_core: dict[str, str]) -> dict:
    """Re-derive the grouping from MIT positions and compare with the embedded table.

    Returns the distance distribution and the ambiguity margin. A satellite whose
    nearest core is not its recorded core is a hard error: either the table is stale
    or the world moved, and both need a human.
    """
    pos = {e["id"]: (e["x"], e["y"], e["z"]) for e in mit}
    cores = [e["id"] for e in mit if e["classPath"] == "BP_FrackingCore_C"]
    disagree: list[str] = []
    own: list[float] = []
    margins: list[float] = []

    for sat, core in sat_core.items():
        ranked = sorted(cores, key=lambda c: math.dist(pos[sat], pos[c]))
        d_own = math.dist(pos[sat], pos[core])
        own.append(d_own)
        rival = ranked[1] if ranked[0] == core else ranked[0]
        margins.append(math.dist(pos[sat], pos[rival]) / d_own)
        if ranked[0] != core:
            disagree.append(f"{sat}: mCore says {core}, nearest is {ranked[0]}")

    if disagree:
        for d in disagree:
            print("  GEOMETRY DISAGREES:", d)
        raise SystemExit(f"{len(disagree)} satellite(s) contradict the embedded core table")

    own.sort()
    return {
        "method": "nearest fracking core by MIT position, vs the embedded mCore link",
        # Equal by construction -- any disagreement raised above.
        "satellites_checked": len(own),
        "nearest_core_agrees": len(own),
        "distance_to_own_core_cm": {
            "min": round(own[0], 1),
            "median": round(own[len(own) // 2], 1),
            "max": round(own[-1], 1),
        },
        "runner_up_core_distance_ratio_min": round(min(margins), 2),
    }


def check_build_skew(nodes: list[dict]) -> dict:
    """Re-derive the installed-build position deltas from the recorded positions.

    The generator cannot read the game's IoStore container itself, so the installed
    build's positions are transcribed -- but the *deltas* are not: they are recomputed
    here against whatever the table currently says, which is what keeps the artifact's
    claim and the artifact's data from drifting apart. Two ways this refuses to lie:

    * a recorded row that is no longer in the table is a hard error, because the record
      would then be describing rows that do not exist;
    * a recorded row the table has caught up with is dropped, so refreshing the MIT file
      shrinks the disclosure to nothing instead of leaving a stale warning behind.
    """
    by_id = {n["instance"].removeprefix(INSTANCE_PREFIX): n for n in nodes}

    gone = sorted(set(_INSTALLED_BUILD_POS) - set(by_id))
    if gone:
        for g in gone:
            print("  SKEW RECORD IS STALE:", g, "is no longer a row in this table")
        raise SystemExit(f"{len(gone)} recorded skew row(s) are not in the table any more")
    if _INSTALLED_BUILD_RENAME["shipped"] not in by_id:
        raise SystemExit(
            f"{_INSTALLED_BUILD_RENAME['shipped']} is gone from the table; the recorded "
            "rename no longer applies and _INSTALLED_BUILD_RENAME must be re-measured"
        )
    if _INSTALLED_BUILD_RENAME["installed"] in by_id:
        raise SystemExit(
            f"the table now has {_INSTALLED_BUILD_RENAME['installed']}, so the recorded "
            "rename is history; delete _INSTALLED_BUILD_RENAME and re-measure the skew"
        )

    behind, caught_up, deltas = [], [], []
    for node_id, installed in _INSTALLED_BUILD_POS.items():
        row = by_id[node_id]
        shipped = (row["x"], row["y"], row["z"])
        delta = math.dist(installed, shipped)
        deltas.append(delta)
        if delta <= ROUNDING_FLOOR_CM:
            caught_up.append(node_id)
            continue
        behind.append(
            {
                "instance": row["instance"],
                "resource": row["resource"],
                "shipped_z": row["z"],
                "installed_z": round(installed[2], 2),
                "dz_cm": round(installed[2] - row["z"], 2),
                "delta_cm": round(delta, 2),
            }
        )
    behind.sort(key=lambda r: -r["delta_cm"])
    for node_id in caught_up:
        print(f"  the table has caught up with {node_id}; drop it from _INSTALLED_BUILD_POS")

    missing = [_INSTALLED_BUILD_RENAME["shipped"]]
    return {
        "measured": MEASURED,
        **_VS_INSTALLED_BUILD,
        "rows_compared": len(nodes) - len(missing),
        "rows_only_in_this_table": [INSTANCE_PREFIX + m for m in missing],
        "rows_only_in_the_installed_build": [
            INSTANCE_PREFIX + _INSTALLED_BUILD_RENAME["installed"]
        ],
        "renamed_row_moved_cm": _INSTALLED_BUILD_RENAME["moved_cm"],
        "max_position_delta_cm": round(max(deltas), 2) if deltas else None,
        "rounding_floor_cm": round(ROUNDING_FLOOR_CM, 3),
        "rows_past_the_rounding_floor": behind,
    }


def main() -> int:
    mit = load_mit()
    sat_core = satellite_cores()
    resource_of = {e["id"]: e["resource"] for e in mit}

    nodes: list[dict] = []
    cores: dict[str, dict] = {}
    unknown_purity: list[str] = []
    core_resource_conflict: list[str] = []
    missing_core_link: list[str] = []

    for entry in mit:
        if entry["classPath"] == "BP_FrackingCore_C":
            cores[entry["id"]] = entry
        if entry["classPath"] in _EXCLUDED:
            continue
        kind = _KINDS.get(entry["classPath"])
        if kind is None:
            continue

        resource = _GEYSER_RESOURCE if kind == "geyser" else entry["resource"]
        purity = entry["purity"]
        if purity not in _PURITIES:
            unknown_purity.append(f"{entry['id']}: {purity!r}")

        well_core = None
        if kind == "well_sat":
            core_id = sat_core.get(entry["id"])
            if core_id is None:
                missing_core_link.append(entry["id"])
            else:
                well_core = INSTANCE_PREFIX + core_id

        nodes.append(
            {
                "instance": INSTANCE_PREFIX + entry["id"],
                "resource": resource,
                "purity": purity,
                "kind": kind,
                "x": round(float(entry["x"]), 2),
                "y": round(float(entry["y"]), 2),
                "z": round(float(entry["z"]), 2),
                "well_core": well_core,
            }
        )

    # A core and its satellites tap one deposit, so they must name one resource.
    for core_id, sats in _WELL_CORES.items():
        core = cores.get(core_id)
        if core is None:
            core_resource_conflict.append(f"{core_id}: referenced but not in the MIT set")
            continue
        seen = {resource_of[s] for s in sats if s in resource_of}
        if seen != {core["resource"]}:
            core_resource_conflict.append(
                f"{core_id}: core is {core['resource']}, satellites are {sorted(seen)}"
            )

    orphan_cores = sorted(set(cores) - set(_WELL_CORES))
    geometry = check_geometry(mit, sat_core)

    nodes.sort(key=lambda n: n["instance"])
    by_kind: dict[str, int] = {}
    for n in nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1

    # The recorded position deltas are only rounding-free if the MIT rows really are whole
    # centimetres; ROUNDING_FLOOR_CM is meaningless otherwise.
    fractional = [e["id"] for e in mit if any(float(e[a]) % 1 for a in "xyz")]
    if fractional:
        raise SystemExit(
            f"{len(fractional)} MIT row(s) carry sub-centimetre coordinates, e.g. "
            f"{fractional[0]}; ROUNDING_FLOOR_CM no longer describes the source"
        )
    installed = check_build_skew(nodes)
    source_build = {"measured": MEASURED, **_VS_SOURCE_BUILD}
    source_build["rows_compared"] = len(nodes) - len(source_build["rows_missing_from_the_save"])
    source_build["rounding_floor_cm"] = round(ROUNDING_FLOOR_CM, 3)

    # Every number in the prose below comes off the derived block, so the interpretation
    # cannot outlive the measurement it interprets.
    behind = installed["rows_past_the_rounding_floor"]
    dz = sorted(abs(r["dz_cm"]) for r in behind)
    position_impact = (
        f"the {len(behind)} named rows are {dz[0]}-{dz[-1]} cm stale in z, and horizontally "
        f"within rounding, so a plan sited from this table picks the right node with a z up "
        f"to {dz[-1]:.0f} cm behind the game's"
        if behind
        else "no row is past the rounding floor, so positions match the installed build"
    )

    out = {
        "_meta": {
            "description": "Static resource node table: type, purity, world position.",
            "why": (
                "Save files serialize only mResourcesLeft for node actors; resource "
                "type and purity are level data and must come from a static table."
            ),
            "sources": {
                "primary": {
                    "name": "rockfactory/satisfactory-logistics WorldResourceNodes.json",
                    "licence": "MIT",
                    "derivation": "FModel dump of Persistent_Level.umap (game assets)",
                    "role": "authoritative node set, resource, purity, position",
                },
                "secondary": {
                    "name": "_WELL_CORES in tools/gen_resource_nodes.py",
                    "licence": "none needed; read from the installed game's own assets",
                    "derivation": (
                        "mCore ObjectProperty on each BP_FrackingSatellite export in "
                        "FactoryGame/Content/FactoryGame/Map/GameLevel01/"
                        "Persistent_Level.umap, read out of the IoStore container"
                    ),
                    "role": "satellite -> fracking core link only",
                    "kind": "reference present in the shipped data, not an inference",
                    # Every source records which game build it was read at. The GPL table
                    # this replaced pinned a version string; the game's own assets pin
                    # themselves, and a reader must be able to tell whether a table
                    # predates the build they are running.
                    "game_version_pinned": (
                        "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), "
                        "the installed build"
                    ),
                    "transcribed": MEASURED,
                },
            },
            "cross_validation": {
                "satellites": len(sat_core),
                "fracking_cores_referenced": len(_WELL_CORES),
                "satellites_without_core_link": missing_core_link,
                "cores_with_no_satellites": orphan_cores,
                "core_resource_conflicts": core_resource_conflict,
                "unknown_purity_values": unknown_purity,
                "geometry": geometry,
                # Positions get two comparisons on purpose. This table is cut from an older
                # build than the one installed, so a single delta cannot be honest: against
                # the source build it is exact, against the installed build 25 rows are
                # stale. Both are recorded, and any row past the rounding floor is named.
                "positions": {
                    "against_the_build_this_table_was_cut_from": source_build,
                    "against_the_installed_build": installed,
                },
            },
            "known_game_version_skew": {
                "measured": MEASURED,
                "what": (
                    "This table is cut from an older build than the installed one. The two "
                    "comparisons under cross_validation.positions say by how much; the rows "
                    "that predate the installed build are named individually in "
                    "cross_validation.positions.against_the_installed_build."
                    "rows_past_the_rounding_floor."
                ),
                "impact": (
                    f"Positions: {position_impact}. Identity: the one renamed row cannot be "
                    "joined against a saveVersion 60 or later save at all -- the save carries "
                    "the new name, which has no row here, so that node's mResourcesLeft is "
                    "unreadable and its position is "
                    f"{_INSTALLED_BUILD_RENAME['moved_cm']:.0f} cm off. Resource and purity "
                    "are unaffected on every row, and so is the whole well group: every "
                    "satellite and core keeps its id, and its position matches the installed "
                    "build to within rounding."
                ),
                "fix": (
                    "refresh data/world_resource_nodes.mit.json, then delete "
                    "_INSTALLED_BUILD_POS and _INSTALLED_BUILD_RENAME from the generator; "
                    "not done here"
                ),
            },
            "excluded": {
                "BP_ResourceDeposit_C": "hand-mineable only, no extractor can be placed",
                "BP_FrackingCore_C": (
                    "produces nothing itself; referenced as well_core on satellites"
                ),
            },
            "geyser_note": (
                "Desc_Geyser_C is a synthetic label. A geyser is not an item in "
                "Docs.json -- it is a placement target for the Geothermal Generator."
            ),
            "join_key": "instance (matches save actor instanceName exactly)",
            "count": len(nodes),
            "by_kind": by_kind,
            "fracking_cores": len(cores),
            "purity_multiplier": {"impure": 0.5, "normal": 1.0, "pure": 2.0},
            "units": "centimetres; north is -Y, east is +X, up is +Z",
        },
        "nodes": nodes,
    }

    dest = ROOT / "data" / "resource_nodes.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")

    print(f"wrote {dest.relative_to(ROOT)}  {len(nodes)} nodes  {dest.stat().st_size} B")
    print("by kind:", by_kind)
    d = geometry["distance_to_own_core_cm"]
    print(
        f"well links: {len(sat_core)} satellites over {len(_WELL_CORES)} cores, "
        f"nearest-core agrees {geometry['nearest_core_agrees']}/{len(sat_core)}, "
        f"distance {d['min']:.0f}-{d['max']:.0f} cm (median {d['median']:.0f}), "
        f"runner-up at least {geometry['runner_up_core_distance_ratio_min']:.2f}x further"
    )
    print(
        f"positions: vs the build this table was cut from "
        f"({_VS_SOURCE_BUILD['build'].split(' --')[0]}) max "
        f"{source_build['max_position_delta_cm']} cm over {source_build['rows_compared']} rows, "
        f"{len(source_build['rows_past_the_rounding_floor'])} past the "
        f"{source_build['rounding_floor_cm']} cm rounding floor"
    )
    print(
        f"           vs the installed build max {installed['max_position_delta_cm']} cm over "
        f"{installed['rows_compared']} rows, "
        f"{len(installed['rows_past_the_rounding_floor'])} past it"
    )
    if installed["rows_past_the_rounding_floor"]:
        print(f"  {len(installed['rows_past_the_rounding_floor'])} row(s) predate the install:")
        for r in installed["rows_past_the_rounding_floor"]:
            print(
                f"    dz {r['dz_cm']:+8.2f} cm  {r['resource']:20s} "
                f"{r['instance'].removeprefix(INSTANCE_PREFIX)}"
            )
    if missing_core_link:
        print(f"  WARNING: {len(missing_core_link)} satellite(s) have no core link")
        for s in missing_core_link:
            print("    ", s)
    for m in core_resource_conflict + unknown_purity:
        print("  MISMATCH:", m)
    if orphan_cores:
        print(f"  cores with no satellites: {', '.join(orphan_cores)}")

    oil = [n for n in nodes if n["resource"] == "Desc_LiquidOil_C"]
    pump = [n for n in oil if n["kind"] == "node"]
    print(
        f"crude oil: {len(oil)} total, {len(pump)} pumpable nodes, "
        f"{len(oil) - len(pump)} well satellites"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
