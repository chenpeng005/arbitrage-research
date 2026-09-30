"""SQLite read-model projection parity for Candidate Pool / Opportunity Record."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from typing import Any
from runtime.opportunity.incremental_storage import connect

PROJECTION_VERSION = "incremental-storage-projection-v1"
HOLD_STATUSES = {"NEEDS_EVIDENCE", "UNRESOLVED"}

def _research_state(trigger: dict[str, Any]) -> str:
    key, status = trigger.get("last_trigger_key"), trigger.get("research_status")
    if not key: return "NOT_TRIGGERED"
    if status == "COMPLETED": return "COMPLETED"
    if status in HOLD_STATUSES: return "HOLD_WAITING_EVIDENCE"
    if status == "PENDING": return "PENDING"
    if status == "WAITING_FOR_CHAT": return "WAITING_FOR_CHAT"
    if status == "IN_PROGRESS": return "IN_PROGRESS"
    raise RuntimeError(f"cannot map research state: {key!r}/{status!r}")

def _record_state(paths: list[dict[str, Any]]) -> str:
    states = {str(x.get("research_state") or "") for x in paths}
    if "HOLD_WAITING_EVIDENCE" in states: return "HAS_HOLD"
    if states & {"PENDING","IN_PROGRESS","WAITING_FOR_CHAT"}: return "RESEARCH_IN_PROGRESS"
    if "COMPLETED" in states: return "HAS_COMPLETED_RESEARCH"
    if states and states <= {"NOT_TRIGGERED"}: return "ECONOMIC_KEEP_WAITING_TRIGGER"
    raise RuntimeError(f"cannot map record state: {sorted(states)}")
def build_candidate_projection(*, target_db: Path) -> dict[str, Any]:
    conn = connect(target_db)
    try:
        rows = list(conn.execute(
            """SELECT s.*, b.bond_name, mo.market_order
               FROM scope_state_current s
               JOIN bond_master b ON b.bond_code=s.bond_code
               JOIN market_observation mo
                 ON mo.snapshot_id=s.source_snapshot_id AND mo.bond_code=s.bond_code
               WHERE s.scope_type='PATH' AND s.economic_status='KEEP'
               ORDER BY mo.market_order, s.scope_order"""
        ))
        by_bond: dict[str,list[dict[str,Any]]] = {}
        names: dict[str,str] = {}
        counter: Counter[str] = Counter()
        for row in rows:
            code, path_id = row["bond_code"], row["scope_id"]
            names[code] = row["bond_name"]
            payload = json.loads(row["payload_json"] or "{}")
            economic = payload.get("economic_path") or {}
            trigger = payload.get("trigger_state") or {}
            state = _research_state(trigger)
            binding = conn.execute(
                """SELECT rb.result_id, rr.review_ready, rr.research_status, rr.artifact_path
                   FROM research_binding rb JOIN research_result_index rr ON rr.result_id=rb.result_id
                   WHERE rb.bond_code=? AND rb.path_id=? AND rb.is_current=1""",
                (code,path_id),
            ).fetchone()
            result_id = result_path = review_ready = None
            research_status = trigger.get("research_status")
            if state in {"COMPLETED","HOLD_WAITING_EVIDENCE"}:
                if binding is None:
                    raise RuntimeError(f"missing binding: {code}:{path_id}")
                result_id = binding["result_id"]
                result_path = binding["artifact_path"]
                review_ready = bool(binding["review_ready"])
                research_status = binding["research_status"]
            history = conn.execute(
                "SELECT COUNT(*) n FROM research_result_index WHERE bond_code=? AND path_id=?",
                (code,path_id),
            ).fetchone()["n"]
            record = {
                "path_id": path_id, "economic_status": "KEEP",
                "economic_judgment": economic,
                "current_event_state": trigger.get("current_event_state"),
                "last_trigger_key": trigger.get("last_trigger_key"),
                "trigger_reason": trigger.get("last_trigger_reason"),
                "research_state": state, "research_status": research_status,
                "review_ready": review_ready,
                "latest_path_result_id": result_id,
                "latest_path_result_path": result_path,
                "research_history_count": int(history),
            }
            by_bond.setdefault(code,[]).append(record)
            counter[state] += 1
        bonds = []
        for code, paths in by_bond.items():
            snapshot_id = conn.execute(
                """SELECT source_snapshot_id FROM scope_state_current
                   WHERE bond_code=? AND scope_type='PATH' AND economic_status='KEEP' LIMIT 1""",
                (code,),
            ).fetchone()["source_snapshot_id"]
            snap = conn.execute(
                "SELECT market_cutoff FROM market_snapshot WHERE snapshot_id=?",(snapshot_id,)
            ).fetchone()
            bonds.append({
                "bond_code": code, "bond_name": names[code],
                "market_snapshot_id": snapshot_id,
                "market_cutoff": snap["market_cutoff"] if snap else None,
                "keep_path_count": len(paths),
                "paths": paths,
            })
        top_snapshot = bonds[0]["market_snapshot_id"] if bonds else None
        top_cutoff = bonds[0]["market_cutoff"] if bonds else None
        return {
            "projection_version": PROJECTION_VERSION,
            "market_snapshot_id": top_snapshot,
            "market_cutoff": top_cutoff,
            "bond_count": len(bonds), "keep_path_count": len(rows),
            "research_state_summary": dict(sorted(counter.items())),
            "bonds": bonds,
        }
    finally:
        conn.close()

def build_opportunity_projection(*, target_db: Path) -> dict[str, Any]:
    pool = build_candidate_projection(target_db=target_db)
    records, counter, count = [], Counter(), 0
    for bond in pool["bonds"]:
        paths = []
        for path in bond["paths"]:
            row = dict(path)
            result = None
            if path["research_state"] in {"COMPLETED","HOLD_WAITING_EVIDENCE"}:
                result = json.loads(Path(str(path["latest_path_result_path"])).read_text(encoding="utf-8"))
            row["path_result"] = result
            paths.append(row); count += 1
        state = _record_state(paths); counter[state] += 1
        records.append({
            "bond_code": bond["bond_code"], "bond_name": bond["bond_name"],
            "market_snapshot_id": bond["market_snapshot_id"],
            "market_cutoff": bond["market_cutoff"],
            "keep_path_count": len(paths), "record_state": state, "paths": paths,
        })
    return {
        "projection_version": PROJECTION_VERSION,
        "market_snapshot_id": pool.get("market_snapshot_id"),
        "market_cutoff": pool.get("market_cutoff"),
        "bond_count": len(records), "keep_path_count": count,
        "record_state_summary": dict(sorted(counter.items())),
        "records": records,
    }

def _index(rows: list[dict[str,Any]]) -> dict[str,dict[str,Any]]:
    return {str(x["bond_code"]).zfill(6):x for x in rows}

def _normalize(value: dict[str,Any]) -> dict[str,Any]:
    return json.loads(json.dumps(value,ensure_ascii=False))
def audit_projection_parity(*, data_root: Path, target_db: Path, max_samples: int=20) -> dict[str,Any]:
    cp = json.loads((data_root/"registry"/"latest_candidate_pool.json").read_text())
    cj = json.loads(Path(cp["result_path"]).read_text())
    rp = json.loads((data_root/"registry"/"latest_opportunity_records.json").read_text())
    rj = json.loads(Path(rp["result_path"]).read_text())
    cs = build_candidate_projection(target_db=target_db)
    rs = build_opportunity_projection(target_db=target_db)
    mismatches: list[dict[str,Any]] = []
    def add(kind: str, **kw: Any) -> None:
        if len(mismatches) < max_samples: mismatches.append({"kind":kind,**kw})
    jc, sc = _index(cj.get("bonds",[])), _index(cs["bonds"])
    jr, sr = _index(rj.get("records",[])), _index(rs["records"])
    if set(jc)!=set(sc): add("CANDIDATE_BOND_SET",json_only=sorted(set(jc)-set(sc))[:10],sqlite_only=sorted(set(sc)-set(jc))[:10])
    if set(jr)!=set(sr): add("RECORD_BOND_SET",json_only=sorted(set(jr)-set(sr))[:10],sqlite_only=sorted(set(sr)-set(jr))[:10])
    for code in sorted(set(jc)&set(sc)):
        if _normalize(jc[code]) != _normalize(sc[code]): add("CANDIDATE_BOND_PAYLOAD",bond_code=code)
    for code in sorted(set(jr)&set(sr)):
        if _normalize(jr[code]) != _normalize(sr[code]): add("OPPORTUNITY_RECORD_PAYLOAD",bond_code=code)
    metrics = {
        "json_candidate_bonds": cj.get("bond_count"), "sqlite_candidate_bonds": cs["bond_count"],
        "json_keep_paths": cj.get("keep_path_count"), "sqlite_keep_paths": cs["keep_path_count"],
        "json_research_state_summary": cj.get("research_state_summary"),
        "sqlite_research_state_summary": cs["research_state_summary"],
        "json_record_state_summary": rj.get("record_state_summary"),
        "sqlite_record_state_summary": rs["record_state_summary"],
    }
    for a,b,label in [
        (metrics["json_candidate_bonds"],metrics["sqlite_candidate_bonds"],"CANDIDATE_COUNT"),
        (metrics["json_keep_paths"],metrics["sqlite_keep_paths"],"KEEP_PATH_COUNT"),
        (metrics["json_research_state_summary"],metrics["sqlite_research_state_summary"],"RESEARCH_STATE_SUMMARY"),
        (metrics["json_record_state_summary"],metrics["sqlite_record_state_summary"],"RECORD_STATE_SUMMARY"),
    ]:
        if a != b: add(label,expected=a,actual=b)
    return {
        "projection_version": PROJECTION_VERSION,
        "status": "PASS" if not mismatches else "FAIL",
        "metrics": metrics, "mismatch_count": len(mismatches),
        "mismatch_samples": mismatches,
    }

