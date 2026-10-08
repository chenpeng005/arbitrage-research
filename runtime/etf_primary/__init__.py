"""ETF primary-market monitoring.

This package is intentionally isolated from the existing LOF runtime.
It discovers the official SSE/SZSE ETF universe, normalizes PCF
(creation/redemption basket) snapshots, and detects capacity changes.
"""

from .models import EtfIdentity, PcfSnapshot, MonitorEvent
from .classification import classify_etf
from .monitor import detect_events

__all__ = [
    "EtfIdentity",
    "PcfSnapshot",
    "MonitorEvent",
    "classify_etf",
    "detect_events",
]
