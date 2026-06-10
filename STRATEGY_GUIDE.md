# Backtest-1 项目运行与策略开发指南

这份文档给后续使用者和维护者看。目标是让新人不需要先读完整源码，也能理解：

- 程序从 demo 运行后，按什么顺序经过哪些模块。
- 每根 bar 传递的是什么数据。
- 策略、撮合、账户分别负责什么。
- `GeneralMultiMAStrategy` 这种多品种策略到底怎么产生标准信号。
- 如果要写新策略，应该继承哪个模板、实现哪个函数。
- 如果要记录信号指标，应该放在哪里，为什么不应该改 `backtest_engine.py`。

## 1. 项目一句话说明

`backtest_1` 是一个国内期货回测框架。它把回测拆成几个明确模块：

```text
data_feed      读取和对齐行情
backtest_engine 按时间推进回测
strategy       根据行情表达目标
broker         接收订单并撮合成交
portfolio      记录资金、持仓、保证金、盈亏
analyzer       计算绩效和复盘数据
frontend_index 生成网页报告
config         配置品种、手续费、数据库路由
```

核心思想：

```text
策略不直接改账户。
策略表达目标。
模板把目标和真实仓位做差，生成订单。
broker 把订单变成成交。
account 把成交变成资金和持仓。
engine 只负责按 bar 把这条链路串起来。
```

## 2. 推荐阅读顺序

第一次接触项目，建议按这个顺序看：

1. `run_scripts/run_general_multi_ma.py`

   看一次回测任务怎么配置。

2. `backtest_engine.py`

   看回测主循环怎么推进。

3. `strategy/custom/general_multi_ma.py`

   看一个多品种策略如何生成标准信号。

4. `strategy/general_template.py` 和 `strategy/common/rebalancer.py`

   看标准信号如何变成目标仓位和 `buy / sell / short / cover`。

5. `broker/match_engine.py`

   看订单如何被撮合成交。

6. `portfolio/account.py`

   看成交如何影响资金、持仓、保证金。

7. `analyzer/performance.py` 和 `frontend_index.py`

   看结果如何被统计和生成网页。

## 2.1 运行前的数据库配置

行情数据由 `data_feed/ch_loader.py` 从 ClickHouse 读取。`config.py` 只保留表路由和品种配置，数据库连接信息通过环境变量传入，避免把账号密码提交到仓库。

Windows PowerShell 示例：

```powershell
$env:BACKTEST_CH_HOST="你的ClickHouse地址"
$env:BACKTEST_CH_USER="你的用户名"
$env:BACKTEST_CH_PASS="你的密码"
python run_scripts/run_general_multi_ma.py
```

## 3. 从运行脚本开始的完整运行顺序

以 `run_scripts/run_general_multi_ma.py` 为例，用户运行：

```bash
python run_scripts/run_general_multi_ma.py
```

运行脚本本身不做回测逻辑，它只是配置任务：

```python
TARGET_SYMBOLS = ['rb', 'hc', 'i', 'ta', 'ma', 'p', 'y', 'sr']

analyzer = run_backtest(
    strategy_class=GeneralMultiMAStrategy,
    symbols_input=TARGET_SYMBOLS,
    start_date='2020-01-01 00:00:00',
    end_date='2026-05-20 23:59:59',
    freq='1d',
    data_type='main',
    initial_capital=5000000.0,
    strategy_kwargs={
        'target_symbols': TARGET_SYMBOLS,
        'fast_window': 10,
        'slow_window': 30,
        'sizing': {'mode': 'equity_pct', 'value': 0.03},
        'execution': {'order_type': 'market', 'slippage_ticks': 0.5},
        'exit': {'close_pct': 1.0, 'allow_reverse': True},
    },
)
```

然后进入 `backtest_engine.py` 的 `run_backtest()`。

完整链路如下：

```text
run_scripts/run_general_multi_ma.py
  -> run_backtest()
    -> _resolve_symbols()
    -> DataProvider.get_history()
      -> ClickHouseLoader.get_data()
      -> DataAligner.align_multi_symbol()
    -> Account()
    -> MatchEngine()
    -> GeneralMultiMAStrategy()
    -> for 每一个 current_time:
      -> account.settle_daily()
      -> extract_bar_data()
      -> broker.process_cross_section()
      -> strategy.on_bar()
        -> generate_signals()
        -> SignalRebalancer.rebalance()
      -> rollover_handler.process()
      -> account.get_total_equity()
    -> StrategyAnalyzer.generate_report()
  -> build_html_dashboard(analyzer)
```

## 4. 每根 bar 到底传什么

`backtest_engine.py` 每次循环会调用：

```python
bar_data = extract_bar_data(row, columns_level_1)
strategy.on_bar(current_time, bar_data)
```

`current_time` 是当前时间点。

`bar_data` 只表示当前这个时间点的行情数据，通常长这样：

```python
{
    'rb': {
        'open': 3500,
        'high': 3550,
        'low': 3480,
        'close': 3520,
        'month_change': 0,
    },
    'hc': {
        'open': 3600,
        'high': 3650,
        'low': 3580,
        'close': 3620,
        'month_change': 0,
    },
    'i': {
        'open': 800,
        'high': 820,
        'low': 790,
        'close': 810,
        'month_change': 0,
    },
}
```

重要：`bar_data` 本身不包含下面这些内容：

```text
不包含当前持仓
不包含挂单列表
不包含账户权益
不包含目标仓位
不包含目标挂单价
不包含 ma10、ma30、factor_score 之类指标
不包含策略信号
```

这些信息分别放在不同地方：

```text
行情数据        bar_data
真实持仓        account.positions
挂单列表        broker.pending_orders
成交历史        broker.trade_history
策略指标        strategy 自己维护，例如 self.history
策略信号记录    strategy 自己维护，例如 self.signal_records
绩效结果        analyzer
```

这种拆分是有意设计的。`bar_data` 只负责行情，避免把策略状态、账户状态、订单状态混在一起。

## 5. 逐 bar 主循环的关键顺序

`backtest_engine.py` 的核心循环顺序是：

```text
1. 如果日期变化，执行 account.settle_daily()
2. 从当前 row 提取 bar_data
3. broker.process_cross_section(current_time, bar_data)
4. strategy.on_bar(current_time, bar_data)
5. 如果是主连数据，处理换月
6. 记录当前权益
```

最关键的是第 3 步和第 4 步：

```text
先撮合旧订单，再运行策略。
```

这意味着：

```text
当前 bar 产生的新订单，不会在当前 bar 成交。
它会进入 broker.pending_orders。
下一根 bar 到来时，broker.process_cross_section() 才会撮合。
```

因此，常见的日线写法是：

```text
今天收盘后确认信号
下一根 K 线开盘成交
```

这通常不算未来函数。

## 6. GeneralMultiMA 是并行跑多个合约吗

`GeneralMultiMAStrategy` 是“同一时间点同步推进多个品种”，不是一个品种完整跑完再跑另一个品种。

它不是多线程并行，而是逻辑上的多品种同步：

```text
current_time = 2024-01-02
  -> 看 rb 当前 bar
  -> 看 hc 当前 bar
  -> 看 i 当前 bar
  -> 看 ta 当前 bar
  -> 汇总出一个 signals 字典

current_time = 2024-01-03
  -> 再重复上面的过程
```

代码在 `strategy/custom/general_multi_ma.py`：

```python
for sym in self.symbols:
    if sym not in bar_data or pd.isna(bar_data[sym].get("close")):
        continue

    close_price = bar_data[sym]["close"]
    self.history[sym].append(close_price)
    ...
```

`self.history` 是每个品种一条历史 close 序列：

```python
{
    'rb': [3500, 3520, 3510, ...],
    'hc': [3600, 3620, 3610, ...],
    'i':  [800, 810, 805, ...],
}
```

所以 `ma10 / ma30` 不是 engine 传进来的，是策略用自己的 `self.history[sym]` 算出来的。

## 7. GeneralMultiMA 发出的到底是什么

`GeneralMultiMAStrategy` 继承的是 `GeneralSignalStrategy`。

它实现的函数是：

```python
def generate_signals(self, bar_data: dict) -> dict:
    ...
```

返回值不是“买入多少手”，而是标准 `signal intent`：

```python
{
    "rb": {
        "signal": 1,
        "position_mode": "target",
        "reason": "golden_cross",
        "metrics": {"close": 3520, "fast_ma": 3510, "slow_ma": 3500},
    },
    "hc": None,
    "i": {
        "signal": -1,
        "position_mode": "target",
        "reason": "death_cross",
        "metrics": {"close": 805, "fast_ma": 800, "slow_ma": 810},
    },
}
```

含义：

```text
rb 发出做多目标信号
hc 保持不动
i 发出做空目标信号
```

`GeneralMultiMAStrategy` 内部逻辑：

```python
golden_cross = (fast_ma_prev <= slow_ma_prev) and (fast_ma_curr > slow_ma_curr)
death_cross = (fast_ma_prev >= slow_ma_prev) and (fast_ma_curr < slow_ma_curr)

if golden_cross:
    signals[sym] = {"signal": 1, "position_mode": "target"}
elif death_cross:
    signals[sym] = {"signal": -1, "position_mode": "target"}
else:
    signals[sym] = None
```

也就是说，策略自己完成：

```text
读取 close
维护历史价格
计算 ma10 和 ma30
判断金叉/死叉
返回标准信号和指标记录
```

它不计算开多少手，也不决定市价单还是限价单。手数和下单方式由通用配置统一处理。

## 8. 信号是谁接收的

`generate_signals()` 返回以后，会被 `GeneralSignalStrategy.on_bar()` 接收。

```python
signals = self.generate_signals(bar_data)
records = self.rebalancer.rebalance(signals, bar_data)
```

真正把信号转换成订单的是 `strategy/common/rebalancer.py` 里的 `SignalRebalancer`。

它会读取账户真实仓位：

```python
long_vol = self.get_position_volume(sym, Direction.LONG)
short_vol = self.get_position_volume(sym, Direction.SHORT)
current_net = long_vol - short_vol
```

再根据 `sizing` 算出基础手数。例如：

```python
sizing = {"mode": "equity_pct", "value": 0.03}
```

表示按当前总权益的 3% 保证金预算计算基础手数。随后根据信号计算目标净仓位：

```python
target_net = direction * base_volume
diff = target_net - working_net
```

例子 1：

```text
当前 rb 净仓位 = 0
基础手数 = 10
signal = 1
目标 rb 净仓位 = 10
diff = 10 - 0 = 10
```

通用模板会调用：

```python
self.buy("rb", volume=10, price=0.0, reference_price=current_close)
```

例子 2：

```text
当前 i 净仓位 = 多 3 手
目标 i 净仓位 = 空 5 手
diff = -5 - 3 = -8
```

模板会：

```text
先 sell 平多 3 手
再 short 开空 5 手
```

所以策略不需要自己处理复杂的反手逻辑。它只要表达信号意图，是否允许反手由 `exit.allow_reverse` 控制。

## 9. 订单是怎么产生并成交的

`SignalRebalancer._submit_diff()` 最终会调用这些方法：

```python
buy()
sell()
short()
cover()
```

这些方法定义在 `strategy/base.py`。

例如：

```python
self.buy(sym, volume=diff, price=0.0, reference_price=current_close)
```

这里有两个价格概念：

```text
price=0.0
    表示市价单。

reference_price=current_close
    用来估算保证金和风控，不代表真实成交价。
```

真正的成交发生在下一根 bar 的 `broker.process_cross_section()`。

市价单成交价：

```text
做多开仓或平空：下一根 bar open + 滑点
做空开仓或平多：下一根 bar open - 滑点
```

限价单成交逻辑：

```text
买入限价单：如果当前 bar low <= limit_price，则认为能成交
卖出限价单：如果当前 bar high >= limit_price，则认为能成交
```

撮合成功后，broker 会生成 `Trade`，再调用：

```python
account.process_trade(trade)
```

## 10. Account 是真实状态来源

账户在 `portfolio/account.py`。

它维护：

```python
self.available       # 可用资金
self.frozen_margin  # 总冻结保证金
self.positions      # 持仓字典
self.total_pnl      # 累计平仓盈亏
self.pending_margin # 挂单预占保证金
```

持仓结构类似：

```python
{
    'rb_LONG': {
        'yd_volume': 10,
        'td_volume': 0,
        'avg_price': 3500.0,
        'frozen_margin': 42000.0,
    }
}
```

注意：当前代码已经改成每个持仓单独记录 `frozen_margin`。

开仓时：

```text
扣手续费
扣保证金
增加持仓
更新均价
增加该持仓自己的 frozen_margin
```

平仓时：

```text
扣手续费
计算平仓盈亏
按比例释放该持仓的 frozen_margin
减少持仓
如果持仓归零则删除该持仓
```

所以如果策略想知道当前真实仓位，应通过基类方法：

```python
self.get_net_position(sym)
self.get_position_volume(sym, Direction.LONG)
self.get_position_volume(sym, Direction.SHORT)
```

不要直接假设“我发了单，就一定成交了”。

## 11. BacktestEngine 是否应该监控 MA 和信号

不应该。

`backtest_engine.py` 不应该知道：

```text
ma10
ma30
golden_cross
death_cross
breakout_high
factor_score
```

原因很简单：这些是策略逻辑。如果把这些判断写进 engine，每新增一个策略都要改 engine，框架就不通用了。

正确分工：

```text
backtest_engine
    只负责时间推进、喂行情、撮合、记权益。

strategy
    负责计算指标、判断信号、表达目标。

broker
    负责接订单和撮合。

account
    负责真实资金和持仓。
```

如果你想在报告里监控信号和指标，也不要让 engine 参与下单决策。更好的做法是在策略里记录：

```python
self.signal_records.append({
    'datetime': current_time,
    'symbol': sym,
    'close': close_price,
    'fast_ma': fast_ma_curr,
    'slow_ma': slow_ma_curr,
    'signal': 'golden_cross',
    'target_volume': vol,
    'current_position': self.get_net_position(sym),
})
```

后续可以让 `analyzer` 或 `frontend_index` 读取这些记录并展示。

一句话：

```text
engine 可以收集策略记录的信号，但不应该替策略判断信号。
```

## 12. 通用策略模板和标准信号协议

当前正式推荐新用户统一使用 `GeneralSignalStrategy`。策略只输出标准 `signal intent`，不直接下单、不直接计算手续费、不直接决定市价/限价。

项目现在只保留一套策略协议：`GeneralSignalStrategy`。现在的 `FactorTemplate` 也继承 `GeneralSignalStrategy`，因子只输出方向和相对强度，实际手数仍由配置中心控制。

| 模板 | 用户实现什么 | 返回什么 | 手数谁算 | 适合场景 |
| --- | --- | --- | --- | --- |
| `GeneralSignalStrategy` | `generate_signals()` | 标准 `signal intent` | 配置中心的 `sizing` 统一计算 | 所有新策略 |
| `FactorTemplate` | `calculate_weights()` | 截面方向和相对强度 | 配置中心的 `sizing` 统一计算 | 多品种截面因子 |

### 12.1 GeneralSignalStrategy 推荐新写法

新策略优先继承：

```python
from strategy.general_template import GeneralSignalStrategy
```

用户只实现：

```python
def generate_signals(self, bar_data: dict) -> dict:
    return {
        "rb": 1,     # 做多或目标多头
        "i": -1,     # 做空或目标空头
        "ta": 0,     # 退出/按 close_pct 减仓
        "ma": None,  # 保持当前状态，不下新单
    }
```

基础信号含义：

```text
1     目标偏多
-1    目标偏空
0     退出或减仓；具体平多少由 close_pct / close_volume / 配置中心决定
None  不动，不产生新订单
```

推荐返回更详细的信号字典，方便记录原因、指标和高级仓位意图：

```python
signals["rb"] = {
    "signal": 1,
    "position_mode": "target",   # target / delta / flat
    "size_scale": 1.0,           # 基础仓位强度，默认 1
    "reason": "golden_cross",
    "metrics": {
        "fast_ma": fast_ma_curr,
        "slow_ma": slow_ma_curr,
    },
    "limit_price": 3500.0,       # 可选。只有限价单时使用
}
```

常用仓位意图：

```python
# 按配置中心基础仓位做多
{"signal": 1, "position_mode": "target"}

# 当前有 2 手多头，只平 1 手
{"signal": 0, "close_volume": 1}

# 当前仓位减半
{"signal": 0, "close_pct": 0.5}

# 突破后按基础仓位增仓一档
{"signal": 1, "position_mode": "delta", "size_scale": 1.0}

# 全部平仓
{"signal": 0, "position_mode": "flat"}
```

通用模板会自动处理：

```text
1. 根据 sizing 配置计算目标手数
2. 根据 execution 配置决定市价/限价
3. 根据 exit 配置决定平仓比例和是否允许反手
4. 读取 account 真实仓位
5. 把目标和真实仓位做差
6. 生成 buy / sell / short / cover
7. 考虑 broker.pending_orders，避免限价单未成交时重复挂同一批单
```

参考文件：

```text
strategy/general_template.py
strategy/common/sizing.py
strategy/common/execution.py
strategy/common/rebalancer.py
strategy/custom/general_multi_ma.py
run_scripts/run_general_multi_ma.py
```

#### 通用配置示例

`run_scripts/run_general_multi_ma.py` 里的核心配置：

```python
STRATEGY_KWARGS = {
    "target_symbols": ["rb", "hc", "i"],
    "fast_window": 10,
    "slow_window": 30,

    "sizing": {
        "mode": "capital_pct",
        "value": 0.03,
        "min_volume": 1,
        "max_volume": None,
    },

    "execution": {
        "order_type": "market",
    },

    "exit": {
        "close_pct": 1.0,
        "allow_reverse": True,
        "respect_pending_orders": True,
    },
}
```

#### sizing 支持的开仓方式

```text
fixed_volume
    固定手数。value=10 表示每个信号目标 10 手。

fixed_margin
    固定保证金金额。value=100000 表示按 10 万保证金计算手数。

fixed_notional
    固定名义本金。value=1000000 表示按 100 万名义本金计算手数。

capital_pct
    初始资金百分比。value=0.03 表示每个品种使用初始资金 3% 的保证金。

equity_pct
    当前权益百分比。value=0.03 表示每个品种使用当前动态权益 3% 的保证金。

available_pct
    当前可用资金百分比。value=0.03 表示每个品种使用当前可用资金 3% 的保证金。
```

例子：

```python
"sizing": {"mode": "fixed_volume", "value": 10}
"sizing": {"mode": "fixed_margin", "value": 100000}
"sizing": {"mode": "capital_pct", "value": 0.03}
"sizing": {"mode": "equity_pct", "value": 0.03}
```

#### execution 支持的下单方式

市价单：

```python
"execution": {
    "order_type": "market",
    "slippage_ticks": 1,
}
```

`slippage_ticks` 只作用于市价单，表示成交价相对下一根 bar 的 open 劣化多少跳：

```text
买入或平空: open + slippage_ticks * tick_size
卖出或平多: open - slippage_ticks * tick_size
```

如果希望没有市价滑点，可以设为：

```python
"execution": {
    "order_type": "market",
    "slippage_ticks": 0,
}
```

限价单，挂当前 close：

```python
"execution": {
    "order_type": "limit",
    "limit_mode": "at_close",
}
```

限价单，挂更优价格，不急着成交：

```python
"execution": {
    "order_type": "limit",
    "limit_mode": "better_ticks",
    "ticks": 1,
}
```

限价单，挂更容易成交的价格：

```python
"execution": {
    "order_type": "limit",
    "limit_mode": "worse_ticks",
    "ticks": 1,
}
```

买入时：

```text
better_ticks = close - ticks * tick_size
worse_ticks  = close + ticks * tick_size
```

卖出时相反。

限价单本身不额外加市价滑点。这里的 `ticks` 是限价挂单价相对参考价移动多少跳，不是成交滑点。比如 `worse_ticks=1` 是主动把限价挂得更容易成交，并不等于撮合后再扣 1 跳滑点。

#### exit 支持的平仓控制

```python
"exit": {
    "close_pct": 1.0,
    "allow_reverse": True,
    "respect_pending_orders": True,
}
```

含义：

```text
close_pct=1.0
    signal=0 时全部平仓。

close_pct=0.5
    signal=0 时只平掉当前仓位 50%。

allow_reverse=True
    多头遇到做空信号时，允许先平多再开空。

allow_reverse=False
    多头遇到做空信号时，只先平到 0，不立刻反手。

respect_pending_orders=True
    调仓时把未成交挂单也算入工作仓位，避免限价单未成交时重复挂单。
```

### 12.2 FactorTemplate

适合多品种截面排序。它现在也继承 `GeneralSignalStrategy`，所以因子只输出方向和相对强度，具体手数由 `sizing` 统一计算。

用户实现：

```python
def calculate_weights(self, cross_section: dict) -> dict:
    return {
        'rb': 0.10,
        'i': -0.10,
    }
```

含义：

```text
rb 相对偏多
i 相对偏空
```

参考文件：

```text
strategy/factor_template/composite_factor.py
strategy/factor_template/cross_momentum.py
```

## 13. 新手应该怎么写一个新策略

建议按这个流程：

1. 判断策略类型。

```text
大多数普通策略：
    优先继承 GeneralSignalStrategy
    只写 generate_signals()
    市价/限价、手数、资金比例、部分平仓都放在 demo 配置里

多品种截面因子：
    继承 FactorTemplate
    只写 calculate_weights()
    仍然复用 sizing / execution / exit

```

2. 在 `strategy/custom/` 新建策略文件。

例如：

```text
strategy/custom/my_strategy.py
```

3. 写一个独立运行脚本。

例如：

```text
run_scripts/run_my_strategy.py
```

4. 运行脚本里调用：

```python
from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.my_strategy import MyStrategy

analyzer = run_backtest(
    strategy_class=MyStrategy,
    symbols_input=['rb', 'i'],
    start_date='2020-01-01 00:00:00',
    end_date='2026-05-20 23:59:59',
    freq='1d',
    data_type='main',
    initial_capital=5000000.0,
    strategy_kwargs={
        "target_symbols": ["rb", "i"],
        "sizing": {"mode": "equity_pct", "value": 0.03},
        "execution": {"order_type": "market", "slippage_ticks": 1},
        "exit": {"close_pct": 1.0, "allow_reverse": True},
    },
)

build_html_dashboard(analyzer)
```

如果使用 `GeneralSignalStrategy`，demo 里至少要配置：

```python
strategy_kwargs={
    "target_symbols": ["rb", "i"],
    "sizing": {"mode": "equity_pct", "value": 0.03},
    "execution": {"order_type": "market", "slippage_ticks": 1},
    "exit": {"close_pct": 1.0, "allow_reverse": True},
}
```

限价单版本：

```python
strategy_kwargs={
    "target_symbols": ["rb", "i"],
    "sizing": {"mode": "fixed_volume", "value": 10},
    "execution": {
        "order_type": "limit",
        "limit_mode": "better_ticks",
        "ticks": 1,
    },
    "exit": {"close_pct": 0.5, "allow_reverse": False},
}
```

## 14. 什么时候需要改 backtest_engine.py

一般不需要。

写策略时，正常只改：

```text
strategy/custom/xxx.py
run_scripts/run_xxx.py
```

只有下面这些情况才考虑改 engine：

```text
要改变全局事件顺序
要支持多周期同时推进
要支持策略间组合
要改变成交前后账户更新顺序
要改变主连换月处理顺序
```

如果只是：

```text
新增指标
新增信号
新增调仓规则
新增止盈止损
新增目标仓位算法
```

都应该写在 strategy 里，不应该改 engine。

## 15. 未来函数边界

当前主流程是：

```text
当前 bar 产生信号
下一根 bar 撮合成交
```

所以：

```text
用当前 bar 的 close 算信号，下一根 bar 成交：
    通常可以接受。

用当前 bar 的 high/low/close 算信号，并假设当前 bar 内已经按更好价格成交：
    有未来函数风险。

用后面行的数据算当前信号：
    明确是未来函数。
```

如果策略设定是“收盘后交易”，可以用当前 close。

如果策略设定是“开盘前交易”，不能用当天 high、low、close。

## 16. 主连换月怎么理解

当 `data_type='main'` 时，会启用 `broker/rollover.py`。

每根 bar 后，engine 会检查 `month_change`。

如果发生主力换月：

```text
先平旧主力持仓
再开新主力持仓
```

换月产生的成交会标记：

```python
is_rollover=True
```

`analyzer` 做 FIFO 开平仓配对时，会跳过这些换月流水，避免把换月当成策略主动交易。

## 17. Analyzer 做了什么

`analyzer/performance.py` 接收：

```python
trades=broker.trade_history
price_df=df
initial_capital=initial_capital
account_summary=account_summary
equity_df=equity_df
```

核心工作：

```text
FIFO 配对开平仓
计算逐笔盈亏
计算收益、回撤、胜率、盈亏比
生成权益曲线
生成多品种 PnL 拆分
生成复盘图表数据
```

当前代码已经修过一个关键点：

```text
FIFO 配对不再修改原始 Trade 对象。
```

也就是说，生成报告不会污染 `broker.trade_history`。

## 18. 常见误解

### 误解 1：strategy 文件只是发信号

当前标准下基本准确，但要说完整：策略发的是标准 `signal intent`，里面可以包含方向、目标/增减仓模式、平仓数量、限价价格和指标记录。

普通策略直接发标准 `signal intent`。因子策略虽然实现的是 `calculate_weights()`，但 `FactorTemplate` 会把权重转换成同一套 `signal intent`，再交给通用调仓器处理。

### 误解 2：signal=1 就是买 1 手

不准确。

`signal=1` 只表示目标方向偏多。具体买多少手由 `sizing` 决定：

```text
fixed_volume      固定手数
fixed_margin      固定保证金
fixed_notional    固定名义金额
capital_pct       初始资金比例
equity_pct        当前权益比例
available_pct     可用资金比例
```

模板会根据当前真实仓位、未成交挂单、目标仓位和 `exit.allow_reverse` 决定是否需要买、卖、平仓、反手。

### 误解 3：bar_data 里有 MA 和持仓

不准确。

`bar_data` 只有行情。MA 是策略自己算的，持仓在 account 里。

### 误解 4：发单后策略可以认为已经成交

不准确。

当前设计是下一根 bar 才撮合。订单也可能因为资金、持仓不足被拒。

策略应该以 account 里的真实持仓为准。

## 19. 写完策略后的检查清单

运行前检查：

- 策略类能正常 import。
- demo 里导入的是正确策略类。
- `symbols_input` 和策略交易品种一致。
- `strategy_kwargs` 包含策略需要的参数。
- 多品种策略确认传入了正确的品种列表。
- 策略没有读取未来数据。
- 如果策略需要历史窗口，确认数据长度足够。
- 如果策略输出目标仓位，确认目标仓位是整数手数。
- runner 最后接住 `analyzer`。
- runner 调用了 `build_html_dashboard(analyzer)`。

## 20. 最小心智模型

如果只记一张图，记这张：

```text
bar_data
  -> strategy 计算指标
  -> generate_signals() 返回 signal intent
  -> SignalRebalancer 读取 account 真实仓位和 pending_orders
  -> sizing / execution / exit 统一决定目标仓位和订单类型
  -> diff = target - current
  -> buy / sell / short / cover
  -> broker.pending_orders
  -> 下一根 bar broker 撮合
  -> Trade
  -> account.process_trade()
  -> analyzer 统计结果
  -> frontend_index 生成网页
```

这就是项目最核心的运行方式。

## 21. 配置页面怎么接入策略

项目现在有一个 Streamlit 配置页，入口在：

```text
ui/app.py
```

本地启动方式：

```bash
python -m streamlit run ui/app.py --server.port 8501
```

也可以使用包装脚本：

```bash
python ui/start_ui.py
```

安装依赖：

```bash
pip install -r requirements.txt
```

配置页不会直接写回测逻辑。它只负责收集参数、生成 JSON，然后调用：

```text
ui/run_from_config.py
```

这个文件是“网页参数 -> run_backtest 参数”的唯一适配层。后续新增需要配置页运行的策略时，优先改这里。独立 demo 则放在 `run_scripts/`。

推荐新策略接入流程：

```text
1. 在 strategy/custom/ 下新增策略文件
2. 优先继承 GeneralSignalStrategy
3. 策略只实现 generate_signals(bar_data)
4. 在 ui/run_from_config.py 的 STRATEGY_SPECS 增加一条策略注册
5. 如果参数结构不同，增加一个 _build_xxx(config, spec) builder
6. 在配置页运行，确认能生成报告
```

`GeneralSignalStrategy` 策略可以直接复用这些通用配置：

```python
strategy_kwargs = {
    "target_symbols": ["rb", "hc"],
    "sizing": {
        "mode": "equity_pct",
        "value": 0.03,
    },
    "execution": {
        "order_type": "market",
        "slippage_ticks": 0.5,
    },
    "exit": {
        "close_pct": 1.0,
        "allow_reverse": True,
    },
}
```

目前配置页默认支持：

```text
general_multi_ma      通用多品种均线
breakout_pyramid      增仓突破
dual_ma               双均线
composite_factor      复合因子
cross_momentum        截面动量反转
```

配置中心只注册 `GeneralSignalStrategy` 或继承它的 `FactorTemplate` 策略。新增策略必须接入这套协议后再加入 `STRATEGY_SPECS`。
