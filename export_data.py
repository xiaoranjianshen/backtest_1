# -*- coding: utf-8 -*-
"""
工业级回测引擎 - 独立数据导出工具 (Data Exporter)
作用：使用者可以通过简单的参数配置，自由拉取 ClickHouse 中的任意品种、任意时间、任意频率的数据，
      并支持【自定义筛选列】与【表头自动拍平】，最终自动创建目录并保存为规范命名的 CSV 或 Parquet 文件。
"""

import os
import sys
import pandas as pd
from datetime import datetime

# 💥 强制将当前脚本所在的目录加入 Python 寻路列表，确保能顺利导入同级模块
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from data_feed.data_provider import DataProvider
from config import SYMBOL_DICT

# =========================================================================
# ⚙️ 核心参数配置区 (使用者在此修改参数)
# =========================================================================

# 1. 📂 目标导出文件夹路径
EXPORT_DIR = r"C:\clickhouse_download"

# 2. 📊 选择导出频率 (freq) —— 【只能从以下选项中选一个，取消对应的注释即可】
FREQ = '1m'
# FREQ = '5m'
# FREQ = '1d'
# FREQ = 'tick'

# 3. 🎯 选择数据形态 (data_type) —— 【只能从以下选项中选一个】
DATA_TYPE = 'main_adj'  #  主力连续【等比复权表】(含 month_change)
# DATA_TYPE = 'main'      # 主力连续【未复权真实价格表】
# DATA_TYPE = 'index'     # 指数连续曲线表
# DATA_TYPE = 'all'       # 全市场明细单月合约底表

# 4. ⚔️ 选择要导出的品种组合 —— 【三种模式，任选一种，其余注释掉】
# 模式 A：全市场所有品种一次性全部导出
# SELECTED_SYMBOLS = [f"KQ.m@{attr[2]}.{code}" for code, attr in SYMBOL_DICT.items()]

# 模式 B：自定义指定品种组合
INPUT_SYMBOLS = ['rb', 'I', 'TA605', 'rb2410']

# 💥 核心修复：在内存中构建一个“全小写键”的影子字典，彻底免疫大小写问题
LOWER_SYMBOL_DICT = {k.lower(): v for k, v in SYMBOL_DICT.items()}

SELECTED_SYMBOLS = []
import re

for sym in INPUT_SYMBOLS:
    raw_input = sym.lower()

    # 1. 嗅探是否带有具体月份数字
    match = re.match(r"^([a-z]+)(\d+)$", raw_input)

    if match:
        pure_code = match.group(1)
        # 💥 替换为 LOWER_SYMBOL_DICT
        if pure_code in LOWER_SYMBOL_DICT:
            exchange = LOWER_SYMBOL_DICT[pure_code][2]
            SELECTED_SYMBOLS.append(f"{exchange}.{raw_input}")
        else:
            print(f"⚠️ 警告: 找不到 '{pure_code}' 的交易所映射，已跳过 {sym}。")

    else:
        # 💥 替换为 LOWER_SYMBOL_DICT
        if raw_input in LOWER_SYMBOL_DICT:
            exchange = LOWER_SYMBOL_DICT[raw_input][2]

            if DATA_TYPE == 'index':
                prefix = "KQ.i@"
            elif DATA_TYPE in ['main', 'main_adj']:
                prefix = "KQ.m@"
            else:
                prefix = ""

            SELECTED_SYMBOLS.append(f"{prefix}{exchange}.{raw_input}")
        else:
            print(f"⚠️ 警告: 品种 '{sym}' 未在 config.py 中配置，已跳过！")

# 模式 C：按照板块筛选导出
# TARGET_SECTOR = '黑色'
# SELECTED_SYMBOLS = [f"KQ.m@{attr[2]}.{code}" for code, attr in SYMBOL_DICT.items() if attr[3] == TARGET_SECTOR]

# 5. ⏱️ 选择导出的时间范围 (支持精确到时分秒)
START_DATE = '2026-05-15 00:00:00'
END_DATE = '2026-05-24 23:59:59'

# 6. 🎛️ 自定义筛选列 —— 【不需要的列直接加 # 注释掉即可】
# 宽表会严格根据这里保留的字段进行过滤输出
EXPORT_FIELDS = [
    # --- K线专属 ---
    'open', 'high', 'low', 'close',
    'month_change',

    # --- Tick高频盘口专属 ---
    'last_price',  # 最新价
    'bid_price_1',  # 买一价
    'ask_price_1',  # 卖一价
    'bid_volume_1',  # 买一量
    'ask_volume_1',  # 卖一量

    # --- 公共通用字段 ---
    'volume', 'oi',
    # 'adjust_factor',  # 复权因子
]

# 7. 📥 导出格式选择
SAVE_FORMAT = 'csv'  # 'csv' 或 'parquet' (Parquet 体积更小，读取速度快10倍)

# 8. 是否把双层 MultiIndex 表头拍平？
# True  -> 表头变成: '螺纹钢(rb)[主连]_close', '铁矿石(i)[主连]_open' (方便 Excel 筛选查看)
# False -> 保持标准的双层结构 (方便未来的策略矩阵直接读取运算)
FLATTEN_COLUMNS = True


## =========================================================================
# 🛠️ 自动化导出核心逻辑执行区
# =========================================================================
def execute_export():
    import re
    print("⏳ 正在初始化数据分发...")
    provider = DataProvider()

    # 1. 调用底座拉取并自动对齐数据
    df = provider.get_history(
        symbols=SELECTED_SYMBOLS,
        start_date=START_DATE,
        end_date=END_DATE,
        freq=FREQ,
        data_type=DATA_TYPE
    )

    if df.empty:
        print("❌ 导出失败：没有获取到任何有效数据，请检查 ClickHouse 状态或合约配置。")
        return

    # =====================================================================
    #  识别实际拉取到的品种（反向解析表头）
    # =====================================================================
    actual_full_syms = df.columns.get_level_values(1).unique().tolist()
    actual_pure_codes = []

    for sym in actual_full_syms:
        # 兼容中文加括号格式，例如 '螺纹钢(rb)[主连]'
        match = re.search(r'\((.*?)\)', sym)
        if match:
            actual_pure_codes.append(match.group(1).lower())
        else:
            # 兼容纯代码格式，例如 'CZCE.ta605' 或 'ta605'
            actual_pure_codes.append(sym.split('.')[-1].lower())

    actual_pure_codes = sorted(list(set(actual_pure_codes)))
    requested_pure_codes = [sym.split('.')[-1].lower() for sym in SELECTED_SYMBOLS]

    # 对比丢失的品种
    missed_codes = set(requested_pure_codes) - set(actual_pure_codes)
    if missed_codes:
        print(f"\n⚠️ [对齐结果] 预期请求 {len(requested_pure_codes)} 个品种，实际有效 {len(actual_pure_codes)} 个。")
        print(f"⚠️ [丢失品种] {', '.join(missed_codes)} (提示: 可能是因为在 '{DATA_TYPE}' 表中不存在该合约)")
    else:
        print(f"\n✅ [对齐结果] 所有 {len(actual_pure_codes)} 个请求品种均已成功拉取！")

    # 2. 执行【可选列】精准过滤
    print("✂️ 正在根据配置筛选目标字段...")
    available_fields = [f for f in EXPORT_FIELDS if f in df.columns.levels[0]]
    df = df.loc[:, df.columns.get_level_values(0).isin(available_fields)]

    # 3. 执行【表头拍平】逻辑
    if FLATTEN_COLUMNS:
        new_columns = [f"{col[1]}_{col[0]}" for col in df.columns]
        df.columns = new_columns

    # 4. 自动创建目标下载文件夹
    if not os.path.exists(EXPORT_DIR):
        os.makedirs(EXPORT_DIR, exist_ok=True)
        print(f"📁 目标下载目录不存在，已自动创建: {EXPORT_DIR}")

    # 5. 智能构建文件名
    if len(actual_pure_codes) <= 5:
        symbol_desc = f"{len(actual_pure_codes)}symbol_{'_'.join(actual_pure_codes)}"
    else:
        symbol_desc = f"{len(actual_pure_codes)}symbol_multi"

    # 💥 关键保留：必须加上 'tick': 'tick'，否则高频导出时文件名会出错
    freq_map = {'1m': 'min', '5m': '5min', '1d': 'day', 'tick': 'tick'}
    freq_desc = freq_map.get(FREQ.lower(), FREQ.lower())

    type_map = {
        'main_adj': 'main_adj_data',
        'main': 'main_data',
        'index': 'index_data',
        'all': 'all_data'
    }
    type_desc = type_map.get(DATA_TYPE.lower(), f"{DATA_TYPE.lower()}_data")

    start_dt = START_DATE[:10].replace('-', '')
    end_dt = END_DATE[:10].replace('-', '')

    filename = f"{symbol_desc}_{start_dt}_{end_dt}_{freq_desc}_{type_desc}.{SAVE_FORMAT}"
    full_save_path = os.path.join(EXPORT_DIR, filename)

    # 6. 安全落盘
    print(f"💾 正在将 {len(df):,} 行数据打包写入磁盘...")
    if SAVE_FORMAT.lower() == 'csv':
        df.to_csv(full_save_path, encoding='utf-8-sig')
    elif SAVE_FORMAT.lower() == 'parquet':
        df.to_parquet(full_save_path)

    print("\n" + "=" * 65)
    print("✨🎉 下载成功！🎉✨")
    print(f"✅ 文件完好保存至: {full_save_path}")
    print(f"📊 最终矩阵形状 (Shape): {df.shape}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    from datetime import datetime
    start_time = datetime.now()
    execute_export()
    print(f"⏱️ 下载及格式转换链总耗时: {(datetime.now() - start_time).total_seconds():.2f} 秒")