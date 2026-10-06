from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch_text(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    referer: str | None = None,
    timeout: int = 20,
    attempts: int = 3,
    base_delay_seconds: float = 0.5,
    headers: dict[str, str] | None = None,
) -> str:
    request_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,text/xml,application/xml,*/*",
    }
    if referer:
        request_headers["Referer"] = referer
    if headers:
        request_headers.update(headers)

    target = url
    if params:
        query = urlencode(params)
        target = f"{url}{'&' if '?' in url else '?'}{query}"

    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            request = Request(target, headers=request_headers)
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                encoding = response.headers.get_content_charset() or "utf-8"
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    return raw.decode("utf-8", errors="replace")
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max(1, attempts):
                raise
            time.sleep(base_delay_seconds * (attempt + 1))

    assert last_exc is not None
    raise last_exc


def parse_json_or_jsonp(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.match(r"^[^(]+\((.*)\)\s*;?$", stripped, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(1))


def fetch_json(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return parse_json_or_jsonp(fetch_text(url, params, **kwargs))
