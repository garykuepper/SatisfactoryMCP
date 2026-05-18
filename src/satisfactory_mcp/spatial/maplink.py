"""Deep links into satisfactory-calculator.com's interactive map.

The fragment format, read off a working link the player supplied::

    #4.75;40351;-208857|gameLayer|oilWellPure;oilNormal;oilWellNormal;oilImpure;...
     ^zoom ^x    ^y     ^group    ^sublayers, semicolon-separated

**Coordinates are save centimetres.** Not proven from the site -- it returns 403 to
automated fetches, so nothing here could be read from it -- but strongly corroborated:
that example falls inside this project's measured content bbox and resolves to the
northern oil region, which is what its oil layers are showing. Every other tool in this
MCP quotes metres, so the conversion happens here and nowhere else.

**Only the oil tokens are verified.** They appear in the supplied link. Everything else in
``LAYERS`` is inferred from the one pattern that link demonstrates --
``<resource><Purity>`` for nodes and ``<resource>Well<Purity>`` for resource wells -- and
is marked ``[UNVERIFIED]`` wherever it is surfaced. A wrong token does not break the link;
the map opens at the right place with that overlay simply not enabled, which is the
failure mode to prefer. Callers can pass ``layers`` explicitly to override the guess.
"""

from __future__ import annotations

from urllib.parse import quote

__all__ = ["BASE", "LAYERS", "VERIFIED_RESOURCES", "layers_for", "map_url"]

BASE = "https://satisfactory-calculator.com/en/interactive-map"

#: The layer group in the supplied link. The site has others (map/game); this is the one
#: resource markers live on.
GROUP = "gameLayer"

#: Resource class -> the site's token for it. Oil is READ from the player's link; the
#: rest follow its pattern and are unverified guesses.
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
    "Desc_NitrogenGas_C": "nitrogen",
    "Desc_Water_C": "water",
    "Desc_Geyser_C": "geyser",
}

#: The only entry the supplied link proves. Everything else is inference.
VERIFIED_RESOURCES = frozenset({"Desc_LiquidOil_C"})

_PURITIES = ("Impure", "Normal", "Pure")


def layers_for(resources: list[str], kinds: list[str] | None = None) -> list[str]:
    """Sublayer tokens for a set of resource classes, both node and well variants.

    Every purity is included rather than only the one being looked at: a link that opens
    the map showing one impure node and hiding the pure one next to it answers a narrower
    question than the player asked.
    """
    kinds = kinds or ["node", "well"]
    out: list[str] = []
    for cls in resources:
        token = LAYERS.get(cls)
        if not token:
            continue
        if token == "geyser":
            out.append("geyser")
            continue
        for purity in _PURITIES:
            if "node" in kinds:
                out.append(f"{token}{purity}")
            if "well" in kinds:
                out.append(f"{token}Well{purity}")
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
