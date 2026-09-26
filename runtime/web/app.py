from __future__ import annotations

import base64
import copy
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import pandas as pd

from runtime.ai_runtime.engine import run_ai_job
from runtime.market_map.resolver import (
    ResolverError,
    resolve_bond_scenarios,
)
from runtime.opportunity.ingress import build_market_ingress
from runtime.opportunity.full_runtime_controller import run_opportunity_full_downstream
from runtime.opportunity.view_contract import build_opportunity_list, build_opportunity_view

ROOT = Path(os.environ.get("RUNTIME_ROOT", Path(__file__).resolve().parents[2]))
DATA_ROOT = Path(os.environ.get("RUNTIME_DATA_ROOT", ROOT / "runtime_data"))
DEPLOYMENT_MANIFEST_PATH = DATA_ROOT / "deployment_manifest.json"
MARKET_MAP_REGISTRY_DIR = DATA_ROOT / "registry" / "market_map_snapshots"
LATEST_FORMAL_MARKET_MAP_PATH = DATA_ROOT / "registry" / "latest_formal_market_map.json"
LATEST_DISCOVERY_INGRESS_PATH = DATA_ROOT / "registry" / "latest_discovery_market_ingress.json"
LATEST_CANDIDATE_POOL_PATH = DATA_ROOT / "registry" / "latest_candidate_pool.json"
LATEST_OPPORTUNITY_RECORDS_PATH = DATA_ROOT / "registry" / "latest_opportunity_records.json"
LATEST_FULL_RUNTIME_PATH = DATA_ROOT / "registry" / "latest_full_runtime.json"
GOLDEN_V2_ROOT = ROOT / "runtime" / "golden_samples" / "path_result_v2"
GOLDEN_V2_PATH_RESULTS = {
    "110092": {
        "MATURITY_CASH": GOLDEN_V2_ROOT / "110092_MATURITY_CASH.json",
        "PUT": GOLDEN_V2_ROOT / "110092_PUT.json",
    },
    "127089": {
        "DOWNWARD_REVISION": GOLDEN_V2_ROOT / "127089_DOWNWARD_REVISION.json",
    },
}
CHAT_TASK_ROOT = DATA_ROOT / "chat_tasks"

ACQ_SCRIPT = ROOT / "runtime" / "market_map" / "acquisition.py"
CALC_SCRIPT = ROOT / "runtime" / "market_map" / "calculation.py"
RUNTIME_PYTHON = os.environ.get("ACQUISITION_PYTHON", sys.executable)

STATIC_DIR = Path(__file__).parent / "static"

RAW_FIXTURE_DIR = Path(
    os.environ.get(
        "RUNTIME_FIXTURE_DIR",
        DATA_ROOT / "fixtures" / "market_map_20260924_intraday",
    )
)

CALC_FIXTURE_DIR = Path(
    os.environ.get(
        "RUNTIME_CALC_FIXTURE_DIR",
        DATA_ROOT / "fixtures" / "market_map_acquisition_20260924_replay",
    )
)

app = FastAPI(title="Opportunity Discovery Runtime")

RUNTIME_USER = os.environ.get("RUNTIME_USER")
RUNTIME_PASSWORD = os.environ.get("RUNTIME_PASSWORD")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    snapshot_mode: str = "LIVE_TEST"
    market_cutoff: str | None = None
    historical_snapshot_id: str | None = None


class CalculationRunRequest(BaseModel):
    input_mode: str = "REPLAY_TEST"
    market_cutoff: str | None = None
    source_job_id: str | None = None


class MarketMapPipelineRequest(BaseModel):
    snapshot_mode: str = "CLOSE"
    market_cutoff: str | None = None
    ai_execution_mode: str = "AUTO_API"
    historical_snapshot_id: str | None = None


class OpportunityFullRunRequest(BaseModel):
    source_mode: str = "LATEST_FORMAL"
    run_research: bool = True
    research_batch_limit: int = 5
    max_research_rounds: int = 20


class BondValuationScenario(BaseModel):
    scenario_id: str | None = None
    target_CV: float
    target_remaining_months: float | None = None
    target_remaining_size: float | None = None


class BondValuationResolverRequest(BaseModel):
    bond_code: str
    scenarios: list[BondValuationScenario]
    snapshot_id: str | None = None


TERMINAL_JOB_STATUSES = {"PASS", "WARNING", "FAIL", "NEEDS_REVIEW"}


@app.middleware("http")
async def runtime_basic_auth(request, call_next):
    # The public listener is protected by Caddy Basic Auth. Only the local
    # reverse proxy may skip the application's separate credentials.
    if (
        os.environ.get("RUNTIME_TRUST_LOCAL_PROXY_AUTH") == "1"
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
    ):
        return await call_next(request)

    if not RUNTIME_USER or not RUNTIME_PASSWORD:
        return Response("Runtime authentication is not configured.", status_code=503)

    header = request.headers.get("Authorization", "")
    valid = False
    if header.startswith("Basic "):
        try:
            raw = base64.b64decode(header[6:]).decode("utf-8")
            username, password = raw.split(":", 1)
            valid = secrets.compare_digest(username, RUNTIME_USER) and secrets.compare_digest(
                password, RUNTIME_PASSWORD
            )
        except Exception:
            valid = False

    if not valid:
        return Response(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Opportunity Discovery Runtime"'},
        )
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def china_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _latest_cached_trade_calendar() -> Path | None:
    candidates = list((DATA_ROOT / "runs").glob("*/raw/trade_calendar_sina.csv"))
    if not candidates:
        candidates = list(DATA_ROOT.glob("**/trade_calendar_sina.csv"))
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _is_cached_trade_day(date_text: str) -> bool:
    path = _latest_cached_trade_calendar()
    if path is None:
        return datetime.fromisoformat(date_text).weekday() < 5
    try:
        frame = pd.read_csv(path, usecols=["trade_date"])
        dates = set(frame["trade_date"].astype(str))
        return date_text in dates
    except Exception:
        return datetime.fromisoformat(date_text).weekday() < 5


def _full_run_candidates() -> list[dict]:
    candidates = []
    with _lock:
        candidates.extend(
            dict(job) for job in _jobs.values()
            if job.get("unit") == "Opportunity Discovery / Full Runtime"
        )
    for job in load_persisted_jobs():
        if job.get("unit") == "Opportunity Discovery / Full Runtime":
            candidates.append(job)
    return candidates


def _completed_full_run_for_cutoff(cutoff: str) -> dict | None:
    matches = [
        job for job in _full_run_candidates()
        if job.get("status") == "PASS" and job.get("market_cutoff") == cutoff
    ]
    if not matches:
        return None
    matches.sort(key=lambda x: x.get("completed_at") or x.get("updated_at") or "", reverse=True)
    return matches[0]


def close_mode_precheck() -> dict:
    now = china_now()
    date_text = now.date().isoformat()
    after_close_gate = (now.hour, now.minute) >= (15, 10)
    is_trade_day = _is_cached_trade_day(date_text)
    completed = _completed_full_run_for_cutoff(date_text)
    active = next(
        (
            job for job in _full_run_candidates()
            if job.get("market_cutoff") == date_text
            and job.get("status") in {"PENDING", "RUNNING"}
        ),
        None,
    )
    return {
        "china_time": now.isoformat(timespec="minutes"),
        "china_date": date_text,
        "after_close_gate": after_close_gate,
        "is_trade_day": is_trade_day,
        "close_gate_time": "15:10",
        "formal_run_completed": completed is not None,
        "completed_job_id": completed.get("job_id") if completed else None,
        "formal_run_active": active is not None,
        "active_job_id": active.get("job_id") if active else None,
        "formal_run_available": (
            is_trade_day
            and after_close_gate
            and completed is None
            and active is None
        ),
    }


def load_deployment_manifest() -> dict:
    if not DEPLOYMENT_MANIFEST_PATH.exists():
        return {
            "application_commit_sha": None,
            "knowledge_commit_sha": None,
            "deployed_at": None,
            "deployment_method": None,
        }
    try:
        data = json.loads(DEPLOYMENT_MANIFEST_PATH.read_text(encoding="utf-8"))
        return {
            "application_commit_sha": data.get("application_commit_sha"),
            "knowledge_commit_sha": data.get("knowledge_commit_sha"),
            "deployed_at": data.get("deployed_at"),
            "deployment_method": data.get("deployment_method"),
        }
    except Exception:
        return {
            "application_commit_sha": None,
            "knowledge_commit_sha": None,
            "deployed_at": None,
            "deployment_method": "INVALID_MANIFEST",
        }


def persist_run_metadata_payload(job: dict) -> None:
    job_id = job.get("job_id")
    if not job_id:
        return

    run_dir = DATA_ROOT / "runs" / job_id
    run_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {}
    snapshot_path_raw = job.get("snapshot_path")
    if snapshot_path_raw:
        snapshot_path = Path(snapshot_path_raw)
        if snapshot_path.exists():
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except Exception:
                snapshot = {}

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    deployment = load_deployment_manifest()

    artifacts = {
        "result": job.get("result_path"),
        "source_manifest": job.get("source_manifest_path"),
        "acquisition_audit": job.get("acquisition_audit_path"),
        "semantic_review_request": job.get("semantic_review_request_path"),
        "semantic_resolution": job.get("semantic_resolution_path"),
        "semantic_resolution_validation": job.get("semantic_resolution_validation_path"),
        "semantic_evidence_manifest": job.get("semantic_evidence_manifest_path"),
        "trusted_market_input": job.get("trusted_market_input_path"),
        "market_input_audit": job.get("market_input_path"),
        "snapshot": job.get("snapshot_path"),
        "model_audit": job.get("model_audit_path"),
        "calculated_table": job.get("calculated_table_path"),
        "output_contract_manifest": job.get("output_contract_path"),
        "output_contract_validation": job.get("output_contract_validation_path"),
    }

    runtime_run_id = job.get("runtime_run_id")
    metadata = {
        "job_id": job_id,
        "unit": job.get("unit"),
        "status": job.get("status"),
        "phase": job.get("phase"),
        "source_job_id": job.get("source_job_id"),
        "acquisition_job_id": job.get("acquisition_job_id"),
        "ai_job_id": job.get("ai_job_id"),
        "ai_status": job.get("ai_status"),
        "ai_execution_mode": job.get("ai_execution_mode"),
        "chat_task_id": job.get("chat_task_id"),
        "chat_task_path": job.get("chat_task_path"),
        "calculation_job_id": job.get("calculation_job_id"),
        "snapshot_mode": job.get("snapshot_mode"),
        "historical_snapshot_id": job.get("historical_snapshot_id"),
        "historical_source_acquisition_job_id": job.get(
            "historical_source_acquisition_job_id"
        ),
        "input_mode": job.get("input_mode"),
        "market_cutoff": job.get("market_cutoff"),
        "created_at": job.get("created_at"),
        "command_started_at": job.get("command_started_at"),
        "completed_at": job.get("completed_at"),
        "runtime_run_id": runtime_run_id,
        "runtime_status": job.get("runtime_status"),
        "data_snapshot_id": (
            f"market-input-{job.get('market_cutoff')}-{runtime_run_id}"
            if job.get("unit") == "Market Map Builder / Acquisition" and runtime_run_id
            else None
        ),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_class": snapshot.get("snapshot_class"),
        "model_version": snapshot.get("model_version") or result.get("model_version"),
        "artifacts": artifacts,
        "deployment": deployment,
    }
    (run_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_formal_market_map_entries() -> list[dict]:
    if not MARKET_MAP_REGISTRY_DIR.exists():
        return []

    entries: list[dict] = []
    for path in MARKET_MAP_REGISTRY_DIR.glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if entry.get("snapshot_class") != "FORMAL_CLOSE":
            continue

        snapshot_path = Path(entry.get("snapshot_path") or "")
        table_path = Path(entry.get("calculated_table_path") or "")
        acquisition_job_id = entry.get("acquisition_job_id")
        acquisition_dir = (
            DATA_ROOT / "runs" / acquisition_job_id
            if acquisition_job_id
            else None
        )

        replay_ready = bool(
            snapshot_path.exists()
            and table_path.exists()
            and acquisition_dir is not None
            and acquisition_dir.exists()
            and (acquisition_dir / "raw").exists()
            and (acquisition_dir / "raw" / "maturity_ths.csv").exists()
            and (acquisition_dir / "raw" / "size_jisilu.csv").exists()
            and (
                (acquisition_dir / "raw" / "market_primary_eastmoney_push2.csv").exists()
                or (
                    (acquisition_dir / "raw" / "market_fallback_jisilu.csv").exists()
                    and (
                        acquisition_dir
                        / "raw"
                        / "market_fallback_eastmoney_datacenter.csv"
                    ).exists()
                )
            )
        )

        item = dict(entry)
        item["replay_ready"] = replay_ready
        entries.append(item)

    entries.sort(
        key=lambda x: (
            str(x.get("market_cutoff") or ""),
            str(x.get("created_at") or ""),
        ),
        reverse=True,
    )
    return entries


def get_formal_market_map_entry(snapshot_id: str) -> dict:
    path = MARKET_MAP_REGISTRY_DIR / f"{snapshot_id}.json"
    if not path.exists():
        raise HTTPException(404, "historical formal snapshot not found")
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            500,
            f"historical snapshot registry is invalid: {type(exc).__name__}",
        )

    if entry.get("snapshot_class") != "FORMAL_CLOSE":
        raise HTTPException(400, "historical replay source must be FORMAL_CLOSE")
    return entry


def resolve_historical_source(snapshot_id: str) -> tuple[dict, Path]:
    entry = get_formal_market_map_entry(snapshot_id)
    acquisition_job_id = entry.get("acquisition_job_id")
    if not acquisition_job_id:
        raise HTTPException(400, "historical snapshot has no acquisition job")

    source_dir = DATA_ROOT / "runs" / acquisition_job_id
    raw_dir = source_dir / "raw"
    required = [
        raw_dir / "maturity_ths.csv",
        raw_dir / "size_jisilu.csv",
    ]
    market_ready = (
        (raw_dir / "market_primary_eastmoney_push2.csv").exists()
        or (
            (raw_dir / "market_fallback_jisilu.csv").exists()
            and (raw_dir / "market_fallback_eastmoney_datacenter.csv").exists()
        )
    )
    if not source_dir.exists() or not raw_dir.exists():
        raise HTTPException(400, "historical acquisition raw is not available")
    if any(not p.exists() for p in required) or not market_ready:
        raise HTTPException(
            400,
            "historical snapshot was frozen before the replay-ready Raw contract; "
            "this snapshot cannot be replayed deterministically",
        )
    return entry, source_dir


def register_formal_market_map_snapshot(job_id: str) -> None:
    with _lock:
        job = dict(_jobs.get(job_id, {}))
    if not job:
        return

    snapshot_path_raw = job.get("snapshot_path")
    table_path_raw = job.get("calculated_table_path")
    contract_path_raw = job.get("output_contract_path")
    contract_validation_path_raw = job.get("output_contract_validation_path")
    if (
        not snapshot_path_raw
        or not table_path_raw
        or not contract_path_raw
        or not contract_validation_path_raw
    ):
        return

    snapshot_path = Path(snapshot_path_raw)
    table_path = Path(table_path_raw)
    contract_path = Path(contract_path_raw)
    contract_validation_path = Path(contract_validation_path_raw)
    if (
        not snapshot_path.exists()
        or not table_path.exists()
        or not contract_path.exists()
        or not contract_validation_path.exists()
    ):
        return

    try:
        contract_validation = json.loads(
            contract_validation_path.read_text(encoding="utf-8")
        )
    except Exception:
        return
    if contract_validation.get("status") != "PASS":
        return

    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return

    if snapshot.get("snapshot_class") != "FORMAL_CLOSE":
        return

    snapshot_id = snapshot.get("snapshot_id")
    if not snapshot_id:
        return

    MARKET_MAP_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_FORMAL_MARKET_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "snapshot_id": snapshot_id,
        "snapshot_class": snapshot.get("snapshot_class"),
        "market_cutoff": snapshot.get("market_cutoff"),
        "model_version": snapshot.get("model_version"),
        "created_at": snapshot.get("created_at"),
        "calculation_job_id": job_id,
        "acquisition_job_id": job.get("source_job_id"),
        "snapshot_path": str(snapshot_path),
        "calculated_table_path": str(table_path),
        "model_audit_path": job.get("model_audit_path"),
        "output_contract_version": contract_validation.get("contract_version"),
        "output_contract_status": contract_validation.get("status"),
        "output_contract_path": str(contract_path),
        "output_contract_validation_path": str(contract_validation_path),
        "run_metadata_path": str(DATA_ROOT / "runs" / job_id / "run_metadata.json"),
        "input": snapshot.get("input", {}),
        "deployment": load_deployment_manifest(),
    }

    registry_path = MARKET_MAP_REGISTRY_DIR / f"{snapshot_id}.json"
    registry_path.write_text(
        json.dumps(entry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    LATEST_FORMAL_MARKET_MAP_PATH.write_text(
        json.dumps(entry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def persist_job(job_id: str) -> None:
    with _lock:
        payload = dict(_jobs[job_id])
    path = DATA_ROOT / "jobs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "live_status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    persist_run_metadata_payload(payload)


def update_step(job_id: str, event: dict) -> None:
    sid = event.get("id")
    with _lock:
        job = _jobs[job_id]
        steps = job.setdefault("steps", {})
        if sid:
            steps[sid] = event
        job["updated_at"] = utcnow()

        statuses = [x.get("status") for x in steps.values()]
        if "FAIL" in statuses:
            job["status"] = "FAIL"
        elif "NEEDS_REVIEW" in statuses:
            job["status"] = "NEEDS_REVIEW"
        elif "WARNING" in statuses:
            job["status"] = "RUNNING_WARNING"
        else:
            job["status"] = "RUNNING"

    persist_job(job_id)


def execute_job(
    job_id: str,
    cmd: list[str],
    result_path: Path,
    extra_result_paths: dict[str, Path] | None = None,
) -> None:
    with _lock:
        _jobs[job_id]["status"] = "RUNNING"
        _jobs[job_id]["command_started_at"] = utcnow()
    persist_job(job_id)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None

        raw_lines: list[str] = []
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            raw_lines.append(line)

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "step":
                update_step(job_id, event)
            elif event.get("type") == "run_complete":
                with _lock:
                    _jobs[job_id]["runtime_status"] = event.get("status")
                    _jobs[job_id]["runtime_run_id"] = event.get("run_id")
                persist_job(job_id)

        code = proc.wait()

        final_result = None
        if result_path.exists():
            final_result = json.loads(result_path.read_text(encoding="utf-8"))

        with _lock:
            job = _jobs[job_id]
            job["exit_code"] = code
            job["completed_at"] = utcnow()
            job["result_path"] = str(result_path)
            job["result"] = final_result
            job["status"] = (
                final_result.get("status")
                if isinstance(final_result, dict)
                else ("PASS" if code == 0 else "FAIL")
            )

            if extra_result_paths:
                for key, path in extra_result_paths.items():
                    job[key] = str(path) if path.exists() else None

            if code != 0 and not final_result:
                job["error"] = "Runtime process exited without a structured result."

            job["raw_output_tail"] = raw_lines[-20:]

        persist_job(job_id)
        register_formal_market_map_snapshot(job_id)

    except Exception as exc:
        with _lock:
            job = _jobs[job_id]
            job["status"] = "FAIL"
            job["completed_at"] = utcnow()
            job["error"] = f"{type(exc).__name__}: {exc}"
        persist_job(job_id)


def acquisition_worker(job_id: str, request: RunRequest) -> None:
    cutoff = request.market_cutoff or datetime.now().date().isoformat()
    out = DATA_ROOT / "runs" / job_id
    out.mkdir(parents=True, exist_ok=True)

    cmd = [
        RUNTIME_PYTHON,
        str(ACQ_SCRIPT),
        "--output",
        str(out),
        "--snapshot-mode",
        request.snapshot_mode,
        "--market-cutoff",
        cutoff,
    ]

    if request.snapshot_mode == "REPLAY_TEST":
        cmd.extend(["--fixture-dir", str(RAW_FIXTURE_DIR)])
    elif request.snapshot_mode == "HISTORICAL_REPLAY":
        if not request.historical_snapshot_id:
            raise RuntimeError("HISTORICAL_REPLAY requires historical_snapshot_id")
        entry, historical_source_dir = resolve_historical_source(
            request.historical_snapshot_id
        )
        cmd.extend(["--fixture-dir", str(historical_source_dir)])
        with _lock:
            _jobs[job_id]["historical_snapshot_id"] = request.historical_snapshot_id
            _jobs[job_id]["historical_source_acquisition_job_id"] = entry.get(
                "acquisition_job_id"
            )
        persist_job(job_id)

    execute_job(
        job_id,
        cmd,
        out / "acquisition_result.json",
        extra_result_paths={
            "source_manifest_path": out / "source_manifest.json",
            "acquisition_audit_path": out / "acquisition_audit.json",
            "semantic_review_request_path": out / "semantic_review_request.json",
            "trusted_market_input_path": out / "trusted_market_input.csv",
            "market_input_path": out / "market_input_audit.csv",
            "excluded_path": out / "universe_excluded.csv",
        },
    )


def calculation_worker(
    job_id: str,
    request: CalculationRunRequest,
    input_dir: Path,
) -> None:
    cutoff = request.market_cutoff or datetime.now().date().isoformat()
    out = DATA_ROOT / "runs" / job_id
    out.mkdir(parents=True, exist_ok=True)

    cmd = [
        RUNTIME_PYTHON,
        str(CALC_SCRIPT),
        "--input-dir",
        str(input_dir),
        "--output",
        str(out),
        "--market-cutoff",
        cutoff,
    ]

    execute_job(
        job_id,
        cmd,
        out / "calculation_result.json",
        extra_result_paths={
            "snapshot_path": out / "market_map_snapshot.json",
            "model_audit_path": out / "model_audit.json",
            "calculated_table_path": out / "market_map_calculated.csv",
            "output_contract_path": out / "market_map_output_v1" / "manifest.json",
            "output_contract_validation_path": out / "output_contract_validation.json",
        },
    )


def wait_for_job_terminal(
    job_id: str,
    *,
    timeout_seconds: int = 900,
    poll_seconds: float = 0.5,
) -> dict:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        with _lock:
            job = dict(_jobs.get(job_id, {}))

        if not job:
            persisted = DATA_ROOT / "jobs" / job_id / "live_status.json"
            if persisted.exists():
                try:
                    job = json.loads(persisted.read_text(encoding="utf-8"))
                except Exception:
                    job = {}

        status = job.get("status")
        if status in TERMINAL_JOB_STATUSES:
            return job

        time.sleep(poll_seconds)

    raise TimeoutError(f"job did not finish within {timeout_seconds}s: {job_id}")


def update_controller_job(
    job_id: str,
    **fields,
) -> None:
    with _lock:
        if job_id not in _jobs:
            return
        _jobs[job_id].update(fields)
        _jobs[job_id]["updated_at"] = utcnow()
    persist_job(job_id)


def update_full_stage(job_id: str, stage: str, status: str, meta: dict | None = None) -> None:
    event = {"stage": stage, "status": status, "at": utcnow(), **(meta or {})}
    with _lock:
        if job_id not in _jobs:
            return
        job = _jobs[job_id]
        job.setdefault("full_stages", []).append(event)
        job["phase"] = stage
        job["updated_at"] = event["at"]
    persist_job(job_id)


def opportunity_full_worker(job_id: str, request: OpportunityFullRunRequest) -> None:
    try:
        update_controller_job(job_id, status="RUNNING", phase="MARKET_MAP")
        if request.source_mode == "CLOSE":
            update_full_stage(job_id, "MARKET_MAP", "RUNNING")
            child_id = create_market_map_pipeline(
                MarketMapPipelineRequest(snapshot_mode="CLOSE", ai_execution_mode="AUTO_API")
            )["job_id"]
            update_controller_job(job_id, market_map_job_id=child_id)
            child = wait_for_job_terminal(child_id, timeout_seconds=1800)
            if child.get("status") not in {"PASS", "WARNING"}:
                raise RuntimeError(f"Market Map ended with status={child.get('status')}")
            if not LATEST_FORMAL_MARKET_MAP_PATH.exists():
                raise RuntimeError("Market Map passed but no FORMAL_CLOSE registry exists")
            entry = json.loads(LATEST_FORMAL_MARKET_MAP_PATH.read_text(encoding="utf-8"))
            if entry.get("calculation_job_id") != child.get("calculation_job_id"):
                raise RuntimeError("latest FORMAL_CLOSE does not belong to this full run")
            update_full_stage(
                job_id, "MARKET_MAP", "PASS",
                {"market_snapshot_id": entry.get("snapshot_id"), "market_cutoff": entry.get("market_cutoff")},
            )
        elif request.source_mode == "LATEST_FORMAL":
            if not LATEST_FORMAL_MARKET_MAP_PATH.exists():
                raise RuntimeError("no FORMAL_CLOSE Market Map is available")
            entry = json.loads(LATEST_FORMAL_MARKET_MAP_PATH.read_text(encoding="utf-8"))
            update_full_stage(
                job_id, "MARKET_MAP", "REUSED",
                {"market_snapshot_id": entry.get("snapshot_id"), "market_cutoff": entry.get("market_cutoff")},
            )
        else:
            raise RuntimeError(f"unsupported source_mode={request.source_mode}")

        provider_name = os.environ.get("AI_PROVIDER", "")
        model = os.environ.get("AI_MODEL", "")
        if request.run_research and (not provider_name or not model):
            raise RuntimeError("AI provider/model is not configured")

        def stage_callback(stage: str, status: str, meta: dict) -> None:
            update_full_stage(job_id, stage, status, meta)

        result = run_opportunity_full_downstream(
            formal_entry_path=LATEST_FORMAL_MARKET_MAP_PATH,
            data_root=DATA_ROOT,
            deployment=load_deployment_manifest(),
            provider_name=provider_name or "disabled",
            model=model or "disabled",
            research_batch_limit=max(1, min(request.research_batch_limit, 20)),
            max_research_rounds=max(1, min(request.max_research_rounds, 100)),
            run_research=request.run_research,
            stage_callback=stage_callback,
        )
        update_controller_job(
            job_id,
            status="PASS",
            phase="COMPLETE",
            completed_at=utcnow(),
            full_runtime_run_id=result.get("run_id"),
            market_snapshot_id=result.get("market_snapshot_id"),
            market_cutoff=result.get("market_cutoff"),
            summary=result.get("summary"),
            artifacts=result.get("artifacts"),
        )
    except Exception as exc:
        update_full_stage(job_id, "FULL_RUNTIME", "FAIL", {"error": f"{type(exc).__name__}: {exc}"})
        update_controller_job(
            job_id,
            status="FAIL",
            phase="FULL_RUNTIME_ERROR",
            completed_at=utcnow(),
            error=f"{type(exc).__name__}: {exc}",
        )


def create_interactive_chat_task(
    *,
    pipeline_job_id: str,
    acquisition_job_id: str,
    acquisition_run_dir: Path,
    market_cutoff: str,
) -> dict:
    request_path = acquisition_run_dir / "semantic_review_request.json"
    if not request_path.exists():
        raise RuntimeError("semantic_review_request.json is missing")

    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    task_id = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_chat_"
        + uuid.uuid4().hex[:6]
    )
    task_dir = CHAT_TASK_ROOT / task_id
    task_dir.mkdir(parents=True, exist_ok=False)

    task = {
        "task_id": task_id,
        "task_type": "MARKET_MAP_SEMANTIC_AUDIT",
        "execution_mode": "INTERACTIVE_CHAT",
        "status": "WAITING_FOR_CHAT",
        "created_at": utcnow(),
        "pipeline_job_id": pipeline_job_id,
        "acquisition_job_id": acquisition_job_id,
        "business_run_dir": str(acquisition_run_dir),
        "market_cutoff": market_cutoff,
        "semantic_review_request": request_payload,
        "expected_output": "semantic_resolution.json",
        "resume_endpoint": (
            f"/api/market-map-runs/{pipeline_job_id}/resume-after-chat"
        ),
        "chat_instruction": (
            "处理机会发现 Runtime 的 Chat Task："
            f"{task_id}。请读取服务器标准任务，使用项目证据工具完成语义审计，"
            "提交结构化 Resolution，通过 Program Validator 后自动恢复 Pipeline。"
        ),
    }
    (task_dir / "chat_task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (task_dir / "input.json").write_text(
        json.dumps(request_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    prompt_path = (
        ROOT
        / "runtime"
        / "ai_runtime"
        / "prompts"
        / "market_map_semantic_audit_v0.1.md"
    )
    if prompt_path.exists():
        (task_dir / "prompt_snapshot.md").write_text(
            prompt_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    with _lock:
        if acquisition_job_id in _jobs:
            _jobs[acquisition_job_id]["chat_task_id"] = task_id
            _jobs[acquisition_job_id]["chat_task_path"] = str(
                task_dir / "chat_task.json"
            )
    if acquisition_job_id in _jobs:
        persist_job(acquisition_job_id)

    return task


def continue_pipeline_calculation(
    pipeline_job_id: str,
    acquisition_job_id: str,
    cutoff: str,
) -> None:
    update_controller_job(
        pipeline_job_id,
        status="RUNNING",
        phase="CALCULATION",
    )

    calculation_request = CalculationRunRequest(
        input_mode="LATEST_SUCCESS",
        market_cutoff=cutoff,
        source_job_id=acquisition_job_id,
    )
    calculation_job_id = create_calculation_run(
        calculation_request
    )["job_id"]
    update_controller_job(
        pipeline_job_id,
        calculation_job_id=calculation_job_id,
    )

    calculation_job = wait_for_job_terminal(calculation_job_id)
    calculation_status = calculation_job.get("status")

    if calculation_status in {"PASS", "WARNING"}:
        update_controller_job(
            pipeline_job_id,
            status=calculation_status,
            phase="COMPLETE",
            completed_at=utcnow(),
            snapshot_path=calculation_job.get("snapshot_path"),
            calculated_table_path=calculation_job.get(
                "calculated_table_path"
            ),
            output_contract_path=calculation_job.get(
                "output_contract_path"
            ),
            output_contract_validation_path=calculation_job.get(
                "output_contract_validation_path"
            ),
        )
        return

    update_controller_job(
        pipeline_job_id,
        status="FAIL",
        phase="STOPPED_AT_CALCULATION",
        completed_at=utcnow(),
        error=f"calculation ended with status={calculation_status}",
    )


def run_semantic_ai_for_acquisition(
    *,
    acquisition_job_id: str,
    acquisition_run_dir: Path,
) -> dict:
    request_path = acquisition_run_dir / "semantic_review_request.json"
    if not request_path.exists():
        return {
            "status": "FAIL",
            "error": "semantic_review_request.json is missing",
        }

    provider_name = os.environ.get("AI_PROVIDER")
    model = os.environ.get("AI_MODEL")

    if not provider_name or not model:
        return {
            "status": "FAIL",
            "error": "AI provider/model is not configured",
        }

    result = run_ai_job(
        root=ROOT,
        data_root=DATA_ROOT,
        task_type="MARKET_MAP_SEMANTIC_AUDIT",
        input_file=request_path,
        business_run_dir=acquisition_run_dir,
        provider_name=provider_name,
        provider_config={},
        model_config={
            "model": model,
            "temperature": 0,
            "max_tokens": 1800,
        },
    )

    ai_job_id = result.get("ai_job_id")
    ai_status = result.get("status")

    with _lock:
        if acquisition_job_id in _jobs:
            _jobs[acquisition_job_id]["ai_job_id"] = ai_job_id
            _jobs[acquisition_job_id]["ai_status"] = ai_status
            _jobs[acquisition_job_id]["semantic_resolution_path"] = (
                str(acquisition_run_dir / "semantic_resolution.json")
                if (acquisition_run_dir / "semantic_resolution.json").exists()
                else None
            )
            _jobs[acquisition_job_id]["semantic_resolution_validation_path"] = (
                str(acquisition_run_dir / "semantic_resolution_validation.json")
                if (acquisition_run_dir / "semantic_resolution_validation.json").exists()
                else None
            )
            _jobs[acquisition_job_id]["semantic_evidence_manifest_path"] = (
                str(acquisition_run_dir / "semantic_evidence_manifest.json")
                if (acquisition_run_dir / "semantic_evidence_manifest.json").exists()
                else None
            )
            _jobs[acquisition_job_id]["trusted_market_input_path"] = (
                str(acquisition_run_dir / "trusted_market_input.csv")
                if (acquisition_run_dir / "trusted_market_input.csv").exists()
                else None
            )

    if acquisition_job_id in _jobs:
        persist_job(acquisition_job_id)

    return result


def market_map_pipeline_worker(
    pipeline_job_id: str,
    request: MarketMapPipelineRequest,
) -> None:
    if (
        request.snapshot_mode == "HISTORICAL_REPLAY"
        and request.historical_snapshot_id
    ):
        historical_entry, _ = resolve_historical_source(
            request.historical_snapshot_id
        )
        cutoff = historical_entry.get("market_cutoff")
    else:
        cutoff = request.market_cutoff or datetime.now().date().isoformat()

    try:
        update_controller_job(
            pipeline_job_id,
            status="RUNNING",
            phase="ACQUISITION",
            command_started_at=utcnow(),
        )

        acquisition_request = RunRequest(
            snapshot_mode=request.snapshot_mode,
            market_cutoff=cutoff,
            historical_snapshot_id=request.historical_snapshot_id,
        )
        acquisition_job_id = create_run(acquisition_request)["job_id"]
        update_controller_job(
            pipeline_job_id,
            acquisition_job_id=acquisition_job_id,
        )

        acquisition_job = wait_for_job_terminal(acquisition_job_id)
        acquisition_status = acquisition_job.get("status")
        acquisition_run_dir = DATA_ROOT / "runs" / acquisition_job_id

        if acquisition_status == "NEEDS_REVIEW":
            if request.ai_execution_mode == "INTERACTIVE_CHAT":
                chat_task = create_interactive_chat_task(
                    pipeline_job_id=pipeline_job_id,
                    acquisition_job_id=acquisition_job_id,
                    acquisition_run_dir=acquisition_run_dir,
                    market_cutoff=cutoff,
                )
                update_controller_job(
                    pipeline_job_id,
                    status="WAITING_FOR_CHAT",
                    phase="WAITING_FOR_CHAT",
                    chat_task_id=chat_task["task_id"],
                    chat_task_path=str(
                        CHAT_TASK_ROOT
                        / chat_task["task_id"]
                        / "chat_task.json"
                    ),
                    ai_execution_mode="INTERACTIVE_CHAT",
                )
                return

            update_controller_job(
                pipeline_job_id,
                phase="AI_SEMANTIC_AUDIT",
                ai_execution_mode="AUTO_API",
            )
            ai_result = run_semantic_ai_for_acquisition(
                acquisition_job_id=acquisition_job_id,
                acquisition_run_dir=acquisition_run_dir,
            )
            update_controller_job(
                pipeline_job_id,
                ai_job_id=ai_result.get("ai_job_id"),
                ai_status=ai_result.get("status"),
            )

            if ai_result.get("status") != "PASS":
                update_controller_job(
                    pipeline_job_id,
                    status=(
                        "NEEDS_REVIEW"
                        if ai_result.get("status") == "NEEDS_REVIEW"
                        else "FAIL"
                    ),
                    phase="STOPPED_AT_AI",
                    completed_at=utcnow(),
                    error=ai_result.get("error"),
                )
                return

            source_result_path = acquisition_run_dir / "acquisition_result.json"
            source_status = None
            if source_result_path.exists():
                source_status = json.loads(
                    source_result_path.read_text(encoding="utf-8")
                ).get("status")

            if not acquisition_input_is_trusted(
                acquisition_run_dir,
                source_status,
            ):
                update_controller_job(
                    pipeline_job_id,
                    status="FAIL",
                    phase="STOPPED_AFTER_AI_VALIDATION",
                    completed_at=utcnow(),
                    error="AI job passed but acquisition input is still not trusted",
                )
                return

        elif acquisition_status not in {"PASS", "WARNING"}:
            update_controller_job(
                pipeline_job_id,
                status="FAIL",
                phase="STOPPED_AT_ACQUISITION",
                completed_at=utcnow(),
                error=f"acquisition ended with status={acquisition_status}",
            )
            return

        continue_pipeline_calculation(
            pipeline_job_id,
            acquisition_job_id,
            cutoff,
        )

    except Exception as exc:
        update_controller_job(
            pipeline_job_id,
            status="FAIL",
            phase="CONTROLLER_ERROR",
            completed_at=utcnow(),
            error=f"{type(exc).__name__}: {exc}",
        )


def acquisition_input_is_trusted(path: Path, status: str | None) -> bool:
    trusted_path = path / "trusted_market_input.csv"
    if not trusted_path.exists():
        return False

    if status in {"PASS", "WARNING"}:
        return True

    if status == "NEEDS_REVIEW":
        validation_path = path / "semantic_resolution_validation.json"
        if not validation_path.exists():
            return False
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            return validation.get("status") == "PASS"
        except Exception:
            return False

    return False


def load_persisted_jobs() -> list[dict]:
    jobs: list[dict] = []
    jobs_dir = DATA_ROOT / "jobs"
    if not jobs_dir.exists():
        return jobs

    for path in jobs_dir.glob("*/live_status.json"):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue

    return jobs


def find_latest_acquisition_input() -> tuple[str, Path] | None:
    candidates: list[dict] = []

    with _lock:
        candidates.extend(_jobs.values())
    candidates.extend(load_persisted_jobs())

    valid: list[dict] = []
    seen: set[str] = set()

    for job in candidates:
        job_id = job.get("job_id")
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)

        if job.get("unit") != "Market Map Builder / Acquisition":
            continue
        path = DATA_ROOT / "runs" / job_id
        if not acquisition_input_is_trusted(path, job.get("status")):
            continue

        valid.append(job)

    if not valid:
        return None

    valid.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    latest = valid[0]
    return latest["job_id"], DATA_ROOT / "runs" / latest["job_id"]


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/review", response_class=HTMLResponse)
def review_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "review.html").read_text(encoding="utf-8"))


@app.get("/workbench", response_class=HTMLResponse)
def workbench_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "workbench.html").read_text(encoding="utf-8"))


@app.get("/run-center", response_class=HTMLResponse)
def run_center_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "workbench.html").read_text(encoding="utf-8"))


@app.get("/market-map", response_class=HTMLResponse)
def market_map_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/opportunities", response_class=HTMLResponse)
def opportunities_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "workbench.html").read_text(encoding="utf-8"))


@app.get("/opportunities/{bond_code}", response_class=HTMLResponse)
def opportunity_detail_page(bond_code: str) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "workbench.html").read_text(encoding="utf-8"))


@app.get("/opportunities-v2-preview/{bond_code}", response_class=HTMLResponse)
def opportunity_v2_preview_page(bond_code: str) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "workbench.html").read_text(encoding="utf-8"))


@app.get("/audit", response_class=HTMLResponse)
def audit_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "workbench.html").read_text(encoding="utf-8"))


@app.post("/api/runs")
def create_run(request: RunRequest) -> dict:
    if request.snapshot_mode not in {
        "LIVE_TEST",
        "CLOSE",
        "REPLAY_TEST",
        "HISTORICAL_REPLAY",
    }:
        raise HTTPException(400, "unsupported snapshot_mode")

    if request.snapshot_mode == "REPLAY_TEST" and not RAW_FIXTURE_DIR.exists():
        raise HTTPException(400, "replay fixture is not available")

    historical_entry = None
    if request.snapshot_mode == "HISTORICAL_REPLAY":
        if not request.historical_snapshot_id:
            raise HTTPException(400, "historical_snapshot_id is required")
        historical_entry, _ = resolve_historical_source(
            request.historical_snapshot_id
        )

    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    cutoff = (
        historical_entry.get("market_cutoff")
        if historical_entry is not None
        else (request.market_cutoff or datetime.now().date().isoformat())
    )

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "unit": "Market Map Builder / Acquisition",
            "status": "PENDING",
            "snapshot_mode": request.snapshot_mode,
            "historical_snapshot_id": request.historical_snapshot_id,
            "historical_source_acquisition_job_id": (
                historical_entry.get("acquisition_job_id")
                if historical_entry is not None
                else None
            ),
            "market_cutoff": cutoff,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "steps": {},
        }

    persist_job(job_id)

    threading.Thread(
        target=acquisition_worker,
        args=(job_id, request),
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.post("/api/calculation-runs")
def create_calculation_run(request: CalculationRunRequest) -> dict:
    if request.input_mode not in {"REPLAY_TEST", "LATEST_SUCCESS"}:
        raise HTTPException(400, "unsupported input_mode")

    source_job_id = None

    if request.input_mode == "REPLAY_TEST":
        input_dir = CALC_FIXTURE_DIR
        if not (input_dir / "market_input_audit.csv").exists():
            raise HTTPException(400, "calculation replay fixture is not available")

    else:
        if request.source_job_id:
            input_dir = DATA_ROOT / "runs" / request.source_job_id
            source_job_id = request.source_job_id

            if not (input_dir / "trusted_market_input.csv").exists():
                raise HTTPException(400, "source acquisition job has no trusted market input")

            result_path = input_dir / "acquisition_result.json"
            if not result_path.exists():
                raise HTTPException(400, "source acquisition result is missing")
            source_result = json.loads(result_path.read_text(encoding="utf-8"))
            if not acquisition_input_is_trusted(
                input_dir,
                source_result.get("status"),
            ):
                raise HTTPException(
                    400,
                    "source acquisition job is not trusted for calculation",
                )
        else:
            latest = find_latest_acquisition_input()
            if latest is None:
                raise HTTPException(400, "no successful acquisition run is available")
            source_job_id, input_dir = latest

    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    cutoff = request.market_cutoff or datetime.now().date().isoformat()

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "unit": "Market Map Builder / Calculation",
            "status": "PENDING",
            "input_mode": request.input_mode,
            "source_job_id": source_job_id,
            "input_dir": str(input_dir),
            "market_cutoff": cutoff,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "steps": {},
        }

    persist_job(job_id)

    threading.Thread(
        target=calculation_worker,
        args=(job_id, request, input_dir),
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.post("/api/market-map-runs")
def create_market_map_pipeline(
    request: MarketMapPipelineRequest,
) -> dict:
    if request.snapshot_mode not in {
        "LIVE_TEST",
        "CLOSE",
        "REPLAY_TEST",
        "HISTORICAL_REPLAY",
    }:
        raise HTTPException(400, "unsupported snapshot_mode")

    if request.snapshot_mode == "REPLAY_TEST" and not RAW_FIXTURE_DIR.exists():
        raise HTTPException(400, "replay fixture is not available")

    historical_entry = None
    if request.snapshot_mode == "HISTORICAL_REPLAY":
        if not request.historical_snapshot_id:
            raise HTTPException(400, "historical_snapshot_id is required")
        historical_entry, _ = resolve_historical_source(
            request.historical_snapshot_id
        )

    if request.ai_execution_mode not in {"AUTO_API", "INTERACTIVE_CHAT"}:
        raise HTTPException(400, "unsupported ai_execution_mode")

    if request.snapshot_mode == "CLOSE":
        market_status = close_mode_precheck()
        if not market_status["after_close_gate"]:
            raise HTTPException(
                409,
                "今天正式收盘截面尚未可用：中国市场时间 "
                f"{market_status['china_time']}，请在 15:10 后运行；"
                "当前请使用历史回放或盘中测试。",
            )
        request.market_cutoff = market_status["china_date"]

    job_id = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_pipeline_"
        + uuid.uuid4().hex[:6]
    )
    cutoff = (
        historical_entry.get("market_cutoff")
        if historical_entry is not None
        else (request.market_cutoff or datetime.now().date().isoformat())
    )

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "unit": "Market Map / Full Runtime",
            "status": "PENDING",
            "phase": "PENDING",
            "snapshot_mode": request.snapshot_mode,
            "ai_execution_mode": request.ai_execution_mode,
            "historical_snapshot_id": request.historical_snapshot_id,
            "historical_source_acquisition_job_id": (
                historical_entry.get("acquisition_job_id")
                if historical_entry is not None
                else None
            ),
            "market_cutoff": cutoff,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "acquisition_job_id": None,
            "ai_job_id": None,
            "ai_status": None,
            "calculation_job_id": None,
            "steps": {},
        }

    persist_job(job_id)

    threading.Thread(
        target=market_map_pipeline_worker,
        args=(job_id, request),
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.get("/api/market-map/history")
def get_market_map_history() -> dict:
    entries = list_formal_market_map_entries()
    return {
        "count": len(entries),
        "items": [
            {
                "snapshot_id": x.get("snapshot_id"),
                "market_cutoff": x.get("market_cutoff"),
                "model_version": x.get("model_version"),
                "created_at": x.get("created_at"),
                "acquisition_job_id": x.get("acquisition_job_id"),
                "calculation_job_id": x.get("calculation_job_id"),
                "replay_ready": x.get("replay_ready", False),
            }
            for x in entries
        ],
    }


@app.get("/api/chat-tasks/{task_id}")
def get_chat_task(task_id: str) -> dict:
    task_path = CHAT_TASK_ROOT / task_id / "chat_task.json"
    if not task_path.exists():
        raise HTTPException(404, "chat task not found")
    task = json.loads(task_path.read_text(encoding="utf-8"))

    business_run_dir = Path(task["business_run_dir"])
    validation_path = business_run_dir / "semantic_resolution_validation.json"
    if validation_path.exists():
        try:
            validation = json.loads(
                validation_path.read_text(encoding="utf-8")
            )
            task["validation_status"] = validation.get("status")
        except Exception:
            task["validation_status"] = "INVALID"
    return task


@app.post("/api/market-map-runs/{pipeline_job_id}/resume-after-chat")
def resume_market_map_after_chat(pipeline_job_id: str) -> dict:
    with _lock:
        job = dict(_jobs.get(pipeline_job_id, {}))

    if not job:
        persisted = DATA_ROOT / "jobs" / pipeline_job_id / "live_status.json"
        if not persisted.exists():
            raise HTTPException(404, "pipeline job not found")
        job = json.loads(persisted.read_text(encoding="utf-8"))
        with _lock:
            _jobs[pipeline_job_id] = job

    if job.get("status") != "WAITING_FOR_CHAT":
        raise HTTPException(
            400,
            f"pipeline is not waiting for chat: {job.get('status')}",
        )

    acquisition_job_id = job.get("acquisition_job_id")
    if not acquisition_job_id:
        raise HTTPException(400, "pipeline has no acquisition job")

    acquisition_run_dir = DATA_ROOT / "runs" / acquisition_job_id
    result_path = acquisition_run_dir / "acquisition_result.json"
    if not result_path.exists():
        raise HTTPException(400, "acquisition result is missing")
    source_status = json.loads(
        result_path.read_text(encoding="utf-8")
    ).get("status")

    if not acquisition_input_is_trusted(
        acquisition_run_dir,
        source_status,
    ):
        raise HTTPException(
            409,
            "Chat result has not passed Program Validator yet",
        )

    update_controller_job(
        pipeline_job_id,
        status="RUNNING",
        phase="RESUMING_AFTER_CHAT",
        ai_status="PASS",
    )

    threading.Thread(
        target=continue_pipeline_calculation,
        args=(
            pipeline_job_id,
            acquisition_job_id,
            job.get("market_cutoff") or datetime.now().date().isoformat(),
        ),
        daemon=True,
    ).start()

    return {
        "pipeline_job_id": pipeline_job_id,
        "status": "RUNNING",
        "phase": "RESUMING_AFTER_CHAT",
    }


@app.post("/api/opportunity/full-runs")
def create_opportunity_full_run(request: OpportunityFullRunRequest) -> dict:
    if request.source_mode not in {"LATEST_FORMAL", "CLOSE"}:
        raise HTTPException(400, "unsupported source_mode")

    planned_cutoff = None
    if request.source_mode == "CLOSE":
        policy = close_mode_precheck()
        planned_cutoff = policy["china_date"]
        if not policy["is_trade_day"]:
            raise HTTPException(
                409,
                "今天不是交易日，正式 Full Runtime 只在交易日收盘后运行。",
            )
        if not policy["after_close_gate"]:
            raise HTTPException(
                409,
                f"今天正式收盘截面尚未可用，请在 {policy['close_gate_time']} 后运行。",
            )
        if policy["formal_run_completed"]:
            raise HTTPException(
                409,
                "今天的正式收盘 Full Runtime 已经完成；同一收盘截面不重复正式运行。",
            )
        active = [
            job for job in _full_run_candidates()
            if job.get("market_cutoff") == policy["china_date"]
            and job.get("status") in {"PENDING", "RUNNING"}
        ]
        if active:
            raise HTTPException(
                409,
                "今天的正式 Full Runtime 已经在运行中，请查看当前运行进度。",
            )

    job_id = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_opportunity_full_"
        + uuid.uuid4().hex[:6]
    )
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "unit": "Opportunity Discovery / Full Runtime",
            "status": "PENDING",
            "phase": "PENDING",
            "source_mode": request.source_mode,
            "market_cutoff": planned_cutoff,
            "run_research": request.run_research,
            "research_batch_limit": request.research_batch_limit,
            "max_research_rounds": request.max_research_rounds,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "full_stages": [],
        }
    persist_job(job_id)
    threading.Thread(
        target=opportunity_full_worker,
        args=(job_id, request),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/opportunity/full-runs/latest")
def latest_opportunity_full_run() -> dict:
    candidates = []
    with _lock:
        candidates.extend(
            dict(job) for job in _jobs.values()
            if job.get("unit") == "Opportunity Discovery / Full Runtime"
        )
    for job in load_persisted_jobs():
        if job.get("unit") == "Opportunity Discovery / Full Runtime":
            candidates.append(job)
    if candidates:
        candidates.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return candidates[0]
    if LATEST_FULL_RUNTIME_PATH.exists():
        pointer = json.loads(LATEST_FULL_RUNTIME_PATH.read_text(encoding="utf-8"))
        status_path = Path(str(pointer.get("status_path") or ""))
        if status_path.exists():
            return json.loads(status_path.read_text(encoding="utf-8"))
    raise HTTPException(404, "no full opportunity runtime has completed")


@app.get("/api/runs/{job_id}")
def get_run(job_id: str) -> dict:
    with _lock:
        if job_id not in _jobs:
            path = DATA_ROOT / "jobs" / job_id / "live_status.json"
            if not path.exists():
                raise HTTPException(404, "run not found")
            _jobs[job_id] = json.loads(path.read_text(encoding="utf-8"))

        return _jobs[job_id]



def find_latest_market_map_output() -> tuple[dict, Path, Path] | None:
    """
    Visualization only follows the Formal Snapshot Registry.

    The web layer must not guess the latest model from file mtimes and must
    not silently fall back to TEST_ONLY calculation outputs.
    """
    if not LATEST_FORMAL_MARKET_MAP_PATH.exists():
        return None

    try:
        entry = json.loads(
            LATEST_FORMAL_MARKET_MAP_PATH.read_text(encoding="utf-8")
        )
    except Exception:
        return None

    snapshot_path_raw = entry.get("snapshot_path")
    table_path_raw = entry.get("calculated_table_path")
    if not snapshot_path_raw or not table_path_raw:
        return None

    snapshot_path = Path(snapshot_path_raw)
    table_path = Path(table_path_raw)
    if not snapshot_path.exists() or not table_path.exists():
        return None

    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if snapshot.get("snapshot_class") != "FORMAL_CLOSE":
        return None
    if snapshot.get("snapshot_id") != entry.get("snapshot_id"):
        return None

    return entry, snapshot_path, table_path


def find_market_map_output_for_calculation_job(
    calculation_job_id: str,
) -> tuple[dict, Path, Path] | None:
    run_dir = DATA_ROOT / "runs" / calculation_job_id
    snapshot_path = run_dir / "market_map_snapshot.json"
    table_path = run_dir / "market_map_calculated.csv"
    if not snapshot_path.exists() or not table_path.exists():
        return None
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if snapshot.get("snapshot_class") not in {
        "FORMAL_CLOSE",
        "HISTORICAL_REPLAY",
    }:
        return None

    source_job_id = None
    metadata_path = run_dir / "run_metadata.json"
    if metadata_path.exists():
        try:
            source_job_id = json.loads(
                metadata_path.read_text(encoding="utf-8")
            ).get("source_job_id")
        except Exception:
            source_job_id = None

    entry = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "calculation_job_id": calculation_job_id,
        "acquisition_job_id": source_job_id,
    }
    return entry, snapshot_path, table_path


@app.get("/api/market-map/view")
def get_market_map_view(calculation_job_id: str | None = None) -> dict:
    found = (
        find_market_map_output_for_calculation_job(calculation_job_id)
        if calculation_job_id
        else find_latest_market_map_output()
    )
    if found is None:
        raise HTTPException(
            404,
            "no formal market map snapshot is registered",
        )

    registry_entry, snapshot_path, table_path = found
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    df = pd.read_csv(table_path, dtype={"bond_code": str})

    reference_col = (
        "discovery_reference"
        if "discovery_reference" in df.columns
        else "discovery_reference_candidate"
    )

    wanted = [
        "bond_code",
        "bond_name",
        "P",
        "trusted_CV",
        "source_CV",
        "remaining_months",
        "remaining_size",
        "base_anchor",
        "duration_adjustment",
        "scale_neutral",
        "anchor_neutral",
        "residual_base",
        "residual_after_duration",
        "residual_final",
        reference_col,
    ]
    missing = [col for col in wanted if col not in df.columns]
    if missing:
        raise HTTPException(500, f"market map output missing columns: {missing}")

    view = df[wanted].copy()
    if reference_col != "discovery_reference":
        view = view.rename(columns={reference_col: "discovery_reference"})

    numeric_cols = [
        col for col in view.columns
        if col not in {"bond_code", "bond_name"}
    ]
    for col in numeric_cols:
        view[col] = pd.to_numeric(view[col], errors="coerce")

    view["diff_to_reference"] = (
        view["P"] - view["discovery_reference"]
    )

    rows = json.loads(
        view.to_json(
            orient="records",
            force_ascii=False,
        )
    )

    return {
        "source_type": (
            "HISTORICAL_REPLAY"
            if snapshot.get("snapshot_class") == "HISTORICAL_REPLAY"
            else "FORMAL_REGISTRY"
        ),
        "snapshot_class": snapshot.get("snapshot_class"),
        "market_cutoff": snapshot.get("market_cutoff"),
        "model_version": snapshot.get("model_version"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "registry_calculation_job_id": registry_entry.get("calculation_job_id"),
        "registry_acquisition_job_id": registry_entry.get("acquisition_job_id"),
        "zones": snapshot.get("zones", {}),
        "components": snapshot.get("components", {}),
        "residual_core_after_scale": snapshot.get("residual_core_after_scale", {}),
        "diagnostics": snapshot.get("diagnostics", {}),
        "discovery_reference": snapshot.get("discovery_reference", {}),
        "acquisition_warnings": snapshot.get("input", {}).get(
            "acquisition_warnings", []
        ),
        "rows": rows,
    }


@app.post("/api/market-map/resolve")
def resolve_market_map_bond(
    request: BondValuationResolverRequest,
) -> dict:
    if request.snapshot_id:
        entry = get_formal_market_map_entry(request.snapshot_id)
    else:
        if not LATEST_FORMAL_MARKET_MAP_PATH.exists():
            raise HTTPException(404, "no formal market map snapshot is registered")
        try:
            entry = json.loads(
                LATEST_FORMAL_MARKET_MAP_PATH.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise HTTPException(
                500,
                f"latest formal market map registry is invalid: {type(exc).__name__}",
            )

    contract_path_raw = entry.get("output_contract_path")
    contract_status = entry.get("output_contract_status")
    if not contract_path_raw or contract_status != "PASS":
        raise HTTPException(
            409,
            "selected formal snapshot does not expose a validated "
            "Market Map Output Contract V1",
        )

    try:
        return resolve_bond_scenarios(
            Path(contract_path_raw),
            bond_code=request.bond_code,
            scenarios=[x.dict() for x in request.scenarios],
            require_formal=True,
        )
    except ResolverError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/opportunity/market-ingress")
def create_discovery_market_ingress() -> dict:
    """Use the latest validated formal Market Map as the Discovery market input."""
    if not LATEST_FORMAL_MARKET_MAP_PATH.exists():
        raise HTTPException(404, "no formal market map snapshot is registered")
    try:
        entry = json.loads(LATEST_FORMAL_MARKET_MAP_PATH.read_text(encoding="utf-8"))
        if entry.get("snapshot_class") != "FORMAL_CLOSE" or entry.get("output_contract_status") != "PASS":
            raise ResolverError("latest formal snapshot has no validated output contract")
        if not entry.get("snapshot_id"):
            raise ResolverError("latest formal snapshot has no snapshot_id")
        manifest_path = Path(entry["output_contract_path"])
        result = build_market_ingress(manifest_path, DATA_ROOT, load_deployment_manifest(),
                                      expected_snapshot_id=entry.get("snapshot_id"))
        LATEST_DISCOVERY_INGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        LATEST_DISCOVERY_INGRESS_PATH.write_text(
            json.dumps({"run_id": result["run_id"], "market_snapshot_id": result["market_snapshot_id"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return result
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, f"discovery market ingress failed: {exc}")


@app.get("/api/opportunity/market-ingress/latest")
def latest_discovery_market_ingress() -> dict:
    if not LATEST_DISCOVERY_INGRESS_PATH.exists():
        raise HTTPException(404, "no discovery market ingress run has completed")
    try:
        pointer = json.loads(LATEST_DISCOVERY_INGRESS_PATH.read_text(encoding="utf-8"))
        run_id = str(pointer["run_id"])
        if not run_id.replace("_", "").isalnum():
            raise ValueError("invalid discovery run_id")
        result_path = DATA_ROOT / "runs" / run_id / "discovery_market_input.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("run_id") != run_id or result.get("market_snapshot_id") != pointer.get("market_snapshot_id"):
            raise ValueError("discovery ingress pointer and result disagree")
        return result
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, f"discovery market ingress result is invalid: {exc}")


def _load_latest_runtime_artifact(pointer_path: Path, label: str) -> dict:
    if not pointer_path.exists():
        raise HTTPException(404, f"no {label} run has completed")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        run_id = str(pointer["run_id"])
        result_path = Path(str(pointer["result_path"]))
        if not result_path.is_absolute():
            result_path = ROOT / result_path
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("run_id") != run_id:
            raise ValueError(f"{label} pointer and result disagree")
        if result.get("status") != "PASS":
            raise ValueError(f"{label} latest result is not PASS")
        return result
    except HTTPException:
        raise
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, f"{label} result is invalid: {exc}")


@app.get("/api/opportunity/candidate-pool/latest")
def latest_candidate_pool() -> dict:
    return _load_latest_runtime_artifact(
        LATEST_CANDIDATE_POOL_PATH,
        "candidate pool",
    )


@app.get("/api/opportunity/records/latest")
def latest_opportunity_records() -> dict:
    return _load_latest_runtime_artifact(
        LATEST_OPPORTUNITY_RECORDS_PATH,
        "opportunity records",
    )


@app.get("/api/opportunity/view/latest")
def latest_opportunity_view() -> dict:
    records = _load_latest_runtime_artifact(
        LATEST_OPPORTUNITY_RECORDS_PATH,
        "opportunity records",
    )
    return build_opportunity_list(records)


@app.get("/api/opportunity/view/{bond_code}")
def latest_opportunity_view_for_bond(bond_code: str) -> dict:
    record = latest_opportunity_record_for_bond(bond_code)
    return build_opportunity_view(record)


@app.get("/api/opportunity/records/{bond_code}")
def latest_opportunity_record_for_bond(bond_code: str) -> dict:
    code = str(bond_code).strip().zfill(6)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "bond_code must be a 6-digit code")

    records = _load_latest_runtime_artifact(
        LATEST_OPPORTUNITY_RECORDS_PATH,
        "opportunity records",
    )
    matches = [
        item for item in records.get("records", [])
        if str(item.get("bond_code") or "").zfill(6) == code
    ]
    if len(matches) != 1:
        raise HTTPException(
            404 if not matches else 409,
            "bond is not uniquely present in latest opportunity records",
        )
    return matches[0]


@app.get("/api/opportunity/preview-v2/{bond_code}")
def opportunity_v2_preview_for_bond(bond_code: str) -> dict:
    code = str(bond_code).strip().zfill(6)
    replacements = GOLDEN_V2_PATH_RESULTS.get(code)
    if not replacements:
        raise HTTPException(404, "no Path Result V2 golden preview for this bond")

    record = copy.deepcopy(latest_opportunity_record_for_bond(code))
    replaced_paths: list[str] = []
    for path in record.get("paths", []):
        path_id = str(path.get("path_id") or "")
        result_path = replacements.get(path_id)
        if result_path is None:
            continue
        if not result_path.exists():
            raise HTTPException(
                409,
                f"golden V2 result is missing for {code}:{path_id}",
            )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if (
            str(result.get("bond_code") or "").zfill(6) != code
            or result.get("path_id") != path_id
            or result.get("review_ready") is not True
        ):
            raise HTTPException(
                409,
                f"golden V2 result failed identity/readiness check: {code}:{path_id}",
            )
        path["path_result"] = result
        path["research_state"] = "COMPLETED"
        path["research_status"] = result.get("research_status")
        path["review_ready"] = True
        path["latest_path_result_id"] = result.get("path_result_id")
        path["latest_path_result_path"] = str(result_path)
        replaced_paths.append(path_id)

    view = build_opportunity_view(record)
    view["preview"] = {
        "type": "PATH_RESULT_V2_GOLDEN_SAMPLE",
        "is_formal_ledger": False,
        "message": (
            "这是 Path Result V2 隔离 Golden Sample 预览；"
            "未写入 Research Ledger，不覆盖当前正式研究结果。"
        ),
        "replaced_paths": replaced_paths,
    }
    return view


@app.get("/api/market-status")
def market_status() -> dict:
    status = close_mode_precheck()
    history = list_formal_market_map_entries()
    latest_replay = next(
        (x for x in history if x.get("replay_ready")),
        None,
    )
    status["latest_replay"] = (
        {
            "snapshot_id": latest_replay.get("snapshot_id"),
            "market_cutoff": latest_replay.get("market_cutoff"),
            "model_version": latest_replay.get("model_version"),
        }
        if latest_replay
        else None
    )
    return status


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "units": [
            "Market Map Builder / Acquisition",
            "Market Map Builder / Calculation",
        ],
        "root": str(ROOT),
        "data_root": str(DATA_ROOT),
        "acquisition_script_exists": ACQ_SCRIPT.exists(),
        "calculation_script_exists": CALC_SCRIPT.exists(),
        "replay_fixture_exists": RAW_FIXTURE_DIR.exists(),
        "calculation_fixture_exists": CALC_FIXTURE_DIR.exists(),
    }
