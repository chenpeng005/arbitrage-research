from __future__ import annotations

import unittest

from runtime.opportunity.incremental_information_scan import extract_published_at


class InformationScanPublishedAtV1Test(unittest.TestCase):
    def test_eastmoney_id_yields_minute_level_shanghai_time(self) -> None:
        url = (
            "https://data.eastmoney.com/notices/detail/601313/"
            "AN202610201830722252.html"
        )
        self.assertEqual(
            extract_published_at(url),
            "2026-10-20T18:30:00+08:00",
        )

    def test_midnight_and_early_morning_are_valid(self) -> None:
        self.assertEqual(
            extract_published_at("x/AN202610210005000001.html"),
            "2026-10-21T00:05:00+08:00",
        )

    def test_unparseable_inputs_degrade_to_none(self) -> None:
        self.assertIsNone(extract_published_at(""))
        self.assertIsNone(extract_published_at(None))  # type: ignore[arg-type]
        self.assertIsNone(extract_published_at("https://example.com/notice/123"))
        # Invalid calendar values must not raise.
        self.assertIsNone(extract_published_at("x/AN202613401830722252.html"))
        self.assertIsNone(extract_published_at("x/AN202610202530722252.html"))


if __name__ == "__main__":
    unittest.main()
