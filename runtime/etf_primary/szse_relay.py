from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


RELAY_VERSION = "etf-primary-official-relay-v2"
LEGACY_RELAY_VERSION = "etf-primary-szse-official-relay-v1"
SUPPORTED_RELAY_VERSIONS = {RELAY_VERSION, LEGACY_RELAY_VERSION}
DEFAULT_RELAY_REF = "etf-primary-data-relay"
DEFAULT_RELAY_DIR = "etf_primary_relay"
DEFAULT_RELAY_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "chenpeng005/arbitrage-research/etf-primary-data-relay/etf_primary_relay"
)
DEFAULT_REPO_API_BASE_URL = (
    "https://api.github.com/repos/chenpeng005/arbitrage-research"
)
DEFAULT_CONTENTS_BASE_URL = DEFAULT_REPO_API_BASE_URL + "/contents/" + DEFAULT_RELAY_DIR
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class SzseEtfRelayBundle:
    # Name retained for compatibility with the original SZSE-only relay caller.
    # V2 is a combined SSE+SZSE official relay and also exposes the normalized
    # full-market PCF snapshot when available.
    base_url: str
    manifest: dict[str, Any]
    universe: dict[str, Any]
    manifest_sha256: str
    full_universe: dict[str, Any] | None = None
    pcf_snapshot: dict[str, Any] | None = None

    @property
    def fetched_at(self) -> str | None:
        value = self.manifest.get("fetched_at")
        return str(value) if value else None

    @property
    def target_trade_date(self) -> str | None:
        value = self.manifest.get("target_trade_date")
        return str(value) if value else None


def _fetch_bytes(url: str, *, timeout: int, attempts: int = 3) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "ETF-Primary-Runtime",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max(1, attempts):
                raise
            time.sleep(0.25 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _resolve_default_ref(*, timeout: int) -> str:
    url = DEFAULT_REPO_API_BASE_URL + "/git/ref/heads/" + quote(DEFAULT_RELAY_REF)
    request = Request(
        url,
        headers={
            "User-Agent": "ETF-Primary-Runtime",
            "Accept": "application/vnd.github+json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    sha = str((payload.get("object") or {}).get("sha") or "").strip()
    if len(sha) != 40:
        raise ValueError("ETF primary relay branch head is not a commit SHA")
    return sha


def _fetch_contents_file(filename: str, *, timeout: int, ref: str) -> bytes:
    url = DEFAULT_CONTENTS_BASE_URL + "/" + quote(filename) + "?ref=" + quote(ref)
    request = Request(
        url,
        headers={
            "User-Agent": "ETF-Primary-Runtime",
            "Accept": "application/vnd.github+json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("encoding") != "base64":
        raise ValueError("ETF primary relay contents encoding is not base64")
    content = str(payload.get("content") or "").replace("\n", "")
    if not content:
        raise ValueError("ETF primary relay contents payload is empty")
    return base64.b64decode(content)


def _fetch_relay_file(
    base_url: str,
    filename: str,
    *,
    timeout: int,
    pinned_ref: str | None = None,
) -> bytes:
    if base_url.rstrip("/") != DEFAULT_RELAY_BASE_URL.rstrip("/"):
        return _fetch_bytes(base_url.rstrip("/") + "/" + filename, timeout=timeout)

    ref = pinned_ref or _resolve_default_ref(timeout=max(timeout, 20))
    try:
        return _fetch_contents_file(filename, timeout=max(timeout, 20), ref=ref)
    except Exception:
        immutable_raw_url = (
            "https://raw.githubusercontent.com/chenpeng005/arbitrage-research/"
            + ref
            + "/"
            + DEFAULT_RELAY_DIR
            + "/"
            + filename
        )
        return _fetch_bytes(immutable_raw_url, timeout=max(timeout, 20), attempts=2)


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _v2_file_hash(manifest: dict[str, Any], filename: str) -> str:
    meta = (manifest.get("files") or {}).get(filename) or {}
    return str(meta.get("sha256") or "")


def _fetch_verified_json(
    *,
    base_url: str,
    filename: str,
    expected_hash: str,
    timeout: int,
    pinned_ref: str | None,
    label: str,
) -> dict[str, Any]:
    raw = _fetch_relay_file(
        base_url,
        filename,
        timeout=timeout,
        pinned_ref=pinned_ref,
    )
    actual_hash = hashlib.sha256(raw).hexdigest()
    if expected_hash and actual_hash != expected_hash:
        raise ValueError(f"ETF primary relay {label} hash mismatch")
    return _json_object(raw, label=f"relay {label}")


def fetch_szse_etf_relay_bundle(
    base_url: str = DEFAULT_RELAY_BASE_URL,
    *,
    timeout: int = 20,
) -> SzseEtfRelayBundle:
    pinned_ref = (
        _resolve_default_ref(timeout=max(timeout, 20))
        if base_url.rstrip("/") == DEFAULT_RELAY_BASE_URL.rstrip("/")
        else None
    )
    manifest_bytes = _fetch_relay_file(
        base_url,
        "manifest.json",
        timeout=timeout,
        pinned_ref=pinned_ref,
    )
    manifest = _json_object(manifest_bytes, label="relay manifest")
    version = str(manifest.get("relay_version") or "")
    if version not in SUPPORTED_RELAY_VERSIONS:
        raise ValueError("unsupported ETF primary relay version")

    if version == LEGACY_RELAY_VERSION:
        if manifest.get("source") != "SZSE_OFFICIAL":
            raise ValueError("ETF primary SZSE relay provenance mismatch")
        file_meta = (manifest.get("files") or {}).get("universe") or {}
        filename = str(file_meta.get("filename") or "szse_etf_universe.json")
        universe = _fetch_verified_json(
            base_url=base_url,
            filename=filename,
            expected_hash=str(file_meta.get("sha256") or ""),
            timeout=timeout,
            pinned_ref=pinned_ref,
            label="universe",
        )
        rows = universe.get("rows") or []
        if len(rows) != int(manifest.get("universe_count") or -1):
            raise ValueError("ETF primary SZSE relay universe count mismatch")
        full_universe = None
        pcf_snapshot = None
    else:
        if manifest.get("source") != "SSE_OFFICIAL+SZSE_OFFICIAL":
            raise ValueError("ETF primary combined relay provenance mismatch")
        universe = _fetch_verified_json(
            base_url=base_url,
            filename="szse_etf_universe.json",
            expected_hash=_v2_file_hash(manifest, "szse_etf_universe.json"),
            timeout=timeout,
            pinned_ref=pinned_ref,
            label="SZSE universe",
        )
        full_universe = _fetch_verified_json(
            base_url=base_url,
            filename="full_universe.json",
            expected_hash=_v2_file_hash(manifest, "full_universe.json"),
            timeout=timeout,
            pinned_ref=pinned_ref,
            label="full universe",
        )
        pcf_snapshot = _fetch_verified_json(
            base_url=base_url,
            filename="pcf_snapshot.json",
            expected_hash=_v2_file_hash(manifest, "pcf_snapshot.json"),
            timeout=timeout,
            pinned_ref=pinned_ref,
            label="PCF snapshot",
        )
        rows = universe.get("rows") or []
        full_rows = full_universe.get("rows") or []
        if len(full_rows) != int(manifest.get("full_universe_count") or -1):
            raise ValueError("ETF primary relay full-universe count mismatch")
        if int(pcf_snapshot.get("pcf_found_count") or -1) != int(
            manifest.get("pcf_found_count") or -2
        ):
            raise ValueError("ETF primary relay PCF count mismatch")
        if str(pcf_snapshot.get("target_trade_date") or "") != str(
            manifest.get("target_trade_date") or ""
        ):
            raise ValueError("ETF primary relay target trade date mismatch")

    if len(rows) < 400:
        raise ValueError("ETF primary SZSE relay universe sanity floor failed")

    return SzseEtfRelayBundle(
        base_url=base_url,
        manifest=manifest,
        universe=universe,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        full_universe=full_universe,
        pcf_snapshot=pcf_snapshot,
    )


def fetch_etf_primary_relay_bundle(
    base_url: str = DEFAULT_RELAY_BASE_URL,
    *,
    timeout: int = 20,
) -> SzseEtfRelayBundle:
    """Canonical name for the V2 combined official relay loader."""
    return fetch_szse_etf_relay_bundle(base_url=base_url, timeout=timeout)


def relay_fetched_at(bundle: SzseEtfRelayBundle) -> datetime:
    raw = bundle.fetched_at
    if not raw:
        raise ValueError("ETF primary relay manifest missing fetched_at")
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("ETF primary relay fetched_at must include timezone")
    return value


def validate_relay_freshness(
    bundle: SzseEtfRelayBundle,
    *,
    as_of: datetime,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> int:
    if as_of.tzinfo is None:
        raise ValueError("relay freshness as_of must include timezone")
    age_seconds = int(
        (
            as_of.astimezone(timezone.utc)
            - relay_fetched_at(bundle).astimezone(timezone.utc)
        ).total_seconds()
    )
    if age_seconds < -300:
        raise ValueError("ETF primary relay timestamp is unexpectedly in the future")
    if age_seconds > max_age_seconds:
        raise ValueError(
            f"ETF primary relay is stale: age_seconds={age_seconds} max={max_age_seconds}"
        )
    return max(0, age_seconds)
