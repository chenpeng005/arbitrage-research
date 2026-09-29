# LOF Opportunity Runtime

状态：APP BASELINE / NOT DEPLOYED  
日期：2026-09-29

## 1. 角色

本目录是 LOF 机会发现项目的 App GitHub 实现区。

业务规则与当前 Canonical 位于 Knowledge GitHub：

- `05 套利研究/LOF机会发现/00_当前有效/00_恢复入口.md`
- `05 套利研究/LOF机会发现/00_当前有效/01_LOF机会发现_Canonical.md`
- `05 套利研究/LOF机会发现/01_研究/P1_QDII-LOF小额限购溢价申购套利_研究基线.md`

本目录只保存可执行实现，不复制长期业务规则作为第二份 Canonical。

## 2. 当前实现

### Domain

- `models.py`
  - LOF Market State；
  - 申购 / 赎回 State；
  - NAV quality。

### Deterministic Calculation

- `premium.py`
  - static premium；
  - estimated premium；
  - 两者严格分离。

- `economics.py`
  - P1 expected net profit 基础计算；
  - 只计算，不做尚未 Canonical 化的阈值 Gate。

### Universe

- `universe.py`
  - 上交所官方 LOF Universe Adapter；
  - 深交所官方 LOF Universe Adapter；
  - 按 exchange + code 去重；
  - 不把某日 Universe 数量写死。

### Official NAV

- `nav.py`
  - 上交所官方批量 NAV；
  - 深交所官方按代码 NAV；
  - nav_date / fetched_at / source；
  - freshness 为显式参数，不硬编码所有基金同一 T+N。

### Quote

- `quote.py`
  - 沪深批量行情；
  - quote timestamp；
  - FRESH / STALE / UNAVAILABLE；
  - 数据源失败不沿用旧值伪装实时。

### Subscription / Redemption State

- `state.py`
  - 申购 / 赎回状态；
  - 单日限额；
  - 最低申购额；
  - 申购确认 T+N；
  - 原始费率阶梯；
  - limit_scope 与真实可卖日保持 UNKNOWN/null，等待官方规则审计。

### Classification

- `classification.py`
  - 保留 raw fund type；
  - 归一化 QDII_COMMODITY / QDII_EQUITY / COMMODITY / EQUITY / BOND / FOF / MIXED / OTHER。

### Market Snapshot / Controller

- `snapshot.py`
  - 实现 LOF Market Snapshot Contract V1；
  - 保证全量 Universe 行不因数据缺失消失。

- `controller.py`
  - Universe 为硬前提；
  - Quote / NAV / State / Type lane 独立降级；
  - PASS / DEGRADED + lane_errors。

### Tests

- `tests/test_lof_runtime_baseline.py`
- `tests/test_lof_universe.py`

单元测试不依赖实时网络；live source smoke test 应放在后续 Runtime / Deployment preflight。

GitHub Actions：

- `.github/workflows/lof-runtime-tests.yml`
- 2026-09-29 首轮当前基线 CI：SUCCESS。

## 3. 尚未接入 / 尚未完成

- QDII / 重点 LOF estimated NAV Resolver；
- 限额口径 authoritative audit；
- 申购确认 → 转托管 → 真实可卖日；
- 用户真实执行渠道费率；
- 公告增量与 state delta；
- P1 Runtime Gate；
- Web/API；
- Server Runtime Store；
- Production Deployment。

## 4. 关键纪律

### 全市场展示

Universe 与页面层不得用 Opportunity Gate 删除普通 LOF。

### NAV 语义分离

必须分别保存：

- static premium：价格 vs 最新官方 NAV；
- estimated premium：价格 vs 估算当前 NAV。

不得把 QDII 的滞后官方 NAV 计算结果伪装成实时溢价。

### Program First

价格、NAV、折溢价、确定性费用和经济性计算全部程序化。

AI 仅在公告语义复杂、限额口径歧义、新 Path 研究等节点介入。

### Source Failure

未来 Adapter 必须显式输出 stale / unavailable。

禁止：

> 数据源失败后继续沿用旧值，并把旧值显示成实时状态。

## 5. 下一实现顺序

当前切换为 Display First：

```text
Universe
→ NAV
→ Quote
→ Subscription / Redemption / Limit
→ Classification
→ Market Snapshot Contract
→ Controller
→ Web/API
→ Sort / Filter / Auto Refresh
→ Share / Index Enhancements
→ State Delta
→ Estimated NAV
→ Simple P1 Projection
→ Execution Detail
```

第一版 Web 不等待完整 P1 Runtime Gate。

策略层先做轻量派生列，复杂 Execution Layer 放到详情增强。

## 6. 部署纪律

服务器不得先于 App GitHub 实现。

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

当前目录未部署到生产服务器。
