# ETF 一级市场监控：Current / Canonical

> 本文件只保存“当前有效”的项目认识与系统边界。历史争论、错误和被舍弃路径统一放在 `DEVELOPMENT_LOG.md`。

## 项目目标

构建一个面向全市场 ETF 一级市场的监控底座，优先发现：

**申购状态变化、申购容量异常释放、单账户限制变化、最小申赎单位变化。**

当前阶段先把一级市场事实层做可信，不提前混入二级市场溢价、成交承接、到账速度和券商执行质量。

## Canonical 数据源

- 上交所 ETF 官方列表与官方 PCF；
- 深交所 ETF 官方列表与官方 PCF；
- 服务器无法稳定直连深交所时，通过 GitHub Actions 官方数据 relay 获取 last-good 快照，但来源仍是交易所官方数据。

第三方网站只用于研究、交叉验证或案例发现，不作为 universe / PCF 的 canonical 数据源。

## 当前全市场基线（2026-10-06 实跑）

最近一次成功 relay：

- 全市场 ETF universe：**1693**
  - SSE：944
  - SZSE：749
- PCF 找到：**1693 / 1693**
- 与目标交易日 `2026-09-30` 一致：**1692 / 1693**
- `missing`：**0**
- `stale`：**1**
- SZSE：**749 / 749**，覆盖率 100%
- SSE：944 / 944 找到，其中 1 只官方返回的 PCF 交易日落后于目标交易日，系统保留为 stale，不静默回填。

唯一 stale 已定位为 **512390 中国低波ETF平安**。其最后 PCF 为 `2026-09-04`，随后进入终止清算生命周期，因此这里的 stale 是正常历史残留，不是抓取链异常。

因此“文件存在”和“当日有效”是两个不同门控：**missing=0 不等于 current-day coverage=100%**。同时，stale PCF 中残留的历史“允许申购”等字段不能解释成当前市场状态。

## 标的分类

底层采用多维分类，不使用单棵硬编码树：

- `region_scope`：DOMESTIC / CROSS_BORDER / MIXED / UNKNOWN；
- `asset_class`：EQUITY / BOND / COMMODITY / MONEY_MARKET / OTHER；
- `strategy_style`：INDEX / ACTIVE / UNKNOWN；
- `qdii_flag`：True / False / Unknown。

2026-10-06 全市场分类审计为 **problem_rows = 0**。人工边界审计同时确认：

- 252 只 `CROSS_BORDER` 中，121 只是 QDII，131 只不是 QDII，主要是港股通路径，因此“跨境”不能等同于“QDII”；
- 32 只 `MIXED` 的沪港深 / 沪深港混合暴露语义自然；
- 35 只 `strategy_style=UNKNOWN` 均来自货币 ETF 或商品 ETF，不是股票 ETF 漏判；
- 约 55 只名称含“增强”的 ETF 暂继续归为 `INDEX`，V0 不为 taxonomy 完整度增加额外复杂度。

前台仍可按阅读需要重组成“境内 / 跨境 → 股票 / 债券 / 商品”等结构，但底层不牺牲原始维度。

## PCF 摘要层

每只 ETF 当前至少保留：

- 是否允许申购 / 赎回；
- 最小申购赎回单位；
- 最小申赎单位资产净值；
- 累计申购 / 赎回上限；
- 净申购 / 净赎回上限；
- 单账户累计申购 / 赎回上限；
- 单账户净申购 / 净赎回上限；
- 申赎机制；
- 交易日与数据来源。

### 容量语义

累计申购上限与净申购上限是不同规则，**原始字段始终分别保存，绝不相加。**

对于“没有同日赎回抵消、直接发起一笔申购”的情形：

- 若只有累计上限：`capacity_kind = CUMULATIVE`；
- 若只有净申购上限：`capacity_kind = NET`；
- 若两者同时存在：申购必须同时满足两条规则，取较小的正值作为当前 no-offset binding limit；
- 两者相等时：`capacity_kind = BOTH`。

`NET` 约束表示**净额 headroom**，不是“全天总共只能申购这么多”的绝对 gross ceiling；若当日存在赎回，gross creation 仍可能高于该净额。

同样逻辑独立应用于单账户累计 / 单账户净申购上限。

### 篮子派生

同时保留两种表达：

- `*_limit_basket_equivalent = binding_limit / creation_redemption_unit`：原始比例，可为小数；
- `total_baskets` / `account_baskets`：在 no-offset binding limit 下能容纳的**完整最小申赎单位数量**，向下取整，只能为整数。

因此若净申购上限为 50 万份、最小申购单位为 100 万份：

- basket equivalent = 0.5；
- 可执行完整篮子数 = 0；
- 不能解释成“今天有 0.5 个篮子可抢”。

`minimum_accounts_to_fill` 只在市场和单账户完整篮子数均大于 0 时计算。

单篮子资金规模优先使用 `nav_per_cu`，否则使用 `nav_per_share × creation_redemption_unit`；它只是近似一级申购资金量，不等于最终结算成本。

## 统一事实视图

relay 正式生成并发布：

`etf_primary_relay/monitor_view.json`

它把稳定 ETF 身份/分类与当前 PCF 合成一张面向页面/API的事实表，统一输出：

- `pcf_status = FRESH / STALE / MISSING`；
- `event_eligible`；
- 申赎开关和最小申赎单位；
- 原始累计 / 净上限；
- `market_capacity_kind` / `account_capacity_kind`；
- binding no-offset limit；
- basket equivalent 与完整可执行篮子数；
- 理论最低账户数；
- 单篮子资金规模；
- 官方 PCF 页面与数据源链接。

`monitor_view` 会从原始 PCF 字段重新计算派生容量，不信任 relay 输入中可能残留的旧派生字段。

2026-10-06 首次正式发布基线：**1693 rows / fresh 1692 / stale 1 / missing 0**。

该视图只承担一级市场事实，不加入折溢价、成交承接、券商执行或机会评分。

## 官方 relay 架构

当前采用：

**prepare → 12 个 SZSE PCF shard → merge 校验 → Monitor View → last-good relay。**

prepare 先取得 SZSE 官方当日 PCF 索引，因此 shard 使用交易所实际给出的下载文件，而不是猜文件名。若只有极少数 shard 请求遭遇 CDN / WAF 边缘失败，merge 可从新的 runner 对最多 10 个缺口做一次受限重试；不能把 merge 退化为第二次全市场抓取。

任何仍未恢复的数据都必须进入 `missing`；交易日落后的 PCF 必须进入 `stale`。`monitor_view.json` 的 SHA256 已进入 relay manifest 完整性校验。

## 监控原则

**变化优先于状态。**

当前重点事件：

- `CREATION_RESUMED`：暂停申购 → 恢复申购；
- `TOTAL_CAPACITY_JUMP`：同一种 binding rule 下，完整 no-offset 可执行篮子明显增加；
- `ACCOUNT_LIMIT_IMPROVED`：同一种单账户 binding rule 下，单账户完整篮子数明显下降，尤其下降至 1；
- `ACCOUNT_DISTRIBUTION_IMPROVED`：同口径下理论最低参与账户数上升；
- `CREATION_UNIT_REDUCED`：最小申赎单位下降；
- `CREATION_SUSPENDED`：开放申购 → 暂停申购；
- `CAPACITY_RULE_CHANGED` / `ACCOUNT_CAP_RULE_CHANGED`：binding rule 在累计 / 净 / BOTH / 无约束之间切换。

**不同容量规则之间不直接比较篮子数。** 例如从累计上限切到净申购上限时，先报告规则切换，不能把两个不同经济含义的数字直接解释为“容量增加/减少”。

不把“某只 ETF 一直有很多篮子”本身当成事件。

事件层硬门控：**只有 PCF 自身交易日等于该次目标交易日的 fresh row 才参与市场事件 diff。** stale / missing 仍完整保留用于数据质量与生命周期审计，但不参与策略事件。上一交易日若某只也是 stale，则该历史状态视为不可用于比较；以后 stale → fresh 也不会凭空制造“容量变化”。

## 日快照、diff 与持久化

`runtime_cycle.py` 已形成 V0 日运行闭环：

- 首次运行只建立 baseline，不制造历史事件；
- 每个新交易日保存一份压缩 PCF 摘要；
- 与上一有效交易日做 diff，事件单独落盘；
- 同一交易日、同一**市场事实摘要**再次运行返回 `SAME_TRADE_DATE_NO_CHANGE`，不重写 gzip / current / last_run，也不产生无意义 commit；
- 同一交易日若官方真实修正关键市场字段，则更新该日快照为 `SAME_TRADE_DATE_REFRESHED`，但不制造跨日事件；
- schema / 派生语义变化使用一次显式 `SAME_TRADE_DATE_SCHEMA_MIGRATED`；
- 当前 snapshot schema 已升至 **V3**，用于固化新的 binding-capacity / whole-basket 语义；
- 交易日倒退 fail-closed；
- `missing` / `stale` / coverage 一并进入运行快照。

### V3 端到端验收

2026-10-06 已完成全市场 V3 workflow / runtime schema migration：

- 全市场 PCF：1693 / 1693；
- stale：1；missing：0；
- status：`SAME_TRADE_DATE_SCHEMA_MIGRATED`；
- trade date：`20260930`；
- event count：0。

这说明容量派生语义变化被明确记录为 schema migration，没有制造伪市场事件。

### Market Fact Digest

真实复跑曾发现 1693 行全部被判断变化；逐字段核对后确认唯一变化字段均为 `fetched_at`。因此现已正式分离：

- 市场事实字段 → 参与 digest；
- `fetched_at`、source URL、workflow/relay 时间 → 保留用于审计，但不参与 digest。

修复后再次完成全市场完整运行，relay 正常更新，而运行历史没有因为新的 `fetched_at` 再产生同日 refresh commit，事实去噪已通过真实端到端验证。

运行状态独立持久化在 GitHub 分支 `etf-primary-runtime-data`，与代码分支、relay 分支分开。当前 baseline：

- trade date：`20260930`
- PCF：1693 / 1693
- stale：1
- missing：0
- event：0（baseline / schema migration 均不回造跨日事件）

定时抓取为**工作日 08:40（Asia/Shanghai）**。中国法定休市工作日仍可能触发 workflow，但同交易日市场事实无变化不会形成新的 runtime data commit。

## 当前阶段与下一步

分类、全市场 PCF、freshness、事实去噪、容量派生语义以及 **Runtime V3 端到端验收均已收口**。

V0 当前尚缺的唯一核心市场验收是：

> **下一真实交易日完成 `20260930 → 新交易日` 的第一轮真实跨交易日 diff，并人工复核事件质量。**

下一阶段顺序：

1. 下一真实交易日验证 `20260930 → 新交易日` 第一轮真实 diff 与事件文件；
2. 人工复核真实事件，确认 PCF 字段语义和事件噪音；
3. 建一个只读事实监控页，直接消费 `monitor_view.json`，先支持分类、申购状态、完整篮子 / 单户容量、capacity kind、freshness 和官方 PCF 跳转；
4. 再处理功能分支与当前 `main` 的安全整合；
5. 事实层稳定后才接二级市场折溢价、成交承接、`exit_load`；
6. 最后把券商实测命中率和可执行性并入机会排序。

在一级市场事实层稳定前，不进入自动交易。

## 知识结构

- 当前有效认识：`CURRENT.md`
- 演化与决策：`DEVELOPMENT_LOG.md`
- 真实运行历史：`runtime_history/`
- 真实运行数据分支：`etf-primary-runtime-data`
- 官方事实 relay：`etf-primary-data-relay`
