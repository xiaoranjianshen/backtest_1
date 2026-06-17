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

    @staticmethod
    def _uses_intraday_main_rollover(freq: str, data_type: str) -> bool:
        return data_type == 'main' and freq in {'1m', '5m'}

    def get_data(self, symbols: list, start_date: str, end_date: str, freq: str = '1d',
                 data_type: str = 'main') -> pd.DataFrame:
        cache_key = f"{'_'.join(symbols)}_{start_date}_{end_date}_{freq}_{data_type}"
        if self._uses_intraday_main_rollover(freq, data_type):
            cache_key = f"{cache_key}_intraday_rollover_v2"
        cache_hash = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
        cache_path = os.path.join(CACHE_DIR, f"cache_{cache_hash}.parquet")

        if os.path.exists(cache_path):
            print(f"[Data Loader] 命中本地缓存: {cache_hash[:8]}，正在加载。")
            return pd.read_parquet(cache_path)

        print("[Data Loader] 正在从 ClickHouse 获取数据...")
        df = self._fetch_from_db(symbols, start_date, end_date, freq, data_type)
        if self._uses_intraday_main_rollover(freq, data_type):
            df = self._attach_intraday_rollover_marks(df)

        if not df.empty:
            df.to_parquet(cache_path)
        return df

    def _get_table_info(self, freq: str, data_type: str):
        route = DB_ROUTING_MAP.get(freq, {}).get(data_type)
        if not route:
            raise ValueError(f"找不到对应的数据表配置: freq={freq}, data_type={data_type}")
        return route['db'], route['table']

    @staticmethod
    def _sql_quote(value: str) -> str:
        return "'" + str(value).replace("'", "\\'") + "'"

    def _attach_intraday_rollover_marks(self, df: pd.DataFrame) -> pd.DataFrame:
        """把日级主力换月标记映射到分钟级主连数据。

        换月事件只在天级别发生；对 1m/5m 回测，只需要把换月日内每个品种的
        第一根有效 bar 标记为 month_change=1，避免同一天重复触发换月。
        """
        if df.empty or 'symbol' not in df.columns or 'datetime' not in df.columns:
            return df

        result = df.copy()
        result['datetime'] = pd.to_datetime(result['datetime'])
        result['month_change'] = 0

        symbols = sorted(result['symbol'].dropna().astype(str).unique())
        if not symbols:
            return result

        start_day = result['datetime'].min().strftime('%Y-%m-%d')
        end_day = result['datetime'].max().strftime('%Y-%m-%d')
        symbols_sql = ", ".join(self._sql_quote(sym) for sym in symbols)
        query = f"""
            SELECT
                d.symbol,
                toDate(d.datetime) AS roll_date,
                max(toInt32(d.month_change)) AS roll_flag
            FROM futures_data.daily_adj_factor AS d
            WHERE d.symbol IN ({symbols_sql})
              AND d.datetime >= toDate('{start_day}')
              AND d.datetime <= toDate('{end_day}')
              AND d.month_change != 0
            GROUP BY d.symbol, roll_date
        """

        try:
            rows = self.client.execute(query)
        except Exception as exc:
            print(f"[Data Loader Warning] Failed to load intraday rollover marks: {exc}")
            return result

        if not rows:
            print("[Data Loader] Intraday rollover marks: no events found in daily_adj_factor.")
            return result

        rollover_df = pd.DataFrame(rows, columns=['symbol', 'roll_date', 'roll_flag'])
        rollover_df = rollover_df[rollover_df['roll_flag'].fillna(0).astype(int) >= 1]
        if rollover_df.empty:
            return result

        rollover_df['roll_date'] = pd.to_datetime(rollover_df['roll_date']).dt.date
        rollover_keys = set(zip(rollover_df['symbol'].astype(str), rollover_df['roll_date']))

        result['_roll_date'] = result['datetime'].dt.date
        is_roll_date = [
            (str(sym), roll_date) in rollover_keys
            for sym, roll_date in zip(result['symbol'], result['_roll_date'])
        ]
        first_roll_rows = (
            result.loc[is_roll_date]
            .sort_values(['symbol', 'datetime'])
            .groupby(['symbol', '_roll_date'], sort=False)
            .head(1)
            .index
        )
        result.loc[first_roll_rows, 'month_change'] = 1
        result = result.drop(columns=['_roll_date'])
        print(f"[Data Loader] Intraday rollover marks injected: {len(first_roll_rows)} rows.")
        return result

    def _fetch_from_db(self, symbols: list, start_date: str, end_date: str, freq: str, data_type: str) -> pd.DataFrame:
        db_name, table_name = self._get_table_info(freq, data_type)
        symbols_str = ", ".join([f"'{s}'" for s in symbols])
        skip_symbol_filter = (data_type == 'all' and len(symbols) >= 50)
        preview_symbols = symbols[:5]
        print(
            f"[Data Loader] 连接信息 -> host={CH_HOST}, user={CH_USER}, db={db_name}, table={table_name}, "
            f"freq={freq}, data_type={data_type}, symbols={len(symbols)}"
        )
        print(f"[Data Loader] 符号预览 -> {preview_symbols}")
        print(f"[Data Loader] 时间范围 -> {start_date} ~ {end_date}")
        if skip_symbol_filter:
            print("[Data Loader] 检测到 all + 全市场模式，本次将跳过 symbol 过滤，直接按时间范围导出全表。")

        # 根据请求的是 Tick 还是 K 线，动态组装查询字段。
        if freq == 'tick':
            # Tick 数据的专属查询结构
            symbol_filter_clause = "" if skip_symbol_filter else f"AND symbol IN ({symbols_str})"
            query = f"""
                SELECT 
                    symbol, datetime, last_price, volume, 
                    bid_price_1, bid_volume_1, ask_price_1, ask_volume_1,
                    if(hasColumnInTable('{db_name}', '{table_name}', 'open_interest'), open_interest, 
                       if(hasColumnInTable('{db_name}', '{table_name}', 'open_oi'), open_oi, 0.0)) AS oi
                FROM {db_name}.{table_name}
                WHERE datetime >= '{start_date}' AND datetime <= '{end_date}'
                  {symbol_filter_clause}
                ORDER BY symbol, datetime ASC, volume ASC
            """
        elif freq == '1d' and data_type == 'main':
            # 日线主连：需要 JOIN m_daily 和 daily_adj_factor 来获取 month_change
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
                FROM {db_name}.m_daily m
                LEFT JOIN {db_name}.daily_adj_factor d
                    ON m.symbol = d.symbol AND m.datetime = d.datetime
                WHERE m.datetime >= '{start_date}' AND m.datetime <= '{end_date}'
                  AND m.symbol IN ({symbols_str})
                ORDER BY m.symbol, m.datetime ASC
            """
        else:
            # K 线数据 (1m, 5m, 1d) 的专属查询结构
            symbol_filter_clause = "" if skip_symbol_filter else f"AND symbol IN ({symbols_str})"
            query = f"""
                SELECT 
                    symbol, datetime, open, high, low, close, volume, 
                    if(hasColumnInTable('{db_name}', '{table_name}', 'month_change'), month_change, 0) AS month_change,
                    if(hasColumnInTable('{db_name}', '{table_name}', 'open_interest'), open_interest, 
                       if(hasColumnInTable('{db_name}', '{table_name}', 'open_oi'), open_oi, 0.0)) AS oi,
                    if(hasColumnInTable('{db_name}', '{table_name}', 'adjust_factor'), adjust_factor, 1.0) AS adjust_factor
                FROM {db_name}.{table_name}
                WHERE datetime >= '{start_date}' AND datetime <= '{end_date}'
                  {symbol_filter_clause}
                ORDER BY symbol, datetime ASC
            """

        try:
            print(f"[Data Loader] SQL预览 -> {query[:500].strip()}...")
            result, columns = self.client.execute(query, with_column_types=True)
            df = pd.DataFrame(result, columns=[c[0] for c in columns])
            return df
        except Exception as e:
            print(f"ClickHouse 查询失败: {e}")
            return pd.DataFrame()
