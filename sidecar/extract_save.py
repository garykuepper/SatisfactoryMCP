"""Save-file extractor. Runs as a SEPARATE PROCESS from the MCP server.

    python sidecar/extract_save.py <path-to-sav> [--header-only]

Emits a JSON projection on stdout. Diagnostics go to stderr so stdout stays clean.

Why a subprocess rather than an import:
  * sav_parse hard-fails on unrecognised saveVersion, so a game patch breaks parsing
    until upstream updates. One boundary means one thing to fix.
  * A torn autosave (the file is rewritten in place every ~5 min while playing) or a
    parser crash cannot take down the server.
  * The projection is small and serialisable, which makes it the test fixture -- the
    whole suite then runs with no game install and no 2.9 MB .sav in git.

Property-access hazards handled here, all of which fail SILENTLY otherwise:
  * ComponentHeader has no typePath attribute at all.
  * BoolProperty is a raw uint8 where 16 means True (observed 16/1/0).
  * UE omits empty TArray SaveGame properties, so absent means empty, not missing.
  * ObjectReference defines __str__ but not __repr__, so any repr()-based search
    finds nothing.
  * mItemsPickedUp is a MapProperty keyed by player state containing an inner map.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "sat_sav_parse"))

import sav_parse

SCHEMA_VERSION = 6

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


def header_info(path: str) -> dict:
    i = sav_parse.readSaveFileInfo(path)
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
    save = sav_parse.readFullSaveFile(path)

    out: dict = {
        "schema_version": SCHEMA_VERSION,
        "header": header_info(path),
        "progression": {},
        "research": {"unclaimed_hard_drives": [], "ongoing": [], "unlocked_trees": []},
        "unlock_flags": {},
        "building_counts": {},
        "lightweight_counts": {},
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

    # --- connectivity interning -------------------------------------------
    actor_ix: dict[str, int] = {}
    role_ix: dict[str, int] = {}
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
            continue
        if cls == "FGPipeNetwork":
            fluid = ref_class(p.get("mFluidDescriptor"))
            if fluid:
                out["pipe_networks"].append({"instance": instance, "fluid": fluid})
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

        record = {
            "cls": cls,
            "instance": instance,
            "pos": pos_of(header),
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

        if any(h in cls for h in _MANUFACTURER_HINTS):
            record["recipe"] = ref_class(p.get("mCurrentRecipe"))
            out["machines"].append(record)
        elif any(h in cls for h in _EXTRACTOR_HINTS):
            record["node"] = ref_path(p.get("mExtractableResource"))
            out["extractors"].append(record)
        elif any(h in cls for h in _GENERATOR_HINTS):
            record["fuel"] = ref_class(p.get("mCurrentFuelClass"))
            out["generators"].append(record)

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
    out["n_objects"] = n_objects
    out.setdefault("progression", {}).setdefault("available_recipes", [])
    out["progression"].setdefault("purchased_schematics", [])
    return out


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
    """mGamePhaseCosts: remaining delivery amounts per phase (not totals).

    Only 4 phases are stored while the game has 5, so Phase 5 can never be reported.
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
                n = len(nxt) if isinstance(nxt, list) else 1
                if cls:
                    out[cls] = out.get(cls, 0) + n
            else:
                walk(child)

    walk(getattr(obj, "actorSpecificInfo", None))
    return out


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
    except sav_parse.ParseError as exc:
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
