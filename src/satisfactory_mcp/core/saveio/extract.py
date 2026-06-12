"""Save-file extractor. Runs as a SEPARATE PROCESS from the MCP server.

    python -m satisfactory_mcp.core.saveio.extract <path-to-sav> [--header-only]

Emits a JSON projection on stdout. Diagnostics go to stderr so stdout stays clean.

Why a subprocess rather than an import:
  * the parser hard-fails on an unrecognised saveVersion, so a game patch breaks parsing
    until the format is re-derived. One boundary means one thing to fix.
  * A torn autosave (the file is rewritten in place every ~5 min while playing) or a
    parser crash cannot take down the server.
  * The projection is small and serialisable, which makes it the test fixture -- the
    whole suite then runs with no game install and no 2.9 MB .sav in git.
  * Every byte the parser allocated is returned to the OS when the child exits, which a
    long-lived server reading 2.9 MB saves does not otherwise get.

Why this module lives in the application package and not in ``pioneersav``: the parser
answers "what does this file say", and this answers "what does the MCP server need" --
the schema-13 projection is this project's shape, versioned with this project's cache,
and it is the only place in the tree allowed to import the parser at all.

Property-access hazards handled here, all of which fail SILENTLY otherwise:
  * ComponentHeader has no typePath attribute at all.
  * BoolProperty is a raw uint8 where 16 means True (observed 16/1/0).
  * UE omits empty TArray SaveGame properties, so absent means empty, not missing.
  * ObjectReference defines __str__ but not __repr__, so any repr()-based search
    finds nothing.
  * mItemsPickedUp is a MapProperty keyed by player state containing an inner map.

There is one parser: ``pioneersav``. The vendored GPL-3.0 one it replaced is deleted, and
the switch that chose between them went with it -- see the comment above the import.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from pathlib import Path

# There was a second parser here until the vendored GPL-3.0 `sat_sav_parse` was deleted, and a
# `SATISFACTORY_SAVPARSE` switch to pick between them. The switch existed for one purpose --
# running the same save through both and diffing the projection, so that "they agree" was a
# measurement rather than an argument -- and it has no second option left to choose.
#
# What that measurement said, at the moment the library was removed: the two agreed on **every
# projection key, leaf for leaf, on all 31 saves the vendored parser could read**. That diff can
# never be run again, so it is banked in `tests/fixtures/vendor_parity.json` as a per-save,
# per-key digest of what the vendored parser produced, and `test_savparse_parity.py` holds this
# parser to it. `pioneersav` additionally reads the 35 pre-1.0 saves the old one refused.
#
# Absolute, and the only import in this file: the parser is a top-level package beside
# `satisfactory_mcp`, so this module runs the same whether it is started with `-m` or as a
# plain file path.
import pioneersav

read_save_info = pioneersav.read_info
read_full_save = pioneersav.read_full_save
#: What "this save cannot be read" looks like. Resolved here rather than at the raise site so
#: that main()'s except clause names one thing.
PARSE_ERROR: tuple[type[BaseException], ...] = (pioneersav.ParseError,)

SCHEMA_VERSION = 13

#: The four pipeline classes that carry an ``mSplineData`` -- the fluid pipes, Mk1 and Mk2,
#: each in the ordinary and the ``NoIndicator`` variant a player gets when the flow indicator
#: is switched off. Listed rather than pattern-matched: ``Build_PipeHyper_C`` carries the
#: identical property and is a HYPERTUBE, which moves a player and no fluid at all, and
#: ``Build_PipelineSupport_C`` is a pole with a height and no geometry at all. See `_pipes`.
PIPE_CLASSES = (
    "Build_Pipeline_C",
    "Build_PipelineMK2_C",
    "Build_Pipeline_NoIndicator_C",
    "Build_PipelineMK2_NoIndicator_C",
)

_MANUFACTURER_HINTS = (
    "ConstructorMk1",
    "SmelterMk1",
    "FoundryMk1",
    "OilRefinery",
    "Packager",
    "ManufacturerMk1",
    "AssemblerMk1",
    "Blender",
    "HadronCollider",
    "Converter",
    "QuantumEncoder",
)
_EXTRACTOR_HINTS = (
    "MinerMk1",
    "MinerMk2",
    "MinerMk3",
    "OilPump",
    "WaterPump",
    "FrackingExtractor",
    "FrackingSmasher",
)
_GENERATOR_HINTS = (
    "GeneratorCoal",
    "GeneratorFuel",
    "GeneratorNuclear",
    "GeneratorBiomass",
    "GeneratorGeoThermal",
    "GeneratorIntegratedBiomass",
)


def truthy(value) -> bool:
    """BoolProperty comes back as a uint8 where 16 is True. `v == 1` is wrong."""
    return bool(value)


def cls_of(type_path: str) -> str:
    return type_path.rsplit(".", 1)[-1] if type_path else ""


def ref_path(value) -> str | None:
    """instanceName of an ObjectReference, or None. Never use repr() on these."""
    p = getattr(value, "pathName", None)
    return str(p) if p else None


def ref_class(value) -> str | None:
    """Class name from an ObjectReference OR a bare asset-path string.

    Some structs store references as plain path strings rather than
    ObjectReference objects (e.g. the ``Item`` member of an inventory stack is
    ``[path, int]``), so both shapes must resolve.
    """
    p = ref_path(value)
    if p is None and isinstance(value, str) and value:
        p = value
    if not p:
        return None
    tail = p.rsplit(".", 1)[-1]
    return tail or None


def props(obj) -> dict:
    """properties is a list of [name, value] pairs; absent means empty."""
    out: dict = {}
    for entry in getattr(obj, "properties", None) or []:
        try:
            out[entry[0]] = entry[1]
        except (IndexError, TypeError):
            continue
    return out


def struct_fields(entry) -> dict:
    """Flatten one element of a StructProperty array into {field: value}.

    The parser emits each struct as ``[values, propertyTypes]``, where ``values`` is
    the list of ``[name, value]`` pairs and ``propertyTypes`` is a parallel list of
    ``[name, typeName, ...]``. Some nested structs arrive already flattened, so both
    shapes are accepted -- iterating the outer list blindly yields a list where a
    field name is expected and raises "unhashable type: 'list'".
    """
    if not isinstance(entry, list):
        return {}
    candidate = entry
    if len(entry) == 2 and all(isinstance(x, list) for x in entry):
        first = entry[0]
        if first and all(isinstance(p, list) and p and isinstance(p[0], str) for p in first):
            candidate = first
    out: dict = {}
    for pair in candidate:
        if isinstance(pair, list) and len(pair) >= 2 and isinstance(pair[0], str):
            out[pair[0]] = pair[1]
    return out


def iter_objects(save):
    """Yield (type_path, header, object) over every level.

    ComponentHeader lacks typePath, hence the getattr with a default -- attribute
    access would raise partway through a 44k-object walk.
    """
    for level in save.levels:
        headers = getattr(level, "actorAndComponentObjectHeaders", None) or []
        objects = getattr(level, "objects", None) or []
        for header, obj in zip(headers, objects):
            yield (getattr(header, "typePath", "") or ""), header, obj


def pos_of(header) -> list | None:
    p = getattr(header, "position", None)
    if not p:
        return None
    try:
        return [round(float(p[0]), 1), round(float(p[1]), 1), round(float(p[2]), 1)]
    except (TypeError, IndexError, ValueError):
        return None


def yaw_of(quat) -> float:
    """Top-down facing in degrees from a placement quaternion ``(x, y, z, w)``.

    **The convention, and it is measured rather than assumed.**

    * **Axis: world Z (up), and only Z.** Every one of the reference save's 8,347
      lightweight buildables has ``x == y == 0`` exactly, and of its 9,153 ``Build_*``
      actors only 396 do not -- pipeline flow indicators, ceiling attachments and wall
      poles, i.e. parts mounted on a wall, none of them a machine. So a single yaw is the
      whole rotation of everything a top-down or floor view draws, which is why §16b of
      DESIGN.md settles for one float instead of three.
    * **Handedness: positive yaw turns +X towards +Y**, in the same coordinates the
      projection's ``pos`` reports -- that is, it is directly comparable with
      ``atan2(dy, dx)`` between two positions, with no sign flip and no axis swap.
    * **Range** ``(-180, 180]``, the range ``atan2`` gives -- after one fold. A quaternion
      stored as float32, which is what an actor header carries, lands a half-turn on
      ``-179.999...``, so rounding alone would emit both ``-180.0`` and ``180.0`` for the
      same facing and a consumer bucketing yaws would see two of them.

    Verified against the geometry rather than against a formula. Take the 440 ``8x1``
    foundations of the reference world's ``-20`` degree platform: their 928 pairs sitting
    exactly one 800 cm tile apart lie at ``70.0000`` degrees modulo 90 (spread 0.0016),
    and ``-20 mod 90 = 70``. The same holds for the ``-10`` / ``70`` / ``160`` / ``30``
    degree groups -- 2,588 / 425 / 264 / 66 tile-spaced pairs, each within 0.001 degrees of
    its own yaw. A flipped sign or a swapped axis fails all five.

    The general form is kept even though ``x == y == 0`` reduces it to ``2*atan2(z, w)``:
    the 396 wall-mounted actors above do carry pitch, and this is their yaw, not nonsense.
    """
    try:
        x, y, z, w = (float(v) for v in quat)
    except (TypeError, ValueError):
        return 0.0
    deg = round(math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))), 2)
    return 180.0 if deg == -180.0 else deg


def header_info(path: str) -> dict:
    i = read_save_info(path)
    st = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "filename": os.path.basename(path),
        "session_name": i.sessionName,
        "save_identifier": i.saveIdentifier,  # stable per WORLD, groups saves
        "save_header_version": i.saveHeaderType,
        "save_version": i.saveVersion,
        "build_version": i.buildVersion,
        "play_duration_s": i.playDurationInSeconds,
        "save_datetime_ticks": i.saveDateTimeInTicks,
        "is_modded": truthy(getattr(i, "isModdedSave", False)),
        "is_creative": truthy(getattr(i, "isCreativeModeEnabled", False)),
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
    }


def _map_items(value) -> dict:
    """Flatten a MapProperty of ObjectProperty->Int into {class: amount}."""
    out: dict[str, float] = {}
    if not isinstance(value, list):
        return out
    for entry in value:
        try:
            k, v = entry[0], entry[1]
        except (IndexError, TypeError):
            continue
        name = ref_class(k) or (str(k) if isinstance(k, str) else None)
        if name and isinstance(v, (int, float)):
            out[name] = out.get(name, 0) + v
    return out


def extract(path: str) -> dict:
    save = read_full_save(path)
    # Diagnostics, and only to stderr: stdout is the projection and has to stay parseable.
    # pioneersav reports what it skipped rather than silently approximating it, and those
    # notes are worth seeing without becoming a projection field -- adding them to `out`
    # would make the two parsers' output differ for a reason that is not a disagreement.
    for offset, what in getattr(save, "warnings", None) or []:
        print(f"pioneersav: at body offset {offset}: {what}", file=sys.stderr)

    out: dict = {
        "schema_version": SCHEMA_VERSION,
        "header": header_info(path),
        "progression": {},
        "research": {"unclaimed_hard_drives": [], "ongoing": [], "unlocked_trees": []},
        "unlock_flags": {},
        "building_counts": {},
        "lightweight_counts": {},
        # Map-placed actors the save records as GONE. The only record of what has been
        # collected: nothing in a save says a power slug exists, only that one no longer does.
        "removed": {"cells": [], "instances": [], "counts": {}},
        "structures": {"classes": [], "instances": []},
        # Belt routing, as polylines. Schema 12; see `_belts`.
        "belts": {"classes": [], "segments": []},
        # Fluid pipe routing, as polylines. Schema 13; see `_pipes`.
        "pipes": {"classes": [], "networks": [], "segments": []},
        "machines": [],
        "extractors": [],
        "generators": [],
        "pipe_networks": [],
        "depot": {},
        # Split by owner: lumping machine buffers in with carried stock overstates
        # everything. Fluids are raw litres here; the server scales them.
        "inventories": {"player": {}, "storage": {}, "machine": {}},
        "node_state": {},
        # Char_Player_C carries the pawn's transform. BP_PlayerState_C sits at the
        # origin and is NOT a position -- reading it would put every player at (0,0).
        "players": [],
        # Connectivity, interned so the projection stays small: ~11.5k material edges
        # and ~1.3k power edges would be megabytes as repeated instanceNames.
        "graph": {"actors": [], "roles": [], "material": [], "power": []},
        "warnings": [],
    }
    counts: dict[str, int] = {}
    n_objects = 0
    #: (chain actor world position, the actor) for every conveyor chain. Held rather than
    #: decoded here because the trailing bytes decode lazily and doing it in the walk would
    #: interleave a 0.3 s decode with the property pass for no gain -- see `_belts`.
    chain_actors: list[tuple] = []
    #: (class, instanceName, world position, mSplineData) per fluid pipe, and
    #: (mPipeNetworkID, fluid, [member paths]) per pipe network. Both held rather than
    #: resolved in the walk because a pipe's fluid comes off its NETWORK, and a network
    #: actor can be written after the pipes it owns -- see `_pipes`.
    pipe_actors: list[tuple] = []
    pipe_nets: list[tuple] = []

    # --- connectivity interning -------------------------------------------
    actor_ix: dict[str, int] = {}
    role_ix: dict[str, int] = {}
    uptime: dict[str, dict] = {}
    buffers: dict[str, dict] = {}
    #: owner instanceName -> {itemClass: count} slotted into its InventoryPotential.
    potential: dict[str, dict] = {}
    record_by_instance: dict[str, dict] = {}
    material_edges: list[list[int]] = []
    wire_ends: dict[str, list[tuple[str, str]]] = {}

    def actor_id(name: str) -> int:
        short = name.rsplit(".", 1)[-1]
        if short not in actor_ix:
            actor_ix[short] = len(actor_ix)
        return actor_ix[short]

    def role_id(name: str) -> int:
        if name not in role_ix:
            role_ix[name] = len(role_ix)
        return role_ix[name]

    for type_path, header, obj in iter_objects(save):
        n_objects += 1
        cls = cls_of(type_path)
        p = props(obj)
        instance = getattr(header, "instanceName", None) or getattr(obj, "instanceName", "")

        # Inventories live on COMPONENTS, which have no typePath at all, so this
        # must run before the empty-cls guard or every stack is silently skipped.
        if "mInventoryStacks" in p:
            bucket = inventory_bucket(str(instance))
            _accumulate_inventory(p["mInventoryStacks"], out["inventories"][bucket])
            # Input/OutputInventory belong to the OWNING machine and are what tell a
            # starved machine from a backed-up one. Keyed by owner, not by component.
            role = str(instance).rsplit(".", 1)[-1]
            if role in ("InputInventory", "OutputInventory", "FuelInventory"):
                # The OWNER's full instanceName, which is what the actor record uses.
                # rsplit(".", 2)[-2] would give the bare short name and never match.
                owner = str(instance).rpartition(".")[0]
                # A generator has no InputInventory -- its intake is FuelInventory.
                # Without this a starved coal plant shows no evidence either way.
                side = {"InputInventory": "in", "OutputInventory": "out"}.get(role, "fuel")
                totals: dict = {}
                _accumulate_inventory(p["mInventoryStacks"], totals)
                # Per ITEM, not just a total. "Is the output backed up" needs the item's
                # stack size, which only the docs know, so the class has to survive.
                buffers.setdefault(owner, {})[side] = {
                    "items": totals,
                    "slots": len(p["mInventoryStacks"] or []),
                }
            elif role == "InventoryPotential":
                # The overclock slot inventory: what is physically plugged into the
                # building. This is the ONLY record of a committed Power Shard, and it
                # cannot be reconstructed from the clock. A shard raises the MAXIMUM
                # potential; the slider is then set anywhere below it, so two buildings
                # on this save hold 3 shards while running at 2.0. Deriving from clock
                # would report 95 committed shards where 97 are actually spent.
                # Every building has this component (447 of them); 41 are non-empty.
                totals = {}
                _accumulate_inventory(p["mInventoryStacks"], totals)
                if totals:
                    potential[str(instance).rpartition(".")[0]] = totals

        # Productivity. The window is a fixed 300 s, so produce/window is a clean
        # fraction. ProduceDuration is ABSENT when zero -- UE omits defaults -- so a
        # missing value is a real zero, not missing data.
        if "mLastProductivityMeasurementDuration" in p:
            window = p.get("mLastProductivityMeasurementDuration") or 0.0
            produce = p.get("mLastProductivityMeasurementProduceDuration", 0.0) or 0.0
            cur_window = p.get("mCurrentProductivityMeasurementDuration", 0.0) or 0.0
            cur_produce = p.get("mCurrentProductivityMeasurementProduceDuration", 0.0) or 0.0
            uptime[str(instance)] = {
                "window_s": round(float(window), 2),
                "produce_s": round(float(produce), 2),
                "cur_window_s": round(float(cur_window), 2),
                "cur_produce_s": round(float(cur_produce), 2),
                "producing": truthy(p.get("mIsProducing", 0)),
            }

        # Factory connections live on COMPONENTS. instanceName is
        # "<...>.Build_X_C_123.Output1", so the owner is the second-to-last segment
        # and the connector role is the last -- the role is what orients the edge.
        target = p.get("mConnectedComponent")
        target_path = ref_path(target)
        if target_path and "." in instance:
            material_edges.append(
                [
                    actor_id(instance.rsplit(".", 2)[-2]),
                    actor_id(target_path.rsplit(".", 2)[-2]),
                    role_id(instance.rsplit(".", 1)[-1]),
                    role_id(target_path.rsplit(".", 1)[-1]),
                ]
            )
        for wire in p.get("mWires") or []:
            wire_path = ref_path(wire)
            if wire_path and "." in instance:
                wire_ends.setdefault(wire_path, []).append(
                    (instance.rsplit(".", 2)[-2], instance.rsplit(".", 1)[-1])
                )

        if not cls:
            continue

        # ---- singleton managers -------------------------------------------
        if cls == "FGRecipeManager":
            out["progression"]["available_recipes"] = sorted(
                filter(None, (ref_class(r) for r in p.get("mAvailableRecipes") or []))
            )
            continue
        if cls == "BP_SchematicManager_C":
            out["progression"]["purchased_schematics"] = sorted(
                filter(None, (ref_class(r) for r in p.get("mPurchasedSchematics") or []))
            )
            out["progression"]["last_active_schematic"] = ref_class(p.get("mLastActiveSchematic"))
            continue
        if cls == "BP_GamePhaseManager_C":
            out["progression"]["game_phase"] = ref_class(p.get("mCurrentGamePhase")) or str(
                p.get("mCurrentGamePhase") or ""
            )
            out["progression"]["target_phase"] = ref_class(p.get("mTargetGamePhase")) or str(
                p.get("mTargetGamePhase") or ""
            )
            out["progression"]["phase_costs_remaining"] = _phase_costs(p.get("mGamePhaseCosts"))
            # THE live delivery record, and the only one. mGamePhaseCosts above is
            # marked DEPRECATED in FGGamePhaseManager.h and is provably frozen (see
            # _phase_costs). Absent means empty -- nothing delivered toward the target
            # phase yet -- which is a real answer, not missing data.
            out["progression"]["paid_off_target"] = _cost_amounts(
                p.get("mTargetGamePhasePaidOffCosts")
            )
            continue
        if cls == "BP_ResearchManager_C":
            out["research"]["unclaimed_hard_drives"] = _hard_drives(
                p.get("mUnclaimedHardDriveData")
            )
            out["research"]["last_used_hard_drive_id"] = p.get("mLastUsedHardDriveID")
            out["research"]["unlocked_trees"] = sorted(
                filter(None, (ref_class(r) for r in p.get("mUnlockedResearchTrees") or []))
            )
            # Absent means empty: UE omits empty SaveGame TArrays.
            out["research"]["ongoing"] = _ongoing(p.get("mSavedOngoingResearch"))
            continue
        if cls == "BP_UnlockSubsystem_C":
            for k in (
                "mIsMapUnlocked",
                "mIsBuildingOverclockUnlocked",
                # Written only once it becomes true -- UE omits a SaveGame property
                # still at its default -- so ABSENT means not researched. That is why a
                # save from before the research carries no such key at all.
                "mIsBuildingProductionBoostUnlocked",
                "mIsBuildingEfficiencyUnlocked",
                "mIsBlueprintsUnlocked",
                "mIsCustomizerUnlocked",
            ):
                if k in p:
                    out["unlock_flags"][k] = truthy(p[k])
            for k in ("mNumTotalInventorySlots", "mNumTotalArmEquipmentSlots"):
                if k in p:
                    out["unlock_flags"][k] = p[k]
            continue
        if cls == "FGCentralStorageSubsystem":
            out["depot"] = _stored_items(p.get("mStoredItems"))
            continue
        if cls == "FGLightweightBuildableSubsystem":
            # Holds Build_* classes that appear in NO actor header, so a
            # header-only census undercounts what is actually built.
            out["lightweight_counts"] = _lightweight(obj)
            out["structures"] = _structures(obj)
            continue
        if cls == "FGPipeNetwork":
            fluid = ref_class(p.get("mFluidDescriptor"))
            if fluid:
                out["pipe_networks"].append({"instance": instance, "fluid": fluid})
            # Held for `_pipes` whether or not it named a fluid: an empty network still owns
            # its pipes, and "drawn, fluid unknown" beats "not drawn".
            pipe_nets.append(
                (
                    p.get("mPipeNetworkID"),
                    fluid,
                    [ref_path(m) for m in p.get("mFluidIntegrantScriptInterfaces") or []],
                )
            )
            continue
        # Four class names, not one: the three ``_RepSize*`` variants are the same actor
        # with a bigger replication budget and the identical record.
        if cls.startswith("FGConveyorChainActor"):
            chain_actors.append((getattr(header, "position", None), obj))
            continue

        if cls == "Char_Player_C":
            out["players"].append(
                {
                    "instance": instance,
                    "pos": pos_of(header),
                    # Present only while the player is holding it; useful as a hint
                    # that this pawn is the active one in a co-op save.
                    "has_build_gun": "mBuildGun" in p,
                }
            )
            continue

        # ---- resource nodes ------------------------------------------------
        if cls.startswith(("BP_ResourceNode", "BP_Fracking")):
            counts[cls] = counts.get(cls, 0) + 1
            left = p.get("mResourcesLeft")
            if left is not None and left != -1:
                out["node_state"][instance] = {"resources_left": left}
            continue

        if not cls.startswith("Build_"):
            continue
        counts[cls] = counts.get(cls, 0) + 1

        # Held, not `continue`d past: a pipe is still a Build_ actor and still owes
        # `building_counts` its tally, which is a schema-11 key that predates all of this.
        if cls in PIPE_CLASSES:
            pipe_actors.append(
                (cls, instance, getattr(header, "position", None), p.get("mSplineData"))
            )

        record = {
            "cls": cls,
            "instance": instance,
            "pos": pos_of(header),
            # Always emitted, like `pos` and unlike the property-derived fields below: an
            # actor header always carries a transform, so an absent yaw would mean the
            # projection is old rather than the building is unrotated.
            "yaw": yaw_of(getattr(header, "rotation", None)),
        }
        if "mCurrentPotential" in p:
            record["clock"] = round(float(p["mCurrentPotential"]), 6)
        if "mPendingPotential" in p:
            record["pending_clock"] = round(float(p["mPendingPotential"]), 6)
        if "mIsProductionPaused" in p:
            record["paused"] = truthy(p["mIsProductionPaused"])
        # Somersloop runtime property names are UNVERIFIED -- neither appears in the
        # save nor in Docs.json. Record whatever is actually present.
        for key in ("mProductionBoost", "mCurrentProductionBoost", "mPendingProductionBoost"):
            if key in p:
                record["production_boost"] = p[key]
                record["production_boost_field"] = key

        # Uptime lives on the actor's OWN properties, so it is available now.
        live = uptime.get(str(instance))
        if live:
            record["uptime"] = live
        # Buffers do NOT: they are components, and components are visited after the
        # actor they belong to in the same pass, so `buffers` is still empty here.
        # Attached in a post-pass below.
        record_by_instance[str(instance)] = record

        if any(h in cls for h in _MANUFACTURER_HINTS):
            record["recipe"] = ref_class(p.get("mCurrentRecipe"))
            out["machines"].append(record)
        elif any(h in cls for h in _EXTRACTOR_HINTS):
            record["node"] = ref_path(p.get("mExtractableResource"))
            out["extractors"].append(record)
        elif any(h in cls for h in _GENERATOR_HINTS):
            record["fuel"] = ref_class(p.get("mCurrentFuelClass"))
            out["generators"].append(record)

    # Buffers, now that every component has been seen.
    for owner, sides in buffers.items():
        record = record_by_instance.get(owner)
        if record is not None:
            record["buffers"] = sides

    # Slotted shards, same post-pass reason: InventoryPotential is a component.
    for owner, slotted in potential.items():
        record = record_by_instance.get(owner)
        if record is not None:
            record["potential_slots"] = slotted

    # A power wire always joins exactly two connections; anything else is a
    # half-built or orphaned line and is dropped rather than guessed at.
    power_edges = [
        [actor_id(a[0]), actor_id(b[0])]
        for ends in wire_ends.values()
        if len(ends) == 2
        for a, b in [ends]
    ]
    out["graph"] = {
        "actors": [name for name, _ in sorted(actor_ix.items(), key=lambda kv: kv[1])],
        "roles": [name for name, _ in sorted(role_ix.items(), key=lambda kv: kv[1])],
        "material": material_edges,
        "power": power_edges,
    }
    out["building_counts"] = dict(sorted(counts.items()))
    out["belts"] = _belts(chain_actors)
    out["pipes"] = _pipes(pipe_actors, pipe_nets)
    out["removed"] = _removed(save)
    out["n_objects"] = n_objects
    out.setdefault("progression", {}).setdefault("available_recipes", [])
    out["progression"].setdefault("purchased_schematics", [])
    return out


def _removed(save) -> dict:
    """Map-placed actors the save records as GONE -- the only record of what was collected.

    Slugs, mushrooms, Mercer spheres, somersloops, shrines, crashed drop pods and world
    debris are placed by the map and never saved, so nothing in the save says a slug exists.
    What it says is which ones do *not* any more, and that negative record is the only way to
    answer "how many slugs have I picked up" or "which crash sites have I looted".

    Both parsers can produce this. `pioneersav` merges the three lists the format keeps into
    `destroyed_actors`; the vendored parser exposes the same three separately, as each level's
    `collectables1`/`collectables2` plus two save-level lists. Verified equal set for set on
    the reference save -- 889 actors either way -- which is why this is a projection field and
    not a reason the two disagree.

    Interned by cell, and the actor's path is reduced to its leaf: the full path repeats
    `Persistent_Level:PersistentLevel.` on every one of 889 entries and says nothing.
    """
    refs = getattr(save, "destroyed_actors", None)
    if refs is None:
        # The vendored parser's spelling: three lists, none of them merged.
        # ref_path, not str(): the vendored ObjectReference's __str__ renders the whole
        # object as "<ObjectReference: levelName=..., pathName=...>", so str() ends in ">"
        # and every leaf name comes out unique -- 889 distinct classes instead of 270.
        pairs: list[tuple[str, str]] = []
        for level in getattr(save, "levels", None) or []:
            for which in ("collectables1", "collectables2"):
                for ref in getattr(level, which, None) or []:
                    pairs.append((str(getattr(ref, "levelName", "")), ref_path(ref) or ""))
        for which in ("dropPodObjectReferenceList", "extraObjectReferenceList"):
            for ref in getattr(save, which, None) or []:
                pairs.append((str(getattr(ref, "levelName", "")), ref_path(ref) or ""))
        refs = list(dict.fromkeys(pairs))

    cells: dict[str, int] = {}
    instances: list[list] = []
    counts: dict[str, int] = {}
    # Sorted, because the order is an artefact of which of the three lists a parser walks
    # first and means nothing. Both engines then emit byte-identical output, which keeps this
    # field usable as a cache key and keeps the parity diff a measurement of content.
    for cell, path in sorted(refs, key=lambda pair: (pair[0], pair[1])):
        leaf = path.rsplit(".", 1)[-1]
        if not leaf:
            continue
        ix = cells.setdefault(cell, len(cells))
        instances.append([ix, leaf])
        counts[_removed_class(leaf)] = counts.get(_removed_class(leaf), 0) + 1
    return {
        "cells": [c for c, _ in sorted(cells.items(), key=lambda kv: kv[1])],
        "instances": instances,
        "counts": dict(sorted(counts.items())),
    }


def _removed_class(leaf: str) -> str:
    """Class of a destroyed actor, from its instance name alone.

    There is no class path in these lists -- only the actor's name, and the game builds those
    three different ways: `BP_Crystal_mk3_C_2146` (class then index), `BP_Crystal2_228` (a
    numbered class variant then index) and `BP_MercerShrine_C_UAID_..._1397405905` (class, a
    world id, then an index). So the class is recovered by stripping from the right: the
    trailing index, then a `_UAID_<hex>` if present, then a trailing `_C`.

    Approximate on purpose, and the reason is worth stating: `BP_Crystal2_228` cannot be told
    from a class literally named `BP_Crystal2`, so the census groups by what the name shows
    rather than by a class list nobody has. Callers wanting slugs should match a prefix
    (`BP_Crystal`), which is what `save/state.py` does.
    """
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    if len(parts) >= 2 and parts[-2] == "UAID":
        parts = parts[:-2]
    if parts and parts[-1] == "C":
        parts.pop()
    return "_".join(parts) or leaf


def _stored_items(raw) -> dict:
    """FGCentralStorageSubsystem.mStoredItems -> {itemClass: amount} (the Dimensional Depot)."""
    out: dict[str, float] = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        item = ref_class(fields.get("ItemClass")) or ref_class(fields.get("Item"))
        amount = fields.get("Amount", fields.get("NumItems", 0))
        if item is None and isinstance(entry, list) and len(entry) >= 2:
            item = ref_class(entry[0])
            if isinstance(entry[1], (int, float)):
                amount = entry[1]
        if item and isinstance(amount, (int, float)) and amount:
            out[item] = out.get(item, 0) + amount
    return out


def _phase_costs(raw) -> dict:
    """mGamePhaseCosts: remaining delivery amounts per phase. DEPRECATED AND FROZEN.

    FGGamePhaseManager.h calls both this array and the EGamePhase enum it is keyed by
    "DEPRECATED Only kept for save compatibility". That is not a warning about a future
    removal -- the field is already dead, and it is emitted here only so the server can
    show it next to the live record and say so.

    Measured across all 29 parseable saves of the reference world: the array is
    byte-identical at 180 h and at 316 h, spanning the play session (between 244.0 h and
    251.0 h) where mCurrentGamePhase advanced Phase_2 -> Phase_3 and mTargetGamePhase
    Phase_3 -> Phase_4. Completing an entire Space Elevator phase moved nothing in it.
    It still claims 500 Modular Engine and 100 Adaptive Control Unit outstanding on a
    phase the player finished 70 hours ago.

    Only 4 phases are stored while the game has more, so later phases never appear.
    """
    out: dict = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        raw_phase = fields.get("gamePhase")
        # ByteProperty arrives as [enumTypeName, valueName]; take the value.
        if isinstance(raw_phase, list) and raw_phase:
            phase = str(raw_phase[-1])
        else:
            phase = ref_class(raw_phase) or str(raw_phase)
        out[phase] = _cost_amounts(fields.get("cost"))
    return out


def _cost_amounts(raw) -> dict:
    out: dict[str, float] = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        item = ref_class(fields.get("ItemClass"))
        if item:
            out[item] = fields.get("Amount", 0)
    return out


def _hard_drives(raw) -> list:
    """mUnclaimedHardDriveData -> the player's live 2-way choices.

    FHardDriveData = {HardDriveID:int32, PendingRewards:[schematic],
    PendingRewardsRerollsExecuted:int32}, per FGResearchManager.h. Rerolls left is
    1 - rerolls_executed (UFGResearchSettings::mNumRerollsPerHardDrive = 1, a
    config-driven default that a packaged ini could in principle override).
    """
    out: list = []
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        rewards = [ref_class(r) for r in fields.get("PendingRewards") or []]
        out.append(
            {
                "hard_drive_id": fields.get("HardDriveID"),
                "options": [r for r in rewards if r],
                "rerolls_executed": fields.get("PendingRewardsRerollsExecuted", 0),
            }
        )
    return out


def _ongoing(raw) -> list:
    """mSavedOngoingResearch. The float is seconds REMAINING, not a timestamp."""
    out: list = []
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        inner = struct_fields(fields.get("ResearchData")) if "ResearchData" in fields else fields
        out.append(
            {
                "schematic": ref_class(inner.get("Schematic")),
                "seconds_left": fields.get("ResearchCompleteTimestamp"),
                "fields_seen": sorted(fields),
            }
        )
    return out


def _placed(inst) -> bool:
    """Is this lightweight record a piece that exists, or a stale slot?

    173 of the 224,530 records across the 31 saves carry **no swatch and no recipe** -- every
    other one carries both, and not a single record carries exactly one, which is what makes
    this a clean test rather than a heuristic. They occur on 7 saves, up to 80 in one.

    Emitting them is not cosmetic. `graph/structure.py` builds foundation slabs from these
    positions and `spatial/elevation.py` samples every one as ground height, so a stale record
    invents floor: on `Han solo.sav` the phantom records produce an entire extra 7-tile slab,
    and on `Han Solo_260726-212757` the newest construction measures 521 tiles where 496 exist.

    Deliberately NOT positional. Our parser's instance has the swatch at index 3 and the recipe
    at 10; the vendored parser's has them at 2 and 7, because it does not surface the scale. A
    positional guard would therefore read a different field per engine and break the projection
    parity that the whole reimplementation is measured by. Asking "does any field name an asset"
    is true of a real piece and false of a stale slot under either shape.
    """
    for field in inst if isinstance(inst, list) else ():
        if getattr(field, "pathName", None):
            return True
        if isinstance(field, str) and field:
            return True
    return False


def _lightweight(obj) -> dict:
    """Build_* classes held by FGLightweightBuildableSubsystem.

    These appear in NO actor header -- a header-only census reported 86 built
    classes where 103 are actually built. The data lives in ``actorSpecificInfo``,
    not in ``properties``, shaped as ``[count, [buildClassPath, [instance, ...]], ...]``,
    so the count comes from the instance list that follows each class path.
    """
    out: dict[str, int] = {}

    def walk(node) -> None:
        if not isinstance(node, list):
            return
        for i, child in enumerate(node):
            if isinstance(child, str) and "Build_" in child and child.endswith("_C"):
                cls = ref_class(child)
                nxt = node[i + 1] if i + 1 < len(node) else None
                # Stale slots are skipped, not counted -- see `_placed`.
                n = sum(1 for inst in nxt if _placed(inst)) if isinstance(nxt, list) else 1
                if cls:
                    out[cls] = out.get(cls, 0) + n
            else:
                walk(child)

    walk(getattr(obj, "actorSpecificInfo", None))
    return out


def _structures(obj) -> dict:
    """Transforms of every lightweight buildable -- foundations, ramps, walls, catwalks.

    These carry the one signal power and belts both lack: what the player physically
    BUILT AS ONE THING. Measured on the reference save, foundation slabs split into 101
    pieces where power gives 9 and belts give 35, and every named factory lands on its
    own dominant slab.

    ``actorSpecificInfo`` is a list of ``[buildClassPath, [instance, ...]]`` pairs, and
    each instance is ``[rotationQuaternion, position, ...]`` -- so unlike the class
    census above, the transform is the SECOND element, not derivable from the count.

    Rows are ``[classIndex, x, y, z, yaw]``. The yaw column arrived in schema 12 and is
    what stops a client drawing an angled platform as a staircase: 4,631 of the reference
    save's 8,347 pieces sit at a yaw that is not a multiple of 90, across 34 distinct
    angles. It is always present -- 0.0 is "axis-aligned", not "unknown" -- and every
    consumer of these rows reads them positionally with a ``len(row) >= 4`` guard, so the
    extra column is additive for a reader that predates it.

    Interned and rounded to whole centimetres: 8,372 pieces cost 198 KB this way
    against roughly 1.2 MB emitted naively, and sub-centimetre precision is meaningless
    for deciding whether two 8 m foundations touch.

    The slab geometry is deliberately NOT computed here. It lives behind a subprocess
    and a cache, so freezing the link distance in the sidecar would mean a 3-minute
    re-parse to tune a threshold.
    """
    classes: list[str] = []
    index: dict[str, int] = {}
    instances: list[list] = []

    for entry in getattr(obj, "actorSpecificInfo", None) or []:
        if not (isinstance(entry, list) and len(entry) == 2):
            continue
        cls_path, items = entry
        cls = ref_class(cls_path) or str(cls_path).rsplit(".", 1)[-1]
        if not isinstance(items, list):
            continue
        ci = index.get(cls)
        if ci is None:
            ci = index[cls] = len(classes)
            classes.append(cls)
        for inst in items:
            if not (isinstance(inst, list) and len(inst) >= 2) or not _placed(inst):
                continue
            pos = inst[1]
            try:
                instances.append([ci, int(pos[0]), int(pos[1]), int(pos[2]), yaw_of(inst[0])])
            except (TypeError, ValueError, IndexError):
                continue

    return {"classes": classes, "instances": instances}


def _belts(chains: list) -> dict:
    """Every conveyor's route, as polylines. ``chains`` is ``[(actorPosition, actor), ...]``.

    The one thing a map of a factory cannot be drawn without and the projection had no
    field for. Belts are not lightweight buildables and their geometry is not in any
    property: it lives in ``FGConveyorChainActor``'s trailing bytes, which
    ``pioneersav.trailers`` decodes and which nothing read until schema 12.

    Rows are ``[chainIndex, classIndex, [[x, y, z], ...]]``:

    * **chainIndex** groups segments into the run the game itself groups them into -- one
      chain is one continuous flow of items, 1,909 chains over 3,085 belt pieces on the
      reference save. Dense and ordered, so a consumer that wants per-chain polylines
      concatenates the rows sharing an index and a consumer that wants per-belt polylines
      draws each row.
    * **classIndex** interns the belt's class, which carries both the mark and whether the
      piece is a belt or a LIFT -- 302 lift segments here, on 183 chains. A floor view
      needs exactly that distinction: a lift is the connector between two Z bands.
    * The points are the spline's control points at the same whole-centimetre precision
      ``_structures`` uses, and there is nothing to thin: a straight belt is
      2 points and the reference save's 3,085 segments carry 8,292 points between them,
      2.7 apiece. They are already the bends and nothing else.

    **Two facts about the source that this function is entirely about, both measured.**

    1. **The spline is in the chain actor's frame**, so the actor's own position has to be
       added back. Compared against the belt actors' own headers, ``point + chainPos`` is a
       median 0.0 cm from the belt it belongs to, where the raw point is 156,041 cm away --
       a whole map. All **51,200 chains across the 66 saves on this disk** carry an identity
       rotation, so there is no orientation to undo; a chain that ever carried one would
       need this to rotate the points before translating them, which is why the measurement
       is stated rather than the assumption made quietly.
    2. **Segments are stored output-first.** ``pioneersav.trailers`` records that offsets
       grow towards the output and that ``segments[-1]`` holds offset 0, i.e. the chain's
       input, so rows come out reversed -- in TRAVEL ORDER, input first. Measured over the
       reference save's 1,176 joins between consecutive segments of one chain: reversed,
       1,096 of them are the exact same point and the other 80 are the 200/300/400 cm of
       spline-less offset a conveyor lift junction carries. In file order the median join
       is 2,450 cm and the worst 11,200, i.e. every chain is drawn as a zigzag.

    A chain whose trailing bytes will not decode costs that chain and is reported on
    stderr, not the whole projection: belts are new and a save that projected yesterday
    must not stop projecting because one trailer is unreadable.
    """
    classes: list[str] = []
    index: dict[str, int] = {}
    segments: list[list] = []
    chain_ix = 0

    for origin, obj in chains:
        try:
            info = obj.actorSpecificInfo
        except PARSE_ERROR as exc:
            print(f"pioneersav: conveyor chain skipped: {exc}", file=sys.stderr)
            continue
        if not (isinstance(info, list) and len(info) >= 3 and isinstance(info[2], list)):
            continue
        try:
            ox, oy, oz = (float(v) for v in origin)
        except (TypeError, ValueError):
            continue

        rows: list[list] = []
        for seg in reversed(info[2]):
            if not (isinstance(seg, list) and len(seg) >= 3):
                continue
            cls = _conveyor_class(ref_path(seg[1]) or "")
            if not cls:
                continue
            points = []
            for point in seg[2] if isinstance(seg[2], list) else ():
                try:
                    at = point[0]
                    # Rounded, not truncated as `_structures` does. That field's truncation
                    # is in the banked parity digests and cannot move now; a belt point is
                    # new, and rounding is both unbiased and exactly commutative with the
                    # whole-centimetre translation above, which is what lets a test check
                    # the frame correction by moving the chain and subtracting.
                    points.append([round(at[0] + ox), round(at[1] + oy), round(at[2] + oz)])
                except (TypeError, ValueError, IndexError):
                    continue
            # A single point is not a route. 2 is the commonest case by far -- a straight
            # belt -- and the most that can be said about a 1-point segment is where it is.
            if len(points) < 2:
                continue
            ci = index.get(cls)
            if ci is None:
                ci = index[cls] = len(classes)
                classes.append(cls)
            rows.append([chain_ix, ci, points])
        if rows:
            segments.extend(rows)
            chain_ix += 1

    return {"classes": classes, "segments": segments}


def _conveyor_class(path: str) -> str:
    """``...PersistentLevel.Build_ConveyorBeltMk3_C_1264`` -> ``Build_ConveyorBeltMk3_C``.

    A chain names its belts by instance, not by class, so the class is recovered from the
    name -- the same stripping ``_removed_class`` does, except that the trailing ``_C``
    stays, because these names are compared against class names that carry it.

    Checked rather than trusted: unlike a destroyed actor, a conveyor DOES have an actor
    header of its own, so the stripped name can be held against the real ``typePath``. Over
    the whole save folder the two agree on **83,389 of 83,389 segments**, with none whose
    belt is missing a header -- which is why the class is taken from the cheap source
    instead of the projection carrying a second index of every belt actor in the world.
    """
    leaf = path.rsplit(".", 1)[-1]
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    return "_".join(parts) if len(parts) > 1 else ""


def _pipes(actors: list, networks: list) -> dict:
    """Every fluid pipe's route, as polylines, and the fluid each one carries.

    ``actors`` is ``[(class, instanceName, actorPosition, mSplineData), ...]`` and
    ``networks`` is ``[(mPipeNetworkID, fluidClass, [memberPath, ...]), ...]``.

    The other half of "draw what the player built". Belts came out of a trailer; a pipe is
    simpler and was in reach the whole time -- **the spline is a PROPERTY**, ``mSplineData``,
    an array of structs whose ``Location`` is one control point. (Its ``ArriveTangent`` and
    ``LeaveTangent`` are the curve's shape between the points and are dropped: the game
    builds pipes out of straight runs and elbows, and the reference save's 503 pipes are 224
    two-point straights and 195 six-point elbows.)

    Rows are ``[networkIndex, classIndex, [[x, y, z], ...]]``:

    * **networkIndex** points into ``networks``, ``[{"id": ..., "fluid": ...}, ...]`` -- the
      game's own ``FGPipeNetwork`` grouping, 19 of them here, and the reason this key can say
      WATER or CRUDE OIL rather than only "a pipe". ``-1`` for a pipe no network claims,
      which happens on none of the reference save's 503.
    * **classIndex** interns the build class: Mk1 and Mk2, each with a ``NoIndicator``
      variant, which is a pipe whose flow indicator the player switched off.
    * The points are whole centimetres, like ``_belts``, and there is nothing to thin --
      1,987 points over 503 pipes, 3.9 apiece, and they are already only the corners.

    **The frame is the actor's, translated and not rotated -- measured, not assumed.** Every
    ``Location`` is stored relative to the pipe's own actor position, so that position is
    added back. That the first point of all 503 is exactly ``(0, 0, 0)`` proves the frame is
    local but proves nothing about which correction is right, so the check is made against
    things the pipes did not write:

    * The world's 306 ``Build_PipelineFlowIndicator_C`` actors are hung ON a pipe. Translated,
      294 of them sit within 10 cm of a pipe polyline -- **median 0.0 cm, p95 4.7 cm**. Raw,
      not one is within 10 cm of any pipe.
    * Pipe endpoints against the 141 junctions and pumps: translated, the median endpoint is
      6.0 m from the nearest fitting and 400 of 1,006 are within 3 m; raw, the median is
      1.7 km, which is a map away.

    **No rotation to undo, and that is the same statement `_belts` makes about chains**: all
    **18,069 pipeline actors across the 66 saves on this disk** carry an identity quaternion.
    A pipe that ever carried one would need its points rotated before translating, so the
    measurement is stated rather than the assumption made quietly.

    **Flow direction is NOT in here, because it is not in the save.** A pipe's two connectors
    are named ``PipelineConnection0`` and ``PipelineConnection1`` -- not input and output,
    unlike a belt's -- ``mFluidBox`` is a single float of contents, and the flow indicator
    actor carries nothing but its paint. The spline's own order is the order the player
    dragged it, which is not a claim about which way the fluid goes; in this game that is
    decided at runtime by head lift and demand and reverses when they do. So the points come
    out in file order and no consumer is told they mean travel.
    """
    fluid_of: dict[str, int] = {}
    nets: list[dict] = []
    for net_id, fluid, members in networks:
        ni = len(nets)
        nets.append({"id": net_id if isinstance(net_id, int) else None, "fluid": fluid})
        for member in members:
            if member:
                fluid_of[member] = ni

    classes: list[str] = []
    index: dict[str, int] = {}
    segments: list[list] = []

    for cls, instance, origin, spline in actors:
        if not isinstance(spline, list):
            continue
        try:
            ox, oy, oz = (float(v) for v in origin)
        except (TypeError, ValueError):
            continue
        points = []
        for entry in spline:
            at = struct_fields(entry).get("Location")
            try:
                points.append([round(at[0] + ox), round(at[1] + oy), round(at[2] + oz)])
            except (TypeError, ValueError, IndexError):
                continue
        # A single point is not a route, the same bar `_belts` sets -- and unlike a belt
        # there is no lift here to except: not one of the reference save's 503 pipes is
        # vertical (minimum horizontal span 11.6 cm), so every pipe is drawable as a line.
        if len(points) < 2:
            continue
        ci = index.get(cls)
        if ci is None:
            ci = index[cls] = len(classes)
            classes.append(cls)
        segments.append([fluid_of.get(str(instance), -1), ci, points])

    return {"classes": classes, "networks": nets, "segments": segments}


def inventory_bucket(instance: str) -> str:
    """Which pile a stack belongs to, from the component's instanceName.

    An instanceName looks like
    ``...PersistentLevel.Build_ConstructorMk1_C_2147441119.InputInventory``, so it
    carries both the owning actor class and the inventory's ROLE.

    This distinction is not cosmetic. Summing every stack in the world gives Water
    5,556,375 and Fuel 1,048,762 -- pipe and machine-buffer contents, in litres --
    which is a wildly wrong answer to "what do I have on hand". A build-cost check
    against that number would tell the player they can afford anything.
    """
    owner = instance.rsplit(".", 2)[-2] if instance.count(".") >= 2 else instance
    role = instance.rsplit(".", 1)[-1]
    if "PlayerState" in owner or owner.startswith(("Char_", "BP_Player")):
        return "player"
    if role == "StorageInventory" and any(
        tag in owner for tag in ("StorageContainer", "CentralStorage", "FreightWagon")
    ):
        return "storage"
    return "machine"


def _accumulate_inventory(raw, totals: dict) -> None:
    """mInventoryStacks -> {itemClass: total}.

    The ``Item`` member is a bare ``[assetPath, int]`` pair, not a keyed struct.
    """
    for stack in raw if isinstance(raw, list) else []:
        fields = struct_fields(stack)
        item_raw = fields.get("Item")
        if isinstance(item_raw, list) and item_raw:
            item = ref_class(item_raw[0])
        else:
            item = ref_class(struct_fields(item_raw).get("ItemClass"))
        amount = fields.get("NumItems", 0)
        if item and isinstance(amount, (int, float)) and amount:
            totals[item] = totals.get(item, 0) + amount


def list_dir(root: str) -> dict:
    """Header-only scan of every .sav under ``root``, in ONE process.

    Header parsing is milliseconds but process startup is not, so scanning 63 saves
    with one subprocess each would cost seconds. Unsupported saves are bucketed with
    their reason rather than aborting the scan: of 63 real files, 35 are pre-1.0
    (saveHeaderType 1/8/9) and fail here.
    """
    saves: list[dict] = []
    unsupported: list[dict] = []
    for path in sorted(Path(root).rglob("*.sav")):
        try:
            saves.append(header_info(str(path)))
        except Exception as exc:
            st = path.stat()
            unsupported.append(
                {
                    "path": str(path),
                    "filename": path.name,
                    "reason": str(exc),
                    "mtime_ns": st.st_mtime_ns,
                    "size": st.st_size,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "saves": saves,
        "unsupported": unsupported,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(
            json.dumps(
                {"error": "usage: extract_save.py <path.sav> [--header-only] | --list <dir>"}
            )
        )
        return 2
    try:
        if argv[0] == "--list":
            if len(argv) < 2:
                json.dump({"error": "usage: --list <dir>"}, sys.stdout)
                return 2
            json.dump(list_dir(argv[1]), sys.stdout, separators=(",", ":"))
            return 0
        path = argv[0]
        if "--header-only" in argv:
            payload = {"schema_version": SCHEMA_VERSION, "header": header_info(path)}
        else:
            payload = extract(path)
    except PARSE_ERROR as exc:
        json.dump({"error": "parse_error", "detail": str(exc), "path": path}, sys.stdout)
        return 1
    except Exception as exc:  # unexpected: surface the type, keep stdout valid JSON
        traceback.print_exc(file=sys.stderr)
        json.dump({"error": type(exc).__name__, "detail": str(exc), "path": path}, sys.stdout)
        return 1
    json.dump(payload, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
