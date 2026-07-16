"""A JSON + map interface over the same domain services the MCP tools call.

Deliberately import-free, and that is load-bearing: ``fastapi`` and ``uvicorn`` live
in the optional ``web`` extra, so importing this package must not require them. Only
``app``, ``routers``, ``serial``, ``watch`` and ``__main__`` touch the ASGI
stack, and nothing outside ``interfaces/`` may import any of them --
``tests/test_architecture.py`` checks it.

The MCP surface is a sibling, not a parent. This package never imports
``interfaces.mcp``: the two adapters share the domain, not each other.
"""
