from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(os.environ.get("RUNTIME_ROOT", Path(__file__).resolve().parents[2]))
DATA_ROOT = Path(os.environ.get("RUNTIME_DATA_ROOT", ROOT / "runtime_data"))
ACQ_SCRIPT = ROOT / "runtime" / "market_map" / "acquisition.py"
ACQ_PYTHON = os.environ.get("ACQUISITION_PYTHON", "python3")
STATIC_DIR = Path(__file__).parent / "static"
FIXTURE_DIR = Path(
    os.environ.get(
        "RUNTIME_FIXTURE_DIR",
        DATA_ROOT / "fixtures" / "market_map_20260924_intraday",
    )
)

app = FastAPI(title="Opportunity Discovery Runtime")

RUNTIME_USER = os.environ.get("RUNTIME_USER")
RUNTIME_PASSWORD = os.environ.get("RUNTIME_PASSWORD")


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

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    snapshot_mode: str = "LIVE_TEST"
    market_cutoff: str | None = None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_job(job_id: str) -> None:
    with _lock:
        payload = dict(_jobs[job_id])
    path = DATA_ROOT / "jobs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "live_status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
        elif "WARNING" in statuses:
            job["status"] = "RUNNING_WARNING"
        else:
            job["status"] = "RUNNING"
    persist_job(job_id)


def worker(job_id: str, request: RunRequest) -> None:
    cutoff = request.market_cutoff or datetime.now().date().isoformat()
    out = DATA_ROOT / "runs" / job_id
    out.mkdir(parents=True, exist_ok=True)

    cmd = [
        ACQ_PYTHON,
        str(ACQ_SCRIPT),
        "--output", str(out),
        "--snapshot-mode", request.snapshot_mode,
        "--market-cutoff", cutoff,
    ]
    if request.snapshot_mode == "REPLAY_TEST":
        cmd.extend(["--fixture-dir", str(FIXTURE_DIR)])

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
        raw_lines = []
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
        result_path = out / "acquisition_result.json"
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
                final_result.get("status") if isinstance(final_result, dict)
                else ("PASS" if code == 0 else "FAIL")
            )
            if code != 0 and not final_result:
                job["error"] = "Runtime process exited without a structured result."
            job["raw_output_tail"] = raw_lines[-20:]
        persist_job(job_id)

    except Exception as exc:
        with _lock:
            job = _jobs[job_id]
            job["status"] = "FAIL"
            job["completed_at"] = utcnow()
            job["error"] = f"{type(exc).__name__}: {exc}"
        persist_job(job_id)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.post("/api/runs")
def create_run(request: RunRequest) -> dict:
    if request.snapshot_mode not in {"LIVE_TEST", "CLOSE", "REPLAY_TEST"}:
        raise HTTPException(400, "unsupported snapshot_mode")
    if request.snapshot_mode == "REPLAY_TEST" and not FIXTURE_DIR.exists():
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
    threading.Thread(target=worker, args=(job_id, request), daemon=True).start()
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


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "unit": "Market Map Builder / Acquisition",
        "root": str(ROOT),
        "data_root": str(DATA_ROOT),
        "acquisition_script_exists": ACQ_SCRIPT.exists(),
        "replay_fixture_exists": FIXTURE_DIR.exists(),
    }
