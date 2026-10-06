# ETF Primary Shadow Web

This directory documents the current read-only Shadow reading layer used before formal portal integration.

## User-visible contract

The page should prefer Chinese labels. Engineering enums remain internal and are rendered as:

- `DOMESTIC / CROSS_BORDER / MIXED` → 境内 / 跨境 / 境内+跨境
- `EQUITY / BOND / COMMODITY / MONEY_MARKET` → 股票 / 债券 / 商品 / 货币
- `FRESH / STALE / MISSING` → 当日有效 / 旧数据 / 缺失
- `CUMULATIVE / NET / BOTH` → 累计申购上限 / 净申购上限 / 累计+净申购同时约束

Market-standard abbreviations such as ETF, QDII, IOPV and PCF may remain when useful, but user-facing text should include Chinese meaning where ambiguity exists.

## Auxiliary IOPV layer

The Shadow page may display:

- 当前场内价格
- 参考净值（IOPV）
- IOPV 溢价率

with:

```text
IOPV premium = (market price / IOPV - 1) * 100%
```

The current Shadow implementation reads public Eastmoney market fields in the browser and refreshes about every 30 seconds. This is an **auxiliary market-data layer only**.

It must not replace or mutate the canonical primary-market facts:

- exchange official ETF universe;
- SSE/SZSE official PCF;
- creation/redemption permissions and limits;
- Runtime V3 market-fact digest;
- PCF change events.

An IOPV being absent must never be interpreted as the PCF being missing.

## Current Shadow server projection

Server directory:

```text
/home/admin/projects/etf-primary-shadow/
```

Hidden read-only URL:

```text
http://47.109.176.100/opportunity-portal/etf-shadow/
```

It is intentionally not linked from the main portal and does not emit alerts or trades.

The server page is still being interactively iterated with the user. Once the reading surface stabilizes, the exact static page should be promoted from server-local Shadow assets into this App directory and deployed from versioned code rather than maintained as a server snowflake.
