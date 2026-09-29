from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / ".claude").is_dir())
sys.path.insert(0, str(ROOT / ".claude/os/engine/scripts"))

from policy_batch_review import build_html  # noqa: E402


class PolicyBatchValueNamesTest(unittest.TestCase):
    def test_report_value_labels_come_from_the_current_task(self) -> None:
        manifest = {"field": "material", "cases": [{"id": "case-1", "productionValue": "COTTON"}]}
        results = {
            "case-1": {
                "status": "NO_GOLD",
                "steps": [],
                "output": {"value": "COTTON"},
                "productionComparison": {
                    "source": "productionValue",
                    "isGold": False,
                    "productionValue": "COTTON",
                    "extractedValue": "COTTON",
                    "matches": True,
                },
            }
        }

        page = build_html(manifest, results, value_names={"COTTON": "면"})

        self.assertIn("면 (COTTON)", page)
        self.assertNotIn("남성", page)


if __name__ == "__main__":
    unittest.main()
