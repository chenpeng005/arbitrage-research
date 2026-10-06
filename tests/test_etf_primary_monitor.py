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


def test_sse_current_field_names_are_normalized():
    snapshot = parse_sse_basic_row(
        "513100",
        {
            "TradingDay": "20260928",
            "CreationRedemptionUnit": "500000",
            "NAVperCU": "1024354.72",
            "NAV": "2.0487",
            "CreationLimit": "2500000",
            "NetCreationLimit": "0",
            "CreationLimitPerAcct": "500000",
            "NetCreationLimitPerAcct": "0",
            "CreationRedemptionSwitch": "1",
            "CreationRedemptionMechanism": "0",
        },
    )
    assert snapshot.creation_allowed is True
    assert snapshot.redemption_allowed is True
    assert snapshot.total_baskets() == 5
    assert snapshot.account_baskets() == 1
    assert snapshot.basket_value() == 1024354.72


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
