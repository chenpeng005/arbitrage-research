from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.opportunity.incremental_change_notification import deep_research_change
from runtime.opportunity.incremental_event_ledger import (
    _insert_change,
    _persist_notification_groups,
)
from runtime.opportunity.incremental_storage import connect, initialize_schema


class InformationChangeSourceFkV1Test(unittest.TestCase):
    def test_deep_research_change_persists_and_links_notification(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "runtime.sqlite"
            initialize_schema(db)
            conn = connect(db)
            try:
                now = "2026-09-30T00:00:00+00:00"
                conn.execute(
                    """INSERT INTO bond_master
                       (bond_code,bond_name,stock_code,active,first_seen_at,updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    ("123456", "测试转债", "600000", 1, now, now),
                )
                conn.execute(
                    """INSERT INTO event_family
                       (event_family_id,bond_code,event_family,latest_confirmed_version,
                        confirmed_watermark,semantic_candidate_watermark,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        "123456:REVISION:FINAL_K_CHANGE",
                        "123456",
                        "REVISION:FINAL_K_CHANGE",
                        1,
                        "EWM_TEST",
                        "ECM_EMPTY",
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO event_update
                       (event_update_id,event_family_id,confirmation_status,event_version,
                        candidate_version,occurred_at,materiality_status,payload_json,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        "EVU_TEST",
                        "123456:REVISION:FINAL_K_CHANGE",
                        "CONFIRMED",
                        1,
                        None,
                        "2026-09-30",
                        "ROUTABLE",
                        "{}",
                        now,
                    ),
                )

                change = deep_research_change(
                    bond_code="123456",
                    bond_name="测试转债",
                    path_id="DOWNWARD_REVISION",
                    event_update_id="EVU_TEST",
                    event_family="REVISION:FINAL_K_CHANGE",
                    reason="TEST",
                )
                self.assertEqual(change["change_source"], "RESEARCH")

                inserted = _insert_change(conn, change, now)
                self.assertEqual(inserted, 1)
                groups, links = _persist_notification_groups(conn, [change], now)
                self.assertEqual(groups, 1)
                self.assertEqual(links, 1)
                conn.commit()

                row = conn.execute(
                    "SELECT source_type FROM change_ledger WHERE change_id=?",
                    (change["change_id"],),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["source_type"], "RESEARCH")
                linked = conn.execute(
                    "SELECT COUNT(*) n FROM notification_change_link"
                ).fetchone()["n"]
                self.assertEqual(linked, 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
