from runtime.etf_primary.models import PcfSnapshot
from runtime.etf_primary.monitor import detect_events
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
