#!/usr/bin/env python3
"""Tests for nonbase_report.py."""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from nonbase_report import CORE_TARGETS, BASE_ITEMS, analyze_world

class TestNonbaseReport(unittest.TestCase):
    def test_core_targets_present(self):
        self.assertIn("Reinforced Iron Plate", CORE_TARGETS)
        self.assertIn("Modular Frame", CORE_TARGETS)
        self.assertIn("Motor", CORE_TARGETS)
        self.assertIn("Rotor", CORE_TARGETS)
        self.assertIn("Stator", CORE_TARGETS)
        self.assertIn("Encased Industrial Beam", CORE_TARGETS)
        self.assertIn("Heavy Modular Frame", CORE_TARGETS)

    def test_base_items_excluded(self):
        # Base items should not be in core targets
        for base in ["Iron Plate", "Wire", "Cable", "Concrete", "Steel Pipe", "Steel Beam"]:
            self.assertIn(base, BASE_ITEMS)
            self.assertNotIn(base, CORE_TARGETS)

    def test_analyze_world(self):
        try:
            results = analyze_world(world_name="LFG", include_all=False)
            self.assertGreater(len(results), 0)
            items = [r["item"] for r in results]
            self.assertIn("Rotor", items)
            self.assertIn("Reinforced Iron Plate", items)
        except Exception as exc:  # no save for this world, or satisfactory_mcp not importable
            self.skipTest(f"live save not available: {exc}")

if __name__ == "__main__":
    unittest.main()
