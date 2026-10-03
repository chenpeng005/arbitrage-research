from __future__ import annotations


def nav_freshness_summary(snapshot: dict | None) -> dict:
    rows = (snapshot or {}).get("rows") or []
    r1_rows = [
        row
        for row in rows
        if row.get("resolver_class") == "R1_DOMESTIC_INDEX"
    ]
    fresh_rows = [
        row for row in r1_rows
        if row.get("official_nav_lag_label") == "T-1"
    ]
    lagging_rows = [
        row for row in r1_rows
        if row.get("official_nav_lag_label") != "T-1"
    ]
    expected_dates = sorted(
        {
            str(row.get("official_nav_date"))
            for row in fresh_rows
            if row.get("official_nav_date")
        }
    )
    return {
        "status": (
            "PASS"
            if r1_rows and len(fresh_rows) == len(r1_rows)
            else "WARN"
        ),
        "r1_total": len(r1_rows),
        "r1_t1_count": len(fresh_rows),
        "r1_lagging_count": len(lagging_rows),
        "expected_nav_date": (
            expected_dates[-1] if expected_dates else None
        ),
        "lagging_codes": [
            str(row.get("code") or "")
            for row in lagging_rows
            if row.get("code")
        ],
    }
