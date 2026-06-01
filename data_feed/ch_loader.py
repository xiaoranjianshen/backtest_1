# -*- coding: utf-8 -*-
import os
import pandas as pd
import hashlib
from clickhouse_driver import Client
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CH_HOST, CH_USER, CH_PASS, CACHE_DIR, DB_ROUTING_MAP


class ClickHouseLoader:
    """ClickHouse 数据加载器 添加了适配"""

    def __init__(self):
        self.client = Client(host=CH_HOST, user=CH_USER, password=CH_PASS)
        os.makedirs(CACHE_DIR, exist_ok=True)

    def get_data(self, symbols: list, start_date: str, end_date: str, freq: str = '1d',
                 data_type: str = 'main') -> pd.DataFrame:
        cache_key = f"{'_'.join(symbols)}_{start_date}_{end_date}_{freq}_{data_type}"
        cache_hash = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
        cache_path = os.path.join(CACHE_DIR, f"cache_{cache_hash}.parquet")

        if os.path.exists(cache_path):
            print(f"📦 [Loader] 命中本地缓存: {cache_hash[:8]}... 加载中。")
            return pd.read_parquet(cache_path)

        print(f"📡 [Loader] 连接 ClickHouse 获取数据...")
        df = self._fetch_from_db(symbols, start_date, end_date, freq, data_type)

        if not df.empty:
            df.to_parquet(cache_path)
        return df

    def _get_table_info(self, freq: str, data_type: str):
        route = DB_ROUTING_MAP.get(freq, {}).get(data_type)
        if not route:
            raise ValueError(f"❌ 找不到对应的数据表配置: freq={freq}, data_type={data_type}")
        return route['db'], route['table']

    def _fetch_from_db(self, symbols: list, start_date: str, end_date: str, freq: str, data_type: str) -> pd.DataFrame:
        db_name, table_name = self._get_table_info(freq, data_type)
        symbols_str = ", ".join([f"'{s}'" for s in symbols])

        # 💥 核心逻辑：根据请求的是 Tick 还是 K线，动态组装查询字段！
        if freq == 'tick':
            # Tick 数据的专属查询结构
            query = f"""
                SELECT 
                    symbol, datetime, last_price, volume, 
                    bid_price_1, bid_volume_1, ask_price_1, ask_volume_1,
                    if(hasColumnInTable('{db_name}', '{table_name}', 'open_interest'), open_interest, 
                       if(hasColumnInTable('{db_name}', '{table_name}', 'open_oi'), open_oi, 0.0)) AS oi
                FROM {db_name}.{table_name}
                WHERE datetime >= '{start_date}' AND datetime <= '{end_date}'
                  AND symbol IN ({symbols_str})
                ORDER BY symbol, datetime ASC
            """
        elif freq == '1d' and data_type == 'main':
            # 💥 日线主连：需要 JOIN m_daily 和 daily_adj_factor 来获取 month_change
            query = f"""
                SELECT 
                    m.symbol, 
                    m.datetime, 
                    m.open, 
                    m.high, 
                    m.low, 
                    m.close, 
                    m.volume,
                    m.open_oi AS oi,
                    if(d.month_change IS NULL, 0, d.month_change) AS month_change,
                    d.underlying_symbol,
                    1.0 AS adjust_factor
                FROM i_contract_daily_data.m_daily m
                LEFT JOIN i_contract_daily_data.daily_adj_factor d
                    ON m.symbol = d.symbol AND m.datetime = d.datetime
                WHERE m.datetime >= '{start_date}' AND m.datetime <= '{end_date}'
                  AND m.symbol IN ({symbols_str})
                ORDER BY m.symbol, m.datetime ASC
            """
        else:
            # K 线数据 (1m, 5m, 1d) 的专属查询结构
            query = f"""
                SELECT 
                    symbol, datetime, open, high, low, close, volume, 
                    if(hasColumnInTable('{db_name}', '{table_name}', 'month_change'), month_change, 0) AS month_change,
                    if(hasColumnInTable('{db_name}', '{table_name}', 'open_interest'), open_interest, 
                       if(hasColumnInTable('{db_name}', '{table_name}', 'open_oi'), open_oi, 0.0)) AS oi,
                    if(hasColumnInTable('{db_name}', '{table_name}', 'adjust_factor'), adjust_factor, 1.0) AS adjust_factor
                FROM {db_name}.{table_name}
                WHERE datetime >= '{start_date}' AND datetime <= '{end_date}'
                  AND symbol IN ({symbols_str})
                ORDER BY symbol, datetime ASC
            """

        try:
            result, columns = self.client.execute(query, with_column_types=True)
            df = pd.DataFrame(result, columns=[c[0] for c in columns])
            return df
        except Exception as e:
            print(f"❌ ClickHouse 查询失败: {e}")
            return pd.DataFrame()