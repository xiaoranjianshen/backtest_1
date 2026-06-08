# 策略开发指南

这份文档给后续写策略的用户使用。目标是：不用读完整回测引擎，也能知道应该继承哪个模板、实现哪个函数、怎么运行测试。

## 1. 先选策略类型

目前推荐三种写法。

### 单品种规则策略：`RuleTemplate`

适合只交易一个品种，并且策略只需要表达“做多 / 做空 / 保持当前状态”的情况。

参考文件：

- `strategy/rule_template/dual_ma.py`

你只需要实现：

```python
def calculate_signal(self, bar: dict) -> int:
    ...
```

返回值含义：

- `1`：目标状态为多头
- `-1`：目标状态为空头
- `self.current_pos`：保持当前仓位不变

适合：

- 双均线
- RSI 反转
- 简单趋势跟踪
- 只有“多/空/不动”状态的策略

### 目标手数策略：`PortfolioTemplate`

适合策略需要直接控制“目标净手数”的情况。这是目前最通用、最推荐新手先学的模板。

参考文件：

- `strategy/rule_template/multi_ma.py`
- `strategy/custom/breakout_pyramid.py`

你只需要实现：

```python
def generate_target_portfolio(self, bar_data: dict) -> dict:
    return {"rb": 10, "i": -3}
```

返回值含义：

- `{"rb": 10}`：螺纹钢目标净多 10 手
- `{"rb": -3}`：螺纹钢目标净空 3 手
- `{"rb": 0}`：螺纹钢目标空仓

模板会自动处理：

- 当前仓位是多少
- 需要加仓还是减仓
- 需要先平仓还是反手
- 下单调用 `buy / sell / short / cover`

适合：

- 增仓突破
- 网格类目标仓位
- 分批加仓/减仓
- 多品种轮动
- 任何需要精确控制手数的策略

### 多因子权重策略：`FactorTemplate`

适合多品种横截面排序，最后输出目标权重。

参考文件：

- `strategy/factor_template/composite_factor.py`

你只需要实现：

```python
def calculate_weights(self, cross_section: dict) -> dict:
    return {"rb": 0.10, "i": -0.10}
```

返回值含义：

- `{"rb": 0.10}`：用 10% 初始资金保证金去做多 rb
- `{"i": -0.10}`：用 10% 初始资金保证金去做空 i

适合：

- 截面动量
- carry 因子
- 期限结构因子
- 多因子合成
- 多空组合

## 2. 策略能拿到什么数据？

每根 K 线都会传入一个 `bar_data`。

单品种例子：

```python
bar = bar_data["rb"]
close = bar["close"]
open_ = bar["open"]
high = bar["high"]
low = bar["low"]
volume = bar["volume"]
```

多品种例子：

```python
for sym, bar in bar_data.items():
    close = bar["close"]
```

常用字段：

- `open`：开盘价
- `high`：最高价
- `low`：最低价
- `close`：收盘价
- `volume`：成交量
- `oi`：持仓量
- `month_change`：主力换月标记

Tick 模式可能还有：

- `last_price`
- `bid_price_1`
- `ask_price_1`
- `bid_volume_1`
- `ask_volume_1`

## 3. 下单和成交时点

当前主回测引擎的顺序是：

1. 先撮合之前已经提交的订单。
2. 策略读取当前这根 K 线。
3. 策略根据当前 K 线提交新订单。
4. 新订单在下一根 K 线撮合。

所以，如果你用今天的 `close / high / low` 生成信号，成交发生在下一根 K 线。这种写法等价于“收盘确认信号，下一根 K 线交易”，一般不算未来函数。

但是，如果你的策略设定是“今天开盘前就要交易”，那就不能使用今天的 `high / low / close`，因为这些数据在开盘前还不知道。

## 4. 新手写策略时不要做什么

不要直接改：

- `account.positions`
- `account.available`
- `broker.pending_orders`
- `broker.trade_history`

不要在策略里直接读取完整历史 DataFrame。

不要在策略里使用未来行数据。

不要自己手写 `reference_price=0` 的订单。

正常情况下不需要改：

- `backtest_engine.py`
- `broker/`
- `portfolio/`
- `data_feed/`
- `analyzer/`

## 5. 推荐的新策略目录

自定义策略建议放在：

```text
strategy/custom/
```

例如：

```text
strategy/custom/breakout_pyramid.py
```

然后为它写一个独立 runner：

```text
demo_run_breakout_pyramid.py
```

这样每个策略都有自己的运行入口，互不影响。

## 6. 最小 runner 示例

```python
from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.breakout_pyramid import BreakoutPyramidStrategy

analyzer = run_backtest(
    strategy_class=BreakoutPyramidStrategy,
    symbols_input="rb",
    start_date="2020-01-01 00:00:00",
    end_date="2026-05-20 23:59:59",
    freq="1d",
    data_type="main",
    initial_capital=5000000.0,
    strategy_kwargs={
        "lookback": 20,
        "step_volume": 5,
        "max_volume": 20,
        "allow_short": True,
    },
)

build_html_dashboard(analyzer)
```

## 7. 增仓突破示例说明

示例策略：

```text
strategy/custom/breakout_pyramid.py
```

运行入口：

```text
demo_run_breakout_pyramid.py
```

逻辑：

1. 计算过去 `lookback` 根 K 线的最高价和最低价。
2. 如果当前收盘价突破过去高点，加多 `step_volume` 手。
3. 如果当前收盘价跌破过去低点，加空 `step_volume` 手。
4. 最大仓位不超过 `max_volume`。

注意：突破高低点只用历史 K 线计算，不包含当前 K 线，所以不会把当前最高/最低价拿来和自己比较。

## 8. 写完策略后的检查清单

运行前检查：

- 策略类能正常 import。
- runner 里导入的是正确策略类。
- `symbols_input` 和策略交易品种一致。
- `strategy_kwargs` 包含策略需要的参数。
- 多品种策略要确认传入了正确的品种列表。
- 策略没有读取未来数据。
- 策略运行后能返回 `analyzer`。
- runner 最后调用了 `build_html_dashboard(analyzer)`。

## 9. 最常见错误

### 策略没有交易

常见原因：

- 预热窗口太长，数据长度不够。
- 信号条件太苛刻。
- 资金太小，保证金不够。
- 返回的目标仓位一直等于当前仓位。

### 报告没有网页

检查 runner 里是否写了：

```python
analyzer = run_backtest(...)
build_html_dashboard(analyzer)
```

如果只是调用 `run_backtest(...)`，但没有接住 `analyzer` 并交给网页生成器，就不会生成交互页面。

### 怀疑有未来函数

先问自己：

- 这个数据在下单那个时点是否已经知道？
- 如果信号是收盘后生成，成交是否发生在下一根 K 线？
- 计算突破、均线、因子排名时，是否不小心用了未来行？

如果不确定，优先按更保守的写法：当前 bar 只生成信号，下一根 bar 执行。
