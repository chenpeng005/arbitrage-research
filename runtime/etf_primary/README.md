# ETF 一级市场申购监控（V0）

## 目标

这个模块先建立可信的一级市场事实层：

**全市场 ETF → 多维分类 → 官方 PCF → 申赎容量 → 日快照 → 相邻交易日差分事件。**

它与现有 `runtime/lof` 独立，不复用 LOF 的产品范围、估值逻辑或页面。V0 不自动交易，也暂不把二级市场溢价和成交承接混进事实层。

## 官方来源与 relay

### 上海证券交易所

- ETF 产品列表：`https://www.sse.com.cn/assortment/fund/etf/list/`
- ETF 申购赎回清单：`https://www.sse.com.cn/disclosure/fund/etflist/`
- PCF 基本信息 API：`https://query.sse.com.cn/commonQuery.do`

上交所 PCF 栏目的正式网站披露时间为交易日上午 8:30。系统读取官方全量表，并显式保留“某只 PCF 交易日落后于目标交易日”的 stale 状态，禁止用旧数据伪装当日数据。

### 深圳证券交易所

- ETF 产品列表：`https://www.szse.cn/market/product/list/etfList/index.html`
- ETF 产品列表 API：`https://www.szse.cn/api/report/ShowReport/data?CATALOGID=1945`
- 申购赎回清单栏目：`https://www.szse.cn/disclosure/fund/currency/index.html`
- PCF 文件目录：`https://reportdocs.static.szse.cn/files/text/ETFDown/`

服务器当前不能稳定直连深交所，因此采用：

**GitHub Actions 读取官方源 → 12 个 shard 拉取 PCF → merge 校验 → `etf-primary-data-relay` last-good 分支 → 服务器消费。**

深交所 PCF 不再只猜文件名。prepare 阶段先解析官方当日 PCF 列表，保存每只基金的官方候选下载 URL；shard 按这个索引抓取。少量 shard 边缘失败会在 merge 阶段由新的 runner 做一次受限重试，避免重新全市场抓取。

## 分类：底层不用一棵互斥树

底层保存彼此独立的维度：

- `region_scope`: `DOMESTIC / CROSS_BORDER / MIXED / UNKNOWN`
- `asset_class`: `EQUITY / BOND / COMMODITY / MONEY_MARKET / OTHER`
- `strategy_style`: `INDEX / ACTIVE / UNKNOWN`
- `qdii_flag`: `true / false / unknown`
- `raw_exchange_class`: 永久保留交易所原始分类

这样沪港深、港股通、商品、债券、货币及未来新类型不会被错误塞进同一个互斥分类树。

## PCF 事实字段

每只 ETF 至少保存：交易日、是否允许申购/赎回、最小申购赎回单位、最小申赎单位净值、累计申购上限、净申购上限、单账户累计申购上限、单账户净申购上限、申赎模式及来源。

派生字段：

- `total_baskets = 市场申购上限 / 最小申购单位`
- `account_baskets = 单账户申购上限 / 最小申购单位`
- `minimum_accounts_to_fill`
- `basket_value`

累计申购上限与净申购上限语义不同，系统不把两者相加。

## 第一批变化事件

监控的是变化，不是静态排行榜：

- `CREATION_RESUMED`：暂停 → 恢复申购
- `TOTAL_CAPACITY_JUMP`：总篮子异常增加
- `ACCOUNT_LIMIT_IMPROVED`：单账户限制收紧到更有利于分散账户
- `ACCOUNT_DISTRIBUTION_IMPROVED`：理论最少参与账户数上升
- `CREATION_UNIT_REDUCED`：最小申赎单位缩小
- `CREATION_SUSPENDED`：开放 → 暂停

`runtime_cycle.py` 负责消费 relay、建立基线、保存压缩日快照并对相邻交易日做 diff。同一交易日重复运行只刷新快照，不制造跨日事件。

## 存储

V0 不上重数据库：

- `current.json`：下一次 diff 使用的当前指针；
- `snapshots/YYYYMMDD.json.gz`：压缩后的交易日 PCF 摘要；
- `events/YYYYMMDD.json`：只有发生跨交易日比较时才生成；
- `last_run.json`：本次运行状态、覆盖、缺失和事件数。

完整 PCF 原始篮子不做长期全量保存；当前阶段只保存判断一级市场容量变化所需的摘要字段。

## V0 边界

- 不自动交易。
- 不把“总篮子多”直接等同于机会。
- 不把券商通道能力写进产品规则；券商命中率单独实测。
- 不依赖集思录作为基础数据源；交易所官方源是 canonical source。
- `missing` 与 `stale` 必须显式暴露，不静默补值。
- 下一层才叠加：`PCF 容量事件 × 溢价空间 × 二级市场承接能力 × 券商可执行性`。
