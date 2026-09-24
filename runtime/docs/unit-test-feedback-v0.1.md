# Unit Test Feedback V0.1

## 目标

单元测试页面不能只在最后显示 PASS / FAIL。

用户需要能够在运行过程中判断：

> 系统走到了哪里？这一小步做了什么？结果是否可信？为什么可以继续？

但也不能把底层网络日志、Python traceback、每条 HTTP 请求直接倾倒给用户。

因此采用三层反馈。

## Layer 1｜Run Summary

页面顶部始终显示：

- Unit 名称
- run_id
- snapshot_mode
- market_cutoff
- 当前阶段
- 总状态
- 已完成步骤 / 总步骤
- warning / error 数量

状态：

`PENDING / RUNNING / PASS / WARNING / FAIL / BLOCKED`

## Layer 2｜Step Cards

每个业务可判断小步骤一张卡。

每张卡固定回答六件事：

1. 本步目标
2. 输入
3. 动作
4. 关键结果
5. Audit
6. 本步结论

结论必须是自然语言的局部判断，而不是复制最终结论。

示例：

> 主行情获取完成：321个候选对象，核心字段返回正常，可以进入 Universe 分类。

## Layer 3｜Technical Detail

默认折叠，仅排错时展开：

- source URL / adapter
- fetched_at
- 原始字段名
- excluded objects
- mismatch rows
- traceback
- raw JSON path
- timing

## Market Map Acquisition 第一版步骤

### S0 Trigger

显示：

- 用户触发时间
- run_id
- snapshot_mode
- 目标 market_cutoff
- knowledge / application version

局部结论：

> 本次运行已冻结运行身份和市场时点，可以开始取数。

### S1 Main Market Snapshot

显示：

- 主数据源
- candidate count
- P/S/K/CV 覆盖率
- 请求是否成功

局部结论示例：

> 主行情取得成功，共321个候选对象；进入 Universe 分类。

### S2 Universe Classification

显示：

- candidate
- valid priced
- excluded
- exclusion reasons 分组
- 可展开查看对象

局部结论示例：

> 311只形成有效公开交易价格；10只因未上市、停止交易、定向转债等原因退出 Base 样本。

### S3 Maturity Enrichment

显示：

- maturity coverage
- fallback count
- cross-source mismatches
- 是否影响 Duration Sample

局部结论示例：

> 311只有效样本全部获得到期日；可以计算 T 并进入 Duration Gate。

### S4 Remaining Size Enrichment

显示：

- Size coverage
- sanity PASS
- missing / invalid
- source risk

局部结论示例：

> 311只全部取得剩余规模且结构检查通过；可以进入 Scale Gate。

### S5 Cross-source Audit

显示：

- CV 自算差异
- P/S/K 跨源差异
- maturity 口径差
- warning / unresolved

局部结论示例：

> 未发现阻塞性冲突；盘中 CV 存在轻微刷新差，正式 Snapshot 应使用收盘模式。

### S6 Acquisition Ready

显示：

- Base sample count
- Duration sample count
- Scale sample count
- Support / Core counts
- acquisition_result 路径

局部结论：

> Acquisition Unit PASS，可以把结构化输入交给 Market Map Calculation。

## 反馈节奏原则

- 一步完成即刷新，不等整个 Unit 结束；
- 只展示“业务上有意义的步骤”，不展示每个函数调用；
- WARNING 不等于 FAIL；
- FAIL 必须明确：停在哪一步、为什么停、需要什么才能继续；
- excluded object 必须可展开，不能只给一个数量；
- 所有步骤保留机器 JSON，页面负责可读表达。

## 人工交互边界

第一版运行中默认不要求用户逐步点击“继续”。

程序自动推进，用户实时观察。

只有出现 `BLOCKED / NEEDS_REVIEW` 时，才允许 Runtime 暂停并要求人工处理。

这样既保持 Engineering 外循环自动运行，也保留必要的人类控制点。
