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


def test_pure_hk_stock_connect_is_cross_border_not_qdii():
    result = classify_etf(
        name="港股通互联网ETF",
        tracking_index="港股通互联网指数",
        exchange="SZSE",
    )
    assert result["region_scope"] == "CROSS_BORDER"
    assert result["asset_class"] == "EQUITY"
    assert result["qdii_flag"] is False


def test_szse_fast_cash_etf_is_money_market():
    result = classify_etf(
        name="招商快线ETF",
        tracking_index=None,
        exchange="SZSE",
    )
    assert result["asset_class"] == "MONEY_MARKET"
    assert result["region_scope"] == "DOMESTIC"


def test_free_cash_flow_etf_remains_equity():
    result = classify_etf(
        name="自由现金流ETF华夏",
        tracking_index="980092 CNIFCF",
        exchange="SZSE",
    )
    assert result["asset_class"] == "EQUITY"
    assert result["strategy_style"] == "INDEX"


def test_sse_domestic_official_class_overrides_sp_provider_name():
    result = classify_etf(
        name="红利低波50ETF南方",
        tracking_index="标普中国A股大盘红利低波50指数",
        exchange="SSE",
        raw_exchange_class="03",
    )
    assert result["region_scope"] == "DOMESTIC"
    assert result["qdii_flag"] is False


def test_sse_domestic_official_class_overrides_hang_seng_provider_name():
    result = classify_etf(
        name="电网设备ETF南方",
        tracking_index="恒生A股电网设备指数",
        exchange="SSE",
        raw_exchange_class="03",
    )
    assert result["region_scope"] == "DOMESTIC"
    assert result["qdii_flag"] is False


def test_sse_domestic_sp_a_share_opportunity_not_qdii():
    result = classify_etf(
        name="标普A股红利ETF华宝",
        tracking_index="标普中国A股红利机会指数",
        exchange="SSE",
        raw_exchange_class="03",
    )
    assert result["region_scope"] == "DOMESTIC"
    assert result["qdii_flag"] is False


def test_szse_official_pcf_cross_border_type_is_qdii():
    result = classify_etf(
        name="测试ETF",
        tracking_index="TESTINDEX",
        exchange="SZSE",
        pcf_type="2",
    )
    assert result["region_scope"] == "CROSS_BORDER"
    assert result["qdii_flag"] is True


def test_provider_fallback_respects_explicit_a_share_scope():
    result = classify_etf(
        name="测试ETF",
        tracking_index="恒生A股电网设备指数",
        exchange="SZSE",
    )
    assert result["region_scope"] == "DOMESTIC"
    assert result["qdii_flag"] is False
