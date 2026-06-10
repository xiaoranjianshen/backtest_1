# backtest_1 期货回测项目

这是一个面向国内期货的事件驱动回测框架，支持主连、复权、指数、明细合约、日线、分钟线和 tick 级数据。项目核心目标是把数据、策略、撮合、账户、分析报告拆开，让用户只需要写策略逻辑，就能完成回测和网页报告输出。

如果你是第一次看这个项目，先读：

```text
STRATEGY_GUIDE.md
```

这份文档详细解释了：

- 程序从 demo 运行后的完整顺序。
- 每根 bar 传给策略的是什么。
- `GeneralMultiMAStrategy` 多品种策略是怎么同步推进的。
- 策略信号、目标仓位、订单、成交、账户之间怎么流转。
- 新策略应该继承哪个模板、实现哪个函数。

## 快速运行

常用运行入口都放在 `run_scripts/`。这些脚本运行完成后会直接打开 HTML 报告：

```bash
python run_scripts/run_general_multi_ma.py
python run_scripts/run_dual_ma.py
python run_scripts/run_factor.py
python run_scripts/run_breakout_pyramid.py
```

运行入口里会调用：

```python
build_html_dashboard(analyzer)
```

就会生成交互式 HTML 回测报告。报告右上角有 `下载PDF报告` 按钮，会把产品业绩、交易分析和交易复盘导出为 PDF。

## 核心模块

```text
backtest_engine.py          回测主调度器，负责逐 bar 推进
config.py                   数据库、品种、手续费、保证金配置

data_feed/
  ch_loader.py              从 ClickHouse 读取原始数据
  aligner.py                多品种时间轴对齐
  data_provider.py          数据统一入口

strategy/
  base.py                   策略基类和订单 API
  general_template.py       推荐新用户继承的通用信号模板
  common/                   仓位计算、执行方式、通用调仓模块
  custom/general_multi_ma.py 通用多品种均线示例
  custom/dual_ma.py         通用双均线示例
  custom/breakout_pyramid.py 通用增仓突破示例
  factor_template/factor.py 通用因子策略模板
  custom/                   推荐放自定义策略

broker/
  order.py                  Order、Trade、Direction、Offset 定义
  match_engine.py           订单撮合和成交生成
  fee_model.py              手续费和滑点
  rollover.py               主连换月处理

portfolio/
  account.py                资金、持仓、保证金、盈亏

analyzer/
  performance.py            FIFO 配对、绩效指标、图表数据

frontend_index.py           生成 HTML 看板
```

## 最核心的数据流

```text
run_scripts/run_xxx.py
  -> run_backtest()
  -> DataProvider 获取行情
  -> Account / MatchEngine / Strategy 初始化
  -> 每根 bar:
       broker 先撮合上一根 bar 的订单
       strategy 根据当前 bar 计算目标
       模板把目标和真实仓位做差
       broker 接收新订单
       account 更新成交后的资金和持仓
  -> StrategyAnalyzer 统计绩效
  -> frontend_index 生成网页
```

重点：`backtest_engine.py` 不负责计算策略指标，也不负责判断是否下单。指标、信号、目标仓位都应该写在 `strategy/` 里。

## 写新策略看哪里

优先看 `STRATEGY_GUIDE.md`。

简要判断：

```text
普通新策略，想通过配置选择市价/限价、固定手数/资金比例/部分平仓：
  优先继承 GeneralSignalStrategy

多品种截面排序：
  继承 FactorTemplate
  仍然复用 sizing / execution / exit
```

推荐新手优先参考：

```text
strategy/custom/general_multi_ma.py
strategy/custom/dual_ma.py
run_scripts/run_general_multi_ma.py
```

自定义策略建议放在：

```text
strategy/custom/
```

并为每个策略单独写一个：

```text
run_scripts/run_xxx.py
```

## 依赖

参考 `requirements.txt`：

```bash
pip install -r requirements.txt
```

项目依赖 ClickHouse 历史行情库。表路由在 `config.py` 里配置，数据库连接信息从环境变量读取，避免把账号密码写进代码仓库。

Windows PowerShell 示例：

```powershell
$env:BACKTEST_CH_HOST="你的ClickHouse地址"
$env:BACKTEST_CH_USER="你的用户名"
$env:BACKTEST_CH_PASS="你的密码"
python run_scripts/run_general_multi_ma.py
```
