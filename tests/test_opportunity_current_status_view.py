from __future__ import annotations

import unittest

from runtime.opportunity.view_contract import (
    PATH_LABELS,
    _current_status_text_and_sort,
)


class OpportunityCurrentStatusViewTest(unittest.TestCase):
    def test_maturity_redemption_uses_exact_months(self) -> None:
        path = {
            "path_id": "MATURITY_CASH",
            "current_event_state": "T_LE_1M",
        }
        text, sort_key = _current_status_text_and_sort(
            path,
            market_cutoff="2026-09-24",
            maturity_contract_fact={"contract_maturity_date": "2026-10-15"},
        )
        self.assertEqual(PATH_LABELS["MATURITY_CASH"], "到期赎回")
        self.assertEqual(text, "距到期赎回 0.7个月")
        self.assertEqual(sort_key, 0.7)

    def test_put_uses_exact_months_until_put_window(self) -> None:
        path = {
            "path_id": "PUT",
            "current_event_state": "BEFORE_PUT_WINDOW",
            "path_result": {},
        }
        text, sort_key = _current_status_text_and_sort(
            path,
            market_cutoff="2026-09-24",
            maturity_contract_fact={"contract_maturity_date": "2029-01-06"},
        )
        self.assertEqual(text, "距普通回售期 3.4个月")
        self.assertEqual(sort_key, 3.4)


if __name__ == "__main__":
    unittest.main()
