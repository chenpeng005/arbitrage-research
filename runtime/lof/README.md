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

### Tests

- `tests/test_lof_runtime_baseline.py`
- `tests/test_lof_universe.py`

单元测试不依赖实时网络；live source smoke test 应放在后续 Runtime / Deployment preflight。

## 3. 尚未接入

- 实时行情 Adapter；
- 官方 NAV 全市场 Adapter；
- 申购 / 赎回 / 限额 Adapter；
- 公告增量；
- Market Snapshot Contract；
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

```text
Universe
→ NAV
→ Quote
→ Market Snapshot Contract
→ Subscription / Redemption / Limit
→ State Delta
→ P1 Runtime Gate
→ Web/API
```

P1 Gate 必须等历史案例研究把阈值和执行约束 Canonical 化后再写。

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
