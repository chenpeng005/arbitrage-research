from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from runtime.deployment_gate import validate_deployment_gate
from runtime.intelligence_radar.daily import build_daily_plan
from runtime.intelligence_radar.final_gate import evaluate_candidate
from runtime.intelligence_radar.filtering import filter_post
from runtime.intelligence_radar.jisilu import collect_latest_posts
from runtime.intelligence_radar.storage import connect as connect_radar
from runtime.market_map.acquisition import run_market_map_acquisition
from runtime.market_map.calculation import calculate_market_map
from runtime.opportunity.candidate_pool import build_candidate_pool
from runtime.opportunity.contract_facts import extract_contract_facts
from runtime.opportunity.discovery_controller import run_discovery_controller
from runtime.opportunity.downward_revision import build_downward_revision_paths
from runtime.opportunity.full_runtime_controller import run_full_runtime_controller
from runtime.opportunity.incremental_change_notification import (
    build_notification_feed,
)
from runtime.opportunity.incremental_storage import connect as connect_incremental
from runtime.opportunity.opportunity_record import build_opportunity_records
from runtime.opportunity.put_back import build_put_back_paths
from runtime.opportunity.research_evidence import build_research_evidence
from runtime.opportunity.research_trigger import build_research_triggers
from runtime.opportunity.research_workbench import build_research_workbench
from runtime.opportunity.research_writeback import apply_research_result
from runtime.opportunity.risk_control import build_risk_controls
from runtime.opportunity.strong_redemption import build_strong_redemption_paths
from runtime.opportunity.time_anchor import build_time_anchor_paths
from runtime.opportunity.web_projection import build_web_projection
from runtime.reference.credit_risk import load_credit_risk
from runtime.reference.daily_basic import load_daily_basic
from runtime.reference.daily_price import load_daily_price
from runtime.reference.instrument_master import load_instrument_master
from runtime.reference.issuer_rating import load_issuer_rating
from runtime.reference.terms import load_terms

ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = ROOT / "runtime" / "web" / "static"
DATA_ROOT = Path(os.environ.get("RUNTIME_DATA_ROOT", ROOT / "runtime_data"))
DEPLOYMENT_PATH = DATA_ROOT / "deployment_manifest.json"
RUNS_ROOT = DATA_ROOT / "runs"
CHAT_TASKS_ROOT = DATA_ROOT / "chat_tasks"
WEB_ROOT = DATA_ROOT / "web"
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
CHAT_TASKS_ROOT.mkdir(parents=True, exist_ok=True)
WEB_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Arbitrage Runtime")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

# NOTE: full file replacement omitted in this patch attempt; this call should not be used.
