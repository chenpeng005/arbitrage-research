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

```text
正式 Market Map Registry
→ 经过验证的 Market Map Output Contract V1
→ Discovery Market Ingress（市场字段审计、冻结）
→ 合同条款和机制数据获取、审计（后续）
→ 三条 Path 的经济性判断（后续）
```

网页“市场数据进入机会发现”按钮调用 `POST /api/opportunity/market-ingress`，从最新正式市场快照生成 `runtime_data/runs/<run_id>/discovery_market_input.json` 和运行元数据。`GET /api/opportunity/market-ingress/latest` 读取最后一次成功结果。结果明确标记合同数据缺口，不把当前市场估值差或缺失合同数据当成经济性 KEEP / DROP。

三条 Path 的合同事实、条款生效状态及条件性硬底价得到审计后，再进入经济判断。每一次下游运行固定消费的快照 ID、合同版本和知识 / 工程版本。

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
- 当前证书为 Caddy internal TLS，浏览器可能显示自签名/不受信任提示。
