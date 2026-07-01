# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from clickhouse_driver import Client


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class TableColumn:
    name: str
    type: str
    default_type: str = ""
    default_expression: str = ""
    comment: str = ""


@dataclass(frozen=True)
class QuerySpec:
    database: str
    table: str
    columns: tuple[str, ...]
    time_column: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    symbol_column: str | None = None
    symbol_values: tuple[str, ...] = ()
    symbol_filter_mode: str = "exact"
    order_by_time: bool = True
    limit: int | None = 1000


class ReadOnlyClickHouse:
    """只读 ClickHouse 访问层。

    页面层不暴露自由 SQL；这里也只提供 SHOW / DESCRIBE / SELECT。
    即使底层账号有写权限，本模块仍会用 readonly 查询设置和严格 SQL 构造来降低误操作风险。
    """

    def __init__(self, host: str, user: str, password: str, port: int = 9000):
        self.host = host
        self.user = user
        self.password = password
        self.port = int(port)
        self._client = Client(host=host, port=self.port, user=user, password=password)

    def ping(self) -> bool:
        return bool(self._client.execute("SELECT 1", settings={"readonly": 1})[0][0])

    def databases(self) -> list[str]:
        rows = self._client.execute("SHOW DATABASES", settings={"readonly": 1})
        return sorted(str(row[0]) for row in rows)

    def tables(self, database: str) -> list[str]:
        db = quote_identifier(database)
        rows = self._client.execute(f"SHOW TABLES FROM {db}", settings={"readonly": 1})
        return sorted(str(row[0]) for row in rows)

    def describe_table(self, database: str, table: str) -> list[TableColumn]:
        sql = f"DESCRIBE TABLE {quote_identifier(database)}.{quote_identifier(table)}"
        rows = self._client.execute(sql, settings={"readonly": 1})
        columns: list[TableColumn] = []
        for row in rows:
            values = list(row) + [""] * 5
            columns.append(TableColumn(
                name=str(values[0]),
                type=str(values[1]),
                default_type=str(values[2] or ""),
                default_expression=str(values[3] or ""),
                comment=str(values[4] or ""),
            ))
        return columns

    def preview_table(self, spec: QuerySpec) -> pd.DataFrame:
        query_spec = spec
        needs_client_side_projection = requires_select_all_projection(spec.columns)
        if needs_client_side_projection:
            query_spec = QuerySpec(**{**spec.__dict__, "columns": ("*",)})

        sql, params = build_select_query(query_spec)
        rows, columns = self._client.execute(sql, params=params, with_column_types=True, settings={"readonly": 1})
        df = pd.DataFrame(rows, columns=[item[0] for item in columns])
        if needs_client_side_projection:
            requested_columns = [column for column in spec.columns if column in df.columns]
            return df.loc[:, requested_columns]
        return df
    def count_rows(self, spec: QuerySpec) -> int:
        count_spec = QuerySpec(
            database=spec.database,
            table=spec.table,
            columns=("count()",),
            time_column=spec.time_column,
            start_time=spec.start_time,
            end_time=spec.end_time,
            symbol_column=spec.symbol_column,
            symbol_values=spec.symbol_values,
            symbol_filter_mode=spec.symbol_filter_mode,
            order_by_time=False,
            limit=None,
        )
        sql, params = build_select_query(count_spec, allow_function_columns=True)
        return int(self._client.execute(sql, params=params, settings={"readonly": 1})[0][0])

    def distinct_values(self, database: str, table: str, column: str, limit: int = 5000) -> list[str]:
        limit = max(1, min(int(limit), 20000))
        sql = (
            f"SELECT DISTINCT {quote_identifier(column)} AS value "
            f"FROM {quote_identifier(database)}.{quote_identifier(table)} "
            f"WHERE {quote_identifier(column)} IS NOT NULL "
            f"ORDER BY value LIMIT {limit}"
        )
        rows = self._client.execute(sql, settings={"readonly": 1})
        return [str(row[0]) for row in rows]

    def latest_time(self, database: str, table: str) -> str:
        columns = self.describe_table(database, table)
        time_column = choose_default_time_column(columns)
        if not time_column:
            return ""
        sql = (
            f"SELECT max({quote_identifier(time_column)}) "
            f"FROM {quote_identifier(database)}.{quote_identifier(table)}"
        )
        rows = self._client.execute(sql, settings={"readonly": 1})
        if not rows or rows[0][0] is None:
            return ""
        return str(rows[0][0])

    def export_query(self, spec: QuerySpec, output_path: Path, file_format: str) -> Path:
        df = self.preview_table(spec)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if file_format == "parquet":
            df.to_parquet(output_path, index=False)
        elif file_format == "csv":
            df.to_csv(output_path, index=False, encoding="utf-8-sig")
        else:
            raise ValueError(f"不支持的导出格式: {file_format}")
        return output_path


def requires_select_all_projection(columns: tuple[str, ...]) -> bool:
    return any(column != "*" and not _IDENTIFIER_RE.match(str(column or "")) for column in columns)


def quote_identifier(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("标识符不能为空")
    return "`" + text.replace("`", "``") + "`"


def build_select_query(spec: QuerySpec, allow_function_columns: bool = False) -> tuple[str, dict]:
    if not spec.columns:
        raise ValueError("至少选择一列。")

    selected_columns = []
    for column in spec.columns:
        if allow_function_columns and column == "count()":
            selected_columns.append("count()")
        elif column == "*":
            selected_columns.append("*")
        else:
            selected_columns.append(quote_identifier(column))

    params: dict[str, object] = {}
    where_parts = []

    if spec.time_column and spec.start_time:
        where_parts.append(f"{quote_identifier(spec.time_column)} >= %(start_time)s")
        params["start_time"] = spec.start_time
    if spec.time_column and spec.end_time:
        where_parts.append(f"{quote_identifier(spec.time_column)} <= %(end_time)s")
        params["end_time"] = spec.end_time

    symbol_values = tuple(clean_values(spec.symbol_values))
    if spec.symbol_column and symbol_values:
        symbol_expr = quote_identifier(spec.symbol_column)
        if spec.symbol_filter_mode == "exact":
            placeholders = []
            for index, value in enumerate(symbol_values):
                key = f"symbol_{index}"
                placeholders.append(f"%({key})s")
                params[key] = value
            where_parts.append(f"{symbol_expr} IN ({', '.join(placeholders)})")
        elif spec.symbol_filter_mode == "prefix":
            parts = []
            for index, value in enumerate(symbol_values):
                key = f"symbol_prefix_{index}"
                parts.append(f"lower(toString({symbol_expr})) LIKE %({key})s")
                params[key] = value.lower() + "%"
            where_parts.append("(" + " OR ".join(parts) + ")")
        elif spec.symbol_filter_mode == "contains":
            parts = []
            for index, value in enumerate(symbol_values):
                key = f"symbol_contains_{index}"
                parts.append(f"lower(toString({symbol_expr})) LIKE %({key})s")
                params[key] = "%" + value.lower() + "%"
            where_parts.append("(" + " OR ".join(parts) + ")")
        else:
            raise ValueError(f"不支持的合约过滤模式: {spec.symbol_filter_mode}")

    sql = (
        f"SELECT {', '.join(selected_columns)} "
        f"FROM {quote_identifier(spec.database)}.{quote_identifier(spec.table)}"
    )
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if spec.order_by_time and spec.time_column:
        sql += f" ORDER BY {quote_identifier(spec.time_column)} ASC"
    if spec.limit is not None and int(spec.limit) > 0:
        sql += f" LIMIT {int(spec.limit)}"
    return sql, params


def clean_values(values: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def choose_default_time_column(columns: list[TableColumn]) -> str | None:
    preferred = ("datetime", "date", "trade_date", "trading_date", "trading_day", "time", "timestamp")
    by_lower = {column.name.lower(): column.name for column in columns}
    for name in preferred:
        if name in by_lower:
            return by_lower[name]
    time_columns = detect_time_columns(columns)
    return time_columns[0] if time_columns else None


def detect_time_columns(columns: list[TableColumn]) -> list[str]:
    result = []
    for column in columns:
        col_type = column.type.lower()
        col_name = column.name.lower()
        if "date" in col_type or "datetime" in col_type or col_name in {"datetime", "timestamp", "time", "date"}:
            result.append(column.name)
    return result


def detect_symbol_columns(columns: list[TableColumn]) -> list[str]:
    keywords = ("symbol", "instrument", "contract", "product", "underlying")
    return [column.name for column in columns if any(item in column.name.lower() for item in keywords)]


def parse_value_list(text: str) -> tuple[str, ...]:
    raw = str(text or "").replace("，", ",").replace("；", ",").replace(";", ",").replace("\n", ",").split(",")
    return tuple(clean_values(raw))


def timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
