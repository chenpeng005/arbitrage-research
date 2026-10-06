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

2026-10-06 全市场分类审计为 **problem_rows = 0**。前台仍可按阅读需要重组成“境内 / 跨境 → 股票 / 债券 / 商品”等结构，但底层不牺牲原始维度。

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

派生字段至少包括：总篮子数、单账户最多篮子数、理论最低需要多少账户才能吃满、单篮子资金规模。

累计申购上限与净申购上限语义不同，不相加。

## 统一事实视图

relay 已正式生成并发布：

`etf_primary_relay/monitor_view.json`

它把稳定 ETF 身份/分类与当前 PCF 合成一张面向页面/API的事实表，统一输出：

- `pcf_status = FRESH / STALE / MISSING`；
- `event_eligible`；
- 申赎开关和最小申赎单位；
- 累计/净容量及 `capacity_kind`；
- 总篮子、单账户篮子、理论最低账户数；
- 单篮子资金规模；
- 官方 PCF 页面与数据源链接。

2026-10-06 首次正式发布：**1693 rows / fresh 1692 / stale 1 / missing 0**。

该视图只承担一级市场事实，不加入折溢价、成交承接、券商执行或机会评分。

## 官方 relay 架构

当前采用：

**prepare → 12 个 SZSE PCF shard → merge 校验 → Monitor View → last-good relay。**

prepare 先取得 SZSE 官方当日 PCF 索引，因此 shard 使用交易所实际给出的下载文件，而不是猜文件名。若只有极少数 shard 请求遭遇 CDN / WAF 边缘失败，merge 可从新的 runner 对最多 10 个缺口做一次受限重试；不能把 merge 退化为第二次全市场抓取。

任何仍未恢复的数据都必须进入 `missing`；交易日落后的 PCF 必须进入 `stale`。`monitor_view.json` 的 SHA256 已进入 relay manifest 完整性校验。

## 监控原则

**变化优先于状态。**

当前重点事件：

- 暂停申购 → 恢复申购；
- 总申购容量明显增加；
- 单账户限制变得更有利于普通账户；
- 理论需要参与的账户数明显增加；
- 最小申赎单位下降；
- 开放申购 → 暂停申购。

不把“某只 ETF 一直有很多篮子”本身当成事件。

事件层硬门控：**只有 PCF 自身交易日等于该次目标交易日的 fresh row 才参与市场事件 diff。** stale / missing 仍完整保留用于数据质量与生命周期审计，但不参与策略事件。上一交易日若某只也是 stale，则该历史状态视为不可用于比较；以后 stale → fresh 也不会凭空制造“容量变化”。

## 日快照、diff 与持久化

`runtime_cycle.py` 已形成 V0 日运行闭环：

- 首次运行只建立 baseline，不制造历史事件；
- 每个新交易日保存一份压缩 PCF 摘要；
- 与上一有效交易日做 diff，事件单独落盘；
- 同一交易日、同一**市场事实摘要**再次运行返回 `SAME_TRADE_DATE_NO_CHANGE`，不重写 gzip / current / last_run，也不产生无意义 commit；
- 同一交易日若官方真实修正关键市场字段，则更新该日快照为 `SAME_TRADE_DATE_REFRESHED`，但不制造跨日事件；
- schema 规则变化使用一次显式 `SAME_TRADE_DATE_SCHEMA_MIGRATED`；
- 交易日倒退 fail-closed；
- `missing` / `stale` / coverage 一并进入运行快照。

### Market Fact Digest

真实复跑曾发现 1693 行全部被判断变化；逐字段核对后确认唯一变化字段均为 `fetched_at`。因此现已正式分离：

- 市场事实字段 → 参与 digest；
- `fetched_at`、source URL、workflow/relay 时间 → 保留用于审计，但不参与 digest。

修复后再次完成全市场完整运行，relay 正常更新，而 `etf-primary-runtime-data` 仍停留在 `c593e9c...`，没有产生新的同日 refresh commit。事实去噪已通过真实端到端验证。

运行状态独立持久化在 GitHub 分支 `etf-primary-runtime-data`，与代码分支、relay 分支分开。当前 baseline：

- trade date：`20260930`
- PCF：1693 / 1693
- stale：1
- missing：0
- event：0（baseline 按规则不回造事件）

定时抓取为**工作日 08:40（Asia/Shanghai）**。中国法定休市工作日仍可能触发 workflow，但同交易日市场事实无变化不会形成新的 runtime data commit。

## 当前阶段与下一步

事实层内部链路已收口并经过同交易日真实复跑验证。目前 V0 尚缺最后一个关键验收：**真实跨交易日 diff。**

下一阶段顺序：

1. 下一真实交易日验证 `20260930 → 新交易日` 第一轮真实 diff 与事件文件；
2. 人工复核真实事件，确认 PCF 字段语义和事件噪音；
3. 建一个只读事实监控页，直接消费 `monitor_view.json`，先支持分类、申购状态、篮子/单户容量、freshness 和官方 PCF 跳转；
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
