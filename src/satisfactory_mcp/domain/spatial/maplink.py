"""Deep links into satisfactory-calculator.com's interactive map.

The fragment format, read off a working link the player supplied::

    #4.75;40351;-208857|gameLayer|oilWellPure;oilNormal;oilWellNormal;oilImpure;...
     ^zoom ^x    ^y     ^group    ^sublayers, semicolon-separated

**Coordinates are save centimetres.** Corroborated rather than stated by the site: the
supplied coordinate falls inside this project's measured content bbox and resolves to the
northern oil region, which is what its oil layers are showing. Every other tool in this
MCP quotes metres, so the conversion happens here and nowhere else.

**Every token below was READ from the page, not inferred.** ``WebFetch`` gets 403 from
this host, but ``curl`` from the user's own machine returns the 1.5 MB page, and the layer
identifiers are in it. That mattered: inferring from the single oil example got two of
them wrong. Nitrogen is ``nitrogenGasWell*``, not ``nitrogenWell*``; and geysers carry
purity variants (``geyserImpure`` ...) where a bare ``geyser`` was guessed. Both would
have produced a link that opened correctly with the overlay silently missing -- the
failure mode that is hardest to notice.
"""

from __future__ import annotations

from urllib.parse import quote

__all__ = ["BASE", "COLLECTIBLES", "LAYERS", "LOCAL_BASE", "layers_for", "local_map_url", "map_url"]

BASE = "https://satisfactory-calculator.com/en/interactive-map"

#: This project's own web map. The port is pinned in ``interfaces.web.__main__`` (8712,
#: chosen to collide with nothing); repeated here as data rather than imported, because
#: domain may not reach into interfaces and a URL is a string either way.
LOCAL_BASE = "http://127.0.0.1:8712/"

#: The layer group in the supplied link. The site has others (map/game); this is the one
#: resource markers live on.
GROUP = "gameLayer"

#: Resource class -> the site's token, read from the interactive map page itself.
#: The wells carry their own stems: oil is ``oilWell*`` but nitrogen is
#: ``nitrogenGasWell*``, which no amount of pattern-matching on "oil" would have produced.
LAYERS: dict[str, str] = {
    "Desc_LiquidOil_C": "oil",
    "Desc_OreIron_C": "iron",
    "Desc_OreCopper_C": "copper",
    "Desc_OreGold_C": "caterium",
    "Desc_Coal_C": "coal",
    "Desc_Stone_C": "limestone",
    "Desc_Sulfur_C": "sulfur",
    "Desc_RawQuartz_C": "quartz",
    "Desc_OreBauxite_C": "bauxite",
    "Desc_OreUranium_C": "uranium",
    "Desc_SAM_C": "sam",
    "Desc_NitrogenGas_C": "nitrogenGas",
    "Desc_Water_C": "water",
    #: Geysers DO take purities on this map, despite having no purity in our node table.
    "Desc_Geyser_C": "geyser",
}

#: Resources whose markers are wells rather than nodes, so the token carries ``Well``.
#: Read off the page: only these three have Well variants, and oil has BOTH.
WELL_STEMS = frozenset({"oil", "nitrogenGas", "water"})

#: Resources that appear ONLY as wells, so a bare ``<stem><Purity>`` token does not exist.
WELL_ONLY = frozenset({"nitrogenGas", "water"})

#: Collectibles, each a single token with no purity. Also read from the page.
COLLECTIBLES: dict[str, str] = {
    "slugs_green": "greenSlugs",
    "slugs_yellow": "yellowSlugs",
    "slugs_purple": "purpleSlugs",
    "hard_drives": "hardDrives",
    "mercer_spheres": "mercerSpheres",
    "somersloops": "somersloops",
}

_PURITIES = ("Impure", "Normal", "Pure")


def layers_for(resources: list[str], kinds: list[str] | None = None) -> list[str]:
    """Sublayer tokens for a set of resource classes.

    Every purity is included rather than only the one being looked at: a link that opens
    the map showing one impure node and hiding the pure one beside it answers a narrower
    question than the player asked.

    ``kinds`` filters to ``node`` or ``well`` when the caller knows which exist -- the
    node table does -- but the stems themselves decide what is possible: nitrogen and
    water have no bare node token, and coal has no well token, whatever is asked for.
    """
    wanted = set(kinds or ("node", "well"))
    out: list[str] = []
    for cls in resources:
        stem = LAYERS.get(cls)
        if not stem:
            continue
        for purity in _PURITIES:
            if "node" in wanted and stem not in WELL_ONLY:
                out.append(f"{stem}{purity}")
            if "well" in wanted and stem in WELL_STEMS:
                out.append(f"{stem}Well{purity}")
    return list(dict.fromkeys(out))


def map_url(
    x_cm: float,
    y_cm: float,
    layers: list[str] | None = None,
    zoom: float = 4.75,
) -> str:
    """A deep link centred on a save coordinate, with the given sublayers enabled.

    ``x_cm``/``y_cm`` are SAVE units. Callers holding metres must multiply by 100; that
    conversion is deliberately not done here, so a metre value passed by mistake lands
    1/100th of the way across the map rather than silently near the origin.
    """
    fragment = f"{zoom:g};{round(x_cm)};{round(y_cm)}|{GROUP}"
    if layers:
        fragment += "|" + ";".join(layers)
    # The fragment is semicolon- and pipe-delimited by design, so those must survive;
    # only genuinely unsafe characters are escaped.
    return f"{BASE}#{quote(fragment, safe=';|.-')}"


def local_map_url(x_m: float, y_m: float, zoom: float = 1, world: str = "") -> str:
    """A deep link into this project's own web map, centred on a coordinate in METRES.

    The fragment format is read from the frontend's own writer (``writeHash`` in
    ``map.ts``): ``#world=…&z=…&c=x,y``, with ``c`` in metres on save axes and rounded to
    one decimal, exactly as the page itself writes it. ``save`` is omitted on purpose --
    an absent save means "follow the newest", which is what a link pasted later should do.

    Metres, unlike ``map_url`` above, because that is the unit the page's fragment
    carries; the two writers each match their reader.
    """
    parts = []
    if world:
        parts.append("world=" + quote(world, safe=""))
    parts.append(f"z={zoom:g}")
    parts.append(f"c={round(x_m, 1):g},{round(y_m, 1):g}")
    return LOCAL_BASE + "#" + "&".join(parts)
