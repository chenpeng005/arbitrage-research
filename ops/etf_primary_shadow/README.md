# ETF 一级市场 Shadow 运行说明

本目录记录服务器 `/home/admin/projects/etf-primary-shadow` 的运行态约定，避免 Shadow 页面只存在于服务器临时文件而难以恢复。

## 当前定位

- 页面：`/opportunity-portal/etf-shadow/`
- 模式：SHADOW_ONLY，只读，不发提醒，不自动交易。
- 一级市场事实唯一主源：上交所 / 深交所官方 ETF PCF。
- 首页目标：盘前快速看“上一有效 PCF 交易日 → 当前 PCF 交易日”的篮子/规则变化。
- 实时现价 / IOPV / 实时溢价已经退出首页。
- 参考溢价仅作低频粗筛，绝不参与 PCF 数据门控。

## 盘前链路

1. GitHub 官方 relay：08:32 / 08:42 / 08:52（Asia/Shanghai，Mon-Fri）。
2. 正式净值/ETF收盘缓存：08:35 / 08:55。
3. eNAV：08:36 / 08:56。
4. 服务器轻量页面投影：08:38 / 08:48 / 08:58。
5. 09:08 再做完整 Runtime 持久化；盘前页面不等待完整持久化。
6. 16:20 强制刷新一次收盘/正式净值辅助缓存。

核心原则：**PCF 变化优先，估值辅助层永不阻塞主链路。**

## 首页核心字段

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
- 参考溢价（优先 eNAV；否则正式净值）
- 申赎清单日期
- 官方申赎清单

“上一”严格指上一有效 PCF 交易日，不是自然日；例如 2026-10-08 应直接和 2026-09-30 比较。

## eNAV 辅助层

当前只覆盖**有限总容量 + 明确单户上限**的跨境 ETF 可执行池，当前基线共 28 只。

基本公式：

`eNAV = 最新正式NAV锚 × 已完成指数/代理ETF涨跌 × 汇率相对变化 ×（日本当日已发生行情叠加）`

- A：直接指数桥，质量较高。
- B：高相关海外 ETF / 历史桥代理，适合判断量级，不追求小数点精确。
- 美国：优先直接指数（如 NDX、NBI），否则用 SPY/XBI/XOP/XLK 等代理。
- 日本：EWJ 承接多日历史，再叠加中国盘前已发生的 N225/TOPIX 当日行情。
- 德国/巴西/沙特等：先用 EWG/EWZ/KSA 等海外 ETF 代理，减少多币种汇率和指数源复杂度。
- 港股：有直接指数时直接推进。
- 汇率：当前粗估使用 HKD/CNY 相对变化代理 USD/CNY 相对变化；在长假 USDCNY 源停更时比陈旧报价更有用。
- 商品、复杂主动型 QDII：暂不建立 eNAV 模型，明确退回正式净值参考。

估值失败策略：保留旧 eNAV；若没有可用 eNAV，页面自动退回 `最近收盘价 / 最新正式净值 - 1`。

2026-10-07 首次验证：28/28 成功，0 error。

- 159501：正式净值口径约 15.17% → eNAV 参考溢价约 **11.90%**（A，NDX直接指数）。
- 159866：正式净值口径约 2.68% → eNAV 参考溢价约 **1.40%**（B，EWJ历史桥 + 日经当日叠加）。
- 159509：eNAV 参考溢价约 **29.65%**（B，XLK科技代理）。

## 服务器入口

- 项目：`/home/admin/projects/etf-primary-shadow`
- 页面源码：`web/index.html`
- 页面投影脚本：`refresh_web_data.py`
- eNAV 脚本：`refresh_enav.py`
- 轻量页面刷新：`/home/admin/bin/etf-primary-shadow-refresh.sh`
- 正式净值/收盘辅助刷新：`/home/admin/bin/etf-primary-shadow-premium-refresh.sh`
- eNAV 刷新：`/home/admin/bin/etf-primary-shadow-enav-refresh.sh`
- 完整 Runtime：`/home/admin/bin/etf-primary-shadow-run.sh`

## 2026-10-07 验收

- GitHub 默认分支官方 PCF 盘前 workflow 端到端 PASS。
- 官方 universe、12 个 SZSE PCF 分片、merge、comparison、relay 发布全部 PASS。
- 服务器轻量页面刷新约 4 秒。
- 页面 HTTP 200。
- 前端 JavaScript `node --check` PASS。
- 正式净值辅助缓存 28/28 成功。
- eNAV 28/28 成功，0 error。
