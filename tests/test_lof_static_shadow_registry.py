import unittest
from pathlib import Path


APP_JS = Path("runtime/lof/static/app.js")
INDEX = Path("runtime/lof/static/index.html")


class ShadowRegistryStaticTests(unittest.TestCase):
    def test_homepage_loads_shadow_registry(self):
        app = APP_JS.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn("/api/lof/shadow-registry", app)
        self.assertIn("state.shadowRegistry[row.code]", app)
        self.assertIn('id="shadowCount"', index)
        self.assertIn('id="deferredCount"', index)
        self.assertIn("已研究·暂缓", app)
        self.assertIn('data-key="_research_state"', index)


if __name__ == "__main__":
    unittest.main()
