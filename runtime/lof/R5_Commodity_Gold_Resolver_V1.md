# R5 Commodity Gold Resolver V1

状态：DESIGN BASELINE

## 目标

验证非股票指数 LOF 的盘中估值能力。第一阶段选择黄金类 LOF，因为底层资产简单、价格透明。

目标不是精确复制基金实时净值，而是在机会发现场景中提供足够可靠的 Estimated NAV。

## Resolver 类型

`R5_SPECIAL / COMMODITY_FX_BRIDGE`

## 基础公式

```
Estimated NAV
=
Official NAV(T-1)
× (1 + exposure × commodity_return)
× (1 + fx_return)
× tracking_adjustment
```

V1：

- tracking_adjustment = 1
- exposure 由 registry 提供

## 输入

必需：

- official_nav
- commodity_anchor
- commodity_current
- fx_anchor
- fx_current

辅助：

- commodity_proxy_id
- exposure_ratio
- proxy_quality
- timestamp

## 输出

统一 EstimatedNavResult：

- estimated_nav
- estimated_nav_time
- estimated_nav_status
- estimated_nav_quality
- resolver_class
- resolver_method
- proxy_return
- fx_return
- exposure_ratio_used

## 质量规则

HIGH：

- 商品价格近实时
- 汇率近实时
- 时间差可接受

MEDIUM：

- 部分数据存在延迟

LOW：

- 主要依赖历史数据

## 风险边界

必须区分：

- 底层市场开放
- 底层市场关闭
- 数据过期
- 无法估值

不要将海外收盘后的价格偏离直接识别为套利机会。

## 后续

1. 建立黄金 LOF proxy registry；
2. 接入 commodity_quote；
3. 用历史 NAV 校验误差；
4. 再扩展原油、其他商品。