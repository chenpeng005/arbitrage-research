from __future__ import annotations

import json
import unittest
from http.client import RemoteDisconnected
from unittest.mock import patch

from runtime.lof.http_json import fetch_json_with_retry


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload


class LofHttpJsonRetryTest(unittest.TestCase):
    @patch("runtime.lof.http_json.time.sleep")
    @patch("runtime.lof.http_json.urlopen")
    def test_remote_disconnect_retries_then_succeeds(
        self,
        urlopen_mock,
        sleep_mock,
    ) -> None:
        urlopen_mock.side_effect = [
            RemoteDisconnected("remote closed"),
            _Response({"ok": True}),
        ]

        result = fetch_json_with_retry(
            "https://example.com/api",
            {"x": 1},
            referer="https://example.com/",
            timeout=5,
            attempts=3,
            base_delay_seconds=0.25,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.25)

    @patch("runtime.lof.http_json.time.sleep")
    @patch("runtime.lof.http_json.urlopen")
    def test_final_failure_is_re_raised(
        self,
        urlopen_mock,
        sleep_mock,
    ) -> None:
        urlopen_mock.side_effect = RemoteDisconnected("remote closed")

        with self.assertRaises(RemoteDisconnected):
            fetch_json_with_retry(
                "https://example.com/api",
                {},
                timeout=5,
                attempts=3,
                base_delay_seconds=0.25,
            )

        self.assertEqual(urlopen_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
