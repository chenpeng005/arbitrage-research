import unittest
from pathlib import Path


APP_JS = Path("runtime/lof/static/app.js")


class R2CProfileStaticTests(unittest.TestCase):
    def test_r2c_profile_is_runtime_backed_not_hardcoded(self):
        text = APP_JS.read_text(encoding="utf-8")
        self.assertIn("/api/lof/r2c-t1-profile", text)
        self.assertIn("state.r2cProfiles[row.code]", text)
        self.assertNotIn("const r2cT1Profiles = {", text)
        self.assertIn('rawType.includes("固收")', text)
        self.assertNotIn('row.resolver_class !== "R2_DOMESTIC_OTHER"', text)


if __name__ == "__main__":
    unittest.main()
