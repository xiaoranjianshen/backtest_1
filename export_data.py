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

# 将当前脚本所在目录加入 Python 搜索路径，确保可以导入项目模块。
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from data_feed.data_provider import DataProvider
from config import SYMBOL_DICT

# =========================================================================
# 核心参数配置区
# =========================================================================

# 1. 目标导出文件夹路径
EXPORT_DIR = os.path.join(current_dir, "exports")

# 2. 选择导出频率 (freq)
FREQ = '1m'
# FREQ = '5m'
# FREQ = '1d'
# FREQ = 'tick'

# 3. 选择数据形态 (data_type)
DATA_TYPE = 'all'
# DATA_TYPE = 'main_adj'  # 主力连续【等比复权表】(含 month_change)
# DATA_TYPE = 'main'      # 主力连续【未复权真实价格表】
# DATA_TYPE = 'index'     # 指数连续曲线表

# 4. 选择要导出的品种组合
# 可选: 'all' / 'custom' / 'sector'
SYMBOL_SELECTION_MODE = 'custom'

# 模式 A：全市场所有品种一次性全部导出（当前启用）
# 模式 B：自定义指定品种组合（需要时再打开 custom）
INPUT_SYMBOLS = ['rb', 'I', 'TA605', 'rb2410']
# 模式 C：按照板块筛选导出（需要时再打开 sector）
#TARGET_SECTOR = '黑色'

# 构建小写品种映射，兼容用户输入大小写差异。
LOWER_SYMBOL_DICT = {k.lower(): v for k, v in SYMBOL_DICT.items()}

SELECTED_SYMBOLS = []
import re

if SYMBOL_SELECTION_MODE == 'all':
    if DATA_TYPE == 'index':
        prefix = 'KQ.i@'
    elif DATA_TYPE in ['main', 'main_adj']:
        prefix = 'KQ.m@'
    else:
        prefix = ''
    SELECTED_SYMBOLS = [f"{prefix}{attr[2]}.{code}" for code, attr in SYMBOL_DICT.items()]
elif SYMBOL_SELECTION_MODE == 'custom':
    input_symbols = globals().get('INPUT_SYMBOLS', [])
    for sym in input_symbols:
        raw_input = sym.lower()

        # 1. 嗅探是否带有具体月份数字
        match = re.match(r"^([a-z]+)(\d+)$", raw_input)

        if match:
            pure_code = match.group(1)
            if pure_code in LOWER_SYMBOL_DICT:
                exchange = LOWER_SYMBOL_DICT[pure_code][2]
                SELECTED_SYMBOLS.append(f"{exchange}.{raw_input}")
            else:
                print(f"[Data Export Warning] 找不到 '{pure_code}' 的交易所映射，已跳过 {sym}。")

        else:
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
                print(f"[Data Export Warning] 品种 '{sym}' 未在 config.py 中配置，已跳过。")
elif SYMBOL_SELECTION_MODE == 'sector':
    target_sector = globals().get('TARGET_SECTOR')
    if not target_sector:
        raise ValueError("使用 sector 模式时请先设置 TARGET_SECTOR")
    if DATA_TYPE == 'index':
        prefix = 'KQ.i@'
    elif DATA_TYPE in ['main', 'main_adj']:
        prefix = 'KQ.m@'
    else:
        prefix = ''
    SELECTED_SYMBOLS = [
        f"{prefix}{attr[2]}.{code}"
        for code, attr in SYMBOL_DICT.items()
        if attr[3] == target_sector
    ]
else:
    raise ValueError("SYMBOL_SELECTION_MODE 只能是 'all'、'custom' 或 'sector'")

# 5. 选择导出的时间范围
START_DATE = '2026-05-15 09:00:00'
END_DATE = '2026-05-15 23:59:59'


def _extract_product_code(symbol: str) -> str:
    """从 CZCE.SF309 / SHFE.rb2410 / ta605 中提取品种字母代码。"""
    raw = str(symbol).split('.')[-1]
    match = re.match(r'^([A-Za-z]+)', raw)
    return match.group(1).lower() if match else raw.lower()


def _resolve_available_output_path(path: str) -> str:
    """若目标文件被占用或已存在可自动切换到递增新文件名。"""
    base, ext = os.path.splitext(path)
    candidate = path
    suffix = 1
    while os.path.exists(candidate):
        try:
            with open(candidate, 'a', encoding='utf-8-sig'):
                return candidate
        except PermissionError:
            candidate = f"{base}_{suffix}{ext}"
            suffix += 1
    return candidate


def _ensure_datetime_column(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure exported wide data keeps the datetime index as a normal column."""
    if 'datetime' in df.columns:
        return df

    result = df.copy()
    result.insert(0, 'datetime', result.index)
    return result


# 6. 自定义筛选列
# all 模式下会尽量按原始窄表字段输出；连续/指数模式仍按宽表字段过滤
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

# all 明细底表导出时默认使用的最简列
ALL_RAW_EXPORT_FIELDS = [
    'symbol', 'datetime', 'open', 'high', 'low', 'close', 'volume', 'oi'
]

TICK_RAW_EXPORT_FIELDS = [
    'symbol', 'datetime', 'last_price', 'volume', 'bid_price_1', 'bid_volume_1', 'ask_price_1', 'ask_volume_1', 'oi'
]

# 7. 导出格式选择
SAVE_FORMAT = 'csv'  # 'csv' 或 'parquet' (Parquet 体积更小，读取速度快10倍)

# 8. 是否把双层 MultiIndex 表头拍平？
# True  -> 表头变成: '螺纹钢(rb)[主连]_close', '铁矿石(i)[主连]_open' (方便 Excel 筛选查看)
# False -> 保持标准的双层结构 (方便未来的策略矩阵直接读取运算)
FLATTEN_COLUMNS = True


## =========================================================================
# 自动化导出执行区
# =========================================================================
def execute_export():
    import re
    print("[Data Export] 正在初始化数据分发...")
    provider = DataProvider()

    # all 明细底表：直接导出原始长表，不做宽表对齐
    if DATA_TYPE == 'all':
        raw_symbols = SELECTED_SYMBOLS.copy()
        needs_post_filter = SYMBOL_SELECTION_MODE in ['sector', 'custom']
        fetch_symbols = raw_symbols

        # all 明细底表存的是具体合约，若这里只给了品种级代码，则先全表按时间拉取，再本地过滤
        if needs_post_filter:
            fetch_symbols = [f"ALL_SYMBOLS::{SYMBOL_SELECTION_MODE}"] * 60

        df = provider.loader.get_data(
            symbols=fetch_symbols,
            start_date=START_DATE,
            end_date=END_DATE,
            freq=FREQ,
            data_type=DATA_TYPE
        )

        if df.empty:
            print("[Data Export Error] 未获取到有效数据，请检查 ClickHouse 状态或合约配置。")
            return

        if needs_post_filter and 'symbol' in df.columns:
            requested_codes = {_extract_product_code(sym) for sym in raw_symbols}
            before_rows = len(df)
            df = df[df['symbol'].astype(str).map(_extract_product_code).isin(requested_codes)].copy()
            print(f"[Post Filter] all 明细表已按品种前缀过滤: {sorted(requested_codes)}")
            print(f"[Post Filter] 行数变化: {before_rows:,} -> {len(df):,}")

        if df.empty:
            print("[Data Export Error] 本地按品种前缀过滤后没有命中任何合约，请检查板块配置或底表 symbol 格式。")
            return

        print("[Data Export] 正在按原始字段输出 all 明细底表...")
        raw_export_fields = TICK_RAW_EXPORT_FIELDS if FREQ == 'tick' else ALL_RAW_EXPORT_FIELDS
        available_fields = [col for col in raw_export_fields if col in df.columns]
        df = df[available_fields].copy()

        actual_full_syms = sorted(df['symbol'].astype(str).str.lower().unique().tolist()) if 'symbol' in df.columns else []
        print(f"\n[Data Export] all 明细模式导出完成，实际拉取到 {len(actual_full_syms)} 个有效合约。")
    else:
        # 1. 调用底座拉取并自动对齐数据
        df = provider.get_history(
            symbols=SELECTED_SYMBOLS,
            start_date=START_DATE,
            end_date=END_DATE,
            freq=FREQ,
            data_type=DATA_TYPE
        )

        if df.empty:
            print("[Data Export Error] 未获取到有效数据，请检查 ClickHouse 状态或合约配置。")
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

        # 全市场 all 模式下，SELECTED_SYMBOLS 只是触发器，不代表真实底表 symbol
        skip_requested_compare = (DATA_TYPE == 'all' and SYMBOL_SELECTION_MODE == 'all')

        # 对比丢失的品种
        missed_codes = set(requested_pure_codes) - set(actual_pure_codes)
        if skip_requested_compare:
            print(f"\n[Data Export] 全市场 all 模式导出完成，实际拉取到 {len(actual_pure_codes)} 个有效合约/代码。")
        elif missed_codes:
            print(f"\n[Data Export Warning] 预期请求 {len(requested_pure_codes)} 个品种，实际有效 {len(actual_pure_codes)} 个。")
            print(f"[Data Export Warning] 缺失品种: {', '.join(missed_codes)}。可能原因：'{DATA_TYPE}' 表中不存在对应合约。")
        else:
            print(f"\n[Data Export] 所有 {len(actual_pure_codes)} 个请求品种均已成功拉取。")

        # 2. 执行【可选列】精准过滤
        print("[Data Export] 正在根据配置筛选目标字段...")
        available_fields = [f for f in EXPORT_FIELDS if f in df.columns.levels[0]]
        df = df.loc[:, df.columns.get_level_values(0).isin(available_fields)]

        # 3. 执行【表头拍平】逻辑
        if FLATTEN_COLUMNS:
            new_columns = [f"{col[1]}_{col[0]}" for col in df.columns]
            df.columns = new_columns

        df = _ensure_datetime_column(df)

    # 4. 自动创建目标下载文件夹
    if not os.path.exists(EXPORT_DIR):
        os.makedirs(EXPORT_DIR, exist_ok=True)
        print(f"[Data Export] 目标下载目录不存在，已自动创建: {EXPORT_DIR}")

    # 5. 智能构建文件名
    if DATA_TYPE == 'all':
        symbol_desc = f"{len(actual_full_syms)}contract_raw"
    elif len(actual_pure_codes) <= 5:
        symbol_desc = f"{len(actual_pure_codes)}symbol_{'_'.join(actual_pure_codes)}"
    else:
        symbol_desc = f"{len(actual_pure_codes)}symbol_multi"

    # tick 频率需要单独映射，避免高频导出文件名为空或不一致。
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
    resolved_save_path = _resolve_available_output_path(full_save_path)

    # 6. 安全落盘
    print(f"[Data Export] 正在将 {len(df):,} 行数据写入磁盘...")
    if resolved_save_path != full_save_path:
        print(f"[Data Export] 检测到目标文件正在被占用，已改存为: {resolved_save_path}")
    if SAVE_FORMAT.lower() == 'csv':
        df.to_csv(resolved_save_path, index=False, encoding='utf-8-sig')
    elif SAVE_FORMAT.lower() == 'parquet':
        df.to_parquet(resolved_save_path, index=False)

    print("\n" + "=" * 65)
    print("[Data Export] 导出完成。")
    print(f"[Data Export] 文件路径: {resolved_save_path}")
    print(f"[Data Export] 最终矩阵形状: {df.shape}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    from datetime import datetime
    start_time = datetime.now()
    execute_export()
    print(f"[Data Export] 总耗时: {(datetime.now() - start_time).total_seconds():.2f} 秒")
