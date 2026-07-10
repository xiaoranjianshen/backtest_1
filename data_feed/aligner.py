# -*- coding: utf-8 -*-
"""
板块一：时间轴对齐引擎 (Data Aligner)
职责：
1. 将 ClickHouse 吐出的长表 (Long Form) 转换为以时间为索引、品种为列的宽表 (Wide Form)
2. 提取多品种发生过交易的时间轴并集，贴合国内期货复杂的夜盘交易节点
3. 严丝合缝地处理缺失 K 线：价格前值填充、未成交分钟的价格收敛、成交量与换月标志归零
"""

import pandas as pd
import numpy as np


class DataAligner:
    """多品种数据对齐与清洗器"""

    @staticmethod
    def align_multi_symbol(raw_df: pd.DataFrame) -> pd.DataFrame:
        """
        核心对齐算法：将不规则的多品种长表清洗并对齐为标准宽表

        :param raw_df: ClickHouseLoader 吐出的原始长表 DataFrame
                       包含列: ['symbol', 'datetime', 'open', 'high', 'low', 'close', 'volume', 'month_change', 'oi', 'adjust_factor']
        :return: 经过严格对齐和前值填充后的 MultiIndex 宽表
        """
        if raw_df.empty:
            print("[Data Aligner Warning] 收到空数据，跳过对齐逻辑。")
            return pd.DataFrame()

        # 确保时间戳格式正确并排序
        raw_df = raw_df.copy()
        raw_df['datetime'] = pd.to_datetime(raw_df['datetime'])
        is_tick_input = 'last_price' in raw_df.columns or 'ask_price_1' in raw_df.columns

        sort_fields = ['datetime', 'symbol']
        if is_tick_input and 'volume' in raw_df.columns:
            sort_fields.append('volume')
        raw_df = raw_df.sort_values(by=sort_fields).reset_index(drop=True)

        if is_tick_input:
            # Tick data keeps every quote update. Source volume is cumulative,
            # so volume_delta is the per-update traded volume.
            raw_df['is_fresh'] = 1.0
            if 'volume' in raw_df.columns:
                cumulative_volume = pd.to_numeric(raw_df['volume'], errors='coerce')
                volume_delta = cumulative_volume.groupby(raw_df['symbol']).diff()
                raw_df['volume_delta'] = volume_delta.where(volume_delta >= 0, 0.0).fillna(0.0)

            duplicate_seq = raw_df.groupby(['datetime', 'symbol']).cumcount()
            if duplicate_seq.gt(0).any():
                raw_df['datetime'] = raw_df['datetime'] + pd.to_timedelta(duplicate_seq, unit='us')
        else:
            # K-line matrices are also forward-filled for alignment. This flag
            # records whether the row came from the source table or was created
            # by alignment, so stale bars can be used for valuation but not fills.
            raw_df['is_fresh'] = 1.0

        # ---------------------------------------------------------
        # 步骤 1：并集时间轴提取 (Union Datetime Index)
        # ---------------------------------------------------------
        # 提取全市场所有品种发生过交易的时间点并集，兼容不同品种的交易时段。
        master_time_index = pd.DatetimeIndex(raw_df['datetime'].unique()).sort_values()

        # ---------------------------------------------------------
        # 步骤 2：长表转宽表 (Pivot)
        # ---------------------------------------------------------
        # 转换后，行索引为 datetime，列变为二级复合列 (MultiIndex): Level 0 是字段名，Level 1 是品种代码
        # 例如: ('close', 'KQ.m@SHFE.rb')
        print(f"[Data Aligner] 正在对齐 {raw_df['symbol'].nunique()} 个品种，生成宽表矩阵...")
        wide_df = raw_df.pivot(index='datetime', columns='symbol')

        # 将宽表对齐到完整并集时间轴，缺少的分钟会变成 NaN。
        wide_df = wide_df.reindex(master_time_index)

        # ---------------------------------------------------------
        # 步骤 3：分类高精度填充 (双核架构：智能兼容 K线 与 Tick)
        # ---------------------------------------------------------
        # 不同属性的字段，填充逻辑必须分开处理。

        # 根据是否存在 'close' 字段判断是 K 线还是 Tick。
        is_kline = 'close' in wide_df.columns.levels[0]

        if is_kline:
            # ================= [ K线 (OHLC) 专属清洗逻辑 ] =================
            # 1. 价格字段前值填充 (ffill)
            # 如果某一分钟该品种没有成交，其价格默认维持上一分钟的收盘价
            price_fields = ['open', 'high', 'low', 'close']
            for field in price_fields:
                if field in wide_df.columns.levels[0]:
                    wide_df[field] = wide_df[field].ffill()

            # 未成交分钟的价格收敛清洗。
            # 如果某一分钟是由于没成交而“补”出来的虚拟K线，它在这一分钟里没有产生波动。
            # 它的 open, high, low 应等于此刻的 close（即上一分钟的收盘价）。
            # 如果不收敛，直接取 ffill 的结果，会导致这一分钟出现错误的日内最高最低价，引发策略误判。
            if 'volume' in wide_df.columns.levels[0] and 'close' in wide_df.columns.levels[0]:
                # 找出所有成交量为 NaN 的位置，这些就是缺失被补齐的分钟
                is_missing_bar = wide_df['volume'].isna()

                for field in ['open', 'high', 'low']:
                    if field in wide_df.columns.levels[0]:
                        # 如果是缺失补齐的行，用 close 的值覆盖它；否则保留原值
                        wide_df[field] = wide_df[field].where(~is_missing_bar, wide_df['close'])

            # 2. 状态量与绝对量填充为 0
            # 没成交的分钟，成交量(volume)必须是 0 手；没发生换月的分钟，换月标志(month_change)必须是 0
            zero_fill_fields = ['volume', 'month_change']
            for field in zero_fill_fields:
                if field in wide_df.columns.levels[0]:
                    wide_df[field] = wide_df[field].fillna(0.0)

            if 'is_fresh' in wide_df.columns.levels[0]:
                wide_df['is_fresh'] = wide_df['is_fresh'].fillna(0.0)

            # 3. 持仓量 (oi) 与复权因子 (adjust_factor) 前值填充
            # 这两个属于存量和连续状态指标，没成交时，延续上一分钟的状态
            state_fields = ['oi', 'adjust_factor', 'underlying_symbol']
            for field in state_fields:
                if field in wide_df.columns.levels[0]:
                    wide_df[field] = wide_df[field].ffill()

        else:
            # ================= [ Tick (盘口) 专属清洗逻辑 ] =================
            # 1. 价格字段前值填充 (ffill)
            # Tick 数据只有最新价和盘口，直接进行前值填充即可，不存在高低点收敛问题
            tick_price_fields = ['last_price', 'bid_price_1', 'ask_price_1']
            for field in tick_price_fields:
                if field in wide_df.columns.levels[0]:
                    wide_df[field] = wide_df[field].ffill()

            # 2. 盘口挂单量填充
            # 未成交时盘口挂单量同样前值填充
            tick_vol_fields = ['bid_volume_1', 'ask_volume_1']
            for field in tick_vol_fields:
                if field in wide_df.columns.levels[0]:
                    wide_df[field] = wide_df[field].ffill()

            # 3. 绝对量与状态量填充
            # 没真实成交时，当笔成交量必须归零
            if 'volume' in wide_df.columns.levels[0]:
                wide_df['volume'] = wide_df['volume'].ffill().fillna(0.0)

            if 'volume_delta' in wide_df.columns.levels[0]:
                wide_df['volume_delta'] = wide_df['volume_delta'].fillna(0.0)

            if 'is_fresh' in wide_df.columns.levels[0]:
                wide_df['is_fresh'] = wide_df['is_fresh'].fillna(0.0)

            # 持仓量延续上一笔状态
            if 'oi' in wide_df.columns.levels[0]:
                wide_df['oi'] = wide_df['oi'].ffill()

        # ---------------------------------------------------------
        # 步骤 4：截断品种上市前的盲区
        # ---------------------------------------------------------
        # 并集时间轴可能覆盖某些品种上市前的历史区间，需要剔除全市场无价格的行。
        if 'close' in wide_df.columns.levels[0]:
            # 找出全市场整行全部为 NaN 的行（代表这段时间没有任何品种上市，极少发生，做个兜底）
            wide_df = wide_df.dropna(how='all', subset=[('close', sym) for sym in wide_df.columns.levels[1]])

        print(f"[Data Aligner] 矩阵对齐清洗完成，shape={wide_df.shape}")
        return wide_df
