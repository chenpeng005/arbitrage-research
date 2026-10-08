from runtime.etf_primary.models import PcfSnapshot
from runtime.etf_primary.pcf import (
    latest_pcf_trade_date,
    parse_szse_pcf_list_item,
)


def test_latest_pcf_trade_date_ignores_missing_dates():
    rows = [
        PcfSnapshot(code="510300", exchange="SSE", trade_date="20260929"),
        PcfSnapshot(code="513100", exchange="SSE", trade_date="20260930"),
        PcfSnapshot(code="511010", exchange="SSE", trade_date=None),
    ]
    assert latest_pcf_trade_date(rows) == "20260930"


def test_parse_szse_official_list_item_builds_xml_candidates():
    html = """
    <a encode-open="/files/text/etf/ETF15950120260930.txt">纳指ETF嘉实(2026-09-30)</a>
    <a href="/modules/report/views/eft_download_new.html?path=%2Ffiles%2Ftext%2FETFDown%2F&filename=pcf_159501_20260930%3B159501ETF20260930">下载</a>
    """
    item = parse_szse_pcf_list_item(html, "2026-09-30")
    assert item.code == "159501"
    assert item.trade_date == "20260930"
    assert item.page_label == "纳指ETF嘉实"
    assert any("pcf_159501_20260930.xml" in url for url in item.xml_candidate_urls)
    assert any("159501ETF20260930.xml" in url for url in item.xml_candidate_urls)
