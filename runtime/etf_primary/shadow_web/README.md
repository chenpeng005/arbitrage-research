# ETF Primary Shadow Web

This directory documents the current read-only Shadow reading layer used before formal portal integration.

## User-visible contract

The page should prefer Chinese labels. Engineering enums remain internal and are rendered as:

- `DOMESTIC / CROSS_BORDER / MIXED` → 境内 / 跨境 / 境内+跨境
- `EQUITY / BOND / COMMODITY / MONEY_MARKET` → 股票 / 债券 / 商品 / 货币
- `FRESH / STALE / MISSING` → 当日有效 / 旧数据 / 缺失
- `CUMULATIVE / NET / BOTH` → 累计申购上限 / 净申购上限 / 累计+净申购同时约束

Market-standard abbreviations such as ETF, QDII and PCF may remain when useful, but user-facing text should include Chinese meaning where ambiguity exists.

## Reading priority

The Shadow page is a **pre-open decision aid**, not a real-time market terminal.

The primary question is:

> Compared with the previous valid PCF trade date, what changed today?

The home table should therefore emphasize:

- previous valid trade-date whole baskets;
- current whole baskets;
- basket change / ratio when the capacity rule is comparable;
- current per-account whole-basket cap;
- approximate single-basket capital size;
- creation status and capacity rule;
- official PCF source.

Example:

```text
159501  纳指ETF嘉实
上一篮子 1  →  今日篮子 180  ↑180×
单户篮子 1
```

`previous` always means the previous **valid PCF trade date**, not the previous calendar day. After a long holiday, 2026-10-08 therefore compares against 2026-09-30.

The relay publishes `comparison.json`, produced from the two latest persisted PCF snapshots, so the page does not need to decompress the full history merely to show yesterday-vs-today capacity.

## Premium: fuzzy-correct, low frequency

The earlier browser-side real-time price / IOPV / IOPV-premium layer is retired from the home page.

Reason:

- the strategy is driven first by a sudden PCF capacity release;
- exact intraday IOPV is not necessary for the pre-open screen;
- full-market real-time quote pagination adds fragility and request cost;
- a rough, clearly dated premium reference is enough to answer whether the spread is still obviously material.

The home page may later display a single **reference premium** field based on a low-frequency close / official NAV convention. It must always show its date and must never be described as real-time. Until a stable low-frequency source is wired, the page should prefer `—` over a misleading substitute.

Real-time price and IOPV can still be checked manually in the broker terminal before the final order decision.

## Canonical boundary

The reading layer must not replace or mutate the canonical primary-market facts:

- exchange official ETF universe;
- SSE/SZSE official PCF;
- creation/redemption permissions and limits;
- Runtime V3 market-fact digest;
- PCF change events and persisted daily snapshots.

Third-party premium data is auxiliary only.

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

The current server page is still a server-local Shadow asset. The stabilized page should be promoted into versioned code before formal portal integration rather than remaining a server snowflake.
