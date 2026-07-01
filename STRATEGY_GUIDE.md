# Backtest-1 策略开发简明指南

这份文档就是新用户优先看的策略模板文档。它只保留最需要知道的内容：怎么运行、回测链路怎么走、策略应该写什么、run_scripts 脚本怎么写、关键函数在哪里。

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
| `strategy/common/types.py` | 通用信号协议说明。这里定义策略能返回哪些字段。 |
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

常用字段。下面这些字段不是每个策略都必须用，但都是当前框架已经支持的功能：

| 字段 | 含义 |
| --- | --- |
| `signal` | 方向：`1` 做多，`-1` 做空，`0` 平仓，`None` 不操作。 |
| `position_mode` | 仓位模式：`target` 目标仓位，`delta` 增减仓，`reduce` 部分平仓，`flat` 全平。 |
| `signal_score` | 信号分数，只用于信号检测、IC/Rank IC，不直接影响下单。 |
| `reason` | 信号原因，显示在复盘日志里。 |
| `metrics` | 策略指标，例如 MA、zscore、close。 |
| `close_pct` | 平仓比例，例如 `0.5` 表示平一半。 |
| `close_volume` | 指定平几手。 |
| `target_volume` | 指定目标手数。 |
| `target_net` | 指定目标净持仓，多头为正、空头为负。 |
| `target_weight` | 目标名义市值占当前权益比例，可为正负，例如 `0.05` 表示目标名义多头约占权益 5%。 |
| `target_margin_pct` | 目标保证金占当前权益比例，可为正负，适合选品策略分配组合保证金。 |
| `delta_volume` | 指定增减几手。 |
| `risk_pct` | 按单笔最大风险占当前权益比例开仓，需要配合 `stop_loss_ticks` 或 `stop_loss_price`。 |
| `stop_loss_ticks` | 风险开仓使用的止损跳数。 |
| `stop_loss_price` | 风险开仓使用的止损价格。 |
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

按目标名义权重调仓：

```python
{"target_weight": 0.05, "reason": "rank_top_1"}
```

表示目标名义多头约占当前权益 5%。如果写成 `-0.05`，表示目标名义空头约占当前权益 5%。

按目标保证金占比调仓：

```python
{"signal": 1, "target_margin_pct": 0.08, "reason": "selected_symbol"}
```

表示该品种目标占用保证金约为当前权益 8%。这比直接写固定手数更适合“每天选 3-5 个品种”的策略。

按止损风险反推手数：

```python
{"signal": 1, "risk_pct": 0.005, "stop_loss_ticks": 20, "reason": "risk_sized_entry"}
```

表示如果触发 20 跳止损，单笔理论亏损约控制在当前权益的 0.5%。

带模型分数的信号：

```python
{"signal": 1, "signal_score": 0.83, "position_mode": "target", "reason": "model_long"}
```

`signal_score` 不改变下单方向和手数，只给信号检测页计算 IC / Rank IC 使用。没有 `signal_score` 时，系统会从 `target_weight`、`target_margin_pct`、`target_net` 或 `signal` 推断分数。

动态选品策略通常这样写：

```python
signals = {}
selected = self.selector_by_date.get(today, {})

for sym in self.symbols:
    weight = selected.get(sym, 0.0)
    if weight == 0:
        if self.get_net_position(sym) != 0:
            signals[sym] = {"signal": 0, "position_mode": "flat", "reason": "removed_from_pool"}
        continue

    signals[sym] = {"target_margin_pct": weight, "reason": "selected_by_model"}
```

注意：动态选品只能在本次回测已经加载的品种池里切换。策略不能在回测中途交易一个没有被加载进来的新品种。

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

支持的 `sizing.mode`。这些功能已经在 `strategy/common/sizing.py` 里实现，不一定每个策略都会用到：

| mode | 含义 |
| --- | --- |
| `fixed_volume` | 固定手数。`value=2` 表示基础仓位为 2 手。 |
| `fixed_margin` | 固定保证金金额。`value=100000` 表示按 10 万保证金反推手数。 |
| `fixed_notional` | 固定名义金额。`value=1000000` 表示按 100 万合约市值反推手数。 |
| `capital_pct` | 按初始资金比例。`value=0.03` 表示用初始资金的 3% 作为保证金。 |
| `equity_pct` | 按当前总权益比例。`value=0.03` 表示用当前权益的 3% 作为保证金。 |
| `available_pct` | 按当前可用资金比例。`value=0.03` 表示用当前可用资金的 3% 作为保证金。 |

策略信号里也可以单独覆盖仓位：

| 字段 | 用法 |
| --- | --- |
| `target_volume` | 指定目标手数，不再使用 sizing 计算出来的基础手数。 |
| `target_net` | 指定最终净持仓，例如 `3` 是净多 3 手，`-2` 是净空 2 手。 |
| `target_weight` | 目标名义市值占当前权益比例，适合模型权重。 |
| `target_margin_pct` | 目标保证金占当前权益比例，适合动态选品策略。 |
| `risk_pct` | 按止损距离控制单笔风险，需要配合 `stop_loss_ticks` 或 `stop_loss_price`。 |

执行方式配置：

```python
"execution": {
    "order_type": "market",
    "slippage_ticks": 0.5,
}
```

Tick 对价单示例：

```python
"execution": {
    "order_type": "opponent",
    "price_field": "mid_price",
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

## 11. run_scripts 脚本标准格式

每个 `run_scripts/run_xxx.py` 都建议按下面格式写。这样别人打开脚本时，可以直接看到品种、时间、资金、策略参数、仓位规则、执行规则。

```python
# run_scripts/run_my_strategy.py
# -*- coding: utf-8 -*-

import sys
from pathlib import Path


# 让脚本无论从 PyCharm 还是 PowerShell 运行，都能正确导入项目模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.my_strategy import MyStrategy


# 1. 回测品种。多品种策略写 list，单品种也建议先写 list，方便以后扩展。
TARGET_SYMBOLS = ["rb", "hc", "i"]

# 2. 回测时间。分钟和 tick 策略建议写到具体交易时间。
START_DATE = "2021-01-01 00:00:00"
END_DATE = "2022-01-01 23:59:59"

# 3. 策略参数。上面是策略自己的逻辑参数，下面是通用仓位/执行/平仓参数。
STRATEGY_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,

    # 策略自己的参数：这些名字由 MyStrategy.__init__ 决定。
    "lookback": 20,

    # 通用仓位规则：这里决定基础仓位怎么算。
    "sizing": {
        "mode": "equity_pct",
        "value": 0.03,
        "min_volume": 1,
        "max_volume": None,
        "round_lot": 1,
    },

    # 通用执行规则：这里决定市价、限价、对价单和滑点。
    "execution": {
        "order_type": "market",
        "price_field": "close",
        "slippage_ticks": 0.5,
    },

    # 通用平仓规则：这里决定 signal=0 时默认平多少，以及是否允许反手。
    "exit": {
        "close_pct": 1.0,
        "allow_reverse": True,
        "respect_pending_orders": True,
    },

    # 是否记录信号检测数据。建议保持 True。
    "record_signals": True,
}


if __name__ == "__main__":
    analyzer = run_backtest(
        strategy_class=MyStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date=START_DATE,
        end_date=END_DATE,
        freq="1d",
        data_type="main",
        initial_capital=5_000_000.0,
        strategy_kwargs=STRATEGY_KWARGS,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
```

脚本里最常改的是这几块：

| 位置 | 作用 |
| --- | --- |
| `TARGET_SYMBOLS` | 本次回测加载哪些品种。动态选品策略也只能在这个池子里切换。 |
| `START_DATE` / `END_DATE` | 回测时间。 |
| `freq` | 数据周期，例如 `1d`、`5m`、`1m`、`tick`。 |
| `initial_capital` | 初始资金。 |
| `STRATEGY_KWARGS` 顶部 | 策略自己的逻辑参数。 |
| `sizing` | 开仓基础仓位怎么算。 |
| `execution` | 市价/限价/对价单和滑点怎么处理。 |
| `exit` | 默认平仓比例、是否允许反手、是否考虑未成交挂单。 |

## 12. 关键函数速查

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

## 13. 未来函数边界

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

## 14. 新手检查清单

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
