# Backlog: what is broken, what is missing, in the order to fix it

From three audits on 2026-08-02 (tool-chain coherence, map/text seam, unexposed domain
knowledge) plus the field report an LLM client filed after playing with the surface.
Deduplicated: where two audits found the same thing from different sides it appears once,
with both citations. Parked FEATURES are not here — they are docs/parked.md §21 and §22.

Every line names the smallest change that closes it. Verified items were reproduced by
reading the code cited, not taken on the auditor's word.

---

## P0 — answers that are wrong or crash

These produce a false belief in the reader. Nothing else outranks them.

| # | What | Where | Fix |
|---|---|---|---|
| 1 | `trace_upstream` raises `TypeError` for every factory label and every selector — its own parameter help lists labels FIRST. Tests miss it because they cover only the building-name and instance paths. | `interfaces/mcp/tools/factories.py:973` indexes `resolve_factory`'s `list[str]` as dicts | `seeds = list(machines)` |
| 2 | `show_on_map` hands the player a **satisfactory-calculator.com** link for every target except a sited plan, so the README's flagship example ("show the coal powerplant on the map") opens a site that cannot draw their world — and the README also promises nothing is fetched from the network. `local_map_url` exists and is called twice, both gated on `plan:`. | `domain/spatial/maplink.py:20`, emitted `tools/spatial.py:698`; README.md:7, :28 | Append `local_map_url(...)` to `extra_links` unconditionally; correct the README |
| 3 | `describe_location` states "there is no heightmap in any data this reads" while `/api/inspect` prints `terrain 118.3 m (landscape ±0.2 m)` from the 1 m heightfield on the same machine. The domain seam was built for this: `probe()` takes a `terrain_field` the web layer passes and MCP never does. | `tools/spatial.py:90`, `:110`, `:145`; cf. `routers/inspect.py:275` | Pass `heightfield.load_field()`; drop the two false notes |
| 4 | `machine:<id>` accepts any string with no validation and answers "0 machines" for machines that exist; the ids `trace_upstream` prints are truncated to their last 10 chars and resolve to nothing anywhere. `machine:` is not in the selector help. | `domain/factories/select.py:243`; `tools/factories.py:993`; `tools/progression.py:337` | Raise `SelectorError` on unknown ids; print full short instances |
| 5 | `commission_plan(limit=60)` is outside its own published schema (`Limit` is `ge=1, le=25`); Pydantic does not validate defaults, so a client echoing the default gets a hard error. The only such default in the tree. | `tools/planning.py:893` vs `interfaces/mcp/app.py:31` | `limit: Limit = 25` |
| 6 | `search_recipes` and `alternates_for_item` take `save` but not `world`, and both compute HAVE/LOCKED from world state — on a multi-world install they silently answer against the default world. `list_buildings` in the same file takes `world`. | `tools/gamedata.py:102`, `:162`, `:189` | Add `world` and thread it into `_state` |
| 7 | Belt length disagrees between halves: the map draws the true Hermite spline (its own note: chords are out by up to **16.4 m on one piece**), `search_conduits` sums chords. Same source data. | `routers/routes_layer.py:204`; `domain/world/conduits.py:142` | Integrate the spans where `seg.spans` exists |
| 8 | `list_regions` prints a raw centroid; the web side already fixed this because a concave region's mean lands in its neighbour ("Titan Forest's sits in the Swamp"). The assistant can send a player to a coordinate the map paints as somewhere else. | `tools/spatial.py:52`; fix exists at `routers/regions.py:84` | Move `_label_anchor` into `domain/spatial/regions.py`, use it both sides |
| 9 | Three different "already named" filters over one clusterer, so map and tool disagree about which proposals exist. | `routers/factories.py:112` vs `tools/factories.py:658` | One predicate in `domain/factories`, called by both |

## P1 — instructions the client cannot follow, and dead ends

| # | What | Where | Fix |
|---|---|---|---|
| 10 | **15 tools** print "call again with offset=N" and have no `offset` parameter. Only 5 of 44 accept one. Worst case `search_resource_nodes`: 127 iron nodes, 25-row cap, tail unreachable. Same defect the field report filed, at 15× the blast radius. | envelope at `presenters/text/primitives.py:65` | Add `offset` to those tools, or gate the sentence on a `pageable` flag and say "narrow with `sources=`" instead |
| 11 | `search_conduits` prints `connects: pipe:333 -> pipe:335` and tells the reader to follow it, but takes no run id — following a 20-piece route is 20 coordinate round trips. The map spells the same id `pipe #12`. | `tools/spatial.py:186`, `:343`; `frontend/src/routes.ts:331` | Accept `chain:<n>`/`pipe:<n>` in `near=`; settle one spelling |
| 12 | `diff_vs_save` orders actions ("unpause 3 of 23 Water Extractors") and drops the exact instance ids it already computed. The same response renders `targets` as a reusable footer, demonstrating the pattern it omits. | `domain/planning/diff.py:499` computed, `presenters/text/diff.py:269` dropped | Render `have_instances[:3]` for actionable verbs |
| 13 | Bare platforms are listed by an index no tool accepts (`slab:N` is explicitly refused for machine-less slabs), so the table added to retire the nine-probe workflow dead-ends into it. | `tools/factories.py:183`; `domain/factories/select.py:225` | Let `describe_location`/`show_on_map`/`site_at=` take `slab:<n>` |
| 14 | `diff_vs_save` documents a plan-id cross-check that `plan_factory` never prints for an unsaved plan. | `tools/planning.py:672` vs `presenters/text/plan_factory.py:285` | One `kv` entry |

## P2 — the two halves disagree, or one half is blind

| # | What | Where | Fix |
|---|---|---|---|
| 15 | Floors, storage contents, crates and power topology are **web-only**. `domain/factories/floors.py` recovers real storeys and no MCP tool reaches it; `WorldState.stock()` is reachable from exactly one place (an affordability check), so a client can be told "short 500 Quartz" but cannot ask what is in the boxes. | `routers/{floors,storage,crates,power}.py` have no text twin | Four thin tools over existing domain code; floors and storage first |
| 16 | The map says `OreIron`, the assistant says `Iron Ore` — `/api/nodes` is the only payload that does not resolve a display name, though the same handler resolves the occupant's two lines later. | `routers/nodes.py:38`, `:117`; `frontend/src/format.ts:11` | Add `resource_name` to `NodeRow` |
| 17 | The map has no `LOCKED` state: a node the text surface excludes as unreachable is drawn as an ordinary free dot a player may plan around. | `routers/nodes.py`; cf. `tools/spatial.py:463` | Carry `reachable` on `NodeRow`, grey the dot |
| 18 | `name_factory` and `site_plan` write to the labels/plans directories; the live watcher globs `*.sav` only, so the one collaborative moment — name it, then look at the map — is the one the map misses. | `interfaces/web/watch.py:90`; `frontend/src/sse.ts:36` | Watch those directories, emit a second event type |
| 19 | Every popup prints an MCP selector and nothing on the page can copy one: `grep clipboard` across the frontend returns nothing, so the last step is retyping `BP_ResourceNode26_99` by hand. (A generated "ask about this" sentence is NOT wanted — the selectors are already the interchange format.) | `frontend/src/dom.ts:44` | Click-to-copy in `code()`; ~15 lines, upgrades every popup at once |

## P3 — convention drift

Cheap individually, worth one pass together. Each costs a client a retry.

- Two radius grammars: `near:0,-2000,900` (node selectors) vs `near:-1069,-1273@200` (machine selectors).
- `describe_location` takes `x_m`/`y_m` as two floats; every other tool takes `"x,y"` as one string — and it is the tool the "something is wrong" journey ends on.
- Five spellings of "which view": `show=`, `of=`, `detail=`, `mode=`, `status=`.
- `kind=` means four different vocabularies across tools, and rejects `"all"` in one of them.
- Name filters: `query=`, `search=`, `with_resource=`, `group=`.
- `rank_build_sites(top=)` is the one unbounded row cap; every other tool uses `Limit`.
- `render.table`'s `limit=` argument is accepted and never used — a documented no-op.
- Off-map wording: "off-map or ocean" / "ocean/off-map" / "off the map".

## P4 — missing round-trips

- Plans: no rename, and no way to read the arguments a stored plan holds without paying a full
  LP solve that then prints the resolved selection instead.
- Labels: no rename; cannot add or drop one machine, only re-anchor the whole selection.
- Sitings: `list_plans` shows `x,y` only — yaw and footprint need a second call.
- `factory_sites` rows carry no identifier at all, so a "site" cannot be named to any other tool.
- `maplink.COLLECTIBLES` is dead code: "where are the hard drives" gets no map link from either map.

## Not doing, and why

- **Region tint 256 m vs lookups 64 m** (14.1% vs 5.3% mislabel): known, documented, and the
  fix is a different grid, not a bug fix. Leave it, or print the confidence word on the tint.
- **A "copy question" affordance on the map**: the LLM does not read the page and a generated
  English sentence guesses at the human's question. A selector and a coordinate are enough —
  once they can be copied (item 19).
- **Coordinate rounding drift** (`serial._m` rounds, text emitters truncate): sub-metre and
  cosmetic. Worth one sweep if someone is already in those files.
