"""Deterministic email outbox for opportunity reminders.

Outbox is intentionally separate from IN_APP delivery. Building an email batch never
acknowledges or mutates Opportunity Runtime notification status.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTBOX_VERSION = "opportunity-email-outbox-v1"
DEFAULT_RECIPIENT = "peng55151@gmail.com"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _batch_id(group_ids: list[str], recipient: str) -> str:
    canonical = recipient + "|" + "|".join(sorted(group_ids))
    return "EML_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _level_cn(level: Any) -> str:
    return {"IMMEDIATE": "立即提醒", "DAILY_DIGEST": "每日汇总"}.get(
        str(level or ""), "机会提醒"
    )


def _render_item(item: dict[str, Any]) -> str:
    p = item.get("presentation") if isinstance(item.get("presentation"), dict) else {}
    lines = [
        f"【{item.get('bond_name') or '可转债'}（{item.get('bond_code') or '—'}）】",
        f"提醒级别：{_level_cn(item.get('level'))}",
        f"事项：{p.get('title') or '机会状态发生变化'}",
        f"变化：{p.get('summary') or '—'}",
        f"为什么提醒：{p.get('why') or '—'}",
        f"主要风险：{p.get('risk') or '—'}",
        f"下一步观察：{p.get('next_watch') or '—'}",
        f"产生时间：{item.get('created_at') or '—'}",
    ]
    return "\n".join(lines)


def load_outbox(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": OUTBOX_VERSION, "batches": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("outbox root must be object")
    data.setdefault("version", OUTBOX_VERSION)
    data.setdefault("batches", [])
    return data


def save_outbox(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_pending_batch(
    *,
    feed: dict[str, Any],
    sent_notification_group_ids: set[str],
    outbox_path: Path,
    recipient: str = DEFAULT_RECIPIENT,
    created_at: str | None = None,
) -> dict[str, Any] | None:
    items = feed.get("items") if isinstance(feed, dict) else []
    items = items if isinstance(items, list) else []
    fresh = [
        x for x in items
        if isinstance(x, dict)
        and x.get("notification_group_id")
        and str(x["notification_group_id"]) not in sent_notification_group_ids
    ]
    if not fresh:
        return None

    group_ids = sorted({str(x["notification_group_id"]) for x in fresh})
    outbox = load_outbox(outbox_path)
    existing = {
        str(b.get("batch_id")): b
        for b in outbox["batches"]
        if isinstance(b, dict) and b.get("batch_id")
    }
    batch_id = _batch_id(group_ids, recipient)
    if batch_id in existing:
        return existing[batch_id]

    ts = created_at or _now()
    subject_time = ts[:16].replace("T", " ")
    batch = {
        "batch_id": batch_id,
        "status": "PENDING",
        "recipient": recipient,
        "subject": f"可转债机会提醒｜{subject_time}",
        "body": "\n\n".join(_render_item(x) for x in fresh),
        "notification_group_ids": group_ids,
        "created_at": ts,
        "sent_at": None,
        "gmail_message_id": None,
        "last_error": None,
    }
    outbox["batches"].append(batch)
    outbox["updated_at"] = ts
    save_outbox(outbox_path, outbox)
    return batch


def mark_batch_sent(
    *, outbox_path: Path, batch_id: str, gmail_message_id: str, sent_at: str | None = None
) -> dict[str, Any]:
    outbox = load_outbox(outbox_path)
    ts = sent_at or _now()
    for batch in outbox["batches"]:
        if isinstance(batch, dict) and batch.get("batch_id") == batch_id:
            batch["status"] = "SENT"
            batch["sent_at"] = ts
            batch["gmail_message_id"] = gmail_message_id
            batch["last_error"] = None
            outbox["updated_at"] = ts
            save_outbox(outbox_path, outbox)
            return batch
    raise KeyError(f"outbox batch not found: {batch_id}")
