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
            cache_key = f"{cache_key}_contract_mapped_rollover_v3"
        cache_hash = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
        cache_path = os.path.join(CACHE_DIR, f"cache_{cache_hash}.parquet")

        if os.path.exists(cache_path):
            print(f"[Data Loader] 命中本地缓存: {cache_hash[:8]}，正在加载。")
            return pd.read_parquet(cache_path)

        print("[Data Loader] 正在从 ClickHouse 获取数据...")
        if self._uses_intraday_main_rollover(freq, data_type):
            df = self._fetch_intraday_main_contract_data(symbols, start_date, end_date, freq)
            df = self._attach_intraday_rollover_marks(df)
        else:
            df = self._fetch_from_db(symbols, start_date, end_date, freq, data_type)

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
        """在真实合约分钟序列上生成一次性的换月事件。"""
        if df.empty or 'symbol' not in df.columns or 'datetime' not in df.columns:
            return df

        result = df.copy()
        result['datetime'] = pd.to_datetime(result['datetime'])
        if 'underlying_symbol' not in result.columns:
            print("[Data Loader Warning] 分钟主连数据缺少 underlying_symbol，无法安全生成换月事件。")
            result['month_change'] = 0
            return result

        result = result.sort_values(['symbol', 'datetime']).reset_index(drop=True)
        if 'month_change' in result.columns:
            source_roll_flag = pd.to_numeric(result['month_change'], errors='coerce').fillna(0)
        else:
            source_roll_flag = pd.Series(0, index=result.index, dtype='int64')
        result['_trade_date'] = result['datetime'].dt.normalize()
        night_mask = result['datetime'].dt.hour >= 21
        result.loc[night_mask, '_trade_date'] += pd.Timedelta(days=1)

        first_rows = result.groupby(['symbol', '_trade_date'], sort=False).head(1).copy()
        first_rows['_source_roll_flag'] = source_roll_flag.loc[first_rows.index].to_numpy()
        first_rows['_previous_underlying_symbol'] = (
            first_rows.groupby('symbol', sort=False)['underlying_symbol'].shift(1)
        )
        previous_contract = first_rows['_previous_underlying_symbol'].astype('string')
        current_contract = first_rows['underlying_symbol'].astype('string')
        changed_contract = (
            previous_contract.notna()
            & current_contract.notna()
            & previous_contract.str.lower().ne(current_contract.str.lower())
        )
        valid_event = first_rows['_source_roll_flag'].ge(1) & changed_contract
        event_rows = first_rows.loc[valid_event]

        result['month_change'] = 0
        result['previous_underlying_symbol'] = pd.NA
        result['roll_old_close'] = float('nan')
        result['roll_new_open'] = float('nan')

        previous_close = pd.to_numeric(result['close'], errors='coerce').groupby(result['symbol']).shift(1)
        if not event_rows.empty:
            event_index = event_rows.index
            result.loc[event_index, 'month_change'] = 1
            result.loc[event_index, 'previous_underlying_symbol'] = event_rows[
                '_previous_underlying_symbol'
            ].to_numpy()
            result.loc[event_index, 'roll_old_close'] = previous_close.loc[event_index].to_numpy()
            result.loc[event_index, 'roll_new_open'] = pd.to_numeric(
                result.loc[event_index, 'open'], errors='coerce'
            ).to_numpy()

        flagged_without_change = int((first_rows['_source_roll_flag'].ge(1) & ~changed_contract).sum())
        if flagged_without_change:
            print(
                f"[Data Loader Warning] 忽略 {flagged_without_change} 个未发生真实合约切换的换月标记。"
            )

        result = result.drop(columns=['_trade_date'])
        print(f"[Data Loader] 分钟主连换月事件已生成: {len(event_rows)} 个。")
        return result

    def _fetch_intraday_main_contract_data(self, symbols: list, start_date: str,
                                           end_date: str, freq: str) -> pd.DataFrame:
        """按每日主力合约映射，从全合约表重建未复权分钟主连。"""
        db_name, all_table = self._get_table_info(freq, 'all')
        if not symbols:
            return pd.DataFrame()
        symbols_sql = ", ".join(self._sql_quote(sym) for sym in symbols)
        mapping_query = f"""
            SELECT
                symbol,
                toDate(datetime) AS trade_date,
                argMax(underlying_symbol, datetime) AS underlying_symbol,
                max(toInt32(month_change)) AS month_change
            FROM {db_name}.daily_adj_factor
            WHERE symbol IN ({symbols_sql})
              AND datetime >= toDate('{start_date}')
              AND datetime <= addDays(toDate('{end_date}'), 1)
            GROUP BY symbol, trade_date
        """
        try:
            mapping_rows = self.client.execute(mapping_query)
        except Exception as exc:
            print(f"ClickHouse 主力合约映射查询失败: {exc}")
            return pd.DataFrame()
        if not mapping_rows:
            print("[Data Loader Warning] 请求区间内没有找到主力合约映射。")
            return pd.DataFrame()

        mapped_contracts = sorted({str(row[2]) for row in mapping_rows if row[2]})
        contract_candidates = sorted({
            variant
            for contract in mapped_contracts
            for variant in (contract, contract.lower(), contract.upper())
        })
        contracts_sql = ", ".join(self._sql_quote(contract) for contract in contract_candidates)
        oi_expression = (
            f"if(hasColumnInTable('{db_name}', '{all_table}', 'open_interest'), a.open_interest, "
            f"if(hasColumnInTable('{db_name}', '{all_table}', 'open_oi'), a.open_oi, 0.0))"
        )
        query = f"""
            SELECT
                d.symbol AS symbol,
                a.datetime,
                a.open,
                a.high,
                a.low,
                a.close,
                a.volume,
                {oi_expression} AS oi,
                d.month_change AS month_change,
                d.underlying_symbol AS underlying_symbol,
                1.0 AS adjust_factor
            FROM {db_name}.{all_table} AS a
            INNER JOIN
            (
                SELECT
                    symbol,
                    toDate(datetime) AS trade_date,
                    argMax(underlying_symbol, datetime) AS underlying_symbol,
                    max(toInt32(month_change)) AS month_change
                FROM {db_name}.daily_adj_factor
                WHERE symbol IN ({symbols_sql})
                  AND datetime >= toDate('{start_date}')
                  AND datetime <= addDays(toDate('{end_date}'), 1)
                GROUP BY symbol, trade_date
            ) AS d
              ON lower(a.symbol) = lower(d.underlying_symbol)
             AND if(toHour(a.datetime) >= 21,
                    addDays(toDate(a.datetime), 1),
                    toDate(a.datetime)) = d.trade_date
            WHERE a.datetime >= '{start_date}'
              AND a.datetime <= '{end_date}'
              AND a.symbol IN ({contracts_sql})
            ORDER BY symbol, datetime ASC
        """
        print(
            f"[Data Loader] 按真实合约重建分钟主连 -> db={db_name}, table={all_table}, "
            f"freq={freq}, symbols={len(symbols)}"
        )
        try:
            print(f"[Data Loader] SQL预览 -> {query[:500].strip()}...")
            rows, columns = self.client.execute(query, with_column_types=True)
            return pd.DataFrame(rows, columns=[column[0] for column in columns])
        except Exception as exc:
            print(f"ClickHouse 分钟主连重建失败: {exc}")
            return pd.DataFrame()

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
