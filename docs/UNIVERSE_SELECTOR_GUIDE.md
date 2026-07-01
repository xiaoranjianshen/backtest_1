# 动态选品模块说明

动态选品用于回答一个问题：**某个交易日，哪些品种或合约进入策略候选池，以及各自权重是多少**。

选品模块不直接下单。下单、止损、止盈、调仓仍由具体策略负责。

## 1. 标准输出

推荐每个选品模型输出一个 DataFrame：

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `trade_date` | 是 | 选品生效交易日 |
| `symbol` | 是 | 品种或合约代码，例如 `c`、`rb`、`c2601` |
| `rank` | 否 | 当天排名，1 表示最靠前 |
| `score` | 否 | 模型分数或规则分数 |
| `weight` | 否 | 仓位分配权重；不需要加权时填 `1.0` |
| `side` | 否 | 方向建议，`1` 多，`-1` 空，`0` 表示只选品 |
| `reason` | 否 | 选入原因 |
| `model_name` | 否 | 选品模型名称 |

额外字段会自动进入 `meta`，后续可在信号诊断或交易日志里展示。

## 2. 在策略中使用

```python
from strategy.common.universe import DataFrameUniverseSelector

selector = DataFrameUniverseSelector(selection_df, name="my_selector")
```

策略中查询：

```python
entry = selector.entry(current_date, symbol)
if entry is None:
    # 当天未入选，不允许新开仓
    pass
```

`entry` 包含：

```python
entry.rank
entry.score
entry.weight
entry.side
entry.reason
entry.model_name
entry.meta
```

## 3. 品种级和合约级

模块同时支持品种级和合约级：

- 选品表写 `c`，策略查询 `c2601` 时会回退匹配到 `c`。
- 选品表写 `c2601`，则只精确匹配这个合约。

因此：

- 品种轮动模型建议输出纯品种代码。
- 合约选择模型建议输出具体合约代码。

## 4. 新增选品模型时需要做什么

最小接入步骤：

1. 写一个函数生成 `selection_df`。
2. 确保使用当时可见数据，不使用未来收益或未来行情。
3. 用 `DataFrameUniverseSelector(selection_df, name="模型名")` 包装。
4. 把 selector 传给支持动态选品的策略。

不要在策略内部重新写一套选品数据结构。
