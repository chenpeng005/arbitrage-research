#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

CONTRACT_VERSION = "wechat-upstream-snapshot-v0.1"
SOURCE_OWNER = "ETF_RUNTIME"
SOURCE_KIND = "WECHAT_UPSTREAM_SNAPSHOT"
CHINA_TZ = ZoneInfo("Asia/Shanghai")

def _canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text: return None
    try: result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError: return None
    if result.tzinfo is None: result = result.replace(tzinfo=CHINA_TZ)
    return result.astimezone(CHINA_TZ)

def _positive_int(value: Any) -> int | None:
    try: number = int(value)
    except (TypeError, ValueError): return None
    return number if number > 0 else None

def _float(value: Any) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None

def build_etf_wechat_snapshot(monitor: dict[str, Any], reference_premium: dict[str, Any], comparison: dict[str, Any] | None = None, *, generated_at: datetime | None = None, ranking_limit: int = 5, minimum_premium_pct: float = 5.0) -> dict[str, Any]:
    if ranking_limit <= 0: raise ValueError("ranking_limit must be positive")
    comparison = comparison or {}
    generated = generated_at or datetime.now(CHINA_TZ)
    if generated.tzinfo is None: generated = generated.replace(tzinfo=CHINA_TZ)
    generated = generated.astimezone(CHINA_TZ)
    trade_date = str(monitor.get("target_trade_date") or "").strip()
    premium_fetched = _parse_time(reference_premium.get("fetched_at"))
    content_cutoff = premium_fetched or generated
    premium_by_code = {str(r.get("code") or ""): dict(r) for r in (reference_premium.get("rows") or []) if isinstance(r, dict) and r.get("code")}
    comparison_by_code = {str(r.get("code") or ""): dict(r) for r in (comparison.get("rows") or []) if isinstance(r, dict) and r.get("code")}
    evaluated=[]
    for raw in monitor.get("rows") or []:
        if not isinstance(raw, dict): continue
        code=str(raw.get("code") or ""); premium=premium_by_code.get(code) or {}; baskets=_positive_int(raw.get("total_baskets")); premium_pct=_float(premium.get("premium_pct")); close=_float(premium.get("close")); nav=_float(premium.get("formal_nav")); blockers=[]
        if raw.get("creation_allowed") is not True: blockers.append("CREATION_NOT_OPEN")
        if str(raw.get("pcf_status") or "") != "FRESH": blockers.append("PCF_NOT_FRESH")
        if baskets is None: blockers.append("CURRENT_BASKETS_NOT_POSITIVE")
        if close is None or close <= 0: blockers.append("MARKET_PRICE_MISSING")
        if nav is None or nav <= 0: blockers.append("OFFICIAL_NAV_MISSING")
        if not premium.get("nav_date"): blockers.append("OFFICIAL_NAV_DATE_MISSING")
        if premium_pct is None: blockers.append("OFFICIAL_PREMIUM_MISSING")
        elif premium_pct < minimum_premium_pct: blockers.append("PREMIUM_BELOW_GATE")
        change=comparison_by_code.get(code) or {}
        evaluated.append({"code":code,"name":str(raw.get("name") or premium.get("name") or ""),"exchange":str(raw.get("exchange") or premium.get("exchange") or ""),"market_price":close,"market_price_date":premium.get("close_date"),"official_nav":nav,"official_nav_date":premium.get("nav_date"),"official_premium_rate":premium_pct,"current_baskets":baskets,"creation_allowed":raw.get("creation_allowed"),"pcf_trade_date":raw.get("pcf_trade_date"),"capacity_kind":raw.get("market_capacity_kind") or raw.get("capacity_kind"),"previous_baskets":change.get("previous_total_baskets"),"basket_delta":change.get("basket_delta"),"capacity_changed":bool(change.get("changed")),"eligible_for_wechat_table":not blockers,"eligibility_blockers":blockers})
    eligible=[r for r in evaluated if r["eligible_for_wechat_table"]]
    eligible.sort(key=lambda r:(-int(r["current_baskets"]),-float(r["official_premium_rate"]),r["code"]))
    rows=eligible[:ranking_limit]
    delta_items=[{"code":r["code"],"name":r["name"],"previous_baskets":r["previous_baskets"],"current_baskets":r["current_baskets"],"basket_delta":r["basket_delta"]} for r in evaluated if r["capacity_changed"]]
    critical=[]
    if not trade_date: critical.append("logical_date")
    if premium_fetched is None: critical.append("reference_premium.fetched_at")
    if len(rows) != ranking_limit: critical.append("ranked_rows")
    status="READY" if not critical else "BLOCKED"
    source_payload={"monitor":monitor,"reference_premium":reference_premium,"comparison":comparison}
    return {"contract_version":CONTRACT_VERSION,"source_owner":SOURCE_OWNER,"source_kind":SOURCE_KIND,"logical_date":trade_date or None,"generated_at":generated.isoformat(),"content_cutoff_at":content_cutoff.isoformat(),"source_snapshot_id":f"etf-primary:{trade_date}:{reference_premium.get('fetched_at') or 'UNKNOWN'}","source_snapshot_sha256":sha256(_canonical_bytes(source_payload)).hexdigest(),"source_snapshot_generated_at":reference_premium.get("fetched_at"),"source_contract_version":f"monitor-v{monitor.get('schema_version') or 'unknown'}+premium-v{reference_premium.get('schema_version') or 'unknown'}","status":status,"block_reason":None if status=="READY" else "INSUFFICIENT_OR_STALE_ELIGIBLE_ROWS","ranking_limit":ranking_limit,"minimum_premium_pct":minimum_premium_pct,"evaluated_count":len(evaluated),"eligible_count":len(eligible),"rows":rows,"delta_items":delta_items,"critical_empty_fields":critical,"field_provenance":{"capacity":"ETF_PRIMARY_OFFICIAL_PCF","market_price_and_formal_nav":"ETF_REFERENCE_PREMIUM_CACHE"}}

class EtfWechatSnapshotStore:
    def __init__(self, root: str | Path):
        self.root=Path(root); self.archive_dir=self.root/"archive"; self.daily_dir=self.root/"daily"; self.latest_path=self.root/"latest.json"
    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True,exist_ok=True); fd,temp_name=tempfile.mkstemp(prefix=f".{path.name}.",suffix=".tmp",dir=str(path.parent))
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as h:
                json.dump(payload,h,ensure_ascii=False,separators=(",", ":")); h.write("\n"); h.flush(); os.fsync(h.fileno())
            os.replace(temp_name,path)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)
    def persist(self,payload:dict[str,Any])->Path:
        logical_date=str(payload.get("logical_date") or "").strip(); cutoff=_parse_time(payload.get("content_cutoff_at"))
        if not logical_date or cutoff is None: raise ValueError("logical_date and content_cutoff_at are required")
        archive=self.archive_dir/f"{logical_date}_{cutoff.strftime('%Y%m%dT%H%M%S%z')}_{str(payload.get('source_snapshot_sha256') or '')[:12]}.json"
        self._write(archive,payload); self._write(self.daily_dir/f"{logical_date}.json",payload); self._write(self.latest_path,payload); return archive
    def load_latest_at_or_before(self,cutoff:str|datetime)->dict[str,Any]|None:
        cutoff_dt=cutoff if isinstance(cutoff,datetime) else _parse_time(cutoff)
        if cutoff_dt is None: raise ValueError("cutoff must be an ISO datetime")
        if cutoff_dt.tzinfo is None: cutoff_dt=cutoff_dt.replace(tzinfo=CHINA_TZ)
        cutoff_dt=cutoff_dt.astimezone(CHINA_TZ); best=None
        for path in self.archive_dir.glob("*.json") if self.archive_dir.exists() else []:
            try: payload=json.loads(path.read_text(encoding="utf-8")); effective=_parse_time(payload.get("content_cutoff_at"))
            except Exception: continue
            if effective is None or effective>cutoff_dt: continue
            if best is None or effective>best[0]: best=(effective,payload)
        return best[1] if best else None

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--monitor",required=True); p.add_argument("--premium",required=True); p.add_argument("--comparison",required=True); p.add_argument("--output-root",required=True); a=p.parse_args()
    monitor=json.loads(Path(a.monitor).read_text(encoding="utf-8")); premium=json.loads(Path(a.premium).read_text(encoding="utf-8")); comparison=json.loads(Path(a.comparison).read_text(encoding="utf-8")); payload=build_etf_wechat_snapshot(monitor,premium,comparison); path=EtfWechatSnapshotStore(a.output_root).persist(payload); print(json.dumps({"status":payload["status"],"path":str(path),"eligible_count":payload["eligible_count"]},ensure_ascii=False)); return 0 if payload["status"]=="READY" else 2
if __name__=="__main__": raise SystemExit(main())
