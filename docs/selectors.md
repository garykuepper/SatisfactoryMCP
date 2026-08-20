# Selectors: one grammar for saying which

This surface asks the caller to point at things three times — **which resource nodes** may
feed a plan, **which machines** make up a factory, and **which place** a question is about.
Each has its own vocabulary, because the three name different kinds of thing. What they
must not have is three ways to spell the *same* thing, and this page is where that is
settled: the shared rules first, then each vocabulary in full, then the differences that
remain and are deliberate.

Implemented in `domain/spatial/select.py` (nodes), `domain/factories/select.py` (machines)
and `domain/spatial/origin.py` (places). The parser for the one construct all three share,
a circle, lives in `origin.py` and is called from both selector modules.

## The rules every selector obeys

1. **Coordinates are metres.** The save is in centimetres; nothing on this surface is.
   A coordinate is always the pair `x,y`, in that order, and either may be negative or
   fractional.
2. **A comma separates coordinates, or alternatives. It is never a distance.**
3. **A radius follows `@`**: `<place>@<radius_m>`. It is required — there is no default
   radius, because a selector missing its radius is a truncated selector, not a small one.
4. **`:` separates a selector's kind from its value**, and the kind is case-insensitive.
5. **The player is `me`.** `player` and `here` mean the same thing wherever `me` is taken.
6. **An unresolvable selector is an error that names what would have worked.** Nothing on
   this surface widens its scope to compensate for a term it could not read.

`near:0,-2000,900` — a radius as a third comma value — was a second spelling of rule 3 and
is **retired**. It does not parse, on either side; the error prints the rewrite, because
the caller who meets it is usually a stored plan or a copied example:

```
near:'0,-2000,900' has no radius. Write near:0,-2000@900 instead. A circle is
near:<place>@<radius_m> -- e.g. near:-1069,-1273@200, near:me@500,
near:steel factory@150. The radius follows '@', never a comma
```

## Places — `at=`, `near=`, `site_at=`

A **place** is a single point. Resolved by `resolve_origin`, and taken by every tool that
asks where: `describe_location(at=)`, `site_plan(at=)`, `plan_factory(site_at=)`,
`search_conduits(near=, to=)`, `search_resource_nodes(near=)`, `storage(near=)` and
`collected_from_world(near=)`. `show_on_map(target=)` resolves the same places and three
more of its own -- a node id, a resource name, and `plan:<name>` for a sited plan.

| place | resolves to |
|---|---|
| `x,y` | that coordinate, in metres |
| `me` | the player pawn's position in the save |
| `<factory name>` | the centroid of a named factory's machines |
| `slab:<n>` | a foundation platform's tile mean, by the index `factory_map show=slabs` prints |
| `chain:<n>` / `pipe:<n>` | the midpoint of a conduit run, by the ident `search_conduits` prints |

Every form but a bare coordinate needs a readable save. The point a place resolved to is
echoed back with the name that produced it, because `at=` can land somewhere the caller
never typed.

## Which nodes — `sources=`

A **source spec** is a list of selectors, and it is what every `sources=` parameter takes:
`plan_factory`, `plan_layout`, `diff_vs_save`, `commission_plan`, `explain_byproducts`,
`rank_unlocks`, `advise_hard_drive_pick`, `search_resource_nodes` and `rank_build_sites`.

| selector | meaning |
|---|---|
| `north`, `northeast`, … | compass hemisphere, or a 60° cone when an origin is supplied |
| `region:Northern Forest` | named region; a bare name also works |
| `grid:X3Y4` | one exact 1.024 km biome grid cell |
| `node:BP_ResourceNode30_103` | one specific node, repeatable |
| `near:<x_m>,<y_m>@<radius_m>` | a circle |
| `near:me@<radius_m>` | a circle around the player |
| `bbox:<x1>,<y1>,<x2>,<y2>` | a rectangle, corners in metres |
| `resource:Crude Oil` | filter: resource type |
| `purity:pure\|normal\|impure` | filter: purity |
| `kind:node\|well_sat\|geyser` | filter: node kind |
| `all` | every node on the map |

**Locations union, filters intersect.** `["north", "resource:Crude Oil"]` is crude oil in
the northern half; `["region:Spire Coast", "near:120,-2020@1500"]` is the union of two
areas. Omitting locations entirely is a legitimate whole-map query; a location that fails
to resolve returns **nothing**, and says so.

## Which machines — `select=`, `factory=`

A **machine select** is a list of terms, taken by `select_machines(select=)`,
`name_factory(select=)` and `amend_factory(add=, drop=)`. The tools that address one
factory at a time -- `factory_query`, `factory_health`, `factory_floors`, `plan_layout`,
`diff_vs_save` -- take a single such term as `factory=`, so a label name and a selector
are interchangeable there.

| term | meaning |
|---|---|
| `product:Steel Pipe` | everything making it, anywhere |
| `recipe:Alternate: Solid Steel Ingot` | by recipe name or id, substring allowed |
| `building:Foundry` | by display name or class |
| `near:<x_m>,<y_m>@<radius_m>` | a circle |
| `near:<factory name>@<radius_m>` | a circle around a named factory's centroid |
| `base:<n>` | a power island, largest first |
| `line:<n>` | a material component, largest first |
| `slab:<n>` | a foundation platform, by its own printed index |
| `proposal:<n>` | the nth cluster from `propose_factories` |
| `label:<name>` | what a named factory already covers |
| `machine:<instance>` | named instances, exactly as the tools print them |
| `all` | every machine |

**Terms are ANDed; commas inside one term are ORed; a leading `-` excludes.** So
`["product:Concrete", "near:-1059,-1257@150"]` is the concrete feed at one site, and
`["base:0", "-label:steel factory"]` is the base minus what is already named. Intersection
rather than union because carving is subtractive: the player starts from something too big.

Two modifiers, because a factory is delimited from either end: `split` keeps only the
largest spatial cluster, `expand` grows the result to whole material components.
Exclusions apply **after** expanding.

**`base:`, `line:`, `slab:` and `proposal:` are positions in size-ordered lists rebuilt
from the save on every call.** They shift when you build. Read an index and name what it
selected in the same breath; never store one. A label is durable because it holds machine
ids.

## What the two selector languages still do not share

Named here so that the next reader knows it is a known state and not an oversight.

- **`near:` takes different places on each side.** Node selectors take a coordinate or
  `me`; machine selectors take a coordinate or a factory name. Neither takes the other's,
  because neither module is handed what it would need — the node selector never sees the
  label store, and the machine selector never sees the player pawn. The *syntax* is
  identical; the set of places is not.
- **Indices are machine-side only.** `base:`, `line:`, `slab:` and `proposal:` have no
  meaning over resource nodes, which are map facts rather than save facts.
- **`kind:` here is a node-kind filter**, and `kind=` as a tool parameter means five other
  vocabularies elsewhere on the surface. That is tracked in
  [backlog.md](backlog.md) under P3, not settled here.
