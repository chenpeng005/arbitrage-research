from pathlib import Path
import unittest

class LofStaticMethodLabelsTest(unittest.TestCase):
    def test_estimated_nav_method_labels_are_user_readable(self) -> None:
        app_js = (
            Path(__file__).resolve().parents[1]
            / "runtime"
            / "lof"
            / "static"
            / "app.js"
        ).read_text(encoding="utf-8")
        expected = {
            "INDEX_PROXY_PREV_CLOSE": "指数直连",
            "CSI_COMPONENT_WEIGHT_PREV_CLOSE": "成分重建",
            "TARGET_ETF_PREV_CLOSE": "目标ETF代理",
            "MULTIDAY_PROXY_FX_BRIDGE": "跨日指数+汇率",
            "US_FUTURES_FX_BRIDGE": "美股期货桥接",
            "US_LAST_CLOSE_FX_BRIDGE": "美股隔夜收盘",
            "COMMODITY_FX_BRIDGE": "商品+汇率",
            "DOMESTIC_FUTURES_PREV_SETTLEMENT": "国内期货主连",
        }
        for method, label in expected.items():
            self.assertIn(method, app_js)
            self.assertIn(label, app_js)
        self.assertIn("appendEstimatedNavCell(tr, row)", app_js)

if __name__ == "__main__":
    unittest.main()
