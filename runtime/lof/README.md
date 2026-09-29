# LOF Opportunity Runtime

状态：APP INTEGRATION BASELINE / NOT DEPLOYED  
日期：2026-09-29

## 1. 角色

本目录是 LOF 机会发现项目的 App GitHub 实现区。

业务规则与当前 Canonical 位于 Knowledge GitHub：

- `05 套利研究/LOF机会发现/00_当前有效/00_恢复入口.md`
- `05 套利研究/LOF机会发现/00_当前有效/01_LOF机会发现_Canonical.md`
- `05 套利研究/LOF机会发现/02_数据与监控/LOF-Market-Snapshot-Contract-V1.md`
- `05 套利研究/LOF机会发现/02_数据与监控/LOF-Estimated-NAV-Resolver-Contract-V0.1.md`

本目录保存可执行实现，不复制 Knowledge Canonical 作为第二份业务真相。

## 2. 当前实现

### Universe / Classification

- `universe.py`
  - 上交所官方 LOF Universe；
  - 深交所官方 LOF Universe；
  - active universe 全量保留。

- `classification.py`
  - 基金原始类型；
  - LOF 类型归一化。

- `resolver_classification.py`
  - R1 国内指数；
  - R2 国内其他；
  - R3 海外 QDII 指数；
  - R4 海外 QDII 其他；
  - R5 商品 / 特殊。

### Market Facts

- `quote.py`
  - LOF 实时价格；
  - 成交量 / 成交额；
  - quote timestamp；
  - FRESH / STALE / UNAVAILABLE。

- `nav.py`
  - 上交所官方 NAV；
  - 深交所官方 NAV；
  - NAV date / source / freshness。

- `state.py`
  - 申购 / 赎回状态；
  - 单日申购限额；
  - 最低申购额；
  - 申购确认 T+N；
  - 费率阶梯原始参考；
  - 未证明的 limit scope / 真正可卖时间保持 UNKNOWN / null。

### Estimated NAV / IOPV-like

- `resolver.py`
  - 通用 estimated NAV 计算；
  - exposure；
  - FX；
  - tracking adjustment；
  - AVAILABLE / STALE / UNAVAILABLE；
  - quality。

- `tracking_index.py`
  - 基金代码 → `StandarIndexCode` / `IndexName`；
  - R1 当前主 Mapping。

- `mapping.py`
  - F10 benchmark / tracking target；
  - exposure ratio candidate；
  - F10 仅做精度增强，不再承担主 index-code mapping。

- `index_proxy.py`
  - tracking index code → Tencent / Xueqiu quote symbol；
  - common numeric domestic index direct mapping；
  - unsupported pattern fail-closed。

- `index_quote.py`
  - Tencent 国内指数行情。

- `index_quote_xueqiu.py`
  - Xueqiu fallback；
  - 支持 CSI 930xxx / 931xxx 等腾讯缺失指数。

- `domestic_index_resolver.py`
  - R1 previous-close fast path。

- `r1_pipeline.py`
  - R1 批量 index quote 去重；
  - estimated NAV 批量计算。

- `qdii_index_resolver.py` / `r3_pipeline.py`
  - R3 海外 QDII 指数；
  - 美国 / 香港 proxy；
  - USD/CNY / HKD/CNY；
  - futures overlay；
  - timing quality。

- `commodity_resolver.py` / `r5_pipeline.py`
  - R5 商品 / 特殊；
  - 第一批黄金 / 原油 proxy。

- `estimated_nav_lane.py`
  - R1 / R3 / R5 统一 estimated NAV Lane。

- `context_builder.py`
  - 低频装配 resolver context；
  - direct tracking-index code 为 R1 主映射；
  - F10 只补 benchmark / exposure；
  - name search 仅作为可选 fallback。

- `trading_calendar.py`
  - 上一交易日 Resolver。

### Snapshot / Runtime Session

- `snapshot.py`
  - LOF Market Snapshot Contract V1；
  - 全量 Universe invariant；
  - realtime / static premium 严格分离；
  - estimated NAV quality / provenance；
  - Quote / NAV / State quality summary。

- `controller.py`
  - Quote / NAV / State / Type / estimated NAV lanes；
  - PASS / DEGRADED；
  - 单 lane 故障不删除 Universe。

- `runtime_session.py`
  - 缓存低频 metadata / resolver context；
  - 支持重复高频 snapshot。

- `snapshot_store.py`
  - Runtime snapshot persistence。

- `snapshot_job.py`
  - one-shot all-market snapshot job。

### Web / API

- `web_app.py`
  - FastAPI；
  - health；
  - latest snapshot API；
  - static monitor page。

- `static/index.html`
- `static/app.js`
- `static/styles.css`

当前主表已经包含：

```text
code / name
resolver class
price
pct change
volume
amount
estimated NAV
estimated premium
estimated NAV quality
static premium
official NAV / date
subscription status
daily subscription limit
redemption status
quote time
```

默认按 `estimated_premium_rate` 降序；支持筛选与30秒自动刷新。

## 3. 2026-09-29 R1 Live Coverage Evidence

当日 active R1 国内指数 LOF：136只。

验证结果：

- official NAV：136/136；
- LOF realtime quote：136/136；
- direct tracking index code：135/136；
- direct proxy rule：123/136；
- 完整可计算 estimated NAV：122/136；
- 当前覆盖率约 89.7%。

该覆盖率代表“实时估值链可形成”，**不代表全部达到 HIGH quality**。

Exposure：

- benchmark 明确可取时使用真实 candidate；
- exposure 不明确时可回退 1.0；
- 回退时 quality 降级；
- index-enhanced / cross-market /特殊产品不得因为公式可算就自动标 HIGH。

## 4. 当前质量纪律

### Estimated Premium

只有：

```text
estimated_nav_status = AVAILABLE
```

才生成 `estimated_premium_rate` 并进入实时溢价排序。

### Source Failure

任何来源失败必须显式：

- STALE；
- UNAVAILABLE；
- DEGRADED；
- lane_errors。

禁止沿用旧值伪装实时。

### Mapping

优先：

```text
fund_code
→ StandarIndexCode
→ quote symbol
```

F10 / name search 只做补充。

## 5. 当前尚未完成

第一优先级：

1. Production Source Preflight 设计与实现；
2. 提升 R1 exposure / quality；
3. 扩大 R3 / R5 proxy coverage；
4. Knowledge Snapshot + Deployment Manifest 准备。

第二优先级：

- 场内份额 / 份额变化；
- 相关指数涨幅；
- state delta / announcement delta；
- 简单 P1 提示列。

不是第一版阻塞项：

- 完整券商 Execution Profile；
- 精确 subscription-to-sell；
- 复杂 P1 Gate；
- R2 / R4 高精度估值。

## 6. Tests / CI

GitHub Actions：

- `.github/workflows/lof-runtime-tests.yml`

当前 LOF Runtime Tests：SUCCESS。

Cloud Integration Smoke：

- 全市场 Snapshot E2E：PASS；
- Web / API Smoke：PASS；
- FastAPI health：PASS；
- snapshot rows = 404；
- monitor homepage：PASS。

详细证据已持久化至 Knowledge GitHub：
- `LOF-End-to-End-Smoke-2026-09-29.md`
- `LOF-Web-API-Smoke-2026-09-29.md`

Cloud Smoke 对 Universe / NAV / tracking-index 等低频元数据允许使用官方/验证快照 fixture；Production Runtime 仍必须使用 live source 并通过 Production Source Preflight。

单元测试不应依赖实时网络；live-source smoke 属于后续 integration / deployment preflight。

### Release Preparation

当前已实现并通过测试：

- `source_preflight.py`
  - 目标服务器 live-source Production Source Preflight；
  - Universe / quote / NAV / state / R1 mapping coverage；
  - PASS / WARN / FAIL；
  - 可选 `--expect-fresh-quotes`。

- `knowledge_snapshot.py`
  - exact Knowledge checkout；
  - 校验 `git HEAD == knowledge_commit_sha`；
  - 冻结 LOF release profile required canonical；
  - 文件 sha256 / byte_size；
  - 生成 Snapshot manifest hash。

- `release_manifest.py`
  - `deployment_manifest.candidate.json`；
  - Preflight 后原子 promotion；
  - Candidate application SHA 必须与 Preflight application SHA 完全一致。

- `deployment_gate.py`
  - release profile：`lof-opportunity-runtime-v1`；
  - 使用 LOF 自己的 required canonical；
  - 不改变现有可转债 Deployment Gate 默认行为。

共享 `runtime/deployment_gate.py` 已支持可选 release profile / required canonical 注入，原默认 Canonical 集合保持不变，并有回归测试。

当前发布链：

```text
App / Knowledge freeze
→ Knowledge Snapshot
→ deployment candidate
→ Production Source Preflight
→ manifest promotion
→ LOF Deployment Gate
→ Production Runtime
```

## 7. 部署纪律

当前仍然：

> **RELEASE TOOLING READY / NOT DEPLOYED**

未创建生产 LOF Runtime、未启动8095、未改 nginx。

正式部署必须：

```text
Knowledge commit
+
Application commit
+
Knowledge Snapshot
+
Deployment manifest
→ Deployment Gate PASS
→ Server Runtime
```

禁止先在服务器现场开发再回补 GitHub。


## 8. Production Refresh Loop

Production uses `runtime.lof.runtime_loop` rather than repeatedly rebuilding the full one-shot job.

Default cadence:

- market window quote + estimated NAV snapshot: 30 seconds;
- off-hours snapshot: 300 seconds;
- subscription / redemption state refresh: 600 seconds;
- official NAV refresh: 1800 seconds;
- Universe / type / resolver context rebuild: 3600 seconds.

The fast loop reuses cached slow-lane data. It does not issue 404 subscription-state requests every 30 seconds.

Example:

```bash
python -m runtime.lof.runtime_loop \
  --data-root /path/to/runtime_data
```
