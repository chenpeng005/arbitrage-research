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

### Put Economic Judgment V1

正式实现：

- `runtime/opportunity/put.py`：价格门控与 KEEP / DROP；
- `runtime/opportunity/put_contract_facts.py`：仅对 P<100 对象获取和审计普通回售机制；
- `runtime/opportunity/put_discovery.py`：正式 Runtime Unit，负责取数、判断、持久化与 Registry。

2026-09-24 正式市场截面服务器实跑：

- universe = 311；
- mechanism_audit_required = 1；
- mechanism_audit_skipped = 310；
- KEEP = 1；
- DROP = 310；
- INSUFFICIENT_DATA = 0；
- Runtime status = PASS。

该结果验证了 Decision-Invariance：P>=100 时不读取回售条款。

### Downward Revision Economic Judgment V1

正式实现：

- `runtime/opportunity/revision_contract_facts.py`：结构化下修状态、合同到期日、批量经审计 NAV、条件性 NAV 条款证据；
- `runtime/opportunity/downward_revision.py`：CV=100 乐观上界、permanent blocker、NAV Decision-Invariance 与 Resolver 经济判断；
- `runtime/opportunity/revision_discovery.py`：正式 Runtime Unit、持久化与 Registry。

2026-09-24 正式市场截面服务器实跑：

- universe = 311；
- revision watch matched = 311；
- audited NAV available = 311；
- permanent blockers = 10；
- NAV clause exact evidence required = 6；
- KEEP = 152；
- DROP = 159；
- INSUFFICIENT_DATA = 0；
- Runtime status = PASS。

### Unified Opportunity Discovery Controller

`runtime/opportunity/discovery_controller.py`

当前三条 Active Path 已全部 CONNECTED：

- MATURITY_CASH；
- PUT；
- DOWNWARD_REVISION。

Controller 固定消费同一个 Discovery Market Ingress，并以“债券为容器、Path 状态独立”的结构写入 Economic Path Registry。

## 下一工程节点

三条 Opportunity Path 的经济判断 Runtime 已全部打通。

当前进入 **Economic Path Registry → Engineering Research Trigger**：在不改变各 Path `economic_status` 的前提下，为 KEEP Path 维护可观察事件状态并决定何时启动 Path Research。

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
