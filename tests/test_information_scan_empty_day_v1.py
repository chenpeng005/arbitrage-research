from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from runtime.opportunity.incremental_information_scan import (
    scan_daily_relevant_notices,
)
from runtime.opportunity.incremental_storage import initialize_schema


class InformationScanEmptyDayV1Test(unittest.TestCase):
    def test_exact_keyerror_code_is_empty_notice_day(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "runtime.sqlite"
            initialize_schema(db)

            def fetcher(**_: object) -> pd.DataFrame:
                raise KeyError("代码")

            result = scan_daily_relevant_notices(
                target_db=db,
                date="20261003",
                fetcher=fetcher,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["all_notice_count"], 0)
            self.assertEqual(result["relevant_document_count"], 0)
            self.assertTrue(result["source_empty_day_fallback"])

    def test_nonempty_schema_break_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "runtime.sqlite"
            initialize_schema(db)

            def fetcher(**_: object) -> pd.DataFrame:
                return pd.DataFrame([{"公告标题": "x"}])

            with self.assertRaises(RuntimeError):
                scan_daily_relevant_notices(
                    target_db=db,
                    date="20261003",
                    fetcher=fetcher,
                )


if __name__ == "__main__":
    unittest.main()
