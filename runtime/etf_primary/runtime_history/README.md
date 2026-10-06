# ETF 一级市场监控：Runtime History

> 这里保存真实运行中发生过的事件与阶段性统计，不保存研究假设，也不承担 Canonical 规则说明。

## 记录对象

只有系统真实运行后产生的事实进入这里，例如：

- 某日某 ETF 从暂停申购变为开放；
- 某日总篮子从 5 个增加到 100 个；
- 某日单账户上限从 10 篮子收紧到 1 篮子；
- 某日最小申赎单位发生变化；
- 某次官方数据源异常、relay 回退、字段变化导致监控失败；
- 一段时间内事件数量、命中率、持续时间等阶段性统计。

## 不记录什么

- 不把论坛讨论当成运行事实；
- 不把策略判断或“可能有套利”直接写成系统事实；
- 不复制完整 PCF 原始文件，除非未来复盘证明有必要；
- 不在这里修改当前规则，规则变化应先进入 Evolution / Decision Log，再更新 `CURRENT.md`。

## V0 实际存储

真实运行数据独立保存在 GitHub 分支 `etf-primary-runtime-data`，与代码和官方 relay 分开。当前目录结构：

- `etf_primary_runtime/current.json`：下一次 diff 使用的当前有效指针；
- `etf_primary_runtime/last_run.json`：最近一次产生持久化变化的运行摘要；
- `etf_primary_runtime/snapshots/YYYYMMDD.json.gz`：每个交易日一份压缩 PCF 摘要；
- `etf_primary_runtime/events/YYYYMMDD.json`：只有跨交易日 diff 时才产生的事件文件。

第一阶段保持轻量：

- 每个交易日只保存 PCF 摘要快照；
- 事件由相邻有效交易日快照 diff 产生；
- 首次 baseline 不反推历史事件；
- 同一交易日事实完全相同时不重写文件、不新增 commit；
- 同一交易日官方修正只更新快照，不制造跨日事件；
- 事件必须保留交易日、ETF 代码、交易所、旧值、新值、事件类型和数据来源；
- `missing`、`stale`、coverage 随快照保留，不能把数据缺失伪装成市场变化。

## 第一份真实 baseline

2026-10-06 已完成首次持久化运行：

- 目标交易日：`20260930`
- PCF：1693 / 1693
- stale：1
- missing：0
- event：0

这只是 baseline，不代表 2026-09-30 当天没有发生 PCF 变化；系统按设计不在缺少前一有效快照时回造事件。

## 三层关系

`CURRENT.md` 回答：**现在我们认为系统应该怎样工作？**

`DEVELOPMENT_LOG.md` 回答：**为什么最后变成这样？**

`runtime_history/` 与 `etf-primary-runtime-data` 回答：**真实市场和系统后来发生了什么？**
