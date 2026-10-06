from runtime.etf_primary.classification import classify_etf


def test_cross_border_equity_index():
    result = classify_etf(
        name="纳斯达克100交易型开放式指数证券投资基金",
        tracking_index="纳斯达克100指数",
        exchange="SSE",
        raw_exchange_class="33",
    )
    assert result["region_scope"] == "CROSS_BORDER"
    assert result["asset_class"] == "EQUITY"
    assert result["strategy_style"] == "INDEX"
    assert result["qdii_flag"] is True


def test_domestic_bond_is_separate_dimension():
    result = classify_etf(
        name="中债国债ETF",
        tracking_index="中债-国债总指数",
        exchange="SZSE",
        raw_exchange_class=None,
    )
    assert result["region_scope"] == "DOMESTIC"
    assert result["asset_class"] == "BOND"


def test_mixed_hk_mainland_not_forced_to_qdii():
    result = classify_etf(
        name="沪港深红利ETF",
        tracking_index="沪港深红利指数",
        exchange="SSE",
        raw_exchange_class="08",
    )
    assert result["region_scope"] == "MIXED"
    assert result["qdii_flag"] is None
