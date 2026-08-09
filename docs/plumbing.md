# §24 Plumbing: what the game states, what the save carries, what is not there

The model being implemented is the FICSIT Inc. Plumbing Manual's (public domain,
satisfactory.wiki.gg). This file records which of its numbers the game's own data confirms,
which the save measures directly, and the one that is not in either — so the head-lift work
can cite rather than assume.

Measured 2026-08-06 against Docs.json `buildVersion` 495413 and the reference world
(§App. A), save version 60.

## §24.1 Every constant, against the dump

Verified means *read out of `CommunityResources/Docs/en-US.json`*, and every verified value
below is already normalized into `GameData` — no new reader was needed for any of them.

| what | manual says | dump says | field |
|---|---|---|---|
| Pipeline Pump Mk.1 head lift | 20 m, failing at 22 | 20.0, 22.0 | `mDesignPressure`, `mMaxPressure` on `FGBuildablePipelinePump` → `Building.head_lift_m`, `max_head_lift_m` |
| Pipeline Pump Mk.2 head lift | 50 m, failing at 55 | 50.0, 55.0 | same |
| Valve head lift | none | 0.0, 0.0 | same; a Valve is the same native class and lifts nothing |
| Pipeline Mk.1 flow | 300 m³/min | 300.0 | `mFlowLimit` 5.0 (per second) × 60 → `Building.flow_m3_min` |
| Pipeline Mk.2 flow | 600 m³/min | 600.0 | `mFlowLimit` 10.0 × 60 |
| Fluid Buffer capacity | — | 400.0 | `mStorageCapacity` on `FGBuildablePipeReservoir` → `Building.storage_capacity_m3` |
| Industrial Fluid Buffer capacity | — | 2400.0 | same |
| Fluid Buffer head lift when full | 8 m | 8 m | clearance box height, `mClearanceData` → `Footprint.height_m` (6 × 6 × 8 m) |
| Industrial Buffer head lift when full | 12 m | 12 m | same (14 × 14 × 12 m) |
| Junction / pump / valve internal volume | "a few m³" | 5.0 per connection | `mFluidBoxVolume` |

Two corrections fall out of the table. **The pump tolerance is 10%, not the ~12% the manual
rounds to**: 22/20 and 55/50 are both exactly 1.10, on the game's own numbers, so the failure
point is `max_head_lift_m` and never a percentage applied to the rating. And **the buffer's
head lift when full is its building height** — the manual's 8 m and 12 m are reproduced
exactly by the clearance box, which is what lets `BUFFER_BALANCE_HEAD_M` become a level in
m³ instead of a second pair of hard-coded numbers.

## §24.2 The one number that is not in game data

**A normal machine's 10 m of head lift is absent from the dump, and so is its 12 m ceiling.**
Head lift outside a pump lives in the `FluidBox` struct, and Docs.json exports every
`mFluidBox` as the empty tuple `()`; `mDesignPressure` and `mMaxPressure` appear on
`FGBuildablePipelinePump` and on nothing else in 2,868 classes. So a Water Extractor, a
refinery output and a freight platform state no lift anywhere the project can read.

What the model should therefore do: pin 10 m and 12 m in
`core/gamedata/constants.py` beside the other unreadable values, cite the manual, and — since
one number now stands behind every non-pump source in the world — say so wherever a
head-lift verdict depends on it, rather than presenting it as measured.

Gas is the other absence, and it is a rule rather than a number: gas has no head lift at all,
pumps do not work on it and buffers cannot compensate its flow. A gas network must be
excluded from the model entirely, not modelled with a zero.

## §24.3 What the save measures

- **Pipe fill is readable, and this is the useful finding.** Every `Build_Pipeline*` actor
  carries `mFluidBox`, a float of cubic metres — not only the junctions, pumps and valves
  (`core/saveio/extract.py`). Capacity is proportional to length: across the reference
  world's 503 pipes, binned by spline length in 5 m steps, the fullest pipe in every bin from
  15 m to 55 m reads **1.858 m³/m** to four significant figures. So "how full is this pipe"
  is a measurement, and the manual's central rule — a pipe only flows at its rated rate when
  it is full — stops being an inference. The projection does not carry it yet.
- **Buffer fill is carried**, as `storage[].stored_m3` in cubic metres, with `cls`
  distinguishing the two sizes. It is NOT the litre-scaled figure an inventory uses.
- **A pump's power is answerable through its wire and nothing else.** `mHasPower` and
  `mCircuitID` are on no object in the file. A powered pump owns a `PowerInput` component
  listing one wire; an unpowered one owns only its `powerInfo`. On the reference world 15 of
  22 pumps have that wire and 7 do not, which `graph["power"]` reproduces exactly.
- **A valve's user-set flow limit is NOT PROVEN either way.** `mUserFlowLimit` is written
  zero times across 18 saves holding 28 valves and 1,017 pumps — but its class default is
  −1.0 (unlimited), and UE omits a property equal to its default, so this is equally
  consistent with "no valve in any of those saves was ever adjusted". `mDefaultFlowLimit`
  *is* written, 559 times, at 5.0 against a class default of 10.0, which proves the flow
  fields on this class do round-trip. The decisive test costs one minute: set a valve's limit
  in game, save, and look again.

## §24.4 What is shipped

`domain/world/plumbing.py`, surfaced in the `factory_health` sweep over every factory —
world-wide there and scoped to no factory, because a buffer and a pump belong to no machine
set. Both are §24.1 arithmetic and neither walks the pipe graph.

- **Throttled buffers.** `capacity × 1.5 m ÷ height` is 75 m³ in a Fluid Buffer and 300 m³ in
  an Industrial one; below it the buffer outputs slower than it takes in and says nothing.
  Three of the reference world's five buffers are under it — two Industrial tanks at 54 and
  74 m³ of Fuel, and a Fluid Buffer at 42 m³.
- **Dark pumps.** Seven of the reference world's 22 pipeline pumps have no wire, all Mk.2.
  An unpowered pump passes fluid while setting the head lift past it to zero, so nothing
  downstream looks broken. The count is guarded against the graph's own blind spot: its actor
  list is cut from edges, so `building_counts` settles how many pumps were not looked at.
