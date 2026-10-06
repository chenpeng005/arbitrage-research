from runtime.etf_primary.models import PcfSnapshot
from runtime.etf_primary.monitor import detect_events
from runtime.etf_primary.monitor_view import build_monitor_view
from runtime.etf_primary.pcf import parse_sse_basic_row, parse_szse_xml


def _snap(**kwargs):
    base = {
        "code": "513100",
        "exchange": "SSE",
        "trade_date": "20260928",
        "creation_allowed": True,
        "creation_redemption_unit": 500_000,
    }
    base.update(kwargs)
    return PcfSnapshot(**base)


def test_capacity_jump_and_account_limit():
    previous = _snap(creation_limit=2_500_000, account_creation_limit=2_500_000)
    current = _snap(creation_limit=50_000_000, account_creation_limit=500_000)
    event_types = {event.event_type for event in detect_events(previous, current)}
    assert "TOTAL_CAPACITY_JUMP" in event_types
    assert "ACCOUNT_LIMIT_IMPROVED" in event_types
    assert current.total_baskets() == 100
    assert current.account_baskets() == 1
    assert current.minimum_accounts_to_fill() == 100


def test_sse_live_upper_snake_fields_are_normalized():
    # Shape observed from the official SSE commonQuery endpoint for 513100.
    snapshot = parse_sse_basic_row(
        "513100",
        {
            "TRADING_DAY": "20260930",
            "CREATION_REDEMPTION_UNIT": "500000",
            "NAVPERCU": "￥1016765.94",
            "NAV": "￥2.0335",
            "CREATION_LIMIT": "2500000",
            "NET_CREATION_LIMIT": "-",
            "CREATION_LIMIT_PER_ACCT": "500000",
            "NET_CREATION_LIMIT_PER_ACCT": "-",
            "CREATION_REDEMPTION": "申购和赎回皆允许",
            "CREATION_REDEMPTION_MECHANISM": "0",
        },
    )
    assert snapshot.creation_allowed is True
    assert snapshot.redemption_allowed is True
    assert snapshot.total_baskets() == 5
    assert snapshot.account_baskets() == 1
    assert snapshot.basket_value() == 1016765.94


def test_szse_limit_fields_are_normalized():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <PCF xmlns="http://ts.szse.cn/Fund">
      <SecurityID>159501</SecurityID>
      <TradingDay>20260930</TradingDay>
      <Type>2</Type>
      <CreationRedemptionUnit>1000000</CreationRedemptionUnit>
      <NAVperCU>1914600</NAVperCU>
      <NAV>1.9146</NAV>
      <Creation>Y</Creation>
      <Redemption>Y</Redemption>
      <CreationLimit>180000000</CreationLimit>
      <CreationLimitPerUser>1000000</CreationLimitPerUser>
      <NetCreationLimit>0</NetCreationLimit>
      <NetCreationLimitPerUser>0</NetCreationLimitPerUser>
      <ComponentList><Component><UnderlyingSecurityID>AAPL</UnderlyingSecurityID><ComponentShare>10</ComponentShare></Component></ComponentList>
    </PCF>
    """
    snapshot = parse_szse_xml("159501", xml)
    assert snapshot.total_baskets() == 180
    assert snapshot.account_baskets() == 1
    assert snapshot.minimum_accounts_to_fill() == 180


def test_monitor_view_joins_identity_and_pcf_with_freshness_gate():
    universe = {
        "rows": [
            {
                "exchange": "SZSE",
                "code": "159501",
                "name": "纳指ETF",
                "region_scope": "CROSS_BORDER",
                "asset_class": "EQUITY",
                "strategy_style": "INDEX",
                "qdii_flag": True,
                "pcf_page_url": "https://example.invalid/159501-page",
            },
            {
                "exchange": "SSE",
                "code": "512390",
                "name": "中国低波ETF平安",
                "region_scope": "DOMESTIC",
                "asset_class": "EQUITY",
                "strategy_style": "INDEX",
                "qdii_flag": False,
                "pcf_page_url": "https://example.invalid/512390-page",
            },
            {
                "exchange": "SSE",
                "code": "510001",
                "name": "Missing ETF",
                "region_scope": "DOMESTIC",
                "asset_class": "EQUITY",
                "strategy_style": "INDEX",
                "qdii_flag": False,
                "pcf_page_url": "https://example.invalid/510001-page",
            },
        ]
    }
    pcf = {
        "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
        "target_trade_date": "20260930",
        "rows": [
            {
                "exchange": "SZSE",
                "code": "159501",
                "trade_date": "20260930",
                "creation_allowed": True,
                "redemption_allowed": True,
                "creation_redemption_unit": 1_000_000,
                "creation_limit": 180_000_000,
                "account_creation_limit": 1_000_000,
                "market_creation_limit": 180_000_000,
                "account_creation_cap": 1_000_000,
                "total_baskets": 180.0,
                "account_baskets": 1.0,
                "minimum_accounts_to_fill": 180,
                "basket_value": 1_914_600.0,
                "source_url": "https://example.invalid/159501.xml",
            },
            {
                "exchange": "SSE",
                "code": "512390",
                "trade_date": "20260904",
                "creation_allowed": True,
                "redemption_allowed": True,
                "creation_redemption_unit": 3_000_000,
                "source_url": "https://example.invalid/512390",
            },
        ],
    }

    view = build_monitor_view(full_universe=universe, pcf_snapshot=pcf)
    assert view["universe_count"] == 3
    assert view["fresh_count"] == 1
    assert view["stale_count"] == 1
    assert view["missing_count"] == 1

    rows = {(row["exchange"], row["code"]): row for row in view["rows"]}
    fresh = rows[("SZSE", "159501")]
    assert fresh["pcf_status"] == "FRESH"
    assert fresh["event_eligible"] is True
    assert fresh["capacity_kind"] == "CUMULATIVE"
    assert fresh["total_baskets"] == 180.0
    assert fresh["account_baskets"] == 1.0
    assert fresh["minimum_accounts_to_fill"] == 180
    assert fresh["official_pcf_page_url"].endswith("159501-page")
    assert fresh["official_pcf_source_url"].endswith("159501.xml")

    stale = rows[("SSE", "512390")]
    assert stale["pcf_status"] == "STALE"
    assert stale["event_eligible"] is False
    assert stale["pcf_trade_date"] == "20260904"

    missing = rows[("SSE", "510001")]
    assert missing["pcf_status"] == "MISSING"
    assert missing["event_eligible"] is False
    assert missing["creation_allowed"] is None
    assert missing["total_baskets"] is None
