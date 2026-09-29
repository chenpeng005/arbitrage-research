from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch_json_with_retry(
    url: str,
    params: dict[str, Any],
    *,
    referer: str | None = None,
    timeout: int = 15,
    attempts: int = 3,
    base_delay_seconds: float = 0.25,
    headers: dict[str, str] | None = None,
) -> Any:
    """Fetch JSON with small deterministic retry/backoff.

    Intended for low-frequency official/metadata endpoints that occasionally
    reset connections. Final failure is re-raised; callers still fail closed.
    """
    max_attempts = max(1, int(attempts))
    query = urlencode(params)

    request_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }
    if referer:
        request_headers["Referer"] = referer
    if headers:
        request_headers.update(headers)

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            request = Request(
                f"{url}?{query}",
                headers=request_headers,
            )
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts:
                raise
            if base_delay_seconds > 0:
                time.sleep(base_delay_seconds * (attempt + 1))

    assert last_exc is not None
    raise last_exc
