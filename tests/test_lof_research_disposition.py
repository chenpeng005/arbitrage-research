import json
import tempfile
import unittest
from pathlib import Path

from runtime.lof.research_disposition import load_research_dispositions


class ResearchDispositionTests(unittest.TestCase):
    def _root(self, payload):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        sha = "knowledge-1"
        (root / "deployment_manifest.json").write_text(
            json.dumps({"knowledge_commit_sha": sha}),
            encoding="utf-8",
        )
        target = (
            root
            / "knowledge_snapshots"
            / sha
            / "files"
            / "05 套利研究"
            / "LOF机会发现"
            / "02_数据与监控"
        )
        target.mkdir(parents=True)
        (
            target / "LOF-Research-Disposition-Registry-V0.1.json"
        ).write_text(json.dumps(payload), encoding="utf-8")
        return temp, root

    def test_explicit_entry_and_class_rule(self):
        temp, root = self._root(
            {
                "version": "v1",
                "entries": {
                    "160220": {
                        "status": "RESEARCHED_DEFERRED",
                        "reason_code": "NEGATIVE_SAMPLE",
                    }
                },
                "rules": [
                    {
                        "match": {
                            "resolver_class": "R2_DOMESTIC_OTHER",
                            "lof_type": "FOF",
                        },
                        "status": "RESEARCHED_DEFERRED",
                        "reason_code": "FOF_CHAIN",
                    }
                ],
            }
        )
        try:
            result = load_research_dispositions(
                root,
                market_rows=[
                    {
                        "code": "160220",
                        "resolver_class": "R2_DOMESTIC_OTHER",
                        "lof_type": "MIXED",
                    },
                    {
                        "code": "501215",
                        "resolver_class": "R2_DOMESTIC_OTHER",
                        "lof_type": "FOF",
                    },
                ],
            )
        finally:
            temp.cleanup()

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rows"]["160220"]["source"], "ENTRY")
        self.assertEqual(result["rows"]["501215"]["source"], "RULE")

    def test_missing_snapshot_fails_soft(self):
        with tempfile.TemporaryDirectory() as root:
            result = load_research_dispositions(
                root,
                market_rows=[],
            )
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["rows"], {})


if __name__ == "__main__":
    unittest.main()
