"""In-app notification delivery for Incremental Runtime."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage import connect

DELIVERY_VERSION = "incremental-notification-delivery-v1"
IN_APP_CHANNEL = "IN_APP"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delivery_id(group_id: str, channel: str) -> str:
    raw = f"{group_id}|{channel}"
    return "NDL_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def ensure_in_app_deliveries(*, target_db: Path) -> dict[str, Any]:
    conn = connect(target_db)
    now = _now()
    inserted = 0
    try:
        rows = list(
            conn.execute(
                """SELECT notification_group_id
                   FROM notification_group
                   WHERE status='PENDING'
                   ORDER BY created_at, notification_group_id"""
            )
        )
        for row in rows:
            group_id = str(row["notification_group_id"])
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO notification_delivery
                   (delivery_id,notification_group_id,channel,status,
                    attempted_at,sent_at,error_text)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    _delivery_id(group_id, IN_APP_CHANNEL),
                    group_id,
                    IN_APP_CHANNEL,
                    "PENDING",
                    None,
                    None,
                    None,
                ),
            )
            inserted += conn.total_changes - before
        conn.commit()
        return {
            "delivery_version": DELIVERY_VERSION,
            "status": "PASS",
            "pending_group_count": len(rows),
            "inserted_delivery_count": inserted,
            "checked_at": now,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _decode_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return value


def build_notification_feed(
    *,
    target_db: Path,
    include_sent: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    ensure_in_app_deliveries(target_db=target_db)
    conn = connect(target_db)
    try:
        statuses = ["PENDING", "SENT"] if include_sent else ["PENDING"]
        placeholders = ",".join("?" for _ in statuses)
        rows = list(
            conn.execute(
                f"""SELECT ng.notification_group_id,ng.group_key,ng.bond_code,
                           b.bond_name,ng.level,ng.status,ng.created_at,ng.sent_at,
                           nd.delivery_id,nd.status delivery_status,nd.sent_at delivery_sent_at
                    FROM notification_group ng
                    JOIN bond_master b ON b.bond_code=ng.bond_code
                    LEFT JOIN notification_delivery nd
                      ON nd.notification_group_id=ng.notification_group_id
                     AND nd.channel=?
                    WHERE ng.status IN ({placeholders})
                    ORDER BY CASE ng.level WHEN 'IMMEDIATE' THEN 0 ELSE 1 END,
                             ng.created_at DESC, ng.notification_group_id
                    LIMIT ?""",
                (IN_APP_CHANNEL, *statuses, max(1, int(limit))),
            )
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            changes = [
                {
                    "change_id": x["change_id"],
                    "scope_type": x["scope_type"],
                    "scope_id": x["scope_id"],
                    "change_type": x["change_type"],
                    "impact": x["impact"],
                    "research_action": x["research_action"],
                    "notification_level": x["notification_level"],
                    "previous": _decode_json(x["previous_json"]),
                    "current": _decode_json(x["current_json"]),
                    "detected_at": x["detected_at"],
                }
                for x in conn.execute(
                    """SELECT cl.change_id,cl.scope_type,cl.scope_id,cl.change_type,
                              cl.impact,cl.research_action,cl.notification_level,
                              cl.previous_json,cl.current_json,cl.detected_at
                       FROM notification_change_link ncl
                       JOIN change_ledger cl ON cl.change_id=ncl.change_id
                       WHERE ncl.notification_group_id=?
                       ORDER BY cl.detected_at,cl.change_id""",
                    (row["notification_group_id"],),
                )
            ]
            items.append(
                {
                    "notification_group_id": row["notification_group_id"],
                    "group_key": row["group_key"],
                    "bond_code": row["bond_code"],
                    "bond_name": row["bond_name"],
                    "level": row["level"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "sent_at": row["sent_at"],
                    "delivery": {
                        "channel": IN_APP_CHANNEL,
                        "delivery_id": row["delivery_id"],
                        "status": row["delivery_status"] or "PENDING",
                        "sent_at": row["delivery_sent_at"],
                    },
                    "change_count": len(changes),
                    "changes": changes,
                }
            )

        counts = {
            str(row["level"]): int(row["n"])
            for row in conn.execute(
                """SELECT level,COUNT(*) n FROM notification_group
                   WHERE status='PENDING' GROUP BY level"""
            )
        }
        return {
            "delivery_version": DELIVERY_VERSION,
            "status": "PASS",
            "channel": IN_APP_CHANNEL,
            "pending_count": sum(counts.values()),
            "pending_by_level": {
                "IMMEDIATE": counts.get("IMMEDIATE", 0),
                "DAILY_DIGEST": counts.get("DAILY_DIGEST", 0),
            },
            "item_count": len(items),
            "items": items,
        }
    finally:
        conn.close()


def acknowledge_notification(
    *,
    target_db: Path,
    notification_group_id: str,
) -> dict[str, Any]:
    ensure_in_app_deliveries(target_db=target_db)
    conn = connect(target_db)
    now = _now()
    try:
        group = conn.execute(
            """SELECT notification_group_id,status
               FROM notification_group WHERE notification_group_id=?""",
            (notification_group_id,),
        ).fetchone()
        if group is None:
            raise KeyError(f"notification group not found: {notification_group_id}")

        delivery_id = _delivery_id(notification_group_id, IN_APP_CHANNEL)
        conn.execute(
            """UPDATE notification_delivery
               SET status='SENT',attempted_at=?,sent_at=?,error_text=NULL
               WHERE delivery_id=?""",
            (now, now, delivery_id),
        )

        outstanding = conn.execute(
            """SELECT COUNT(*) n FROM notification_delivery
               WHERE notification_group_id=?
                 AND status NOT IN ('SENT','SKIPPED')""",
            (notification_group_id,),
        ).fetchone()["n"]
        if int(outstanding) == 0:
            conn.execute(
                """UPDATE notification_group
                   SET status='SENT',sent_at=?
                   WHERE notification_group_id=?""",
                (now, notification_group_id),
            )
        conn.commit()
        return {
            "delivery_version": DELIVERY_VERSION,
            "status": "PASS",
            "notification_group_id": notification_group_id,
            "delivery_id": delivery_id,
            "channel": IN_APP_CHANNEL,
            "acknowledged_at": now,
            "group_status": (
                conn.execute(
                    "SELECT status FROM notification_group WHERE notification_group_id=?",
                    (notification_group_id,),
                ).fetchone()["status"]
            ),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
