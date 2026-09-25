# Opportunity Workbench V1

## 目标

把已经跑通的机会发现 Runtime 从“工程审计页”提升为用户真正使用的统一工作台。

正常用户视角只回答四件事：

1. 现在是否在运行、运行到哪一步；
2. 当前正式藏宝图是什么；
3. 最终发现了哪些存在正向经济空间的转债；
4. 点进具体转债后，完整中文研究结论是什么。

旧 Review 保留为工程审计入口，不再作为主使用界面。

## Full Runtime Controller V1

正式顺序：

```text
Market Map
→ Discovery Market Ingress
→ Economic Discovery
→ Research Trigger
→ Research Task Builder
→ Evidence Builder
→ Path Research
→ Candidate Pool
→ Opportunity Record
→ Complete
```

Controller 只负责顺序、状态、审计、停止条件和持久化，不重新计算或覆盖任何 Path 的经济判断。

支持两种启动：

- CLOSE：从今日正式收盘藏宝图开始完整运行；
- LATEST_FORMAL：复用最近一次正式藏宝图，仅重跑下游，便于调试和恢复。

Path Research 按批次执行，并设置最大轮数；如果没有进展则停止，避免 AI 无界循环。

## Opportunity View Contract V1

Opportunity Record 继续保持无损工程结构；View Contract 只做展示翻译，不修改 Economic KEEP。

用户页面规则：

- 只展示 Candidate Pool 中的正向经济机会；
- 研究是否完成不能成为删除机会的条件；
- 多路径独立展示，互不覆盖；
- 正常页面以中文为主；
- KEEP、Trigger、Path Result、Review Ready、内部 ID 等工程术语进入审计层；
- 已完成深研的路径展示完整判断、风险、失效条件、更新时间和关键证据；
- 尚未触发深研的下修路径展示当前经济空间、当前事件状态、为什么尚未深研、未来什么节点会触发研究。

## Workbench 页面

`/workbench` 包含：

- 运行中心：启动完整 Runtime + 真实阶段进度；
- 藏宝图：沿用当前正式藏宝图，保留原页面能力；
- 机会结果：只看正向经济机会；
- 个券完整研究：中文分层阅读；
- 工程审计：链接旧 `/review` 与原始 Runtime 信息。

## 当前验证

以 2026-09-24 正式市场截面安全试跑（关闭新增 AI 深研）：

- 市场输入：311 只；
- 机会转债：163 只；
- 正向经济路径：179 条；
- 已完成深研：37 条；
- 等待补充证据：1 条；
- 尚未到深研事件节点：141 条（全部为下修路径）。

“研究产物 → Path Result → Opportunity Record”抽样核对到期现金、回售、下修均为无损传递。当前主要问题属于 View 层，而非研究结果被 Contract 压缩。
