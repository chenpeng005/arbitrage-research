# ETF 一级市场 Shadow 运行说明

本目录记录服务器 `/home/admin/projects/etf-primary-shadow` 的运行态约定，避免 Shadow 页面只存在于服务器临时文件而难以恢复。

## 当前定位

- 页面：`/opportunity-portal/etf-shadow/`
- 模式：SHADOW_ONLY，只读，不发提醒，不自动交易。
- 一级市场事实唯一主源：上交所 / 深交所官方 ETF PCF。
- 首页目标：盘前快速看“上一有效 PCF 交易日 → 当前 PCF 交易日”的篮子/规则变化。
- 实时现价 / IOPV / 实时溢价已经退出首页。
- 参考溢价仅作模糊粗筛：`最近完成交易日收盘价 / 最新正式基金净值 - 1`，显示各自日期；辅助层失败时沿用缓存，不参与 PCF 数据门控。

## 盘前链路

1. GitHub 官方 relay：08:32 / 08:42 / 08:52（Asia/Shanghai，Mon-Fri）。
2. 辅助参考溢价缓存：08:35 / 08:55；另 16:20 做一次收盘后缓存。
3. 服务器轻量页面投影：08:38 / 08:48 / 08:58。
4. 09:08 再做完整 Runtime 持久化；盘前页面不等待完整持久化。

核心原则：**PCF 变化优先，行情辅助层永不阻塞主链路。**

## 首页字段

核心列：

- 代码 / 名称
- 层级
- 申赎清单状态
- 申购状态
- 申购容量口径
- 上一篮子
- 今日篮子
- 变化
- 单户篮子
- 单篮子资金
- 参考溢价（低频辅助）
- 申赎清单日期
- 官方申赎清单

“上一”严格指上一有效 PCF 交易日，不是自然日；例如 2026-10-08 应直接和 2026-09-30 比较。

## 参考溢价辅助层

- 只覆盖小容量跨境/QDII可执行池及当天 PCF 变化标的。
- 最近收盘价：腾讯公开行情辅助源。
- 正式净值：东方财富基金净值页辅助源。
- 两者均为非 canonical 辅助数据，只用于粗筛。
- 缓存：`/home/admin/projects/etf-primary-shadow/aux_cache/reference_premium.json`。
- 失败策略：保留旧缓存；页面仍更新 PCF。

2026-10-07 验证样本：

- 159501：收盘 2.205（2026-09-30），正式净值 1.9146（2026-09-29），参考溢价约 15.17%。
- 159866：收盘 1.723（2026-09-30），正式净值 1.6780（2026-09-30），参考溢价约 2.68%。
- 159509：收盘 3.194（2026-09-30），正式净值 2.3721（2026-09-29），参考溢价约 34.65%。

## 服务器入口

- 项目：`/home/admin/projects/etf-primary-shadow`
- 页面源码：`web/index.html`
- 页面投影脚本：`refresh_web_data.py`
- 轻量页面刷新：`/home/admin/bin/etf-primary-shadow-refresh.sh`
- 参考溢价刷新：`/home/admin/bin/etf-primary-shadow-premium-refresh.sh`
- 完整 Runtime：`/home/admin/bin/etf-primary-shadow-run.sh`

## 2026-10-07 验收

- GitHub 默认分支盘前 workflow 端到端 PASS。
- 官方 universe、12 个 SZSE PCF 分片、merge、comparison、relay 发布全部 PASS。
- 服务器轻量页面刷新约 4 秒。
- 页面 HTTP 200。
- 前端 JavaScript `node --check` PASS。
- 参考溢价缓存 28/28 成功。
