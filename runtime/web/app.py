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
GOLDEN_V2_ROOT = DATA_ROOT / "golden_samples" / "path_result_v2"
GOLDEN_V2_PATH_RESULTS = {
    "110092": {
        "MATURITY_CASH": GOLDEN_V2_ROOT / "110092_MATURITY_CASH" / "path_research_result.json",
        "PUT": GOLDEN_V2_ROOT / "110092_PUT_v2b" / "path_research_result.json",
    },
    "127089": {
        "DOWNWARD_REVISION": GOLDEN_V2_ROOT / "127089_DOWNWARD_REVISION_v2b" / "path_research_result.json",
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


def close_mode_precheck() -> dict:
    now = china_now()
    after_close_gate = (now.hour, now.minute) >= (15, 10)
    return {
        "china_time": now.isoformat(timespec="minutes"),
        "china_date": now.date().isoformat(),
        "after_close_gate": after_close_gate,
        "close_gate_time": "15:10",
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
