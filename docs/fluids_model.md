# Fluids: the model, the measurements, and what is still open

The one place this project states how Satisfactory's fluids behave, what it measured rather
than read, and where it is still guessing. It supersedes `docs/fluid-head.md`, which was
written before the in-game experiments and carried two claims the measurements refuted.
`docs/plumbing.md` §24 remains the register of constants against the dump; where the two
disagree, this file is later and wins.

**Every number below is marked.** `[GAME]` is stated by the game's own data. `[MEASURED]` was
measured in the owner's world by an experiment recorded below. `[ASSUMED]` rests on the
community manual or on reasoning, and is what to attack first when something reads wrong.

---

## The model

**Head lift is an altitude, not a budget.** A source does not hand out a quantity of lift that
gets spent climbing. It establishes a height that fluid can reach, and that height propagates
along the network unchanged.

The propagated quantity is therefore a reachable altitude, and the rule at each element is:

```
source machine at connector z   ->  z + machine_lift
through a pump at its centre    ->  max(incoming, pump_centre + pump_lift)
along a pipe                    ->  unchanged
consumer at z                   ->  reachable iff incoming >= z
```

**A pump is a maximum, never a sum, and this is measured rather than argued** `[MEASURED]`.
In `HL_PUMP` a Mk1 pump stands above a Water Extractor on one column. The two rules predict
very different waterlines and the measurement is not close:

| rule | predicts | measured | error |
|---|---|---|---|
| `max()`, pump ceiling | +7.05 m | +7.91 m | 0.85 m |
| `sum()`, extractor + pump | +16.10 m | +7.91 m | **8.19 m** |

The same extractor with the same lower column stopped at −3.90 m without the pump and +7.91 m
with it — a rise of exactly `pump_z + lift − old_ceiling`, with no trace of the extractor's
own lift carrying through. Three pumps in a row therefore give one pump's lift, which is what
a maximum does and needs no special case. The only way two lifts compound is a pump standing
high, and its own centre already carries that.

**Gravity is free.** Fluid falls without help, so a line that dips and returns to the same
height needs no pump. This falls out of the model rather than being a rule: the altitude is
unchanged along a pipe, so a descent costs nothing to recover.

---

## Constants the game states

All verified directly against `Docs/en-US.json` at buildVersion 495413, exhaustively over all
2,868 classes rather than by sampling.

| what | value | field | note |
|---|---|---|---|
| Pipeline Pump Mk1 | 20 m rated, 22 m ceiling | `mDesignPressure`, `mMaxPressure` | already read as `Building.head_lift_m` |
| Pipeline Pump Mk2 | 50 m rated, 55 m ceiling | same | |
| Valve | 0 m, 0 m | same | **same native class as a pump** — a trap for any class-name match |
| Machine head lift | **10 m** | `mDescription` prose | stated by `Build_WaterPump_C`, `Build_OilPump_C`, `Build_OilRefinery_C`, `Build_Packager_C`, `Build_Blender_C`, `Build_FrackingExtractor_C` |
| Pipe Mk1 / Mk2 flow | 300 / 600 m³/min | `mFlowLimit` 5.0 / 10.0 per **second** | the two Clean Pipeline variants carry the same |
| Fluid Buffer / Industrial | 400 / 2400 m³ | `mStorageCapacity` | |
| Buffer height | 8 m / 12 m | clearance box, confirmed by `mStackingHeight` 800 / 1200 cm | |
| Junction internal volume | 5.0 m³ | `mFluidBoxVolume` | flat per class, **not** per connection |

`mDesignPressure` and `mMaxPressure` exist on the three pump classes and nowhere else; a sweep
of all 989 top-level keys and 117 nested struct field names finds no other pressure field.

**The 12 m machine ceiling is not in the data.** The string appears nowhere in any class. See
below — it is also wrong.

---

## Constants we measured

### A machine's ceiling is ≈ 11 m, not 12 `[MEASURED]`

A dead-end column carries zero flow, so it fills to the **ceiling** rather than to the rating.
`HL_MACHINE`: Water Extractor, capped vertical pipe, no pump, isolated network of exactly
7 members, 10.9 m of dry pipe above the surface.

**11.020 m above the extractor's pipe connection, ±0.26 m.**

Three independent strands agree the ceiling is 11 and not 12: the measurement; the absence of
"12 m" anywhere in the dump; and the pumps' tolerance being exactly 10% in two places
(22/20 and 55/50), which applied to a stated 10 m gives 11.0 — matching the measurement to
0.2%.

The manual's 10 m rating is **correct and confirmed by the game itself**. Only its 12 m
ceiling is wrong. An earlier version of this project's notes claimed the opposite; that claim
was refuted by audit and should not be repeated.

### A pump exceeds even its ceiling `[MEASURED]`

**22.801 m above the pump's centre**, against a 20 m rating and a 22 m ceiling. Read three
times as the rig settled — 22.853 at 09:03, then 22.801 at both 09:31 and 09:35, the last two
agreeing to 13 µm. The first reading was 52 mm high for the same reason the machine reading
was: taken before the column stopped moving.

So a Mk1 pump reaches **0.80 m past its own `mMaxPressure`**. Nothing lands near the 20 m
rating under any reference.

### Connector heights `[MEASURED]`

Head lift is measured from a connector, not an actor origin, and the offsets are fixed
geometry per class. Resolved by following each connection component to the spline endpoint of
the pipe it names — not inferred, which matters: a fallback to the actor position would give
`dxy = 0` for every class, and it does not.

| class | dz | dxy | n |
|---|---|---|---|
| Pipeline Pump Mk1 / Mk2, Valve | **0.000** | 0.000 | 58 |
| Junction T / Cross | 0.000 | 1.000 | 389 |
| Water Extractor | **+2.400** | 4.600 | 23 |
| Oil Extractor, Refinery | +1.750 | 7.600 / 9.220 | 83 |
| Packager | +3.750 | 3.5 / 3.8 | 15 |
| Fluid / Industrial Buffer | +1.750 | 2.0 / 6.0 | 10 |

A pump's zero is real, not missing data: a pump is built onto an existing pipe run, so its
origin sits on the centreline and the pipe passes through it. Its body is 1.2 m tall centred
on that origin, so the visible outlet stands ≈0.6 m above the *base* while the origin and the
connector coincide.

### Pipe capacity `[MEASURED]`

```
capacity(L) = max(7.0, 1.858252 x L)   cubic metres, L in metres
```

The 7.0 m³ floor bites below **3.767 m**; 23 Mk2 pipes of 3.0 m all read exactly 7.000000.
Identical on both tiers — 0 of 472 pipes ≥6 m exceed `K·L` by 0.1%. Converting a short pipe's
fill to a height with `K` alone is wrong by up to 26%.

---

## A full buffer is a step, not a blend `[MEASURED]`

The official wiki says head lift applied to a buffer "will not be transmitted until they are
nearly full" and gives no number. One rig at three fills (byte-identical actor sets — one
geometry, three states) gives one:

| save | buffer fill | own head rise | waterline rise | ratio |
|---|---|---|---|---|
| A → B | 18.8% → 89.6% | 0.0388 m/s | 0.0364 m/s | **0.94** — slaved to its own fill |
| B → C | 89.6% → 100.8% | 0.0135 m/s | 0.2130 m/s | **15.8** — decoupled |

Below the threshold the pipe above a buffer tracks only the buffer's own fill-proportional
head. Above it, incoming head passes through. A proportional blend is excluded arithmetically:
at 89.6% a blend puts the waterline 11 m higher than observed.

**Threshold: between 89.6% and 100.7% of capacity.** "In the last few percent" is as tight as
four points support.

A buffer's **own** head is `base + height × fill_fraction`, and that part is confirmed by the
A→B tracking.

### A full buffer transmits unchanged — it does not add its own column `[MEASURED]`

`HL_BUFFER_D` is the same rig with the stack extended to +23 m, so the ceiling is visible
instead of pressed against the cap: waterline **+7.824 m**, settled to 0.32 mm over 208 s,
with 12 m of dry pipe above the interface.

| candidate | predicts | residual |
|---|---|---|
| pump centre + its measured 22.801 | +7.730 | **0.094 m** |
| pump centre + 22 (`mMaxPressure`) | +6.929 | 0.895 m |
| buffer base + 8 (buffer alone) | −9.000 | 16.8 m |
| pump + 22, **then the buffer adds its 8** | +14.929 | **7.105 m** |

The two rigs — one with a buffer in the line, one without — put their water 22.801 m and
22.895 m above their own pump centres. **Interposing a full 400 m³ buffer changed the
reachable altitude by 94 mm**, which is smaller than the ±0.26 m bar and is therefore
correctly read as "no measurable difference" rather than as a number. Stacking is off by
7.1 m, twenty-seven times the bar, and is excluded.

This is the second independent confirmation of `max()` over `sum()`: once directly through a
pump, once through a full buffer.

### Output throttling is a separate mechanic `[ASSUMED]`

A buffer needs 1.5 m of head to output as fast as it takes in: **75 m³** in a small buffer,
**300 m³** in an industrial one. These reproduce the manual exactly from `capacity × 1.5 /
height`, but identifying clearance height with fluid-column height is a **model, not a
derivation** — a 6×6×8 m box is 288 m³ of space holding a stated 400 m³.

---

## What the save carries

- **Pipe fill** is readable: every `Build_Pipeline*` actor carries `mFluidBox` in cubic
  metres. The projection does **not** carry it yet; reading it needs a schema bump.
- **Buffer fill** is in the projection as `storage[].stored_m3`, in cubic metres, with the
  class distinguishing small from industrial.
- **Valve limits round-trip** `[MEASURED]`: `mUserFlowLimit` is written when set — 137 m³/min
  stored as `2.2833333`, i.e. **per second**, like pipes. It is omitted when equal to the
  class default of −1.0, which is why 18 earlier saves showed none: nobody had adjusted one.
  `mDefaultFlowLimit` is likewise omitted when it equals the connected pipe's limit.
- **Pump power** is knowable only through the wire: `mHasPower` and `mCircuitID` are on no
  object. A powered pump owns a `PowerInput` component naming one wire.
- **An unpowered pump sets exit-side head lift to zero** while still passing fluid `[ASSUMED,
  manual]`. It does not merely reduce it.
- **Gas has no head lift at all** `[ASSUMED, manual]`. Pumps do not work on it and buffers
  cannot compensate its flow. Gas networks are excluded entirely, never modelled with a zero.

---

## The sim is not hydrostatic at rest

Three connected boxes at equilibrium sit at −9.00, −5.26 and −3.88 m: the middle box's level
is below the top box's floor while the top box holds water. Every rig shows 8–20% deficits in
pieces that should be full.

Consequences: a fill-derived surface supports about **±0.26 m**, not millimetres; and "how
full is this pipe" is not a physical statement about a water column even though the number is
real. Nothing in the model depends on it — the model propagates an altitude and never reasons
about fill.

---

## What the model gets wrong today

1. **`MACHINE_MAX_HEAD_LIFT_M = 12.0` should be ≈11.0.** This is the one error that makes the
   tool say *safe* about something unsafe: a climb between 11 and 12 m is currently reported
   as marginal-but-reachable when the game will not deliver it.
2. **Buffer pass-through is ungated, and this is the only rule that needs changing.**
   `_RESERVOIR` sits in `_BODIES`, making a buffer one node, so incoming head passes through
   at any fill. The measurement says it is off below ~90% and exact at full. Any line fed
   through a part-full buffer is credited with head it does not have — the unsafe direction,
   and three of the reference world's five buffers sit under 10% full. `_add_tank`'s own-head
   term is confirmed and stays; the pump rule needs no change at all, since the 0.000 m
   connector offset is exact and `max()` is now doubly confirmed.
3. **The 10 m is labelled unreadable.** It is stated in `mDescription`, and `normalize.py`
   already parses that field for extraction rates and belt speeds. Parsing it would move a
   verdict from "rests on the manual" to "rests on the game".
4. **The fluid ladder has never fired on real data.** 60 dry fluid boxes exist across the
   saves and every one belongs to an `unmonitored` machine, which the classifier can never
   call starved. The honest claim is "no *monitored* machine has had a dry fluid box".

---

## One anomaly, and why it was not real

A reading of +10.81 m in the buffer rig's network briefly looked like a `sum()`: 25.7 m above
the pump connector, past every ceiling. It does not survive.

At the time of that save the network's **tallest pipe topped out at +7.00 m** — the +23 m
extension did not exist yet. Water cannot stand where there is no pipe. The 10.81 came from
two *oil* pipes on a different network, 19 m and 55 m long and **neither vertical**, whose
fill was interpolated as though it were a vertical column.

That is the trap worth remembering: **interpolating a long sloped pipe's fill as a height
manufactures a plausible number.** Only a vertical piece measures an altitude.

## Open

- **Does 11 m generalise?** Measured on a Water Extractor only. The dump states 10 m for six
  classes, so the rating is shared; the ceiling has been measured once.
- **The rating itself is untested.** A dead end measures only the ceiling. The game's
  description is the sole source for 10 m.
- **Buffer transmission threshold** is bracketed, not pinned: four points, A off at 18.8%,
  B off at 89.6%, C and D both on at 100.7%.
- **The Mk2 pump (50/55) is untested**, as are non-extractor machines.
- **No single tolerance multiplier fits.** The machine sits at ×1.102 of its rating, the Mk1
  pump at ×1.140 of its — and the pump passes its own stated ceiling. Ceilings are per-class
  measurements or they are nothing.

---

## How to measure this again

The save file is the instrument; no in-game reading is needed.

1. Build the rig in an isolated network — its own source, nothing shared with the base.
2. Take a **manual save** first as the baseline. Diffing a later save against it isolates the
   rig by actor name; autosaves rotate and cannot be relied on as baselines.
3. Cap the column and leave **dry pipe above the surface**, or the reading is a lower bound
   rather than a measurement.
4. Let it settle, and prove it settled: the network's **total** volume stops changing. A
   single save can be mid-jiggle even when the ceiling has been reached — one reading here
   moved 24 mm after the save it was taken from.
5. Find the waterline by interpolating **within the partial piece**: `bottom_z + fill_fraction
   × vertical_extent`. Piece counting is not precise enough. **Only a vertical piece measures
   an altitude** — interpolating a long sloped pipe this way invents a plausible height, which
   is how one phantom reading got as far as being called an anomaly.
6. Subtract the **connector** height, not the actor origin, using the table above.

---

## Sources and posture

The community FICSIT Plumbing Manual (public domain, on the official wiki) supplied the
*shape* of the model — what to measure and which mechanics exist. Every number is taken from
the game's own data or measured in-game; none is copied from the manual. Where the two
disagree, the measurement wins and the disagreement is recorded above.
