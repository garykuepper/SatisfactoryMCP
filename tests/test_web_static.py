"""The mount at ``/``: the committed bundle, and the licence that ships beside it.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

The one file of this set that is about ``app.py`` rather than about a router. The static
mount is registered LAST -- a mount at ``/`` swallows every path that did not already
match -- so "the page is served from the root" is also the assertion that no router was
mounted after it. ``test_architecture.py`` covers the other half: that everything in
``static/`` is build output.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.web.app import STATIC_DIR

# --------------------------------------------------------------------- static


def test_the_static_bundle_ships_the_page_and_the_vendor_licence():
    """Redistributing Leaflet means shipping its BSD-2-Clause text next to it.

    Leaflet is compiled into ``app.js`` now rather than served as ``vendor/leaflet.js``, so
    there is no file to point at any more -- which is exactly why the licence text still has
    to be here, and why the bundle names the library in its own banner. The obligation did
    not move when the packaging did.
    """
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "app.js").is_file()
    assert (STATIC_DIR / "app.css").is_file()
    licence = (STATIC_DIR / "vendor" / "LEAFLET-LICENSE").read_text(encoding="utf-8")
    assert "BSD 2-Clause License" in licence
    assert "Leaflet" in (STATIC_DIR / "app.js").read_text(encoding="utf-8")[:1000]


def test_the_page_is_served_from_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "app.js" in r.text
