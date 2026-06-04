"""``satisfactory-mcp-web`` -- serve the JSON API and the map on localhost.

Bound to 127.0.0.1 on purpose. The API answers with the contents of the player's save
directory and applies no authentication whatsoever, so it is a local tool; making it
reachable from the network has to be a deliberate act with an argument behind it, not
the default that shipped.

Uvicorn is handed the import string rather than the object so ``--reload`` semantics and
the worker model work the way its documentation says they do.
"""

from __future__ import annotations

import uvicorn

__all__ = ["HOST", "PORT", "main"]

HOST = "127.0.0.1"
#: Arbitrary and high, chosen to collide with nothing: 8712 is free in the IANA list.
PORT = 8712


def main() -> None:
    uvicorn.run("satisfactory_mcp.interfaces.web.app:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
