# Opportunity Discovery Web / Runtime

本目录是机会发现项目的 Engineering / Implementation Persistence。

## 与知识库的关系

- 业务规则、Canonical、Path Knowledge：保存在 `chenpeng005/obsidian-knowledge-base`
- Runtime / Web / Adapter / Audit / Tests：保存在本目录
- 每次真实运行的数据、日志、Snapshot：保存在服务器 Runtime Store，不提交 Git

旧的根目录 `index.html / app.js / styles.css / objects` 作为历史前端原型暂时保留，不在本阶段覆盖。

## 已有公共能力

`Market Map Builder` 已形成冻结的 Output Contract V1；`Bond Valuation Resolver V1` 从指定快照读取模型并为个券的目标状态计算参考价。完整藏宝图 Pipeline 在网页运行。

## 机会发现主流程接入

当前正式主线：

```text
正式 Market Map Registry
→ 经过验证的 Market Map Output Contract V1
→ Discovery Market Ingress
→ Contract Facts Ingress
→ Path Economic Judgment
→ Economic Path Registry
```

### Discovery Market Ingress

`runtime/opportunity/ingress.py`

职责：

- 审计并冻结正式 Market Map Contract；
- 生成主流程市场输入；
- 不在只有市场数据时输出 KEEP / DROP。

网页“市场数据进入机会发现”按钮调用：

- `POST /api/opportunity/market-ingress`
- `GET /api/opportunity/market-ingress/latest`

### Contract Facts Ingress V1

`runtime/opportunity/contract_facts.py`

当前首先实现到期现金合同事实：

- 东方财富批量合同表；
- 同花顺原合同到期日交叉审计；
- 票息序列解析；
- 到期赎回现金解析；
- `remaining_contract_cash_C` 确定性计算；
- 支持带 provenance 的 authoritative override；
- 原合同到期日与事件日期分离。

默认纪律：

```text
批量结构化数据
→ 程序审计
→ 只有异常 / 冲突 / 决策敏感对象
→ authoritative override
```

不默认逐只读取募集说明书。

### Maturity Cash Economic Judgment V1

`runtime/opportunity/maturity_cash.py`

正式顺序：

```text
P + C
↓
C <= P
→ DROP / NO_POSITIVE_CASH_SPREAD

C > P
↓
才要求 normal_maturity_path_available
    TRUE  → KEEP
    FALSE → DROP
    UNKNOWN → INSUFFICIENT_DATA
```

Path Availability 是条件性数据需求，而不是全市场无条件深查。

## 当前 Runtime 验证

2026-09-24 正式市场截面的 311 只转债已经完成到期现金数据层验证：

- 311/311 匹配批量合同事实；
- 311/311 解析票息序列；
- 311/311 最终可确定到期赎回现金；
- 311/311 可计算 `remaining_contract_cash_C`。

具体运行输入、异常 fallback、C 明细和阶段性结果保存在 Server Runtime Store；这些数字是运行证据，不是长期 Canonical。

## 下一工程节点

回售 Path：

```text
正式 311 只市场母集
→ 批量 RESALE_CLAUSE 解析
→ ordinary put mechanism 识别
→ 剩余生命周期可用性
→ P < 100
→ KEEP / DROP / INSUFFICIENT_DATA
```

当前不在 Discovery 研究回售触发概率、公司意愿、信用质量或支付能力。

## 运行外循环

正式设计：

```text
Human
↓
Web
↓
Project Controller
↓
Unit Runtime
↓
Engineering / AI
↓
Persistence
↓
Web Feedback
```

AI 不直接启动或推进 Runtime；AI 只在程序显式请求的语义异常节点被调用。

## 当前测试部署入口

- Web URL：`https://47.109.176.100:8443/`
- Caddy：外部 8443 → `127.0.0.1:7080`
- Runtime 项目目录：`/home/admin/projects/arbitrage-runtime`
- Runtime Store：`/home/admin/projects/arbitrage-runtime/runtime_data`
- 入口带服务器现有 Basic Auth；不在仓库保存密码。
- 通过 Caddy 保护的 7080 监听可显式配置 `RUNTIME_TRUST_LOCAL_PROXY_AUTH=1`，仅信任来自本机回环地址的代理请求；直接启动的预览端口仍使用应用自己的 Basic Auth。
- 当前证书为 Caddy internal TLS，浏览器可能显示自签名/不受信任提示。
