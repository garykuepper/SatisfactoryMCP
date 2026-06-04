"""Map collectibles as text: the per-category census, and the per-placement listings.

Which placements answer the question is ``save.collect``'s decision; this module only
says it. The three shapes are different enough to be three functions -- a census is a
tally with its caveats, a listing is coordinates with their hazards, and the degraded
save-only answer is a name-prefix guess that has to admit it is one.
"""

from __future__ import annotations

from ...domain.spatial import geo
from ...save.collect import CollectiblesView
from . import primitives as render

__all__ = ["render_collectibles"]


def _hazard_tokens(hazard: dict) -> str:
    """The hazard block as a few tokens, distances in metres.

    Every one of these is inference from map geometry plus a radius the other actor's class
    declares -- see the note the tool prints. Gas is presence-only: the volume's shape is
    level geometry and is not in any file this reads.
    """
    out = []
    if hazard.get("hostiles_nearby"):
        n = sum(hazard["hostiles_nearby"].values())
        out.append(f"hostiles{n}@{(hazard.get('nearest_hostile_cm') or 0) / 100:.0f}m")
    if hazard.get("spawns_here"):
        out.append("spawner")
    if hazard.get("inside_spore_flower_damage_sphere"):
        out.append("spore")
    elif hazard.get("nearest_gas_cm"):
        out.append(f"gas@{hazard['nearest_gas_cm'] / 100:.0f}m")
    if hazard.get("nearest_uranium_cm"):
        out.append(f"uranium@{hazard['nearest_uranium_cm'] / 100:.0f}m")
    if hazard.get("nearest_nuclear_hog_spawner_cm"):
        out.append("nuclear-hog")
    return " ".join(out)


def _holds(row: dict, g) -> str:
    """What is in this one, where the map records it.

    A looted drop pod is reported as LOOTED and nothing else: its ``mUnlockCost`` is still
    on the actor after it has given up its hard drive, and quoting a price for something
    already taken is the kind of true-but-useless line a player acts on by mistake.
    """
    contents = row.get("contents") or {}
    if contents.get("item"):
        return f"{contents.get('count', 0):g} {g.item_name(contents['item'])}"
    if row.get("looted"):
        return "LOOTED"
    cost = row.get("unlock_cost") or {}
    if cost.get("item"):
        return f"wants {cost.get('amount', 0):g} {g.item_name(cost['item'])}"
    #: An unlooted pod that does not serialise mUnlockCost holds its class default, which
    #: the map cannot read. Still worth saying it is unlooted.
    return "unlooted, cost unknown" if row.get("looted") is False else ""


def _placement_table(rows: list[dict], g, distance: bool, total: int, limit: int) -> str:
    """One row per placement, with the empty optional columns dropped.

    Names are never truncated: ``(cell, name)`` is the only identity a placement has, and
    half a key joins to nothing. ``hazard`` and ``holds`` are annotations rather than
    identity, so a slug listing -- where both are always blank -- does not carry them.
    """
    hazard = [_hazard_tokens(r["hazard"]) for r in rows]
    holds = [_holds(r, g) for r in rows]
    optional = [("hazard", hazard), ("holds", holds)]
    shown = [(head, values) for head, values in optional if any(values)]
    body = [
        (
            r["category"],
            r["name"],
            *((f"{r['distance_m']:.0f}m",) if distance else ()),
            r["observed"] or ("collected" if r["collected"] else "-"),
            geo.grid_cell(r["pos"][0], r["pos"][1]),
            f"{int(r['pos'][0] / 100)},{int(r['pos'][1] / 100)}",
            f"{r['pos'][2] / 100:.0f}",
            *(values[i] for _head, values in shown),
        )
        for i, r in enumerate(rows)
    ]
    headers = (
        "category",
        "name",
        *(("dist",) if distance else ()),
        "observed",
        "grid",
        "x,y",
        "z",
        *(head for head, _values in shown),
    )
    return render.table(headers, body, total=total, limit=limit)


#: Census columns beyond the four every save has. Rendered only when some row is non-zero:
#: ``gone_later`` needs an older save than the newest on disk, and ``unstated`` needs a table
#: newer than this code. Both are silent when they have nothing to say, and neither is
#: dropped from the arithmetic when they do.
_CONDITIONAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("gone_in_a_later_save", "gone_later"),
    ("unstated", "unstated"),
)


def _census(st, view: CollectiblesView, limit: int) -> str:
    """The per-category table: placed, collected, remaining, and how much is observed.

    ``group`` narrows the table to one category and takes its notes with it. The summary
    line stays whole-world and says so, because a scoped count under an unscoped header is
    how a category total gets read as a world total.
    """
    removed, table, group = view.removed, view.table, view.group
    census = [r for r in removed["census"] if group is None or r["category"] == group]
    extra = [(key, head) for key, head in _CONDITIONAL_COLUMNS if any(r[key] for r in census)]
    rows = [
        (
            row["category"],
            row["placed"],
            row["collected"],
            "-" if row["remaining"] is None else row["remaining"],
            row["standing"],
            row["never_streamed"],
            *(row[key] for key, _head in extra),
        )
        for row in census
    ]

    notes = [
        (
            "placed is the MAP's own count and collected is THIS save's own destroyed list; "
            "both are exact, and remaining is their subtraction. Wherever remaining is a "
            "number, standing + never_streamed (+ any further column) adds up to it"
        ),
        (
            "never_streamed is a placement in a cell no save on disk has ever loaded. It "
            "counts as remaining because nothing collected it, and it is NOT present: the "
            "map says where it is and nothing says whether it is still there"
        ),
    ]
    untracked = [r for r in census if not r["state_tracked"]]
    if untracked:
        notes.append(
            "remaining is withheld (-) for "
            + ", ".join(f"{r['category']} ({r['cls']}, {r['placed']} placed)" for r in untracked)
            + ": no save on disk mentions that class at all, live or gone, so taking one "
            "would leave nothing to read and placed-minus-collected would be a fabrication"
        )
    pedestals = [r for r in census if r["pedestal_of"]]
    if pedestals:
        notes.append(
            "never add categories together: "
            + "; ".join(
                f"{r['category']} is the base {r['pedestal_of']} stands on, paired 1:1 by the "
                f"map's own AttachParent, so the two are {r['placed']} finds and not "
                f"{r['placed'] * 2}"
                for r in pedestals
            )
        )
    pods = next((r for r in census if r["looted_and_standing"]), None)
    if pods:
        notes.append(
            f"{pods['category']}: {pods['looted_and_standing']} of the {pods['standing']} "
            "standing ones are already LOOTED. A pod stays in the world after it is emptied "
            "-- only a dismantled one is destroyed -- so its remaining is not a count of "
            f"hard drives left. Use mode='remaining' group='{pods['category']}' to see which"
        )
    if removed["unresolved"]:
        notes.append(
            f"{removed['unresolved']} of the {removed['total']} destroyed records join no "
            "placement: either a class the map table excludes on purpose (crash-site "
            "scenery, regrowing berry and nut bushes, resource nodes) or an actor the map "
            "never placed -- which is what a pickup the PLAYER dropped is, and it shares its "
            "native class with the loot caches"
        )
    if (st.header.get("save_version") or 99) < 52:
        notes.append(
            "this save predates world partition, so its destroyed actors are keyed through "
            "Persistent_Level and the map places only 32 rows there: nearly everything will "
            "read as unresolved. Load a newer save of the same world for a real census"
        )

    if group and (note := table.note_for(group)):
        notes.append(f"{group}: {note}")

    body = [
        render.table(
            (
                "category",
                "placed",
                "collected",
                "remaining",
                "standing",
                "never_streamed",
                *(head for _key, head in extra),
            ),
            rows,
        )
    ]
    if group is None:
        stems = [
            (k, v, table.excluded_reason(k) or "") for k, v in removed["unresolved_stems"].items()
        ]
        body.append(
            render.table(
                ("name_stem", "destroyed", "why the map table has no row for it"),
                [(k, v, why[:96]) for k, v, why in stems[: render.clamp(limit, default=25)]],
                total=len(stems),
                limit=limit,
            )
        )
    body.append(render.ids_footer([(r["category"], r["cls"]) for r in census], "classes"))
    return render.envelope(
        f"# {st.age_note}\n"
        f"# map table: {len(table)} placements, {table.build}\n"
        # Whole-world figures, labelled as such: they do not narrow with `group`, and a
        # scoped table under an unscoped total is how one gets read as the other.
        + render.kv(
            [
                ("whole_world_collected", removed["resolved"]),
                ("destroyed_records", removed["total"]),
                ("unresolved", removed["unresolved"]),
                ("save_cells", removed["cells"]),
                ("showing", group or "every category"),
            ]
        ),
        "\n\n".join(b for b in body if b),
        notes,
    )


def _listing(st, view: CollectiblesView, limit: int) -> str:
    """Individual placements: collected, remaining, or remaining by distance."""
    g = st.game
    table, group, mode = view.table, view.group, view.mode
    rows, origin, where = view.rows or [], view.origin, view.where
    pedestals, hidden, counts = view.pedestals, view.hidden, view.counts

    shown = render.clamp(limit, default=25)

    notes = []
    if mode == "collected":
        notes.append(
            "these are gone -- the coordinates say where they WERE. The map is the only "
            "source of a position here; a destroyed record carries none"
        )
    else:
        notes.append(
            "never_streamed rows are placements no save has ever loaded: the position is the "
            "map's and is exact, the state is unobserved. They are still remaining"
        )
    if origin is not None:
        notes.append(
            f"distance is planar metres from {where}, straight-line and not a walk: nothing "
            "here knows about cliffs, and a slug 80 m away can be 80 m up"
        )
    if any(r["hazard"] for r in rows[:shown]):
        notes.append(
            "the hazard column is INFERENCE, not placement: geometry between this placement "
            "and other map actors, plus the radii those actors' own classes declare. Gas is "
            "presence-only -- the volume's shape is level geometry and is in no file read here"
        )
    if group and (note := table.note_for(group)):
        notes.append(f"{group}: {note}")
    if group is None:
        notes.append(
            "every category at once. Pass group= to narrow it -- the names are in mode='census'"
        )
    if hidden:
        notes.append(
            f"{hidden} {'/'.join(pedestals)} row(s) are not shown: a shrine is the base its "
            "artifact stands on, 1:1 by the map's own AttachParent, so listing both would put "
            "two rows a metre apart for one find. Ask for it by group to see them"
        )

    return render.envelope(
        f"# {st.age_note}\n"
        f"# mode={mode}"
        + (f" group={group}" if group else " all categories")
        + (f" from {where}" if origin else "")
        + "\n"
        + render.kv([("rows", len(rows)), *sorted(counts.items())]),
        _placement_table(rows[:shown], g, origin is not None, total=len(rows), limit=limit),
        notes,
    )


def _save_only(st, view: CollectiblesView, limit: int) -> str:
    """The census a save can build alone: collected counts by name prefix, and wrong.

    Reached only when ``data/world_collectibles.json`` is absent. It is untracked, so a
    fresh clone lands here, and the honest thing is to answer with what the save does know
    while naming everything this costs.
    """
    removed, group = view.removed, view.group
    notes = [
        (
            "DEGRADED: data/world_collectibles.json is absent, so there is no map table to "
            "join against. Groups below come from a name-prefix rule over the destroyed "
            "actors' instance names, which is measurably wrong -- on the reference save it "
            "misfiles 51 of 713 (40 yellow slugs read as blue) and leaves 65 undecidable. "
            "Regenerate the table with tools/gen_world_collectibles.py"
        ),
        (
            "collected, not remaining: without the map table nothing here knows how many of "
            "anything exists, so these are absolute counts and not a fraction of a total. "
            "mode=remaining and mode=nearest need the table and are unavailable"
        ),
        (
            "'artifact_unsplit' is names of the shape BP_WAT<n>, where the placement counter "
            "is glued onto a stem that is either BP_WAT1 (somersloop) or BP_WAT2 (Mercer "
            "sphere). Splitting them by name would be invention; the map table splits them"
        ),
        "'dropped_pickup' is loot the player dropped and re-collected, not a map collectible",
    ]
    if removed.get("other"):
        notes.append(
            "'other' is classes no prefix matched, reported rather than dropped: "
            + ", ".join(f"{k} {v}" for k, v in list(removed["other"].items())[:6])
        )
    if not removed["total"]:
        notes.append(
            "nothing recorded as removed. On a projection older than schema 11 that means "
            "unreadable rather than none -- re-read the save"
        )

    body = [
        render.table(("group", "collected"), [(k, str(v)) for k, v in removed["groups"].items()])
    ]
    if group is not None:
        rows = [
            (a["name"], a["cell"]) for a in removed["actors"][: render.clamp(limit, default=25)]
        ]
        body.append(
            render.table(("actor", "cell"), rows, total=len(removed["actors"]), limit=limit)
        )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv([("total_removed", removed["total"]), ("map_cells", removed["cells"])]),
        "\n\n".join(body),
        notes,
    )


def render_collectibles(st, view: CollectiblesView, limit: int) -> str:
    """The one entry point: a refusal, the degraded census, the census, or a listing."""
    if view.error:
        return view.error
    if view.save_only:
        return _save_only(st, view, limit)
    if view.mode == "census":
        return _census(st, view, limit)
    return _listing(st, view, limit)
