"""Where "near" points: a coordinate, the player, a named factory, or a conduit run.

Lives with the map code rather than with any one tool group because the map tools and
the node tools both ask the same question.
"""

from __future__ import annotations

from . import geo

#: The conduit-run spelling this project settles on, everywhere: ``chain:<n>`` for a belt
#: chain and ``pipe:<n>`` for a pipeline piece, which is the ident ``search_conduits``
#: prints in its own id and connects columns. The web map's "pipe #12" is a caption.
RUN_PREFIXES = ("chain", "pipe")


def player_xy(st) -> tuple[float, float] | None:
    """Player XY for the near:me selector, or None if the save has no pawn."""
    here = st.player_position() if st else None
    return (here[0], here[1]) if here else None


def _run_origin(st, text: str) -> tuple[tuple[float, float], str]:
    """Centre on a belt chain or pipe piece, by the ident ``search_conduits`` prints.

    Its MIDPOINT, so a radius around it reaches both ways along the run; the answer names
    the run's own length, because a radius smaller than that only sees part of it.
    """
    if st is None:
        raise ValueError(f"{text!r} names a conduit run, which needs a readable save")
    want = text.casefold()
    for run in st.conduit_runs:
        if run.ident.casefold() == want:
            return run.midpoint(), f"{run.ident} (midpoint of a {run.length_m:.0f}m {run.label})"
    raise ValueError(f"no conduit run called {text!r}; search_conduits lists the ids it takes")


def resolve_origin(st, near: str) -> tuple[tuple[float, float], str]:
    """Resolve a location: "x,y" in metres, "me", a named factory, or a conduit run.

    A factory name is the useful one now that factories exist -- "nearest coal to the
    coal powerplant" is the question actually being asked, and hand-copying a centroid
    out of another tool's output is how the wrong coordinate gets used. A run ident --
    ``chain:7``, ``pipe:333`` -- closes the same loop for the ids ``search_conduits``
    prints and told the reader to follow.
    """
    text = near.strip()
    if text.partition(":")[0].casefold() in RUN_PREFIXES and ":" in text:
        return _run_origin(st, text)
    if "," in text:
        try:
            x_m, y_m = (float(v) for v in text.split(",", 1))
        except ValueError as exc:
            raise ValueError(f"{near!r} is not an x,y pair in metres") from exc
        return (x_m * 100.0, y_m * 100.0), f"{int(x_m)},{int(y_m)}"

    if text.casefold() in ("me", "player", "here"):
        here = player_xy(st)
        if here is None:
            raise ValueError("this save has no player pawn, so 'me' cannot be resolved")
        return here, "you"

    label = st.labels.find(text) if st else None
    if label is None:
        known = ", ".join(x.name for x in st.labels.labels) if st else ""
        raise ValueError(
            f"{near!r} is neither an x,y pair, 'me', nor a named factory"
            + (f". Named: {known}" if known else "")
        )
    pos = {}
    for key in ("machines", "extractors", "generators"):
        for record in st.projection.get(key, ()):
            if record.get("pos"):
                pos[record["instance"].rsplit(".", 1)[-1]] = record["pos"]
    points = [pos[m][:2] for m in label.anchors if m in pos]
    if not points:
        raise ValueError(f"{label.name!r} has no machines left to centre on")
    return geo.centroid(points), label.name
