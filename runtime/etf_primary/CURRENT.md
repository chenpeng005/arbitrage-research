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

因此“文件存在”和“当日有效”是两个不同门控：**missing=0 不等于 current-day coverage=100%**。

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

## 官方 relay 架构

当前采用：

**prepare → 12 个 SZSE PCF shard → merge 校验 → last-good relay。**

prepare 先取得 SZSE 官方当日 PCF 索引，因此 shard 使用交易所实际给出的下载文件，而不是猜文件名。若只有极少数 shard 请求遭遇 CDN / WAF 边缘失败，merge 可从新的 runner 对最多 10 个缺口做一次受限重试；不能把 merge 退化为第二次全市场抓取。

任何仍未恢复的数据都必须进入 `missing`；交易日落后的 PCF 必须进入 `stale`。

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

## 日快照、diff 与持久化

`runtime_cycle.py` 已形成 V0 日运行闭环，并于 2026-10-06 首次真实落盘：

- 首次运行只建立 baseline，不制造历史事件；
- 每个新交易日保存一份压缩 PCF 摘要；
- 与上一有效交易日做 diff，事件单独落盘；
- 同一交易日、同一事实摘要再次运行时返回 `SAME_TRADE_DATE_NO_CHANGE`，不重写 gzip / current / last_run，也不产生无意义 commit；
- 同一交易日若官方数据发生修正，则更新该日快照，状态为 `SAME_TRADE_DATE_REFRESHED`，但不制造跨日事件；
- 交易日倒退 fail-closed；
- `missing` / `stale` / coverage 一并进入运行快照。

运行状态独立持久化在 GitHub 分支 `etf-primary-runtime-data`，与代码分支、relay 分支分开。当前 baseline：

- trade date：`20260930`
- PCF：1693 / 1693
- stale：1
- missing：0
- event：0（baseline 按规则不回造事件）

定时抓取调整为**工作日 08:40（Asia/Shanghai）**，在官方盘前 PCF 披露窗口之后、开盘之前运行。中国法定休市工作日仍可能触发 workflow，但同交易日无变化不会形成新的 runtime data commit。

## 当前阶段与下一步

事实层已从“能抓”进入“真实持久运行”阶段，但还没有经历下一真实交易日，因此 V0 尚缺最后一个关键验收：**真实跨交易日 diff。**

下一阶段顺序：

1. 等下一真实交易日，验证 `20260930 → 新交易日` 的第一轮真实 diff 与事件文件；
2. 对真实事件做一次人工复核，确认字段语义和事件噪音；
3. 再处理功能分支与当前 `main` 的安全整合，不在已经明显分叉的情况下直接硬合并；
4. 事实层稳定后接二级市场层：溢价、成交承接、`exit_load`；
5. 最后才把券商实测命中率和可执行性并入机会排序。

在一级市场事实层稳定前，不进入自动交易。

## 知识结构

- 当前有效认识：`CURRENT.md`
- 演化与决策：`DEVELOPMENT_LOG.md`
- 真实运行历史：`runtime_history/`
- 真实运行数据分支：`etf-primary-runtime-data`
