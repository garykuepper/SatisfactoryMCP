"""The compact TSV presenter: primitives, plus one formatter module per concept.

Context budget is the binding constraint on every response here -- see
``primitives`` for the rules that follow from it.

Deliberately empty of imports. ``render.py`` still resolves through
``presenters.text.primitives``, and importing a submodule runs this file first, so
re-exporting the formatters here would drag the whole planning package in behind
every ``render.num`` call -- and back through the ``from .. import render`` shim
that domain modules still use, which is a cycle.
"""
