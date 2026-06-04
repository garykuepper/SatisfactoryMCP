"""The compact TSV presenter: primitives, plus one formatter module per concept.

Context budget is the binding constraint on every response here -- see
``primitives`` for the rules that follow from it.

Deliberately empty of imports, and it has to stay that way. Importing a submodule
runs this file first, so re-exporting the formatters here would drag the whole
planning package in behind every ``primitives.num`` call -- and the tool modules
reach for ``primitives`` on every response.

The ``from .. import render`` spelling this note used to warn about is gone from
``src`` entirely: the interface layer imports ``presenters.text.primitives`` by name,
and ``render.py`` survives only for the test files that still say ``render.table``.
"""
