from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.opportunity.revision_discovery import _load_nav_evidence_layers


class RevisionNavEvidenceBaselineTest(unittest.TestCase):
    def test_bundled_baseline_contains_suli_and_merges_server_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            server_path = data_root / "reference" / "revision_nav_clause_evidence_v1.json"
            server_path.parent.mkdir(parents=True)
            server_path.write_text(
                json.dumps(
                    {
                        "version": "revision-nav-clause-evidence-v1",
                        "evidence": [
                            {
                                "bond_code": "999999",
                                "nav_floor_applicable": False,
                                "source": "test",
                                "reason": "server overlay test",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            merged, loaded_paths = _load_nav_evidence_layers(data_root)

            self.assertTrue(merged["113640"]["nav_floor_applicable"])
            self.assertEqual(
                merged["113640"]["source"],
                "https://static.cninfo.com.cn/finalpage/2024-07-19/1220676352.PDF",
            )
            self.assertFalse(merged["999999"]["nav_floor_applicable"])
            self.assertGreaterEqual(len(loaded_paths), 2)


if __name__ == "__main__":
    unittest.main()
