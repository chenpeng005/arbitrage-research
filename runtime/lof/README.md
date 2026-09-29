# LOF Opportunity Runtime

状态：APP SCAFFOLD / NOT DEPLOYED  
日期：2026-09-29

## 1. 角色

本目录是 LOF 机会发现项目的 App GitHub 实现区。

业务规则与当前 Canonical 位于 Knowledge GitHub：

- `05 套利研究/LOF机会发现/00_当前有效/00_恢复入口.md`
- `05 套利研究/LOF机会发现/00_当前有效/01_LOF机会发现_Canonical.md`
- `05 套利研究/LOF机会发现/01_研究/P1_QDII-LOF小额限购溢价申购套利_研究基线.md`

本目录只保存可执行实现，不复制长期业务规则作为第二份 Canonical。

## 2. 当前工程阶段

当前仅建立：

- LOF 行情 / NAV 领域模型；
- static premium 与 estimated premium 的确定性计算；
- P1 经济性基础计算器；
- 对应单元测试。

尚未接入：

- 交易所 Universe Adapter；
- 实时行情 Adapter；
- NAV Adapter；
- 申购 / 赎回 / 限额 Adapter；
- 公告增量；
- Web/API；
- Server Runtime Store；
- Production Deployment。

## 3. 关键纪律

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

## 4. 部署纪律

服务器不得先于 App GitHub 实现。

正式部署仍必须：

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
