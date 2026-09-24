# Opportunity Discovery Web / Runtime

本目录是机会发现项目的 Engineering / Implementation Persistence。

## 与知识库的关系

- 业务规则、Canonical、Path Knowledge：保存在 `chenpeng005/obsidian-knowledge-base`
- Runtime / Web / Adapter / Audit / Tests：保存在本目录
- 每次真实运行的数据、日志、Snapshot：保存在服务器 Runtime Store，不提交 Git

旧的根目录 `index.html / app.js / styles.css / objects` 作为历史前端原型暂时保留，不在本阶段覆盖。

## 当前第一个工程单元

`Market Map Builder`

当前只实现第一段：

```text
Web Trigger
→ Market Data Acquisition
→ Universe / Gate A-B-C
→ Acquisition Audit
→ acquisition_result.json
```

模型拟合暂不混入这一模块。

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
