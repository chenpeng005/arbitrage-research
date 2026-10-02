from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CANONICAL_PATH = (
    "05 套利研究/LOF机会发现/02_数据与监控/"
    "LOF-Research-Disposition-Registry-V0.1.json"
)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _matches(rule: dict[str, Any], market: dict[str, Any]) -> bool:
    match = rule.get("match") or {}
    if not isinstance(match, dict) or not match:
        return False
    return all(
        str(market.get(key) or "") == str(value)
        for key, value in match.items()
    )


def load_research_dispositions(
    data_root: str | Path,
    *,
    market_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    root = Path(data_root)
    deployment = _load_json(root / "deployment_manifest.json")
    if deployment is None:
        return {
            "status": "UNAVAILABLE",
            "knowledge_commit_sha": None,
            "rows": {},
        }

    knowledge_sha = str(
        deployment.get("knowledge_commit_sha") or ""
    ).strip()
    if not knowledge_sha:
        return {
            "status": "UNAVAILABLE",
            "knowledge_commit_sha": None,
            "rows": {},
        }

    path = (
        root
        / "knowledge_snapshots"
        / knowledge_sha
        / "files"
        / CANONICAL_PATH
    )
    payload = _load_json(path)
    if payload is None:
        return {
            "status": "UNAVAILABLE",
            "knowledge_commit_sha": knowledge_sha,
            "rows": {},
        }

    entries = payload.get("entries") or {}
    rules = payload.get("rules") or []
    result: dict[str, dict[str, Any]] = {}

    for market in market_rows:
        code = str(market.get("code") or "").strip()
        if not code:
            continue
        explicit = entries.get(code)
        if isinstance(explicit, dict):
            item = dict(explicit)
            item["source"] = "ENTRY"
            result[code] = item
            continue

        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if not _matches(rule, market):
                continue
            item = {
                key: value
                for key, value in rule.items()
                if key != "match"
            }
            item["source"] = "RULE"
            item["match"] = rule.get("match")
            result[code] = item
            break

    return {
        "status": "PASS",
        "version": payload.get("version"),
        "updated_at": payload.get("updated_at"),
        "knowledge_commit_sha": knowledge_sha,
        "rows": result,
    }
