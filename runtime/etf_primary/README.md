# ETF 一级市场申购监控（V0）

## 目标

这个模块只做一件事：从上交所、深交所官方来源建立 ETF 全市场标的库，读取每日申购赎回清单（PCF），识别“普通账户可参与申购容量”的异常变化。

它与现有 `runtime/lof` 独立，不复用 LOF 的产品范围、估值逻辑或页面。V0 只建立“标的 → 分类 → 官方 PCF → 日差分”基础层，不自动交易。

## 官方来源

### 上海证券交易所

- ETF 产品列表：`https://www.sse.com.cn/assortment/fund/etf/list/`
- ETF 申购赎回清单：`https://www.sse.com.cn/disclosure/fund/etflist/`
- 单只 PCF：`https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml?fundid=<code>`
- PCF 基本信息 API：`https://query.sse.com.cn/commonQuery.do` / `COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C`

上交所 PCF 栏目的正式网站披露时间为交易日上午 8:30。因此盘前监控应在正式披露后做一次全量差分，而不是盘中高频抓取。

### 深圳证券交易所

- ETF 产品列表：`https://www.szse.cn/market/product/list/etfList/index.html`
- ETF 产品列表 API：`https://www.szse.cn/api/report/ShowReport/data?CATALOGID=1945`
- 申购赎回清单栏目：`https://www.szse.cn/disclosure/fund/currency/index.html`
- PCF 文件目录：`https://reportdocs.static.szse.cn/files/text/ETFDown/`

V0 读取当前交易日 XML。后续上线前补“申赎清单列表解析”，避免只依赖推导文件名，并把交易日/节假日回退做成 fail-closed。

## 分类：不要把一棵树写死在数据层

用户界面可以先按：

`境内 / 跨境 → 股票 / 债券 / 商品 / 货币 → 指数 / 主动`

展示。

但底层保存为彼此独立的维度：

- `region_scope`: `DOMESTIC / CROSS_BORDER / MIXED / UNKNOWN`
- `asset_class`: `EQUITY / BOND / COMMODITY / MONEY_MARKET / OTHER`
- `strategy_style`: `INDEX / ACTIVE / UNKNOWN`
- `qdii_flag`: `true / false / unknown`
- `raw_exchange_class`: 交易所原始分类，永远保留

这样沪港深、港股通、商品、现金债券 ETF、未来新类型不会被错误塞进一个互斥树。

## PCF 监控字段

每只 ETF 至少保存：交易日、是否允许申购/赎回、最小申购赎回单位、最小申赎单位净值、当日累计申购上限、当日净申购上限、单账户累计申购上限、单账户净申购上限、申赎模式、原始 PCF header。

派生字段：

- `total_baskets = 市场申购上限 / 最小申购单位`
- `account_baskets = 单账户申购上限 / 最小申购单位`
- `minimum_accounts_to_fill`
- `basket_value`

注意：累计申购上限与净申购上限语义不同，V0 不把两者相加。

## 第一批事件

监控的是“变化”而不是静态排行榜：

- `CREATION_RESUMED`：暂停 → 恢复申购
- `TOTAL_CAPACITY_JUMP`：总篮子异常增加
- `ACCOUNT_LIMIT_IMPROVED`：单账户限制收紧到更有利于分散账户
- `ACCOUNT_DISTRIBUTION_IMPROVED`：理论最少参与账户数上升
- `CREATION_UNIT_REDUCED`：最小申赎单位缩小
- `CREATION_SUSPENDED`：恢复 → 暂停（状态事件）

下一层才叠加二级市场数据：`PCF 容量事件 × 溢价空间 × 二级市场承接能力`。
其中“新增篮子金额 / 近 5 日成交额”应作为承接风险的核心字段之一。

## V0 边界

- 不自动交易。
- 不把“总篮子多”直接等同于机会。
- 不把券商通道能力写进产品规则；券商命中率单独实测。
- 不依赖集思录作为基础数据源；交易所官方源是 canonical source。
- 当前分支只做基础层，未接入现网门户、定时任务或提醒。
