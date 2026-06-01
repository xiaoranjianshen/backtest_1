# -*- coding: utf-8 -*-
import os
import sys
import pandas as pd
import re
from typing import List

# 确保能找到项目根目录下的 config.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from config import NAME_TO_CODE
from .ch_loader import ClickHouseLoader
from .aligner import DataAligner


class DataProvider:
    """回测数据统一分发中心"""

    def __init__(self):
        self.loader = ClickHouseLoader()

    def _clean_symbol_names(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """
        核心脱壳与命名器：
        将 'KQ.m@SHFE.rb' 转换为 '螺纹钢(rb)[主连]'
        将 'CZCE.ta605' 转换为 'PTA(ta605)[明细]'
        """
        if df.empty:
            return df

        # 1. 动态生成反向字典 (代码转中文)，统一用小写去匹配
        code_to_name = {code.lower(): chs_name for chs_name, code in NAME_TO_CODE.items()}

        # 2. 根据数据路由形态，智能匹配后缀
        suffix = ""
        if 'main' in data_type:
            suffix = "[主连]"
        elif 'index' in data_type:
            suffix = "[指数]"
        elif 'all' in data_type:
            suffix = "[明细]"

        # 3. 获取宽表第二层的所有原始列名
        old_symbols = df.columns.levels[1].tolist()
        rename_dict = {}

        for old_sym in old_symbols:
            # 兼容包含前缀的连续合约(有@)和无前缀的具体合约
            if '.' in old_sym:
                # 提取点号后面的最后一部分，例如 'rb' 或 'ta605'
                raw_code = old_sym.split('.')[-1].lower()

                # 💥 核心修复：用正则剥离出纯字母 (应对 ta605 这种带数字的合约)
                match = re.match(r"^([a-z]+)(\d*)$", raw_code)
                if match:
                    pure_letter = match.group(1)
                else:
                    pure_letter = raw_code

                # 去字典查中文名 (用纯字母 ta 查，而不是用 ta605 查)
                chs_name = code_to_name.get(pure_letter, pure_letter.upper())

                # 组合成终极完美形态：螺纹钢(rb)[主连] 或 PTA(ta605)[明细]
                clean_name = f"{chs_name}({raw_code}){suffix}"
                rename_dict[old_sym] = clean_name
            else:
                rename_dict[old_sym] = old_sym

        # 批量重命名
        return df.rename(columns=rename_dict, level=1)

    def get_history(self,
                    symbols: List[str],
                    start_date: str,
                    end_date: str,
                    freq: str = '1d',
                    data_type: str = 'main') -> pd.DataFrame:

        if not symbols:
            return pd.DataFrame()

        raw_long_df = self.loader.get_data(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            freq=freq,
            data_type=data_type
        )

        if raw_long_df.empty:
            return pd.DataFrame()

        aligned_wide_df = DataAligner.align_multi_symbol(raw_long_df)

        clean_df = self._clean_symbol_names(aligned_wide_df, data_type)


        print(f"[DataProvider] 数据矩阵对齐，最终Shape: {clean_df.shape}")
        return clean_df