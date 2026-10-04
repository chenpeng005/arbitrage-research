from __future__ import annotations

from typing import Any


REGISTRY_VERSION = "LOF_ESTIMATE_MODEL_REGISTRY_V1"

# Single source of truth for production Estimated NAV model identity.
# Runtime result rows still carry resolver_method for compatibility, but
# persistence and validation must use model_id + model_version from here.
MODEL_REGISTRY = {
    "INDEX_PROXY_PREV_CLOSE": {
        "model_id": "R1_INDEX_PROXY",
        "model_version": "R1_INDEX_PROXY_V1",
        "resolver_class": "R1_DOMESTIC_INDEX",
        "lifecycle": "MAIN",
    },
    "CSI_COMPONENT_WEIGHT_PREV_CLOSE": {
        "model_id": "R1_CSI_COMPONENT",
        "model_version": "R1_CSI_COMPONENT_V2",
        "resolver_class": "R1_DOMESTIC_INDEX",
        "lifecycle": "MAIN",
    },
    "TARGET_ETF_PREV_CLOSE": {
        "model_id": "R1_TARGET_ETF",
        "model_version": "R1_TARGET_ETF_V1",
        "resolver_class": "R1_DOMESTIC_INDEX",
        "lifecycle": "MAIN",
    },
    "MULTIDAY_PROXY_FX_BRIDGE": {
        "model_id": "R3_MULTIDAY_FX",
        "model_version": "R3_MULTIDAY_FX_V1",
        "resolver_class": "R3_QDII_INDEX",
        "lifecycle": "MAIN",
    },
    "HK_LIVE_INDEX_FX_BRIDGE": {
        "model_id": "R3_HK_LIVE_FX",
        "model_version": "R3_HK_LIVE_FX_V1",
        "resolver_class": "R3_QDII_INDEX",
        "lifecycle": "MAIN",
    },
    "US_FUTURES_FX_BRIDGE": {
        "model_id": "R3_US_FUTURES_FX",
        "model_version": "R3_US_FUTURES_FX_V1",
        "resolver_class": "R3_QDII_INDEX",
        "lifecycle": "MAIN",
    },
    "US_FUTURES_CNH_FALLBACK_BRIDGE": {
        "model_id": "R3_US_FUTURES_CNH_FALLBACK",
        "model_version": "R3_US_FUTURES_CNH_FALLBACK_V1",
        "resolver_class": "R3_QDII_INDEX",
        "lifecycle": "MAIN",
    },
    "US_LAST_CLOSE_FX_BRIDGE": {
        "model_id": "R3_US_LAST_CLOSE_FX",
        "model_version": "R3_US_LAST_CLOSE_FX_V1",
        "resolver_class": "R3_QDII_INDEX",
        "lifecycle": "MAIN",
    },
    "US_LAST_CLOSE_CNH_FALLBACK_BRIDGE": {
        "model_id": "R3_US_LAST_CLOSE_CNH_FALLBACK",
        "model_version": "R3_US_LAST_CLOSE_CNH_FALLBACK_V1",
        "resolver_class": "R3_QDII_INDEX",
        "lifecycle": "MAIN",
    },
    "COMMODITY_FX_BRIDGE": {
        "model_id": "R5_COMMODITY_FX",
        "model_version": "R5_COMMODITY_FX_V1",
        "resolver_class": "R5_SPECIAL",
        "lifecycle": "MAIN",
    },
    "COMMODITY_BASKET_FX_BRIDGE": {
        "model_id": "R5_COMMODITY_BASKET_FX",
        "model_version": "R5_COMMODITY_BASKET_FX_V1",
        "resolver_class": "R5_SPECIAL",
        "lifecycle": "MAIN",
    },
    "COMMODITY_CNH_FALLBACK_BRIDGE": {
        "model_id": "R5_COMMODITY_CNH_FALLBACK",
        "model_version": "R5_COMMODITY_CNH_FALLBACK_V1",
        "resolver_class": "R5_SPECIAL",
        "lifecycle": "MAIN",
    },
    "COMMODITY_BASKET_CNH_FALLBACK_BRIDGE": {
        "model_id": "R5_COMMODITY_BASKET_CNH_FALLBACK",
        "model_version": "R5_COMMODITY_BASKET_CNH_FALLBACK_V1",
        "resolver_class": "R5_SPECIAL",
        "lifecycle": "MAIN",
    },
    "DOMESTIC_FUTURES_PREV_SETTLEMENT": {
        "model_id": "R5_DOMESTIC_FUTURES",
        "model_version": "R5_DOMESTIC_FUTURES_V1",
        "resolver_class": "R5_SPECIAL",
        "lifecycle": "MAIN",
    },
    "DISCLOSED_HOLDINGS_BASKET": {
        "model_id": "R2A_HOLDINGS_BASKET",
        "model_version": "R2A_HOLDINGS_BASKET_V1",
        "resolver_class": "R2_DOMESTIC_OTHER",
        "lifecycle": "MAIN",
    },
    "R2B2_CASH_HEAVY_HOLDINGS_BASKET": {
        "model_id": "R2B2_CASH_HEAVY",
        "model_version": "R2B2_CASH_HEAVY_V1",
        "resolver_class": "R2_DOMESTIC_OTHER",
        "lifecycle": "MAIN",
    },
    "RISK_ASSET_OVERLAY": {
        "model_id": "R2C_RISK_OVERLAY",
        "model_version": "R2C_RISK_OVERLAY_V1",
        "resolver_class": "R2_DOMESTIC_OTHER",
        "lifecycle": "MAIN",
    },
}


def estimate_model_spec(method: Any) -> dict:
    value = str(method or "UNKNOWN").strip() or "UNKNOWN"
    registered = MODEL_REGISTRY.get(value)
    if registered is not None:
        return {
            "method": value,
            "registered": True,
            **registered,
        }
    return {
        "method": value,
        "registered": False,
        "model_id": f"UNREGISTERED:{value}",
        "model_version": f"{value}_V1",
        "resolver_class": None,
        "lifecycle": "UNREGISTERED",
    }


def estimate_model_id(method: Any) -> str:
    return str(estimate_model_spec(method)["model_id"])


def estimate_model_version(method: Any) -> str:
    return str(estimate_model_spec(method)["model_version"])


def registry_snapshot() -> dict:
    rows = []
    for method in sorted(MODEL_REGISTRY):
        rows.append(estimate_model_spec(method))
    return {
        "registry_version": REGISTRY_VERSION,
        "model_count": len(rows),
        "models": rows,
    }
