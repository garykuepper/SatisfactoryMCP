# Prompt for the planner — multi-site joint solve (idea #5)

Copy the section below to the planning agent verbatim.

---

You raised six gaps after the oil-power session. Five are now closed, and #5 (multi-site
joint solve) is the one left. It is also the only one I do not want to design from my side
alone, because the whole shape of it comes from what actually forced you to split that
plant into three modules — and I only have the summary, not the reasoning.

**Before you answer, five things changed today that may invalidate parts of your design.
Please re-examine against these rather than your session notes:**

1. **Your real headroom is ~6,032 MW, not 831.** `power_report` was reporting nameplate
   draw (6,839 MW) while the save's 300 s productivity monitor puts actual draw at
   1,516 MW — the grid is 22% utilised. Nameplate is still the safe bound, but your
   bootstrap slice was sized against a number ~8× too pessimistic.
2. **Turbo Blend Fuel is worth +13.6% (+14,540 MW) to the Spire plan** — the only one of
   79 locked alternates that moves it, and it is sitting in hard drive 25 with no rerolls
   left. New tool: `rank_unlocks`.
3. **Pump counts are real now.** `mDesignPressure` was in the dump all along: Mk1 lifts
   20 m, Mk2 lifts 50 m. Your one climbing crude trunk needs 1× Mk2. `plan_layout
   show="trunks"` reports it.
4. **Carrier tiers are read from the save, and `belt_tier`/`pipe_tier` never worked before
   today** — they silently fell through to Mk5/Mk2 regardless of what you passed. Your
   assumption happened to be correct (both unlocked), so your pipe counts stand.
5. **Foundation counts were ~28% too high** across the board — block sizing multiplied a
   per-machine figure that ignores shared edges. The Spire deck is 4,719 foundations, not
   6,529, and the uncapped site is 440 m square rather than 496.

## What I need from you

### A. The three modules, as you actually designed them

For each of A (rig), B (generator hall) and C (resin plant):

- What is in it — machine types and counts, and which node set feeds it.
- **Why it is a separate site rather than a block inside one plan.** This is the crux.
  A joint solve with no cost of separation collapses to a single solve, so I need to know
  what makes the split real. Was it platform area? Distance? Water at sea level? The fact
  that a generator hall needs no water and can therefore go anywhere? Something else?

### B. The interfaces, precisely

For every flow that crosses a module boundary: item, rate, direction, and carrier. You
mentioned **8,060 Fuel to the generators and 1,170 back to Module C** — I want the whole
set in that form, including anything that turned out to be zero and mattered *because* it
was zero.

For each interface, what constrains it? A pipe count? A distance you were unwilling to
run? A head you would have had to pump?

### C. What the joint solve must be *forbidden* to do

More useful to me than what it should optimise. If I let an LP move machines between
sites freely it will co-locate everything, because nothing in the model prices distance.
What are the hard constraints that keep the three modules apart — and are they per-site
caps (area, water access, node adjacency), or per-interface caps (this link is at most N
pipes)?

### D. The coupled vs decoupled comparison

You got this out of four `plan_factory` calls and a hand reconciliation. I want the
numbers on both sides — MW, machines, and the fuel split — so I can build the joint solver
against a known answer and check it reproduces yours. **A worked case I can regression-test
is worth more to me than any amount of specification.**

Also: you said the decoupling insight (residual-only needs no fuel return) "only emerged
because I went looking for it." What were you looking for, and what would have made you
find it sooner? If there is a question the tool should have been able to answer that would
have surfaced it, that is probably the actual feature.

### E. Where the hand-reconciliation went

Step by step, what you did between the four solves. That is the thing to automate, and
I would rather automate your real procedure than a guess at it.

### F. What would falsify this

A case where sites should *not* be solved jointly, and where the current one-plan-at-a-time
approach is the right answer. I would rather scope this narrowly and correctly than build a
general site graph nobody needs.

## What already exists, so you do not have to ask for it

`plan_factory` / `plan_layout` (blocks, buses, floors, trunks, materials, deck caps),
`commission_plan` (startup waves with fill times), `diff_vs_save` (with per-stage
tracking), `rank_unlocks`, `mam_research`, plan persistence with `plan_id` drift
detection, and `slice_of` for totalling any subset of a solved plan. Sites do not exist as
a concept anywhere yet — that is the whole of this piece of work.

## Format

Prose is fine; tables where numbers are involved. Do not design the API — tell me what the
problem is and what the real constraints were, and let me argue with you about the shape
afterwards. If you think #5 is the wrong next thing now that the other five have landed,
say that instead.
