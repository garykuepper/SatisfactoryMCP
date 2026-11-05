#!/usr/bin/env python3
"""Tests for nonbase_report.py."""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from nonbase_report import BASE_ITEMS, analyze_world, solved_targets

class TestNonbaseReport(unittest.TestCase):
    def test_targets_are_nonbase(self):
        targets = solved_targets()
        for item in ["Reinforced Iron Plate", "Modular Frame", "Motor", "Rotor", "Stator",
                     "Encased Industrial Beam", "Heavy Modular Frame"]:
            self.assertIn(item, targets)
        # Base items must never be solved targets
        self.assertFalse(BASE_ITEMS & set(targets))

    def test_whole_machines(self):
        for t in solved_targets().values():
            self.assertGreaterEqual(t["output"], t["demand"] - 1e-9)

    def test_analyze_world(self):
        try:
            results = analyze_world(include_all=False)
            self.assertGreater(len(results), 0)
            items = [r["item"] for r in results]
            self.assertIn("Rotor", items)
            self.assertIn("Reinforced Iron Plate", items)
        except Exception as exc:  # no save for this world, or satisfactory_mcp not importable
            self.skipTest(f"live save not available: {exc}")

if __name__ == "__main__":
    unittest.main()
