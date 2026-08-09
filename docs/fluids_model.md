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
| Machine head lift | **10 m** | `mDescription` prose | stated by `Build_WaterPump_C`, `Build_OilPump_C`, `Build_OilRefinery_C`, `Build_Packager_C`, `Build_Blender_C`, `Build_FrackingExtractor_C`; read into `Building.machine_head_lift_m`. The unit separator is **U+202F** |
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

**It generalises across classes** `[MEASURED]`. `HL_REFINERY` repeats the experiment on an Oil
Refinery producing Heavy Oil Residue, 30 m higher in the world, in a different part of the
base, on a different fluid and from a different datum (+1.750 m from the actor, against the
extractor's +2.400 m):

| rig | device | rating | measured ceiling | ×rating |
|---|---|---|---|---|
| `HL_MACHINE` | Water Extractor | 10 | 11.020 | 1.102 |
| `HL_REFINERY` | Oil Refinery | 10 | **11.087** | 1.109 |

**67 mm apart**, well inside the ±0.26 m bar, and both land on the ×1.10 the pumps declare.
So the model's most load-bearing constant rests on two classes and two fluids rather than one
of each, and no per-class table is needed.

A refinery only pressurises its output while producing, and this one had stopped — but the
reading survives, because its output fluid box sits at 6.999468 of 7.0 and is **unchanged to
the last bit across 298 s**. A full box drains the instant its pipe can accept anything; a
pinned box proves the pipe is at its ceiling whatever the machine is doing.

### A pump exceeds even its ceiling `[MEASURED]`

**22.801 m above the pump's centre**, against a 20 m rating and a 22 m ceiling. Read three
times as the rig settled — 22.853 at 09:03, then 22.801 at both 09:31 and 09:35, the last two
agreeing to 13 µm. The first reading was 52 mm high for the same reason the machine reading
was: taken before the column stopped moving.

So a Mk1 pump reaches **0.80 m past its own `mMaxPressure`**. Nothing lands near the 20 m
rating under any reference.

**The Mk2 measures 55.564 m** `[MEASURED]`, settled across five saves with 14.4 m of dry pipe
above the surface, and it decides the shape of the overshoot:

| device | rating | declared max | measured | ÷ rating | over max |
|---|---|---|---|---|---|
| Water Extractor | 10 | — | 11.020 | 1.102 | — |
| Oil Refinery | 10 | — | 11.087 | 1.109 | — |
| Pump Mk1 | 20 | 22 | 22.801 | 1.140 | **+0.801** |
| Pump Mk2 | 50 | 55 | 55.564 | 1.111 | **+0.564** |

**A multiplicative overshoot is refuted.** ×1.036 would put the Mk2 at 57.0 and needs its
interface piece 76.4% full; it reads 40.5%, missing by 1.44 m — 5.5× the bar, on a strictly
vertical piece. What survives is **additive**: +0.801 and +0.564 differ by 0.237 m, inside
the bar, so "a pump reaches `mMaxPressure` + ≈0.68 m" fits both readings and "×1.036" fits
one. Machines overshoot their own ×1.10 by only +0.020 and +0.087, so this is pump-specific.

**And the two hypotheses about ceilings are already separated — by machines, not pumps.**
`mMaxPressure = 1.10 × mDesignPressure` holds for *both* pumps by construction, so no pump
rig anywhere in the game can distinguish "the declared max is the truth" from "rating × 1.10
is the truth". Machines can: they state a rating, carry no `mMaxPressure` at all, and measure
×1.10. **So ×1.10 of the rating is the game-wide rule, and a pump's `mMaxPressure` is the
game printing that product.**

> **Caveat, and it is larger than the ±0.26 m bar.** The overshoot is estimator-dependent.
> Reading the same columns by volume conservation instead of by the topmost partial piece
> gives machine 10.758, Mk1 21.877, Mk2 55.160 — under which *both pumps sit essentially on
> their declared ceilings*. That estimator cannot be adopted for pumps alone, since it moves
> the machine constant too. The bar is set by how many partially-filled pieces lie below the
> surface, and a 22 m column carries three. **A rig built from 1 m or 2 m pieces would shrink
> the interpolation step and decide whether the +0.68 m is physics or bookkeeping** — the
> cheapest open experiment left. This lives in `PUMP_MEASURED_REACH_M` rather than overwriting the
dump: `mMaxPressure` is authoritative for what the game *declares*, and the finding is
precisely that declared and observed disagree.

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

**Capacity is geometric, not per-fluid**: re-derived on Heavy Oil Residue in `HL_REFINERY`, a
4.0000 m piece reads exactly 7.43301 — the same constant water gives.

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

**Threshold: between 95.6% and 100.7% of capacity** `[MEASURED]`. Six points, and the excess
over what the buffer's own head explains falls into two disjoint clusters with nothing
between them — further evidence for a step rather than a blend:

| fill | excess over buffer-alone | verdict |
|---|---|---|
| 18.82% | +2.54 m | off |
| 89.64% | +2.19 m | off |
| 90.09% | +1.70 m | off |
| **95.61%** | **+1.12 m** | **off** |
| 100.74% | +15.36 m | on (pipe-capped) |
| 100.74% | +16.76 m | on (measured) |

At 95.61% the two hypotheses predict waterlines **17.08 m apart** — the pump's reach dwarfs
the buffer's 8 m column — so the reading separates them by 61× the error bar. It is also not
a lag artefact: in the 10 s before the save the buffer gained 22 m³ while the waterline went
*down* 14 cm. Everything banks in the tank; nothing climbs.

`BUFFER_TRANSMITS_ABOVE_FILL = 1.0` is conservative but now well supported: every off-point
is strictly below 1.0 and both on-points are at 100.744%. The true gate lies in
(95.61%, 100.74%].

To pin it further, save every few seconds through 96–101% rather than aiming at a fill — the
switch was about 8 seconds beyond the last reading.

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

1. ~~**`MACHINE_MAX_HEAD_LIFT_M = 12.0` should be ≈11.0.**~~ **FIXED.** It is 11.020, and the
   constant's own note carries the measurement, the citation and the scope limit. A climb
   between 11 and 12 m is a fault now instead of a warning, which is the one verdict that was
   pointing the unsafe way.
2. ~~**Buffer pass-through is ungated.**~~ **FIXED as a rule, and then refused as a verdict** —
   see the section below, which is the more important half. `BUFFER_TRANSMITS_ABOVE_FILL`
   gates it at capacity, `BUFFER_TRANSMIT_BRACKET` records the two fills the step was
   measured between, and `HeadLift.undecided_buffers` counts the buffers the constant rather
   than a measurement settled. `_add_tank`'s own-head term is confirmed and unchanged, and
   the pump rule needed no change: the 0.000 m connector offset is exact and `max()` is
   doubly confirmed.
3. ~~**The 10 m is labelled unreadable.**~~ **FIXED.** Parsed out of `mDescription` into
   `Building.machine_head_lift_m`, which is a separate field from the pump's `head_lift_m`
   because that one means "this is a pipeline pump" to the pump picker and the logistics
   filter. `Crest.assumed` now means "rests on the manual" and is true nowhere on real data.
   The separator in the dump is **U+202F**, a narrow no-break space.
4. ~~**The classifier gates the fluid ladder out.**~~ **FIXED, and the gate is now evidence
   rather than a branch order** `[MEASURED]`.

   **Root cause, unchanged and confirmed: a machine that has never produced carries no
   productivity window at all, permanently.** It is not a window yet to close — still absent
   19 minutes and four window-lengths after the machine was built. `uptime` was therefore
   `None`, `_classify` took the `unmonitored` branch **before it looked at the buffers**,
   `unmonitored` is an OK state, and the feed/rung block is gated on `state == "starved"`. The
   ladder was not silent on these machines; **it was never called**. Injecting a closed window
   into the same record and re-running the unchanged code already gave `starved`,
   `cause=('Crude Oil (connection)',)`, rung `connection` — exactly the hand-walk — which is
   what proved only the gate was wrong.

   **What now happens.** The branch stays, and reads the buffers before it answers. A machine
   with no window is promoted to `starved` only when a required ingredient is at zero **and no
   run of that ingredient's medium arrives at the machine at all**. That is rung (1), the
   manual's own first question, and it is the second fact the promotion needs: an empty buffer
   on a machine nothing has ever flowed through is weak evidence by itself. No new state was
   added — see the commit for why — and `uptime` reading `-` rather than `0%` in the same table
   row is what still separates "never started" from "stopped after running".

   **Two guards, both of which the sweep put there and neither of which was foreseen:**

   * **Per MEDIUM, not per world.** Save versions 25–36 on this machine resolve 88 pipe runs
     between coal generators and **not one conveyor run**, in worlds carrying 6,266 material
     couplings. "No belt reaches this" is a blind spot there, not a fact, and reading it as one
     turned **39 smelters and constructors in a single save** into findings. A world-wide "does
     anything resolve" test passes those saves, because the pipes do.
   * **`OPEN` is not `NOTHING`.** A run that arrives and whose far end the save joins to no
     actor is a feeder *unknown*, which is `health.py`'s own vocabulary and was already written
     down beside the constants. Counting it as absent lit **ten FICSMAS Constructors** on one
     save.

   **The sweep, every `.sav` on this machine.** 82 saves, 584 machines at the largest. **Four
   machines change state world-wide, and all four are the same Oil Refinery** — `Starved.sav`
   and the three autosaves that caught it. Four newly enter `needs_attention`, four carry a
   fluid rung, all four `connection`, and there is **no construction noise at all**: the median
   save changes nothing. That refinery is the deliberately-built case — input pipe connector
   with no `mConnectedComponent`, dry box, empty inventory, wired and idle at 0.1 MW,
   `mTimeSinceStartStopProducing` at the FLT_MAX sentinel. `Starved.sav` reads 22 starved
   before and 23 after.

   **Scope of the population this rule looks at, in that save:** 48 of 584 machine-like records
   carry no window; 19 of those have a recipe set (17 Constructors, 1 Smelter, this Refinery).
   Of the eighteen that stay quiet, eight are a fresh row of Iron Plate Constructors with belts
   already run and nothing yet down them, five sit on 497–499 of a 500 stack, three are paused,
   and the rest hold what they need. **Not one of them is a build the player has to go and
   finish** — which is the bar this was measured against.

   Two neighbours were checked and rejected as markers. `mTimeSinceStartStopProducing == FLT_MAX`
   is **not** "has never produced": 770 of that save's 1,023 carriers hold it and **353 of those
   also hold a closed window**, so it separates nothing. It is not projected either, and there
   is no reason to project it. A full **output** box on a never-run machine is left alone: it is
   the same evidence `blocked` reads with a window, but `blocked` is not actionable on its own —
   the tool's own note says so — and there is no structural second fact behind it.
5. **A pump's declared ceiling and its measured reach are now separate.** `mMaxPressure`
   stays what the game declares; `PUMP_MEASURED_REACH_M` carries what a class was measured to
   do, keyed by class because no multiplier fits both the machine's ×1.102 and the Mk1's
   ×1.140. The Mk2 is not in it.

---

## The buffer barrier is real, and "cut off" is not what it means `[MEASURED]`

Switching the gate on turned a world-wide silence into **33 crests over 31 saves naming 584
machines — of which 580 were producing at the moment of their own save.** Nearly all of it is
one line, and it is worth stating in full because it is the only place the rigs and the
owner's base disagree.

A 400 m³ Fluid Buffer sits **in series** — confirmed by walking its couplings, not inferred —
between seven Packagers and the Mk2 pump that lifts fuel to twenty Fuel Generators at
+17.10 m. Its two connectors are both at −14.90 m, 1.75 m above its base at −16.649 m. Its own
surface therefore tops out at −8.649 m even when completely full, and the pump's inlet stands
at −8.14 m. **The buffer can never reach that pump on its own head, at any fill.** Across 31
saves its fill ranges 14.6%–94.8% and the twenty generators read **100% uptime in every one**.

Reading the save's own `mFluidBox` on that run settles which half is wrong, and it is not the
gate:

| piece | z range | capacity | held | full |
|---|---|---|---|---|
| buffer → valve | −14.90 → −12.77 | 23.84 | 19.39 | 81% |
| valve → rise | −12.77 → −11.90 | 23.06 | 22.50 | 98% |
| **rise → pump inlet** | **−11.90 → −8.14** | **9.61** | **6.03** | **63%** |

The pipe climbing to the pump is **62.8% full and its waterline interpolates to −9.54 m**,
against a buffer surface of −9.07 m and a pump inlet of −8.14 m. The fluid stands at the
buffer's level, exactly where the gate predicts, **and the generators run anyway**.

So the barrier reproduces on the owner's base and the consequence does not. Whatever carries
fuel over that 0.93 m, a fill-derived altitude does not see it, and calling those generators
cut off would be false 31 times over. Hence `Crest.buffer_gated` and `HeadLift.faults`: a
crest whose head is a part-full buffer's surface is reported as *this line has no margin above
its buffer* and is never counted as a fault.

**The cross-check is clean in both directions.** Of the 524 machines named across the 71
non-rotating saves, 520 were producing. The four remaining are the same Packager in four
saves — and it is not starved either: its input holds a **full** 50 m³ fuel box plus 100
canisters while its output sits at 100 Packaged Fuel. It is output-blocked. **Not one of the
524 machines the model names is short of a fluid.**

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

- **What carries fuel over the 0.93 m.** The one open question the calibration created, and
  the most valuable thing to measure next. The barrier reproduces and the line works anyway;
  until that is explained, a buffer-gated crest stays a note rather than a fault.
- **The ten refineries on a network no source reaches still read `unmonitored`.** They keep no
  window and a pipe *does* arrive at each, so the never-run gate above declines them by
  construction — it asks the conduit graph and not the head-lift model, which is the only thing
  that can see an unfed network. Promoting on `HeadLift.unfed_ports` would be the second form
  of rung (1) and would light exactly those ten. Whether ten at once is a finding or a wall is
  the same question this rule was measured against, and it has not been measured for them.
- ~~**Does 11 m generalise?**~~ **CLOSED** — two classes, two fluids, two datums, 67 mm apart.
- **The rating itself is untested.** A dead end measures only the ceiling. The game's
  description is the sole source for 10 m.
- **The pump overshoots its own declared ceiling and the machines do not.** Both machines land
  on ×1.10 of their stated rating, which is exactly what the pumps *declare* (22/20, 55/50) —
  yet the Mk1 pump itself measured 22.801, 3.6% past its own `mMaxPressure`. A 0.8 m question
  about pumps, not about class generality, and it touches neither rule the model rests on.
- **Buffer transmission threshold** is bracketed, not pinned: four points, A off at 18.8%,
  B off at 89.6%, C and D both on at 100.7%. The gate sits at capacity, inside the bracket
  and above the fill measured off; 22 buffer readings across the saves fall in the band and
  are counted rather than silently decided.
- **The Mk2 pump (50/55) is untested**, as are non-extractor machines. It is deliberately
  absent from `PUMP_MEASURED_REACH_M` and the model uses its declared 55.
- **No single tolerance multiplier fits.** The machine sits at ×1.102 of its rating, the Mk1
  pump at ×1.140 of its — and the pump passes its own stated ceiling. Ceilings are per-class
  measurements or they are nothing.

## The calibration, before and after

Every `.sav` on the machine, 78 of 79 parsed (`ServerManager_V2.sav` is not a save). Compared
over the **71 that did not rotate mid-sweep** — the game was running and seven autosaves moved
between the two passes, which is the only difference in the totals.

| | before | after |
|---|---|---|
| fluid networks | 899 | 899 |
| consumer ports | 4,344 | 4,344 |
| unfed ports | 80 | 80 |
| ambiguous ports | 0 | 0 |
| marginal verdicts | 0 | 0 |
| **crests called faults** | **0** | **0** |
| lines running on a buffer's own head | – | 30, naming 524 machines |
| buffers inside the undecided band | – | 22 |

**The zero survived every tightening**, which is the result worth having: the ceiling dropped
to 11.020, the buffer gained a barrier and the Mk1 gained 0.80 m of measured reach, and the
model still calls nothing on this machine a head-lift fault.

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
