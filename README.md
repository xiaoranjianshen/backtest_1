# backtest_1 - 期货量化回测引擎

基于 ClickHouse 的高性能期货回测框架，支持 Tick 级、K 线级策略回测。

## 功能特性

- **多频率回测**：Tick 级、1 分钟线、日线
- **真实交易模拟**：支持手续费、滑点、保证金、换月展期
- **因子框架**：支持多因子组合、跨品种动量
- **绩效分析**：权益曲线、Trade DNA、收益归因
- **数据源**：ClickHouse 期货 Tick/K 线数据库

## 项目结构

```
backtest_1/
├── broker/              # 券商层：订单撮合、费率、换月
├── strategy/            # 策略层：规则策略 / 因子策略
│   ├── rule_template/    # 规则模板（如双均线）
│   └── factor_template/ # 因子模板（如跨品种动量）
├── portfolio/           # 组合层：账户资金、持仓管理
├── data_feed/            # 数据层：ClickHouse 数据加载、对齐
├── analyzer/             # 分析层：绩效报告、可视化
├── tick_algorithms/      # Tick 算法：VWAP、TWAP、做市
└── demo_*.py            # 演示脚本
```

## 快速开始

### 前置依赖

- Python 3.10+
- ClickHouse（本地或远程）
- 依赖包：`pip install clickhouse-driver pandas numpy matplotlib`

### 配置文件

修改 `config.py` 中的数据库连接：

```python
CH_HOST = 'your_clickhouse_host'  # 默认 localhost
CH_USER = 'default'
CH_PASS = ''
```

### 运行示例

```bash
# 双均线策略日线回测
python demo_run_DualMA.py

# 多因子策略回测
python demo_run_factor.py

# Tick 级回测
python demo_run_tick.py
```

## 注意事项

- 本框架需要连接包含期货 Tick/K 线数据的 ClickHouse 数据库
- 费率配置（`config.py` 中的 `FEE_DICT`）基于 2024 年各大交易所规则
- 保证金比例仅供参考，实际以期货公司标准为准

## License

MIT License
