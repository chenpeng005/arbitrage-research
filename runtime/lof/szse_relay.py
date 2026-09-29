from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import time
from typing import Any
from urllib.request import Request, urlopen


RELAY_VERSION = "lof-szse-official-relay-v1"
DEFAULT_SZSE_RELAY_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "chenpeng005/arbitrage-research/lof-data-relay/lof_relay"
)
DEFAULT_RELAY_MAX_AGE_SECONDS = 48 * 60 * 60


@dataclass(frozen=True)
class SzseRelayBundle:
    base_url: str
    manifest: dict[str, Any]
    universe: dict[str, Any]
    nav: dict[str, Any]
    manifest_sha256: str

    @property
    def fetched_at(self) -> str | None:
        value = self.manifest.get("fetched_at")
        return str(value) if value else None


def _fetch_bytes(
    url: str,
    *,
    timeout: int,
    attempts: int = 3,
    base_delay_seconds: float = 0.25,
) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max(1, attempts):
                raise
            if base_delay_seconds > 0:
                time.sleep(base_delay_seconds * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _join(base_url: str, filename: str) -> str:
    return base_url.rstrip("/") + "/" + filename


def _json_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


@lru_cache(maxsize=4)
def fetch_szse_relay_bundle(
    base_url: str,
    *,
    timeout: int = 20,
) -> SzseRelayBundle:
    manifest_bytes = _fetch_bytes(
        _join(base_url, "manifest.json"),
        timeout=timeout,
    )
    manifest = _json_bytes(manifest_bytes, label="relay manifest")

    if manifest.get("relay_version") != RELAY_VERSION:
        raise ValueError("unsupported SZSE relay version")
    if manifest.get("source") != "SZSE_OFFICIAL":
        raise ValueError("SZSE relay source provenance mismatch")

    files = manifest.get("files") or {}
    universe_meta = files.get("universe") or {}
    nav_meta = files.get("nav") or {}
    universe_name = str(universe_meta.get("filename") or "szse_universe.json")
    nav_name = str(nav_meta.get("filename") or "szse_nav.json")

    universe_bytes = _fetch_bytes(
        _join(base_url, universe_name),
        timeout=timeout,
    )
    nav_bytes = _fetch_bytes(
        _join(base_url, nav_name),
        timeout=timeout,
    )

    universe_hash = hashlib.sha256(universe_bytes).hexdigest()
    nav_hash = hashlib.sha256(nav_bytes).hexdigest()

    if universe_hash != str(universe_meta.get("sha256") or ""):
        raise ValueError("SZSE relay universe hash mismatch")
    if nav_hash != str(nav_meta.get("sha256") or ""):
        raise ValueError("SZSE relay NAV hash mismatch")

    universe = _json_bytes(universe_bytes, label="relay universe")
    nav = _json_bytes(nav_bytes, label="relay NAV")

    universe_rows = universe.get("rows") or []
    nav_rows = nav.get("rows") or []
    if len(universe_rows) != int(manifest.get("universe_count") or -1):
        raise ValueError("SZSE relay universe count mismatch")
    if len(nav_rows) != int(manifest.get("nav_count") or -1):
        raise ValueError("SZSE relay NAV count mismatch")

    if len(universe_rows) < 200:
        raise ValueError("SZSE relay universe sanity floor failed")
    if len(nav_rows) < 200:
        raise ValueError("SZSE relay NAV sanity floor failed")

    return SzseRelayBundle(
        base_url=base_url,
        manifest=manifest,
        universe=universe,
        nav=nav,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def relay_fetched_at(bundle: SzseRelayBundle) -> datetime:
    raw = bundle.fetched_at
    if not raw:
        raise ValueError("SZSE relay manifest missing fetched_at")
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("SZSE relay fetched_at must include timezone")
    return value


def validate_szse_relay_freshness(
    bundle: SzseRelayBundle,
    *,
    as_of: datetime,
    max_age_seconds: int = DEFAULT_RELAY_MAX_AGE_SECONDS,
) -> int:
    if as_of.tzinfo is None:
        raise ValueError("relay freshness as_of must include timezone")
    fetched_at = relay_fetched_at(bundle)
    age_seconds = int(
        (as_of.astimezone(timezone.utc) - fetched_at.astimezone(timezone.utc))
        .total_seconds()
    )
    if age_seconds < -300:
        raise ValueError("SZSE relay fetched_at is unexpectedly in the future")
    if age_seconds > max_age_seconds:
        raise ValueError(
            f"SZSE relay is stale: age_seconds={age_seconds} "
            f"max={max_age_seconds}"
        )
    return max(0, age_seconds)
