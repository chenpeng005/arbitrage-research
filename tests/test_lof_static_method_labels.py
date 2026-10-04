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
            "DISCLOSED_GOLD_EXPOSURE_FX_BRIDGE": "披露黄金暴露+汇率",
            "DISCLOSED_GOLD_EXPOSURE_CNH_FALLBACK_BRIDGE": "披露黄金暴露+离岸人民币回退",
            "DOMESTIC_FUTURES_PREV_SETTLEMENT": "国内期货主连",
        }
        for method, label in expected.items():
            self.assertIn(method, app_js)
            self.assertIn(label, app_js)
        self.assertIn("appendEstimatedNavCell(tr, row)", app_js)
        self.assertIn("last_estimated_nav", app_js)
        self.assertIn("最后可靠", app_js)
        self.assertIn("displayedEstimatedPremium", app_js)

    def test_r2_subclasses_are_visible_and_filterable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_js = (root / "runtime/lof/static/app.js").read_text(encoding="utf-8")
        index_html = (root / "runtime/lof/static/index.html").read_text(encoding="utf-8")
        self.assertIn("估算净值", index_html)
        self.assertIn("估算溢价", index_html)
        self.assertIn('id="lastEstimatedCount"', index_html)
        self.assertIn('id="navFreshness"', index_html)
        self.assertIn("official_nav_lag_label", app_js)
        self.assertIn("nav-lagging", app_js)
        expected = {
            "EQUITY": "R2-A 主动股票",
            "MIXED": "R2-B 混合型",
            "BOND": "R2-C 债券型",
            "FOF": "R2-D FOF",
        }
        for lof_type, label in expected.items():
            self.assertIn(lof_type, app_js)
            self.assertIn(label, app_js)
            self.assertIn(label, index_html)
        self.assertIn("R2-待归类", app_js)
        for value in ["R2A_EQUITY", "R2B_MIXED", "R2C_BOND", "R2D_FOF"]:
            self.assertIn(value, app_js)
            self.assertIn(value, index_html)

if __name__ == "__main__":
    unittest.main()
