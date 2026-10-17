from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "etf_primary_shadow" / "wechat_snapshot.py"
spec = importlib.util.spec_from_file_location("etf_wechat_snapshot", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
EtfWechatSnapshotStore = module.EtfWechatSnapshotStore
build_etf_wechat_snapshot = module.build_etf_wechat_snapshot

def _inputs():
    monitor_rows=[]; premium_rows=[]; comparison_rows=[]
    for idx in range(1,8):
        code=f"15950{idx}"
        monitor_rows.append({"exchange":"SZSE","code":code,"name":f"ETF-{idx}","pcf_status":"FRESH","creation_allowed":True,"pcf_trade_date":"2026-10-16","total_baskets":idx*10,"market_capacity_kind":"CUMULATIVE"})
        premium_rows.append({"exchange":"SZSE","code":code,"name":f"ETF-{idx}","close":1.0+idx/100,"close_date":"20261016","formal_nav":0.9,"nav_date":"20261015","premium_pct":6.0+idx})
        comparison_rows.append({"code":code,"previous_total_baskets":idx*9,"current_total_baskets":idx*10,"basket_delta":idx,"changed":idx==7})
    return ({"schema_version":2,"target_trade_date":"2026-10-16","rows":monitor_rows},{"schema_version":1,"fetched_at":"2026-10-16T16:21:42+08:00","rows":premium_rows},{"schema_version":1,"rows":comparison_rows})

class EtfWechatSnapshotTest(unittest.TestCase):
    def test_top_five_sort_by_capacity_then_premium(self):
        monitor,premium,comparison=_inputs(); payload=build_etf_wechat_snapshot(monitor,premium,comparison)
        self.assertEqual(payload["source_owner"],"ETF_RUNTIME"); self.assertEqual(payload["status"],"READY")
        self.assertEqual([r["current_baskets"] for r in payload["rows"]],[70,60,50,40,30]); self.assertTrue(all(r["official_premium_rate"]>=5 for r in payload["rows"])); self.assertEqual(payload["content_cutoff_at"],"2026-10-16T16:21:42+08:00"); self.assertEqual(payload["delta_items"][0]["code"],"159507")
    def test_closed_creation_and_low_premium_are_excluded(self):
        monitor,premium,comparison=_inputs(); monitor["rows"][6]["creation_allowed"]=False; premium["rows"][5]["premium_pct"]=1.0; payload=build_etf_wechat_snapshot(monitor,premium,comparison)
        self.assertEqual(payload["status"],"READY"); self.assertNotIn("159507",[r["code"] for r in payload["rows"]]); self.assertNotIn("159506",[r["code"] for r in payload["rows"]])
    def test_cutoff_safe_archive(self):
        monitor,premium,comparison=_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            store=EtfWechatSnapshotStore(tmp); early=build_etf_wechat_snapshot(monitor,premium,comparison); store.persist(early); premium2=dict(premium); premium2["fetched_at"]="2026-10-16T23:00:00+08:00"; store.persist(build_etf_wechat_snapshot(monitor,premium2,comparison)); selected=store.load_latest_at_or_before("2026-10-16T21:56:29+08:00")
            self.assertEqual(selected["content_cutoff_at"],early["content_cutoff_at"]); self.assertLess((Path(tmp)/"latest.json").stat().st_size,100_000)
if __name__=="__main__": unittest.main()
