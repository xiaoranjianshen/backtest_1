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
            print("⚠️ [Aligner] 收到空数据，跳过对齐逻辑。")
            return pd.DataFrame()

        # 确保时间戳格式正确并排序
        raw_df['datetime'] = pd.to_datetime(raw_df['datetime'])
        raw_df = raw_df.sort_values(by=['datetime', 'symbol']).reset_index(drop=True)

        # ---------------------------------------------------------
        # 💥 Tick 数据可能有重复时间戳（同一时刻多条数据），需要先聚合
        # ---------------------------------------------------------
        if 'last_price' in raw_df.columns or 'ask_price_1' in raw_df.columns:
            # Tick 数据：取每个时间点的最后一条
            agg_dict = {col: 'last' for col in raw_df.columns if col not in ['datetime', 'symbol']}
            raw_df = raw_df.groupby(['datetime', 'symbol'], as_index=False).agg(agg_dict)

        # ---------------------------------------------------------
        # 步骤 1：并集时间轴提取 (Union Datetime Index)
        # ---------------------------------------------------------
        # 提取全市场所有品种发生过交易的“绝对真实时间点”
        # 这样能自动、完美地契合国内期货各品种不同的交易节（如黄金到02:30，螺纹到23:00）
        master_time_index = pd.DatetimeIndex(raw_df['datetime'].unique()).sort_values()

        # ---------------------------------------------------------
        # 步骤 2：长表转宽表 (Pivot)
        # ---------------------------------------------------------
        # 转换后，行索引为 datetime，列变为二级复合列 (MultiIndex): Level 0 是字段名，Level 1 是品种代码
        # 例如: ('close', 'KQ.m@SHFE.rb')
        print(f"🔄 [Aligner] 正在对齐 {raw_df['symbol'].nunique()} 个品种，生成宽表矩阵...")
        wide_df = raw_df.pivot(index='datetime', columns='symbol')

        # 将宽表强行贴合到完整的并集时间轴上，缺少的分钟会自动变成 NaN
        wide_df = wide_df.reindex(master_time_index)

        # ---------------------------------------------------------
        # 步骤 3：分类高精度填充 (双核架构：智能兼容 K线 与 Tick)
        # ---------------------------------------------------------
        # 不同属性的字段，填充逻辑必须彻底隔离！

        # 💥 探针：根据是否存在 'close' 字段来判断是 K线 还是 Tick
        is_kline = 'close' in wide_df.columns.levels[0]

        if is_kline:
            # ================= [ K线 (OHLC) 专属清洗逻辑 ] =================
            # 1. 价格字段前值填充 (ffill)
            # 如果某一分钟该品种没有成交，其价格默认维持上一分钟的收盘价
            price_fields = ['open', 'high', 'low', 'close']
            for field in price_fields:
                if field in wide_df.columns.levels[0]:
                    wide_df[field] = wide_df[field].ffill()

            # 💥 核心防错：未成交分钟的价格收敛清洗
            # 如果某一分钟是由于没成交而“补”出来的虚拟K线，它在这一分钟里没有产生波动。
            # 它的 open, high, low 必须强行等于它此刻的 close（即上一分钟的收盘价）。
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

            # 3. 持仓量 (oi) 与复权因子 (adjust_factor) 前值填充
            # 这两个属于存量和连续状态指标，没成交时，延续上一分钟的状态
            state_fields = ['oi', 'adjust_factor']
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
                wide_df['volume'] = wide_df['volume'].fillna(0.0)

            # 持仓量延续上一笔状态
            if 'oi' in wide_df.columns.levels[0]:
                wide_df['oi'] = wide_df['oi'].ffill()

        # ---------------------------------------------------------
        # 步骤 4：截断品种上市前的盲区
        # ---------------------------------------------------------
        # 并集时间轴会导致在某些品种尚未上市的历史时期，也被强行塞入了前值填充的价格。
        # 我们必须把所有品种在正式产生第一条真实成交量之前的“伪数据”全部剔除。
        if 'close' in wide_df.columns.levels[0]:
            # 找出全市场整行全部为 NaN 的行（代表这段时间没有任何品种上市，极少发生，做个兜底）
            wide_df = wide_df.dropna(how='all', subset=[('close', sym) for sym in wide_df.columns.levels[1]])

        print(f"📊 [Aligner] 矩阵对齐清洗完毕。当前数据矩阵形状 (Shape): {wide_df.shape}")
        return wide_df