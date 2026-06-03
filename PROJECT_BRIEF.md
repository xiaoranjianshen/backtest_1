# 回测平台架构说明 (Backtest-1 Project Brief)

> 目标：让另一个 AI 能完全理解本项目的结构、数据流、关键接口，在不熟悉源码的情况下也能正确扩展。

---

## 一、项目定位

这是一套**期货量化回测平台**，当前为单品种/单策略，未来扩展为**多品种 × 多周期 × 全网格参数寻优**的机构级回测系统。

- 语言：Python 3.11
- 图表：Plotly (交互式)
- 数据源：内部历史数据库（MySQL），通过 `ch_loader` 从 ClickHouse 拉取
- 目录入口：`backtest_1/demo_run_DualMA.py`

---

## 二、运行方式（最小可运行示例）

```python
from backtest_engine import run_backtest
from strategy.rule_template.dual_ma import DualMAStrategy

run_backtest(
    strategy_class=DualMAStrategy,
    symbols_input='rb',        # 极简品种代码
    start_date='2020-05-20',
    end_date='2026-05-20',
    freq='1d',                # 支持: 1m, 5m, 1d, tick
    data_type='main',         # main(主连)/main_adj(复权)/all(全合约)/index(指数)
    initial_capital=1_000_000.0,
    strategy_kwargs={'fast_window': 10, 'slow_window': 30, 'capital_pct': 0.10}
)
```

`run_backtest()` 是唯一对外暴露的调度入口，内部自动处理数据拉取→策略初始化→事件循环→绩效分析→报告生成的全链路。

---

## 三、核心数据流

```
[MySQL/ClickHouse]
    ↓ ch_loader
[DataProvider] → 多品种宽表 (MultiIndex: datetime × fields)
    ↓ aligner
[Aligner] → 清洗后矩阵 (1454 rows × 9 cols)，datetime 为 index
    ↓
[回测引擎 loop] for (current_time, row) in df.iterrows():
    ├── bar_data = extract_bar_data(row, columns)   # dict: {symbol: {open/high/low/close/...}}
    ├── broker.process_cross_section(time, bar_data)  # 撮合引擎
    ├── strategy.on_bar(time, bar_data)              # 策略信号
    ├── rollover_handler.process(...)                # 换月处理（可选）
    └── equity_records.append({'datetime': time, 'equity': ...})
    ↓
[Account] → 账户持仓/资金
    ↓
[MatchEngine] → trade_history (list of Trade objects)
    ↓
[StrategyAnalyzer] → 绩效报告 + PNG 图表
```

---

## 四、关键类/模块详解

### 4.1 `backtest_engine.py`（核心调度器）

**函数：`run_backtest(...)`** — 唯一入口

关键步骤：
1. `_resolve_symbols()`：解析品种，`symbols_input='rb'` → `query_sym='KQ.m@SHFE.rb'`
2. `DataProvider.get_history(...)` → 返回 MultiIndex DataFrame
3. `Account(initial_capital)` → 账户
4. `MatchEngine(account)` → 撮合引擎
5. `MainContractRollover()` → 换月处理器（可选）
6. `strategy_class(broker, account, symbol, **kwargs)` → 策略实例
7. 主循环：`for current_time, row in df.iterrows()`，逐 K 线事件驱动
8. 循环结束后调用 `StrategyAnalyzer(trades=..., price_df=..., ...)`

**`extract_bar_data(row, columns)`** 返回结构：
```python
{
    'rb': {'close': 3711.0, 'open': 3705.0, 'high': 3720.0, 'low': 3700.0, 'month_change': 0},
    'hc': {...},
    ...
}
```

### 4.2 `broker/order.py`（交易订单）

```python
class Direction: LONG = 1, SHORT = -1
class Offset: OPEN = 0, CLOSE = 1, CLOSE_TODAY = 2

class Trade:
    symbol: str       # 'rb'
    direction: Direction  # LONG / SHORT
    offset: Offset       # OPEN / CLOSE / CLOSE_TODAY
    price: float
    volume: float
    commission: float    # 已扣手续费
    trade_time: datetime
    is_rollover: bool    # 是否为换月流水（不参与策略配对）
```

### 4.3 `broker/match_engine.py`（撮合引擎）

```python
class MatchEngine:
    account: Account
    pending_orders: list[Order]
    trade_history: list[Trade]      # ← 核心输出！

    def submit_order(symbol, direction, offset, volume, price, broker): ...
    def process_cross_section(current_time, bar_data): ...  # 逐K线推进
```

### 4.4 `broker/rollover.py`（换月处理）

在 `data_type='main'` 时启用，主力合约换月时：
1. 用**昨收价**平旧仓（结算真实盈亏）
2. 用**今开盘价**开新仓
3. 换月流水标记 `is_rollover=True`，**不参与策略开平配对**

### 4.5 `portfolio/account.py`（账户）

```python
class Account:
    initial_capital: float
    available: float        # 可用资金
    frozen_margin: float    # 冻结保证金
    total_pnl: float        # 累计平仓盈亏

    def get_position(symbol) -> dict:    # {'direction': LONG, 'volume': 29, 'avg_price': 3711.0, ...}
    def get_total_equity(close_prices) -> float:  # 可用 + 浮动盈亏
    def settle_daily(): ...              # 日结
```

### 4.6 `strategy/base.py`（策略基类）

```python
class Strategy:
    def __init__(self, broker, account, symbol, **kwargs): ...
    def on_init(self): ...      # 预热/初始化
    def on_bar(self, current_time, bar_data): ...
```

### 4.7 `strategy/rule_template/dual_ma.py`（双均线示例）

```python
class DualMAStrategy(BaseStrategy):
    def on_bar(self, current_time, bar_data):
        # bar_data['rb']['close'] 获取最新收盘价
        # self.broker.submit_order(...) 发单
        # self.account.get_position(symbol) 查持仓
```

### 4.8 `data_feed/data_provider.py`

```python
class DataProvider:
    def get_history(symbols: list, start_date, end_date, freq, data_type) -> pd.DataFrame:
        # 返回 MultiIndex DataFrame，columns = (field, symbol)
        # 如：columns = [('close','KQ.m@SHFE.rb'), ('open','KQ.m@SHFE.rb'), ...]
        # 数据会缓存到 cache_data/*.parquet
```

---

## 五、`StrategyAnalyzer`（绩效分析器）

**文件：** `analyzer/performance.py`

**构造参数：**
```python
StrategyAnalyzer(
    trades=list[Trade],           # MatchEngine.trade_history
    price_df=pd.DataFrame,        # 含 datetime 列的 OHLC 价格矩阵
    initial_capital=float,
    symbol=str,                   # 'rb'
    freq=str,                    # '1d'
    strategy_name=str,           # 'DualMAStrategy'
    account_summary=dict,         # {'total_pnl', 'rollover_count', 'rollover_commission', ...}
    equity_df=pd.DataFrame,      # {'datetime': [...], 'equity': [...]}
    describe_params=dict,        # 回测参数描述表
)
```

**`_match_trades_fifo()`** — FIFO 开平仓配对：
- 返回 `self.match_df`（配对后 DataFrame，columns 如下）
- **不参与配对**的平仓量记录在 `self.unmatched_close_volume`

**`match_df` 列结构：**
```python
open_time, close_time,          # datetime，开仓/平仓时间
direction,                      # LONG / SHORT
volume,                         # 手数
open_price, close_price,        # 开仓/平仓价
gross_pnl,                      # 毛利（未扣费）
commission,                     # 手续费
net_pnl,                        # 净盈亏 = gross_pnl - commission
hold_time_hours,               # 持仓时长（小时）
multiplier,                     # 合约乘数
```

**`_calculate_metrics()`** — 计算指标，存到 `self.metrics` dict：
```python
{
    '合约': 'RB',
    '参数': 'DualMAStrategy',
    '初始资金': '¥1,000,000',
    '总收益': '-20.02%',
    '累计手续费': '¥11,127',
    '累计净值': '¥-200,217',
    '年化收益': '-3.65%',
    '单笔利润跳数': '-17800',
    '最大开仓市值': '¥227,344',
    '单日最大回撤': '¥-467,452',
    '最大回撤率': '-52.11%',
    '年化Sharpe': '-0.03',
    '卡玛比': '-0.07',
    '逐笔胜率': '37.25%',
    '逐笔盈亏比': '1.45',
    '逐日胜率': '37.25%',
    '逐日盈亏比': '1.45',
    '交易次数': 51,
    '交易日数': 51,
    '日均成交额': '¥46,670',
    '主力换月次数': 17,
    '换月手续费': '¥3,681',
    ...
}
```

**`_get_equity_series()`** — 返回 `(equity_x: list, equity_y: list)`
- 优先用 `self.equity_df`（引擎盯市权益）
- 兜底用配对累计盈亏 + 初始资金重建

**`_calculate_benchmark()`** — 返回 `pd.Series`（买入持有净值曲线）：
- 单品种：用对应品种的 `close` 价格累乘
- MULTI：用所有品种等权平均

**`generate_report()`** — 生成报告入口：
1. 调用 `_calculate_metrics()` + `_match_trades_fifo()`
2. 调用 `_plot_equity_series()` → `1_equity_curve.png`（四行图）
3. 调用 `_plot_trade_dna()` → `2_trade_dna.png`（四宫格）
4. 调用 `_plot_equity_and_markers()` → 根据品种/类型分发

**`_plot_equity_series()`** 当前输出结构（4行 subplot）：
```
Row 1: 回测参数表 (7列横向)
Row 2: 绩效统计表 (22列横向)
Row 3: 净值曲线 + 等权基准 (蓝色实线+填充 / 灰色虚线)
Row 4: 累计盈亏(绿) + 累计手续费(红)
```

---

## 六、`config.py` 关键配置

```python
FEE_DICT = {
    'rb': {
        'multiplier': 10,         # 合约乘数（螺纹钢 10吨/手）
        'tick_size': 1.0,        # 最小变动价位
        'margin_rate': 0.12,     # 保证金率 12%
        'fee_type': 'ratio',     # 'ratio' | 'fixed'
        # ratio 模式下（万分率）：
        'fee_open': 0.0001,
        'fee_close_history': 0.0001,
        'fee_close_today': 0.0003,
        # fixed 模式下（元/手）：
        # 'fee_open': 5.0,
        # 'fee_close_history': 5.0,
        # 'fee_close_today': 5.0,
    },
    'hc': {...}, 'i': {...}, ...
}

def build_query_symbol(symbol, data_type) -> str:
    # 'rb' + 'main' → 'KQ.m@SHFE.rb'

def pure_product_code(symbol) -> str:
    # 'rb' ← 'KQ.m@SHFE.rb'
```

---

## 七、关键数据对比（理解账户 vs 配对）

```
账户累计平仓盈亏 (Account.total_pnl)
    = 配对毛利 (match_df.gross_pnl.sum())
    - 配对手续费 (match_df.commission.sum())
    - 换月手续费 (account_summary['rollover_commission'])

最终动态权益 = 初始资金 + Account.total_pnl
```

---

## 八、当前文件清单

```
backtest_1/
├── demo_run_DualMA.py        ← 入口配置文件（改这里来运行不同回测）
├── demo_run_factor.py        ← 因子策略示例
├── backtest_engine.py        ← 核心调度引擎
├── config.py                 ← 品种元数据/手续费配置
├── initial_project.py        ← 项目初始化脚本
├── export_data.py            ← 数据导出
├── tick_backtest_demo.py     ← Tick级回测示例
├── tick_synthesizer.py      ← Tick数据合成
├── visualize_demo.py         ← 可视化演示
│
├── broker/
│   ├── match_engine.py       ← 撮合引擎
│   ├── tick_match_engine.py  ← Tick撮合
│   ├── order.py              ← Trade/Direction/Offset 定义
│   ├── fee_model.py          ← 手续费计算
│   └── rollover.py            ← 换月处理
│
├── portfolio/
│   └── account.py            ← 账户（持仓/资金/保证金）
│
├── strategy/
│   ├── base.py               ← 策略基类
│   ├── rule_template/
│   │   ├── rule.py          ← 规则模板基类
│   │   └── dual_ma.py       ← 双均线策略实现
│   └── factor_template/
│       ├── factor.py         ← 因子基类
│       ├── composite_factor.py ← 复合因子
│       └── cross_momentum.py  ← 截面动量因子
│
├── data_feed/
│   ├── data_provider.py      ← 数据拉取入口
│   ├── ch_loader.py          ← ClickHouse 加载器
│   └── aligner.py            ← 多品种宽表对齐
│
├── analyzer/
│   ├── performance.py        ← 绩效分析器（当前重点文件）
│   └── RB_1d_DualMAStrategy_Backtest/  ← 输出目录
│       ├── 1_equity_curve.png
│       ├── 2_trade_dna.png
│       └── 0_performance_report.txt
│
└── cache_data/               ← parquet 缓存目录
```

---

## 九、HTML 报告生成器设计提示

`StrategyAnalyzer` 输出的所有数据都已就绪，未来 HTML 报告生成器需要：

1. **从 `StrategyAnalyzer` 实例**读取数据（不要重复计算）
2. 每个图表用 `fig.to_html(full_html=False, include_plotlyjs=False)` 嵌入
3. 布局交给外层 HTML/CSS 控制（Tailwind grid）

关键接入点：
```python
analyzer = StrategyAnalyzer(trades=..., price_df=..., ...)
analyzer.generate_report()  # ← 内部已经算好所有指标

# 报告生成后，直接从实例拿数据：
analyzer.metrics          # dict，所有绩效指标
analyzer.match_df        # DataFrame，配对流水
analyzer.equity_df       # DataFrame，权益曲线时序
analyzer.describe_params  # dict，回测参数描述
```

---

## 十、未来扩展方向

1. **多品种 × 多周期**：改 `run_backtest` 支持品种列表 + 周期列表
2. **全网格参数寻优**：在外层遍历 `fast_window × slow_window` 参数组合
3. **因子策略**：`demo_run_factor.py` 已有雏形，参考 `CompositeFactorStrategy`
4. **Tick级回测**：`tick_backtest_demo.py` 已有框架，撮合用 `tick_match_engine.py`
5. **HTML终端**：基于 `StrategyAnalyzer` 输出的所有数据，用 Tailwind + Plotly 构建 5 Tab 交互看板
