# Backtest-1 策略开发简明指南

这份文档只保留新用户最需要知道的内容：怎么运行、回测链路怎么走、策略应该写什么、关键函数在哪里。

更完整的安装说明看 `README.md`。

## 1. 快速运行

在项目根目录执行：

```powershell
python run_scripts/run_general_multi_ma.py
```

常用入口都在 `run_scripts/`：

| 脚本 | 策略 |
| --- | --- |
| `run_general_multi_ma.py` | 通用多品种均线 |
| `run_dual_ma.py` | 双均线 |
| `run_breakout_pyramid.py` | 增仓突破 |
| `run_zscore_reversal.py` | Z-Score 反转 |
| `run_factor.py` | 因子策略 |

运行结束后会生成 HTML 报告。

如果是第一次安装依赖：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\install_windows.ps1
```

真实回测需要配置 ClickHouse 环境变量：

```powershell
$env:BACKTEST_CH_HOST="你的ClickHouse地址"
$env:BACKTEST_CH_USER="你的用户名"
$env:BACKTEST_CH_PASS="你的密码"
```

## 2. 项目核心模块

| 模块 | 作用 |
| --- | --- |
| `run_scripts/` | 用户运行入口，配置策略、品种、时间、资金。 |
| `backtest_engine.py` | 回测主循环，按时间逐 bar 推进。 |
| `data_feed/` | 从 ClickHouse 取数，并对齐多品种行情。 |
| `strategy/` | 策略逻辑。新策略主要写在这里。 |
| `strategy/general_template.py` | 推荐新策略继承的通用模板。 |
| `strategy/common/` | 信号解析、仓位计算、市价/限价执行、调仓。 |
| `broker/` | 订单、撮合、成交、换月。 |
| `portfolio/account.py` | 资金、持仓、保证金、盈亏。 |
| `analyzer/` | 绩效统计和复盘数据。 |
| `frontend_index.py` | 生成网页报告。 |
| `ui/` | 配置中心页面。 |

## 3. 一次回测的运行顺序

以 `run_scripts/run_general_multi_ma.py` 为例：

```text
run_general_multi_ma.py
  -> run_backtest()
    -> DataProvider 读取并对齐行情
    -> Account 初始化账户
    -> MatchEngine 初始化撮合器
    -> Strategy 初始化策略
    -> 每根 bar:
         1. broker.process_cross_section() 撮合上一根 bar 留下的订单
         2. strategy.on_bar() 调用策略
         3. generate_signals() 生成策略信号
         4. SignalRebalancer.rebalance() 把信号转成订单
         5. account 通过成交更新资金和持仓
    -> StrategyAnalyzer 统计结果
    -> build_html_dashboard() 生成报告
```

关键点：

- `backtest_engine.py` 不写策略指标，也不判断买卖。
- 策略只表达交易意图。
- 真实下单手数、开平仓、是否反手、市价/限价由通用模板和配置处理。
- 当前 bar 生成的新订单，最早在下一根 bar 撮合。

## 4. 每根 bar 传给策略什么

策略收到的是 `bar_data`，格式大致如下：

```python
{
    "rb": {
        "open": 3500,
        "high": 3520,
        "low": 3480,
        "close": 3510,
        "month_change": 0,
    },
    "hc": {
        "open": 3600,
        "high": 3625,
        "low": 3580,
        "close": 3610,
        "month_change": 0,
    },
}
```

`bar_data` 只放当前 bar 的行情。

不直接放这些东西：

- 当前持仓
- 当前挂单
- MA10、MA30、zscore 等策略指标
- 目标仓位

这些应该在策略对象内部维护，或通过账户查询函数获取。

常用账户查询：

```python
self.get_net_position("rb")                 # 多头为正，空头为负
self.get_position_volume("rb", Direction.LONG)
self.get_position_volume("rb", Direction.SHORT)
```

## 5. 新策略应该继承哪个类

普通新策略优先继承：

```python
from strategy.general_template import GeneralSignalStrategy
```

然后只实现：

```python
def generate_signals(self, bar_data: dict) -> dict:
```

不推荐新手直接继承 `BaseStrategy`，因为那样需要自己处理更多订单细节。

## 6. `generate_signals()` 输入和输出

输入：

```python
def generate_signals(self, bar_data: dict) -> dict:
```

`bar_data` 是当前时间点所有品种的行情。

输出：

```python
return {
    "rb": {
        "signal": 1,
        "position_mode": "target",
        "reason": "golden_cross",
        "metrics": {
            "close": close_price,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
        },
    }
}
```

常用字段：

| 字段 | 含义 |
| --- | --- |
| `signal` | 方向：`1` 做多，`-1` 做空，`0` 平仓，`None` 不操作。 |
| `position_mode` | 仓位模式：`target` 目标仓位，`delta` 增减仓，`reduce` 部分平仓，`flat` 全平。 |
| `reason` | 信号原因，显示在复盘日志里。 |
| `metrics` | 策略指标，例如 MA、zscore、close。 |
| `close_pct` | 平仓比例，例如 `0.5` 表示平一半。 |
| `close_volume` | 指定平几手。 |
| `target_volume` | 指定目标手数。 |
| `target_net` | 指定目标净持仓，多头为正、空头为负。 |
| `delta_volume` | 指定增减几手。 |
| `limit_price` | 指定限价单价格。 |

## 7. 最常用的信号写法

不操作：

```python
{"signal": None, "reason": "hold"}
```

按配置仓位做多：

```python
{"signal": 1, "position_mode": "target", "reason": "long_entry"}
```

按配置仓位做空：

```python
{"signal": -1, "position_mode": "target", "reason": "short_entry"}
```

平一半：

```python
{"signal": 0, "position_mode": "reduce", "close_pct": 0.5}
```

全平：

```python
{"signal": 0, "position_mode": "flat"}
```

增仓一档：

```python
{"signal": 1, "position_mode": "delta", "size_scale": 1.0}
```

指定目标净持仓：

```python
{"target_net": 3}
```

表示最终净多 3 手。

## 8. 仓位和下单由哪里控制

策略文件只负责信号。

仓位配置在运行脚本或配置中心里：

```python
"sizing": {
    "mode": "equity_pct",
    "value": 0.03,
    "min_volume": 1,
    "max_volume": None,
}
```

支持的 `sizing.mode`：

| mode | 含义 |
| --- | --- |
| `fixed_volume` | 固定手数。 |
| `fixed_margin` | 固定保证金金额。 |
| `fixed_notional` | 固定名义金额。 |
| `capital_pct` | 按初始资金比例。 |
| `equity_pct` | 按当前总权益比例。 |
| `available_pct` | 按当前可用资金比例。 |

执行方式配置：

```python
"execution": {
    "order_type": "market",
    "slippage_ticks": 0.5,
}
```

限价单示例：

```python
"execution": {
    "order_type": "limit",
    "limit_mode": "at_close",
    "ticks": 0,
    "price_field": "close",
}
```

平仓和反手配置：

```python
"exit": {
    "close_pct": 1.0,
    "allow_reverse": True,
    "respect_pending_orders": True,
}
```

如果策略信号和配置冲突，以通用调仓器的最终解释为准。例如 `allow_reverse=False` 时，策略发出反手信号也会先平到 0，不会直接反向开仓。

## 9. 最小策略模板

```python
# strategy/custom/my_strategy.py
# -*- coding: utf-8 -*-

import pandas as pd

from strategy.general_template import GeneralSignalStrategy


class MyStrategy(GeneralSignalStrategy):
    def __init__(self, broker, account, symbol="multi", target_symbols=None, lookback=20, **kwargs):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.lookback = int(lookback)
        self.history = {sym: [] for sym in self.symbols}

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}

        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not bar or pd.isna(bar.get("close", pd.NA)):
                continue

            close_price = float(bar["close"])
            history = self.history.setdefault(sym, [])
            history.append(close_price)
            if len(history) > self.lookback:
                history.pop(0)

            if len(history) < self.lookback:
                signals[sym] = {"signal": None, "reason": "warming_up"}
                continue

            mean_price = sum(history) / len(history)

            if close_price > mean_price:
                signals[sym] = {"signal": 1, "position_mode": "target", "reason": "above_mean"}
            elif close_price < mean_price:
                signals[sym] = {"signal": -1, "position_mode": "target", "reason": "below_mean"}
            else:
                signals[sym] = {"signal": None, "reason": "hold"}

        return signals
```

## 10. 新策略接入配置中心

如果希望配置中心能选择新策略，需要改 `ui/run_from_config.py`。

最少加三处：

1. 写一个 `_build_xxx(config, spec)`，把 UI 参数转成 `run_backtest()` 参数。
2. 在 `STRATEGY_SPECS` 里注册策略。
3. 确认策略类能正常 import。

示例注册：

```python
"my_strategy": StrategySpec(
    key="my_strategy",
    label="我的策略 (My Strategy)",
    module="strategy.custom.my_strategy",
    class_name="MyStrategy",
    kind="general_signal",
    builder=_build_my_strategy,
)
```

如果只想先本地跑通，也可以先写一个 `run_scripts/run_my_strategy.py`，不接配置中心。

## 11. 关键函数速查

| 函数 | 位置 | 作用 |
| --- | --- | --- |
| `run_backtest()` | `backtest_engine.py` | 回测总入口。 |
| `extract_bar_data()` | `backtest_engine.py` | 把宽表行情转成策略用的 `bar_data`。 |
| `strategy.on_bar()` | `backtest_engine.py` 调用 | 每根 bar 调一次策略。 |
| `GeneralSignalStrategy.on_bar()` | `strategy/general_template.py` | 调用 `generate_signals()`，再调用调仓器。 |
| `generate_signals()` | 具体策略文件 | 新策略最主要写的函数。 |
| `SignalRebalancer.rebalance()` | `strategy/common/rebalancer.py` | 把标准信号转成订单。 |
| `PositionSizer.calculate()` | `strategy/common/sizing.py` | 根据资金模式计算手数。 |
| `ExecutionPolicy.get_order_prices()` | `strategy/common/execution.py` | 根据市价/限价配置生成订单价格。 |
| `process_cross_section()` | `broker/match_engine.py` | 撮合订单，生成成交。 |
| `process_trade()` | `portfolio/account.py` | 成交后更新资金、持仓和保证金。 |

## 12. 未来函数边界

当前设计是：

```text
当前 bar 撮合旧订单
当前 bar 行情传给策略
策略生成新订单
新订单下一根 bar 才能撮合
```

所以，用当前 bar 的 `close` 计算信号，再下一根 bar 成交，是可以接受的。

需要避免：

- 用未来 bar 的价格计算当前信号。
- 在策略里假设当前 bar 生成的订单已经成交。
- 用当前 bar 的 high/low 判断“盘中先触发”，同时又按当前 bar 内更好价格成交。
- 在 `backtest_engine.py` 里硬写策略指标或交易规则。

## 13. 新手检查清单

写完新策略后检查：

- 策略类继承 `GeneralSignalStrategy`。
- 策略实现了 `generate_signals(self, bar_data)`。
- 输出信号的 key 是品种代码，例如 `"rb"`、`"hc"`。
- `signal` 只表达方向，不直接代表几手。
- 手数通过 `sizing` 控制。
- 市价/限价通过 `execution` 控制。
- 部分平仓用 `position_mode="reduce"` 加 `close_pct` 或 `close_volume`。
- 全平用 `position_mode="flat"`。
- 指标值放进 `metrics`，方便网页复盘查看。
- 先用 `run_scripts/` 单独跑通，再考虑接入配置中心。
