# Head lift: the model, and what a reading means

`domain/world/headlift.py` answers one question — **can the supply physically climb to this
consumer?** — over every fluid network in a save. This file is the model it implements: the
propagation rule, why it is a maximum and never a sum, the band it reports, what it refuses to
look at, and where its numbers stop being measurements.

The physics is the FICSIT Inc. Plumbing Manual's (public domain, satisfactory.wiki.gg). Which of
its constants the game's own dump confirms — and the one it does not — is
[§24](plumbing.md); this file does not repeat that table, it uses it.

## Head lift is an altitude, not a budget

The one idea the whole model rests on: **head lift sets the maximum HEIGHT fluid can reach, it
is transmitted through a full pipe, and it does not decline along one.** A source standing at
`z` with `lift` metres of head pushes fluid to the absolute altitude `z + lift` — not for the
first pipe and less thereafter, but everywhere on the network it can physically get to.

So the quantity propagated per node is an **absolute world altitude**, `reach`, and the
propagation rule along a pipe is *copy it unchanged*. That single choice is what makes every
other rule fall out:

- **A consumer is supplied when `reach` at its port is at least the port's own height.** There
  is no distance term, because head lift has none.
- **A pump is `max(incoming, its own centre + its lift)`, never a sum.** Three Mk.2 pumps in a
  row give 50 m, not 150, because the second one's `max` cannot improve on a number the first
  one already raised above it. The model does not implement a special case for this; it is what
  a maximum does.
- **A pump stacks with gravity after it, and only with that.** A pump 40 m up a cliff seeds
  `40 + 50`, because its lift is measured from its own centre. That is the one and only way two
  lifts add.
- **Falling is free.** Nothing subtracts height on a descent, because gravity gives it back.

## The crest is the constraint, not the destination

A consumer 5 m below its source is *not* automatically supplied: the pipe between them may
climb over a hill first. The manual's own worked example is exactly this — a pump 9 m up a line
with an 18 m hump and then a 22 m hump has to lift 13 m, which is `22 − 9`, the highest crest
and not the far end.

So propagation across a pipe is gated on the **highest point of that pipe's own polyline**, not
on its two endpoints. The save carries every control point of every pipe, so the crest is
measured rather than inferred. A pipe whose crest stands above the head behind it is a **wall**,
and everything past it is cut off.

This is why the finding is reported as a crest and not as a list of machines. Twenty starved
refineries behind one hill are one problem with one fix — a pump before the crest — and naming
the crest says where to put it. Each cut-off consumer is attributed to the **cheapest** wall
between it and the supply, because that is the one a player would clear first.

## What seeds a reach

| source | seeded at | from |
|---|---|---|
| any machine port that produces — extractor, refinery output, packager output | port height + 10 m, failing at 12 m | `MACHINE_HEAD_LIFT_M`, the manual. **Not in game data**; see [§24.2](plumbing.md) |
| Pipeline Pump Mk.1 | pump centre + 20 m, failing at 22 m | `mDesignPressure` / `mMaxPressure` |
| Pipeline Pump Mk.2 | pump centre + 50 m, failing at 55 m | same |
| Fluid Buffer / Industrial Fluid Buffer | base + `height × fill share`, so 8 m and 12 m when full | `mStorageCapacity`, the clearance box |
| Valve | nothing. It lifts zero and is modelled only for being one-way | `mDesignPressure` = 0 |

A **pump with no wire** is modelled, because phase 1 can now identify one: it still passes
fluid, and it sets the head past it to its own centre. Everything the line had climbed on the
way in is gone at that point. The rule is applied to anything whose `max_head_lift_m` is
non-zero, which is what keeps an unpowered *valve* — a normal thing to have — from being
treated as a barrier.

A **buffer passes head lift through** rather than breaking the column, taking
`max(incoming, its own fill height)`. This is the one propagation rule the manual does not
state either way, and the permissive reading is deliberate: the alternative would invent
failures downstream of every tank on evidence nobody has. It is an open question, not a result.

## The band: rated, tolerated, failed

Every machine keeps working a little past its rating. The dump gives both numbers per class —
`mDesignPressure` and `mMaxPressure` — and the model runs the **whole relaxation twice**, once
with every rating and once with every ceiling. A consumer is then:

- **fine** — reached in the rated pass;
- **marginal** — reached only in the tolerance pass, and reported as a warning that says so;
- **failed** — reached in neither.

A fault's reported head is the one from the **tolerance** pass, the more generous of the two,
so the shortfall a crest quotes is a lower bound on how far the line really falls short.

The tolerance is never modelled as a percentage. On the pumps it is exactly 10% (22/20 and
55/50), while the manual's own 10 m → 12 m machine pair implies 20%; a single rate would be
wrong for one of them. Every ceiling is a per-class number or it is nothing.

The band doubles as the model's noise floor. Node altitudes are averaged over the pipe ends
meeting there, which on the reference world disagree by at most 0.5 m — comfortably inside a
2 m band, so measurement scatter cannot on its own produce a failure.

## What is excluded, and why

**Gas networks are dropped entirely.** Gas has no head lift at all, pumps do not work on it and
buffers cannot compensate its flow. A gas network is removed at the point the graph is built —
its pipes never become spans and its ports never become sources or consumers — rather than
modelled with a lift of zero, which would report every gas consumer as failed.

**A consumer that reaches no source at all is not a head-lift finding.** It is counted in
`unfed` and stays silent. The manual's troubleshooting order is (1) connection, (2) head lift,
(3) flow rate, and it says to move on only once sure it is not the rung before — so the model
runs the reachability question first, with every height ignored, and only consumers that *are*
fed can produce a crest. On the owner's newest save this is 10 refineries wired into a pipe
network that has never carried fluid: a block under construction, and precisely the thing that
would otherwise look like ten head-lift failures.

**Flow rate is rung (3) and is not here.** Nothing in this model reads a pipe's throughput, a
machine's demand or a valve's limit. "Reach" means the fluid can arrive, not that enough of it
does.

## Direction does not enter, and the ambiguity that does

`domain/world/flow.py` declines to orient 138 of the reference world's 503 pipes, and that
costs this model nothing: **head lift is hydrostatic, so it propagates both ways along a pipe
regardless of which way the fluid is moving.** A source at 110 m feeding a line that dives to
0 m and climbs back to 50 m supplies that far end, because a full pipe holds the pressure the
whole way. Computing both orientations of an undirected run gives the same answer by
construction, so the model treats every pipe as undirected and reports no ambiguity for it.

Only two things are directed, and neither is ambiguous: a pump and a valve run `Connection0` to
`Connection1` by construction, which the save states outright.

The ambiguity that *is* real is a **port whose facing nothing settles** — neither the save's own
`PipeInputFactory` / `PipeOutputFactory` typing nor the building's nature as an extractor or a
generator. Such a port is counted **both ways**: as a source that may not be one and as a
consumer that may not be one. The count is reported as `ambiguous_ports`, and a crest behind one
should be read knowing its head may be overstated. On every save on this machine it is zero —
after the T-junction, which is a *body* and not a machine port, was recognised by its native
class rather than by its build class.

## The port-height limitation

A consumer's height is the altitude of the **pipe end plugged into it**, not of the machine's
own origin, and that is the best available reading: every one of the reference world's 540
plumbing nodes has one. Where several pipe ends meet at a node their altitudes are averaged.

A **pump's centre** is the mean of its inlet and outlet node altitudes, because the projection
carries no position for a pump, junction or valve at all — those actors are absent from
`machines` and `attachments`. A pipeline pump is a small building whose pipe passes through it
level, so its connectors and its centre are within a metre of each other, but this is an
estimate and not a reading.

Two smaller ones. A pipe's crest is taken from its stored control points, so a Hermite curve
bulging above them is missed — bounded by the projection's own 1 cm tangent threshold, which is
three orders of magnitude below the band. And a machine with no pipe on a port has no altitude,
so it is not checked at all rather than checked against a guess.

## What a reading means

**Zero crests means the geometry does not forbid supply.** It does not mean a machine is
running: it says nothing about whether there is *enough* fluid, only that what there is can
reach. A refinery reading starved in `factory_health` with no crest against it is a rung-(3)
problem — a flow rate, a recipe ratio, a share at a junction — and the head-lift model has just
ruled its own rung out. That is now what the tool does with this model rather than something a
reader has to do by hand: the ladder is [§24.5](plumbing.md).

**A crest is a claim about height and nothing else.** It names the altitude the line reaches,
the head available behind it, and the consumers past it. `assumed` on a crest says the head
behind it comes from the pinned 10 m rather than from a pump, which is the one figure in the
model that game data does not carry; a verdict carrying that flag rests on the manual's word.

**A marginal crest is not a fault.** It is a line clearing its hill only on the tolerance the
machines have past their rating, which is a thing to know before extending it.

## Calibration

The acceptance test is that the owner's base works, and the number that matters is the
false-positive count.

| | |
|---|---|
| saves swept (every `.sav` on this machine, save versions 21 → 60) | 71 |
| fluid networks modelled | 756 |
| consumer ports checked | 4,048 |
| **crests reported** | **0** |
| marginal crests reported | 0 |
| ambiguous ports | 0 |
| consumers counted `unfed`, i.e. handed to rung (1) | 10, all in the newest three saves |

Zero is only meaningful if the model can say anything at all, so the reference world was
perturbed:

- **Cut power to every pump: 5 crests, 52 of the 96 consumers cut off.** More than half the
  world's fluid consumers depend on a pump for their head, and the model says so.
- **Cut power to one pump at a time: exactly one of the 15 wired pumps is individually
  load-bearing**, and losing it cuts off 20 consumers 2.8 m short. The other fourteen are
  redundant *for head* — which is the manual's "pumps do not stack" showing up in a real base,
  and does not mean they are redundant for flow.
- **Remove the machines' assumed 10 m: 76 of the 96 consumers fall over.** So the one pinned
  constant carries most of the verdicts — but the answer is stable against being wrong about
  it: the world still reports zero crests at 8 m and only starts failing at 6 m, and the
  tightest consumer in it has 7.7 m of headroom (median 17.9 m). No verdict here sits inside
  the assumption's plausible error.

One result worth keeping. The seven unpowered Mk.2 pumps phase 1 found are all on a single
226 m oil line up a cliff that has **no source and no consumer on it yet** — so the model
reports nothing about them, correctly, and the reason it is silent is a fact about the world
rather than a gap in the model.

**What zero does not establish.** This is one player's world, and it is nearly flat: 
essentially all of its plumbing sits between −17 m and +26 m, with one line reaching 226 m.
A base built up a cliff would exercise the crest rule far harder than anything here does, and
the marginal band in particular has never fired on real data — only in tests.
