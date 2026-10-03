from runtime.lof.nav_freshness import nav_freshness_summary


def test_r1_nav_freshness_passes_when_all_t1():
    snapshot = {
        "rows": [
            {
                "code": "160001",
                "resolver_class": "R1_DOMESTIC_INDEX",
                "official_nav_date": "2026-09-30",
                "official_nav_lag_label": "T-1",
            },
            {
                "code": "160002",
                "resolver_class": "R1_DOMESTIC_INDEX",
                "official_nav_date": "2026-09-30",
                "official_nav_lag_label": "T-1",
            },
            {
                "code": "160003",
                "resolver_class": "R2_DOMESTIC_OTHER",
                "official_nav_date": "2026-09-29",
                "official_nav_lag_label": "T-2",
            },
        ]
    }
    result = nav_freshness_summary(snapshot)
    assert result["status"] == "PASS"
    assert result["r1_total"] == 2
    assert result["r1_t1_count"] == 2
    assert result["r1_lagging_count"] == 0
    assert result["expected_nav_date"] == "2026-09-30"


def test_r1_nav_freshness_warns_and_lists_lagging_codes():
    snapshot = {
        "rows": [
            {
                "code": "160001",
                "resolver_class": "R1_DOMESTIC_INDEX",
                "official_nav_date": "2026-09-30",
                "official_nav_lag_label": "T-1",
            },
            {
                "code": "160002",
                "resolver_class": "R1_DOMESTIC_INDEX",
                "official_nav_date": "2026-09-29",
                "official_nav_lag_label": "T-2",
            },
        ]
    }
    result = nav_freshness_summary(snapshot)
    assert result["status"] == "WARN"
    assert result["r1_t1_count"] == 1
    assert result["r1_lagging_count"] == 1
    assert result["lagging_codes"] == ["160002"]
