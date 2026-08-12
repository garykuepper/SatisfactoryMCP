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
into a POWERED pump's inlet     ->  reached iff fluid ARRIVES there, at any altitude
through a pump at its centre    ->  max(incoming, pump_centre + pump_lift)
a buffer below capacity         ->  max(base + height * fill, its own connector z)
along a pipe                    ->  unchanged
consumer at z                   ->  reachable iff incoming >= z
```

The second line is the one exception to the altitude test, and it is measured rather than
assumed — see *A powered pump draws* below. Everything else keeps the test: a consumer, a
plain pipe crest and a buffer all answer "is the incoming altitude at least mine?".

The fourth line is a FLOOR and not an exception. A buffer below capacity still refuses to
pass incoming head on, and what it offers instead is its own surface — but never less than
the height of its own outlet, because it delivers there at any fill. See *A buffer delivers
at its connectors* below.

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

The overshoot was once suspected to be an artefact of our own estimator: reading the same
columns by volume conservation rather than by the topmost partial piece gave machine 10.758,
Mk1 21.877, Mk2 55.160 — under which both pumps sat essentially *on* their declared ceilings.
**`HL_FINE` refuted that** `[MEASURED]`.

Rebuilding the Mk1 column from **2 m pieces** (half the previous 4 m) gives:

| estimator | reach above pump centre | vs declared 22.0 |
|---|---|---|
| topmost-partial-piece | +23.006 | **+1.006** |
| volume conservation | +22.430 | **+0.430** |

**Both now exceed 22.0**, and they are converging from below toward ≈23 m rather than toward
the declared ceiling. The stronger statement needs no estimator at all: the interface piece
spans +7.500 → +9.500 m, so the reach is bracketed in **[+22.244, +24.244]** by piece
occupancy alone — no capacity model, no interpolation — **and that bracket's floor is 0.244 m
above the declared ceiling.** The 4 m rig's bracket was [+21.946, +25.946], whose floor sits
below 22.0, so this is the first rig on which the overshoot is proven rather than inferred.

`PUMP_MEASURED_REACH_M["Build_PipelinePump_C"]` currently holds **22.801**, which is now known
to be a 4 m-quantised reading and about 0.205 m low; the honest interval is the bracket above
with a best estimate near **23.0**. It lives there rather than overwriting the dump, because
`mMaxPressure` is authoritative for what the game *declares* and the finding is precisely that
declared and observed disagree.

### Trapped air is attached to the piece, not to the column `[MEASURED]`

The deficit below the waterline, ranked from the interface downward, on the two rigs:

| rank | 4 m pieces | 2 m pieces |
|---|---|---|
| 1 | 9.67% of the piece | 9.28% |
| 2 | 7.47% | 7.69% |
| 3 | 5.96% | 7.31% |
| 4 | — | 4.51% |
| total void | 1.717 m³ | 2.015 m³ |

**Proportional-to-length is decisively excluded** (predicts 0.286 m³ mean against 0.504
observed, off by 38%). Fixed-volume-per-piece and fixed-fraction-of-capacity cannot yet be
separated: they differ by 6% here, under the 12% scatter, because the 7.0 m³ capacity floor
makes a 2 m piece hold nearly as much as a 4 m one.

**So the estimator gap is bookkeeping, at moderate confidence.** Halving the piece length cut
it 0.924 → 0.576 m (−38%); the bookkeeping prediction is −50%, the physics prediction is 0%.
Physics sits 7× the intra-rig noise away and is excluded; bookkeeping sits ~2× away.

Stated neutrally, the same data two ways: **the trapped-air volume is roughly conserved
(1.72 → 2.02 m³) while the trapped-air height nearly halves (0.92 → 0.58 m).** Which is the
invariant is the open question, and it is the one the 7.0 m³ floor prevents this rig from
answering.

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
| Fluid / Industrial Buffer | **+1.75000** | 2.0 / 6.0 | 10 |

The buffer's offset is **exact**, re-derived in `BUF_OUT` from the raw save rather than the
projection: every one of the eight couplings on the five `Build_PipeStorageTank_C` in the
owner's world puts the pipe endpoint at `base + 1.75000` to five decimals. Earlier notes in
this file said 1.749, which was the projection rounding a pipe point to whole centimetres
(1710 cm) while leaving the actor's own z unrounded. Nothing rested on the difference, but
the threshold does: a buffer's surface reaches its own outlet at exactly **21.875%** of a
400 m³ Fluid Buffer, and at 14.583% of a 2400 m³ Industrial one.

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

**Threshold: between 99.903% and 100.604% of capacity** `[MEASURED]` — a 2.805 m³ window on a
400 m³ buffer, from two adjacent saves 30 s apart in a throttled fill. That is 7× tighter than
the previous bracket and 30× tighter than the one the code shipped with, and **it contains
1.000**, so `BUFFER_TRANSMITS_ABOVE_FILL = 1.0` stops being a conservative choice and becomes a
measurement.

The gate must be spelled `stored_m3 / capacity >= 1.0`, never an equality or a band: the game
overfills, and every transmitting reading in the world sits *above* 1.0 (highest 1.00999),
while the highest non-transmitting reading ever measured is 0.99903. Twelve points, no
misclassification.

One save landed *between* the off and on clusters, at +4.063 m — the transition caught in
flight, and three things prove it is not a blend. A piece 4 m below the claimed waterline
**emptied** (92.2% settled across five points, 75.3% here) while the column above gained
3.5 m³, which a standing column cannot do. The blend weight is flat at 7.0–7.5% across a
3.3-point fill sweep and then jumps to 99.4%. And the stack was still filling, 9.0 → 11.3 →
35.8 m³.

The load-bearing control: the valve was opened to full **after** the switch had already fired.
`mUserFlowLimit` reads 10 m³/min in all six pre-switch saves and 600 only in the last one, and
the interval containing the switch ran at the same throttle as every off point. **A fill
effect, not a flow effect.**

Earlier, coarser reading, kept because it is the same experiment at lower resolution: Six points, and the excess
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

### A buffer delivers at its connectors, however little it holds `[MEASURED]`

`BUF_OUT` settles the question the buffer gate had left open since the pump rule shipped: a
buffer whose surface is below its own outlet **still delivers out of it**. Its own head is
therefore `max(base + height × fill, connector_z)`, and the fill-proportional term is a floor
that the connector height overrides rather than a ceiling that suppresses it.

**The rig.** Two 400 m³ Fluid Buffers on one flat pad, joined by a single Mk2 pipe and by
nothing else. Walking the couplings in the save: the pipe's `PipelineConnection0` names
`Build_PipeStorageTank_C_2147261040.ConnectionAny1` and its `PipelineConnection1` names
`Build_PipeStorageTank_C_2147260764.ConnectionAny0`; the other connector on each buffer has
no `mConnectedComponent` at all. **The pair is a closed system**, and the arithmetic below
proves it independently.

| | buffer #1 `…261040` | buffer #2 `…260764` | the pipe between |
|---|---|---|---|
| world position | (−62.000, −2833.000) | (−62.000, −2841.000) | — |
| base z | **−16.99991** | **−16.99990** | — |
| connector z | **−15.24991** | **−15.24990** | both ends |
| connector offset | +1.75000 | +1.75000 | — |
| length | — | — | **4.000000 m** |
| Δz end to end | — | — | **0.00001 m** |
| capacity | 400 m³ | 400 m³ | 7.433008 m³ |

The pipe is horizontal to **10 µm** over 4 m, which is the one thing that had to be true: a
slope would have let gravity explain the transfer, and interpolating a sloped pipe as a height
is the trap that produced this project's one phantom reading.

**The measurement.** Four saves, ordered by the header's in-game play duration
(`play_duration_s`) rather than by filename, spanning **360 s**, all volumes in m³. The owner
named two of them; the two autosaves that fell inside the window are the other two, and they
are the ones that carry the result:

| save | t (s) | Δt | fill pipe | #1 | #1 fill | #2 | #2 fill | pipe | **#1+#2+pipe** |
|---|---|---|---|---|---|---|---|---|---|
| `BUF_OUT_1` | 1198746 | — | **present** | 20.291323 | 5.0728% | 16.774395 | 4.1936% | 1.574422 | 38.640139 |
| `BUF_OUT_2` | 1198807 | +61 | gone | 17.386824 | 4.3467% | 23.722454 | 5.9306% | 1.814996 | **42.924274** |
| `autosave_0` | 1198808 | +1 | gone | 15.075508 | 3.7689% | 25.957424 | 6.4894% | 1.891332 | **42.924265** |
| `autosave_1` | 1199106 | +298 | gone | 16.895760 | 4.2239% | 24.741213 | 6.1853% | 1.287293 | **42.924265** |

**The fill pipe is gone in the last three, three ways over.** The actor
`Build_PipelineMK2_NoIndicator_C_2147246576` — 12.28 m, ending on buffer #1's connector at
(−62.000, −2831.000, −15.24991) — is in `BUF_OUT_1` and in none of the others. What remains of
the extractor's line is a stub whose `Build_Valve_C.Connection1` has no `mConnectedComponent`,
so it reaches nothing; the stub is also brim-full and static, 7.000000 of 7.000000 in the pipe
and in the valve in all three saves, which is what a dead end looks like.

**And the last three conserve the pair's total to 9 × 10⁻⁶ m³ across 299 s**, which is float32
noise on a 42.9 m³ sum. That is the independent proof that nothing enters or leaves: no source
could add 0.00001 m³ and stop.

**And inside that closed, conserved system the fluid moves, in both directions.** Between
`BUF_OUT_2` and `autosave_0` buffer #1 lost **2.311 m³** while buffer #2 gained **2.235** and
the pipe took the remaining **0.076**. Over the 298 s that followed the flow reversed: buffer
#2 gave **1.216 m³** back, buffer #1 took **1.820** and the pipe gave up **0.604**. Throughout,
both surfaces stood **1.23–1.45 m below their own outlets**:

| save | #1 surface | #1 short of its outlet | #2 surface | #2 short |
|---|---|---|---|---|
| `BUF_OUT_2` | −16.65217 | 1.402 m | −16.52545 | 1.276 m |
| `autosave_0` | −16.69840 | 1.449 m | −16.48075 | 1.231 m |
| `autosave_1` | −16.66199 | 1.412 m | −16.50507 | 1.256 m |

The old rule capped each buffer's node at its surface, 1.4 m under a pipe whose crest is its
own connector, and concluded that **nothing crosses**. 2.311 m³ crossed in about a second, and
1.216 m³ crossed back. The rule is refuted at exactly the point it was suspected.

**What this rig does not say.** It measures a floor and no more. The delivery pipe is *at*
connector height, so the reading establishes `head ≥ connector_z` and cannot distinguish that
from any larger value. `max(base + height × fill, connector_z)` is therefore the weakest rule
the data supports, which is the right one to ship — and it is consistent with `HL_BUFFER_A`,
where 18.8% fill puts the surface at `base + 1.504` against a connector at `base + 1.750`,
a 0.246 m difference that sits inside the ±0.26 m bar a fill-derived surface supports.

**One honest defect in the rig, and why it does not touch the verdict.** The fill pipe from
the Water Extractor was dismantled **between** `BUF_OUT_1` and `BUF_OUT_2`, not before them, so
that first 61 s interval is confounded: the pair's total rises 38.640 → 42.924 across it, and
part of buffer #2's gain came from the extractor rather than from buffer #1. The two autosaves
rescue the experiment entirely, and they are the stronger evidence anyway, because a closed
system that conserves to five decimal places needs no argument about what else might be
feeding it. `BUF_OUT_1` is reported here for completeness and carries none of the verdict.

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
  **Verification DEFERRED by Lukas, 2026-08-09** — he has not reached gases in his world, so
  there is nothing to measure and no answer to get wrong. The exclusion is the safe default
  either way: excluding a network cannot invent a fault, whereas modelling gas as a zero-lift
  fluid would. Revisit when nitrogen appears in a save.

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
   than a measurement settled. The pump rule needed no change: the 0.000 m connector offset
   is exact and `max()` is doubly confirmed.
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
6. ~~**A pump waits to be reached.**~~ **FIXED, and it was the model's last unsafe direction**
   `[MEASURED]`. A powered pump's inlet is settled by rung (1) rather than by an altitude, so
   the model no longer declares twenty producing generators cut off because the surface behind
   their pump sits below its inlet. Consumers, plain pipe crests and buffers keep the altitude
   test unchanged — `HL_MACHINE`, `HL_PUMP` and `HL_FINE` all measure dead-end columns that
   stop at a definite height, and the buffer step is measured too. The scope is exactly one
   node type, and *A powered pump draws* argues why it is spelled ungated.
7. ~~**A buffer's own head is its surface, even below its own outlet.**~~ **FIXED**
   `[MEASURED]`. It is `max(base + height × fill, connector_z)`, because a buffer delivers at
   its connectors at any fill. This was the model's last disagreement with the owner's working
   base: it freed 1,000 machine-readings across 50 saves, moved no consumer into silence and
   invented no fault. See *A buffer delivers at its connectors* and the calibration below.

---

## A powered pump draws `[MEASURED]`

**A powered pump does not wait to be reached. It draws.** Its inlet is settled by rung (1) —
does fluid arrive there — and not by the altitude test. Its outlet then gets
`max(incoming, pump_centre + reach)` exactly as before.

The rig for this is the owner's own fuel line, network 99, `Desc_LiquidFuel_C`, whose
102-member membership is byte-identical across 48 saves and 88 hours of play. The model used
to declare its twenty Fuel Generators cut off; they read uptime 1.000000 in all **960**
readings (48 saves × 20 generators), with **zero variance** against a buffer fill spanning
14.6%–94.8%. A verdict that never moves while its input sweeps eight tenths of its range is
not measuring the thing it names.

Three strands make it conclusive, and the third is the one that closes the argument.

* **The suction pipe fills above the surface it draws from.** In **31 of the 48 saves the
  entire climbing pipe stands above the buffer's fluid surface**, and it is 18–65% full
  anyway. Worst case: pipe bottom **3.58 m above** the surface, pipe 57.1% full, all twenty
  generators at 100%. The excess over the surface reaches **+5.86 m**. A column standing where
  a hydrostatic model says there is nothing to hold it up is being pulled, not pushed.
* **Nothing is coasting on stock.** Each save's closed 300 s window means 2,000 m³ was burned
  in the prior five minutes, while the **entire line** — both buffers, twenty internal
  generator boxes and all 866 m³ of pipe — holds only **1,548–1,627 m³**. Every single save
  refutes "it was running on what was already in the pipe", without assuming continuity
  between saves.
* **There is no other source, ever.** Seven Packager output ports at −12.899 m in all 48
  saves, and nothing else. The upper buffer is no exception: it holds fuel, but it takes it
  from the riser and passes it on, so everything in it came through the pump.

**The competing explanation was killed rather than ignored.** Those seven Packagers run
Unpackage Fuel and each states `machine_head_lift_m` **10 m**, so from −12.899 m they reach
−1.88 m and would clear the pump's inlet at −8.141 m unaided. They do not, because the buffer
stands between them and it destroys that head — which is exactly what `HL_BUFFER_A` shows
directly: at 18.8% fill the extractor's whole output went into the tank and none of it up the
column. **That is what makes the buffer gate load-bearing here rather than incidental**: with
the gate off, the Packagers alone explain the line and the pump proves nothing.

### The spelling chosen, and the one that was not

Two rules both fit every reading:

* **(a) inlets are ungated** — an inlet is fed if and only if fluid arrives at it.
* **(b) suction is bounded by the pump's own reach** — fed iff `incoming >= pump_centre −
  reach`.

The data cannot separate them. The Mk2 here draws from at least **7.34 m** below its inlet
against a declared 55 m of reach, and 7.34 ≪ 55.56 sits deep inside both. **No save the owner
has can separate them either**: network 99 is the only network in his world where a fluid
surface stands below a pump inlet at all.

**(a) is what ships.** The tie-breaker is which error the project can live with. (a) can never
invent a fault — it only ever declares more of the line reachable — but it could miss a real
one, on a pump lifting from an absurd depth that nobody has yet built. (b) could invent a
fault, in a case nobody has ever observed, on a bound nobody has measured: `mMaxPressure` was
measured as a **discharge** height above a pump's centre, and reusing it as a suction depth
below the same centre is a second claim with no evidence behind it. This project has taken the
same side twice already — gas networks are excluded rather than modelled with a zero, and a
part-full buffer's crest is reported rather than called broken — and the reason is the same
each time: **a model that cries wolf on a working factory is wrong.** (b) stays named below as
the open alternative, and the experiment that would settle it is written down with it.

---

## The buffer barrier is real, and "cut off" is not what it means `[MEASURED]`

Switching the gate on turned a world-wide silence into crests naming the owner's fuel line in
every save that holds it. Nearly all of the model's output on this machine was that one line,
and it is worth stating in full because it is where the rigs and the owner's base disagreed for
longest — two gates, each one measured away by a rig built for it.

**The line is gated twice, and neither gate binds any more.**

**The lower gate, cleared by the pump rule.** A 400 m³ Fluid Buffer sits **in series** —
confirmed by walking its couplings, not inferred — between seven Packagers and the Mk2 pump
that lifts fuel to twenty Fuel Generators. Its two connectors are both at −14.90 m, 1.75 m
above its base at −16.649 m, so its surface tops out at −8.649 m when completely full while
the pump's inlet stands at −8.141 m: **at true capacity the gap is 0.508 m, and across the 48
saves it runs 0.93 m to 7.34 m.** The often-quoted **0.93 m is the minimum over the whole
history**, at the highest fill ever recorded (94.8%) — not a typical figure and not the
barrier's size. The pump draws over all of it, and this gate no longer produces a crest.

Reading the save's own `mFluidBox` on that run is what showed the head really does stop at the
buffer, which is why the pump rule and not the buffer rule had to give:

| piece | z range | capacity | held | full |
|---|---|---|---|---|
| buffer → valve | −14.90 → −12.77 | 23.84 | 19.39 | 81% |
| valve → rise | −12.77 → −11.90 | 23.06 | 22.50 | 98% |
| **rise → pump inlet** | **−11.90 → −8.14** | **9.61** | **6.03** | **63%** |

**The upper gate, cleared by the buffer rule.** A **second** 400 m³ Fluid Buffer sits in series
again, at base +15.351 m, between the riser and the generators' flat manifold at +17.100 m.
Its connectors are 1.75000 m up its own side, so its surface does not reach **its own outlet**
until it is **21.875% full** — and across the 48 saves it runs **1.60%–18.75%**, never once
above that line. The model used to say nothing leaves it, with a shortfall of **0.249 m at the
fullest and 1.621 m at the emptiest**, while the twenty generators ran at 100% through all of
it. `BUF_OUT` settles it: the buffer delivers at its connectors at 4.3% fill, so the head there
is +17.10135 and not +16.19.

**And the manifold is flat at exactly that height**, which is worth stating plainly rather than
quoting as a margin. The generators' run and the buffer's outlet are the same pipe altitude;
the projection rounds both to 1710 cm, so the model's test is `17.10 >= 17.10` and passes as an
equality. There is no climb at all on this line above the buffer — not a small one the model
now squeaks past. A manifold a centimetre higher would still be called cut off, and this rig
cannot say whether that verdict would be right.

So both barriers reproduce on the owner's base and neither consequence does. **`Crest.buffer_gated`
survives and means the same thing**: the head behind this crest is a part-full buffer's own
delivery height rather than a source's, which is a line running on what a buffer alone can give
and never a fault. What changed is how much of that height there is.

**The cross-check is clean in both directions.** Of the 1,005 machine-readings named across the
93 saves before the change, 1,000 were those generators and all of them were producing; after
it, none of them is named. The five remaining are the same Packager in five saves — and it is
not starved either: its input holds a **full** 50 m³ fuel box plus 100 canisters while its
output sits at 100 Packaged Fuel. It is output-blocked. **Not one machine the model names is
short of a fluid**, before or after.

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

- ~~**What carries fuel over the 0.93 m.**~~ **ANSWERED for the pump: the pump does.** It
  draws, and the gap it draws over runs 0.93–7.34 m rather than 0.93. See *A powered pump
  draws*.
- ~~**What carries fuel out of a buffer that cannot clear its own connectors.**~~ **ANSWERED:
  the buffer does.** It delivers at its connectors at any fill, so its head is
  `max(base + height × fill, connector_z)`. `BUF_OUT` measured 2.311 m³ leaving a buffer 4.3%
  full whose surface stood 1.40 m below its own outlet, inside a closed pair conserved to
  0.00001 m³ over 299 s. See *A buffer delivers at its connectors*.
- **Is a buffer's delivery height bounded above its connectors, and by what?** The floor the
  rig measured is `connector_z`; the pipe it delivered into was flat AT that height, so nothing
  in `BUF_OUT` separates "exactly its connectors" from "its connectors plus some lift". The
  owner's fuel line cannot separate them either — its manifold is flat at the same altitude, so
  the model's test there is an equality and a climb of any size above it is untested. **The
  rig:** the same two-buffer pad, with the receiving buffer replaced by a capped vertical
  column off the flat pipe and the source buffer held near 5%. If the column stands at the
  connector height the rule is exactly right; if it stands 10 or 11 m higher, a part-full
  buffer is a source with a machine's own lift and the gate is far weaker than it looks.
- **Is a pump's suction bounded, and by what?** The alternative spelling the fuel line could
  not separate: **(b)** an inlet is fed iff `incoming >= pump_centre − reach`, against the
  shipped **(a)** ungated. Nothing in the owner's world can decide it, so it needs a built rig.
  **The settling experiment:** drain the 400 m³ buffer to ≈19% (surface −15.5 m) and take two
  saves with the Mk1 pump — measured reach 22.8–23.0 m — moved up the existing `HL_BUFFER`
  column, once at **≈+6 m** (21.5 m above the surface, inside its reach) and once at
  **≈+10 m** (25.5 m above, outside it), each with dry capped pipe above the pump. **Both
  columns filling means inlets are ungated and (a) is right; only the low one filling means
  suction is bounded and (b) is.** One rig, two saves, and it is the only question on this page
  whose answer could turn a silence into a fault.
- **The ten refineries on a network no source reaches still read `unmonitored`.** They keep no
  window and a pipe *does* arrive at each, so the never-run gate above declines them by
  construction — it asks the conduit graph and not the head-lift model, which is the only thing
  that can see an unfed network. Promoting on `HeadLift.unfed_ports` would be the second form
  of rung (1) and would light exactly those ten. Whether ten at once is a finding or a wall is
  the same question this rule was measured against, and it has not been measured for them.
- ~~**Does 11 m generalise?**~~ **CLOSED** — two classes, two fluids, two datums, 67 mm apart.
- **The rating itself is untested.** A dead end measures only the ceiling. The game's
  description is the sole source for 10 m.
- **Is the trapped air a fixed VOLUME per piece or a fixed FRACTION of its capacity?** The
  one question `HL_FINE` could not answer, because the 7.0 m³ capacity floor makes a 2 m
  piece hold nearly as much as a 4 m one. **The rig that settles it uses LONGER pieces, not
  shorter** — the opposite of the standing assumption. An 8 m column (capacity 14.87 m³, safely
  above the knee) predicts the estimator gap **doubles to ≈1.85 m** if the deficit is a fixed
  fraction, and stays **unchanged at ≈0.92 m** if it is a fixed volume. Those are 18× the
  noise apart.
- **Pinning the pump reach without any model.** Rebuild the 2 m column shifted vertically by
  0.5 m and save; repeat. Each shift moves the piece boundaries through the waterline, and
  intersecting the assumption-free brackets narrows the answer without invoking a capacity
  model at all. Four shifts pin the Mk1 to ±0.25 m on evidence nobody can dispute.
- **The pump overshoots its own declared ceiling and the machines do not.** Both machines land
  on ×1.10 of their stated rating, which is exactly what the pumps *declare* (22/20, 55/50) —
  yet the Mk1 pump itself measured 22.801, 3.6% past its own `mMaxPressure`. A 0.8 m question
  about pumps, not about class generality, and it touches neither rule the model rests on.
- **Buffer transmission threshold** is bracketed, not pinned: four points, A off at 18.8%,
  B off at 89.6%, C and D both on at 100.7%. The gate sits at capacity, inside the bracket
  and above the fill measured off; 22 buffer readings across the saves fall in the band and
  are counted rather than silently decided.
- **The Mk2 pump is measured at 55.564 m but is deliberately absent from
  `PUMP_MEASURED_REACH_M`**, so the model uses its declared 55. Non-extractor machines are
  still untested.
- **No single tolerance multiplier fits.** The machine sits at ×1.102 of its rating, the Mk1
  pump at ×1.140 of its — and the pump passes its own stated ceiling. Ceilings are per-class
  measurements or they are nothing.

## The calibration of the buffer rule, before and after

Every `.sav` on the machine, **93 of 93 parsed**, over one set of projections cut once and read
by both passes — so the columns are the same worlds actor for actor, and no save could rotate
between them. The "before" pass was re-run from a pristine checkout of `master` and agrees with
the in-flight one on every field of every save.

| | before | after |
|---|---|---|
| fluid networks | 1,448 | 1,448 |
| consumer ports | 6,380 | 6,380 |
| unfed ports | 80 | 80 |
| ambiguous ports | 0 | 0 |
| marginal verdicts | 0 | 0 |
| **crests called faults** | **0** | **0** |
| consumers the model calls cut off | 1,005 | **5** |
| …named by a crest | 1,005 | 5 |
| …**named by nothing at all** | **0** | **0** |
| crests, all buffer-gated | 55 | **5** |
| distinct places a crest stands | 2 | **1** |
| buffers inside the undecided band | 1 | 1 |

**1,000 machine-readings move from cut off to fed, and nothing moves the other way.** All 1,000
are the same twenty Fuel Generators, in the 50 saves that hold them, every one of which reads
uptime 1.000000 — so the change agrees with the game 1,000 times and disagrees with it nowhere.
**Fifty saves change; the other forty-three are identical field for field.** No consumer becomes
silent, no crest becomes a fault, and the unfed, ambiguous and marginal counts do not move at
all, which is what says the change is confined to the rule it names.

The five crests left are the same output-blocked Packager in five saves, at
(238.33, −1968.41, −14.01), and it is still buffer-gated: the head behind it is a part-full
buffer's delivery height. That is the whole of the model's output on 6,380 consumer ports.

**`HeadLift.undecided_buffers` is unmoved at 1**, and correctly so: the change touches what a
buffer below the threshold offers, never where the threshold sits.

---

## The calibration of the pump rule, before and after

The earlier sweep, kept because it is the pump rule's evidence and not this one's. Every `.sav`
on the machine at the time, **91 of 92 parsed** (`ServerManager_V2.sav` is not a save), and
compared over the 91 that did not rotate between the two passes.

| | before | after |
|---|---|---|
| fluid networks | 1,379 | 1,379 |
| consumer ports | 6,168 | 6,168 |
| unfed ports | 80 | 80 |
| ambiguous ports | 0 | 0 |
| marginal verdicts | 0 | 0 |
| **crests called faults** | **0** | **0** |
| consumers the model calls cut off | 965 | 965 |
| …named by a crest | 804 | **965** |
| …**named by nothing at all** | **161** | **0** |
| crests, all buffer-gated | 44 | 53 |
| distinct places a crest stands | 4 | **2** |
| buffers inside the undecided band | 1 | 1 |

**The fault count is still zero and the cut-off count did not move**, and both halves of that
matter. The pump rule frees no machine in this world, because a second barrier stands behind
the first; what it does is move the diagnosis onto the barrier that actually binds. Before, the
crest wandered with the lower buffer's fill — 16 saves blamed the pump inlet at −8.14 m, 16 a
pipe crest at −12.77 m, 8 another at −11.90 m. After, **48 of the 53 crests are the same place
in every save**: the upper buffer's own connectors at +17.100 m, naming the same twenty
generators. One barrier, one location, one number.

**161 readings were named by nothing before.** They were cut off by a buffer whose surface
could not clear its own connectors, and `_walls` could not see that as an obstacle because the
incoming head cleared the geometry — so twenty generators in eight saves, and one Packager in
the committed fixture, were dropped in silence. Making a buffer enter the reachable set at its
own head even when that head is below its connectors changed no verdict and made the barrier
nameable. Without it, the pump rule alone would have taken the silent count from 161 to **961**.
The buffer rule has since raised that head to the connectors, which is why the table above
starts from 1,005 named and none silent rather than from these figures.

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
