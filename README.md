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

## 第一次安装

推荐环境：

```text
Windows 10/11
Python 3.11
PowerShell 5 或更高版本
```

如果电脑还没有 Python，先从 Python 官网安装 Python 3.11，并勾选 `Add python.exe to PATH`。

### 一键安装依赖

方式一：双击项目根目录下的文件：

```text
install_windows.bat
```

方式二：在项目根目录打开 PowerShell，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\install_windows.ps1
```

安装脚本会自动完成：

- 检查本机是否存在 Python 3.10 或更高版本，优先使用 Python 3.11。
- 创建项目虚拟环境 `.venv`。
- 升级 `pip / setuptools / wheel`。
- 执行 `pip install -r requirements.txt` 安装依赖。
- 检查关键依赖是否能正常导入。

安装完成后，如果使用 PowerShell 手动运行脚本，先激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

然后再运行回测脚本。

### 数据库配置

项目的历史行情从 ClickHouse 读取。依赖安装只负责安装 Python 包，不会自动提供数据库权限。运行真实回测前，需要确认当前电脑能访问行情库，并配置以下环境变量：

```powershell
$env:BACKTEST_CH_HOST="你的ClickHouse地址"
$env:BACKTEST_CH_USER="你的用户名"
$env:BACKTEST_CH_PASS="你的密码"
```

如果只想临时在当前 PowerShell 窗口运行，用上面的写法即可。关闭窗口后变量会失效。

如果希望长期保存到 Windows 用户环境变量，可以执行：

```powershell
[Environment]::SetEnvironmentVariable("BACKTEST_CH_HOST", "你的ClickHouse地址", "User")
[Environment]::SetEnvironmentVariable("BACKTEST_CH_USER", "你的用户名", "User")
[Environment]::SetEnvironmentVariable("BACKTEST_CH_PASS", "你的密码", "User")
```

设置完成后，重新打开 PowerShell 或 PyCharm，再运行项目。

## 快速运行

常用运行入口都放在 `run_scripts/`。这些脚本运行完成后会直接打开 HTML 报告：

```bash
python run_scripts/run_general_multi_ma.py
python run_scripts/run_dual_ma.py
python run_scripts/run_factor.py
python run_scripts/run_breakout_pyramid.py
python run_scripts/run_zscore_reversal.py
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

## 依赖清单

依赖统一维护在 `requirements.txt`。新用户不要手动一个个安装，优先执行一键安装脚本。

| 依赖包 | 用途 |
| --- | --- |
| `clickhouse-driver` | 连接 ClickHouse，读取历史行情数据。 |
| `pandas` | 处理行情表、成交表、绩效统计表。 |
| `numpy` | 计算收益、波动、回撤等数值指标。 |
| `pyarrow` | 支持 `pandas` 读写 parquet，本地行情缓存和数据导出会用到。 |
| `matplotlib` | 预留给静态图和扩展分析模块使用。 |
| `plotly` | 生成交互式图表和 HTML 回测报告。 |
| `kaleido` | 支持 Plotly 静态图片导出，固定为 `0.2.1` 以减少版本兼容问题。 |
| `streamlit` | 配置中心页面，用于选择策略、品种、周期和参数后运行回测。 |

手动安装命令如下，通常只在调试安装问题时使用：

```powershell
python -m pip install -r requirements.txt
```

注意：Python 包安装成功不等于回测一定能取到数据。真实回测还需要 ClickHouse 地址、账号、密码正确，并且当前电脑在允许访问数据库的网络环境内。
