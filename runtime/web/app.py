from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import pandas as pd

from runtime.ai_runtime.engine import run_ai_job

ROOT = Path(os.environ.get("RUNTIME_ROOT", Path(__file__).resolve().parents[2]))
DATA_ROOT = Path(os.environ.get("RUNTIME_DATA_ROOT", ROOT / "runtime_data"))
DEPLOYMENT_MANIFEST_PATH = DATA_ROOT / "deployment_manifest.json"
MARKET_MAP_REGISTRY_DIR = DATA_ROOT / "registry" / "market_map_snapshots"
LATEST_FORMAL_MARKET_MAP_PATH = DATA_ROOT / "registry" / "latest_formal_market_map.json"

ACQ_SCRIPT = ROOT / "runtime" / "market_map" / "acquisition.py"
CALC_SCRIPT = ROOT / "runtime" / "market_map" / "calculation.py"
RUNTIME_PYTHON = os.environ.get("ACQUISITION_PYTHON", "python3")

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


class CalculationRunRequest(BaseModel):
    input_mode: str = "REPLAY_TEST"
    market_cutoff: str | None = None
    source_job_id: str | None = None


class MarketMapPipelineRequest(BaseModel):
    snapshot_mode: str = "CLOSE"
    market_cutoff: str | None = None


TERMINAL_JOB_STATUSES = {"PASS", "WARNING", "FAIL", "NEEDS_REVIEW"}


@app.middleware("http")
async def runtime_basic_auth(request, call_next):
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
        "calculation_job_id": job.get("calculation_job_id"),
        "snapshot_mode": job.get("snapshot_mode"),
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


def register_formal_market_map_snapshot(job_id: str) -> None:
    with _lock:
        job = dict(_jobs.get(job_id, {}))
    if not job:
        return

    snapshot_path_raw = job.get("snapshot_path")
    table_path_raw = job.get("calculated_table_path")
    if not snapshot_path_raw or not table_path_raw:
        return

    snapshot_path = Path(snapshot_path_raw)
    table_path = Path(table_path_raw)
    if not snapshot_path.exists() or not table_path.exists():
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
        },
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


@app.post("/api/runs")
def create_run(request: RunRequest) -> dict:
    if request.snapshot_mode not in {"LIVE_TEST", "CLOSE", "REPLAY_TEST"}:
        raise HTTPException(400, "unsupported snapshot_mode")

    if request.snapshot_mode == "REPLAY_TEST" and not RAW_FIXTURE_DIR.exists():
        raise HTTPException(400, "replay fixture is not available")

    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    cutoff = request.market_cutoff or datetime.now().date().isoformat()

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "unit": "Market Map Builder / Acquisition",
            "status": "PENDING",
            "snapshot_mode": request.snapshot_mode,
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


@app.get("/api/market-map/view")
def get_market_map_view() -> dict:
    found = find_latest_market_map_output()
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
        "source_type": "FORMAL_REGISTRY",
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
