import unittest

from runtime.ai_runtime.provider import ProviderResponse
from runtime.intelligence_radar.daily import MeteredProvider


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        return ProviderResponse(
            provider="deepseek",
            model="deepseek-chat",
            request_id=f"r{self.calls}",
            finish_reason="stop",
            structured_output={"ok": True},
            tool_calls=[],
            usage={
                "prompt_tokens": 100 * self.calls,
                "completion_tokens": 10 * self.calls,
                "total_tokens": 110 * self.calls,
                "prompt_cache_hit_tokens": 40 * self.calls,
                "prompt_cache_miss_tokens": 60 * self.calls,
            },
            raw_response={},
        )


class IntelligenceRadarUsageTest(unittest.TestCase):
    def test_metered_provider_accumulates_usage(self) -> None:
        meter = MeteredProvider(FakeProvider())
        meter.complete()
        meter.complete()
        usage = meter.snapshot()
        self.assertTrue(usage["metered"])
        self.assertEqual(usage["provider"], "deepseek")
        self.assertEqual(usage["model"], "deepseek-chat")
        self.assertEqual(usage["request_count"], 2)
        self.assertEqual(usage["prompt_tokens"], 300)
        self.assertEqual(usage["completion_tokens"], 30)
        self.assertEqual(usage["total_tokens"], 330)
        self.assertEqual(usage["cache_hit_tokens"], 120)
        self.assertEqual(usage["cache_miss_tokens"], 180)


if __name__ == "__main__":
    unittest.main()
