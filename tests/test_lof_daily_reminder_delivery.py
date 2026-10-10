from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from runtime.lof.daily_reminder import LofDailyReminderStore, main


class LofDailyReminderDeliveryTest(unittest.TestCase):
    def test_skip_if_missing_is_safe_for_non_trading_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with redirect_stdout(output):
                rc = main([
                    "--data-root",
                    tmp,
                    "--date",
                    "2026-10-10",
                    "--skip-if-missing",
                ])
            self.assertEqual(rc, 0)
            self.assertIn("SKIP_NO_CANDIDATE", output.getvalue())
            self.assertFalse(
                (Path(tmp) / "daily_reminder" / "latest_daily_reminder.json").exists()
            )

    def test_specific_and_latest_daily_reminder_loaders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LofDailyReminderStore(tmp)
            payload = '{"contract_version":"LOF_DAILY_REMINDER_V0_1","trade_date":"2026-10-09"}\n'
            daily = Path(tmp) / "daily_reminder" / "daily" / "2026-10-09.json"
            latest = Path(tmp) / "daily_reminder" / "latest_daily_reminder.json"
            daily.parent.mkdir(parents=True, exist_ok=True)
            daily.write_text(payload, encoding="utf-8")
            latest.write_text(payload, encoding="utf-8")
            self.assertEqual(store.load_daily_reminder("2026-10-09")["trade_date"], "2026-10-09")
            self.assertIsNone(store.load_daily_reminder("2026-10-10"))
            self.assertEqual(store.load_latest_daily_reminder()["trade_date"], "2026-10-09")

    def test_page_entry_api_and_2055_timer_are_versioned(self) -> None:
        root = Path(__file__).resolve().parents[1]
        index_html = (root / "runtime/lof/static/index.html").read_text(encoding="utf-8")
        reminder_html = (root / "runtime/lof/static/reminder.html").read_text(encoding="utf-8")
        reminder_js = (root / "runtime/lof/static/reminder.js").read_text(encoding="utf-8")
        web_app = (root / "runtime/lof/web_app.py").read_text(encoding="utf-8")
        timer = (root / "deploy/systemd/lof-daily-reminder.timer").read_text(encoding="utf-8")
        service = (root / "deploy/systemd/lof-daily-reminder.service").read_text(encoding="utf-8")

        self.assertIn('href="reminder"', index_html)
        self.assertIn("今日提醒", index_html)
        self.assertIn('href="./"', reminder_html)
        self.assertIn('id="statusChanges"', reminder_html)
        self.assertIn('id="limitChanges"', reminder_html)
        self.assertIn("officialPremium", reminder_js)
        self.assertIn("estimatedDiscount", reminder_js)
        self.assertIn('@app.get("/reminder")', web_app)
        self.assertIn('@app.get("/api/lof/daily-reminder")', web_app)
        self.assertIn('ZoneInfo("Asia/Shanghai")', web_app)
        self.assertIn("OnCalendar=*-*-* 20:55:00 Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("--skip-if-missing", service)


if __name__ == "__main__":
    unittest.main()
