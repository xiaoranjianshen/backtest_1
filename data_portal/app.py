# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


PORTAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PORTAL_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import CH_HOST, CH_USER
from data_portal.db import (
    QuerySpec,
    ReadOnlyClickHouse,
    detect_symbol_columns,
    detect_time_columns,
    parse_value_list,
    timestamp_for_filename,
)
from data_portal.table_notes import DATA_GROUP_ORDER, table_data_group, table_note_dict


DOWNLOAD_DIR = PORTAL_DIR / "downloads"
DEFAULT_PORT = int(os.getenv("BACKTEST_CH_PORT", "9000"))
PORTAL_CH_HOST = os.getenv("DATA_PORTAL_CH_HOST", CH_HOST)
PORTAL_CH_USER = os.getenv("DATA_PORTAL_CH_USER", CH_USER)
PORTAL_CH_PASSWORD = os.getenv("DATA_PORTAL_CH_PASSWORD", "")
PORTAL_CH_PORT = int(os.getenv("DATA_PORTAL_CH_PORT", str(DEFAULT_PORT)))
MAX_DOWNLOAD_BYTES = int(os.getenv("DATA_PORTAL_MAX_DOWNLOAD_MB", "512")) * 1024 * 1024


st.set_page_config(
    page_title="Data Portal",
    page_icon="D",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.4rem; }
    .metric-card { border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px 14px; background: #fff; }
    .small-note { color: #64748b; font-size: 0.86rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def main() -> None:
    st.title("数据下载中心 (Read-Only Data Portal)")
    st.caption("公司内网数据读取与下载页面。当前页面只提供元数据查看、预览和 SELECT 下载，不提供写入或自由 SQL 执行入口。")

    if not _authenticated():
        _render_login()
        return

    client = _client()
    _render_sidebar(client)
    _render_browser(client)


def _authenticated() -> bool:
    return bool(st.session_state.get("data_portal_authenticated"))


def _render_login() -> None:
    with st.form("data_portal_login"):
        st.subheader("登录")
        st.caption("请输入 ClickHouse 只读账号。登录通过后，本页面后续查询会使用该账号权限访问数据库。")
        username = st.text_input("账号", value="", key="data_portal_login_user")
        password = st.text_input("密码", value="", type="password", key="data_portal_login_password")
        submitted = st.form_submit_button("登录", type="primary", use_container_width=True)

    if not submitted:
        return

    login_user = str(username or "").strip()
    if not login_user:
        st.error("请输入账号。")
        return

    try:
        client = ReadOnlyClickHouse(
            host=PORTAL_CH_HOST,
            port=PORTAL_CH_PORT,
            user=login_user,
            password=str(password or ""),
        )
        client.ping()
    except Exception as exc:
        st.error(f"数据库登录失败: {exc}")
        st.caption("注意：本页面使用 ClickHouse Native TCP 端口，默认是 9000；DBeaver/JDBC 常用 8123 HTTP 端口。若该账号只能走 8123，需要改用 HTTP 驱动。")
        return

    st.session_state["data_portal_authenticated"] = True
    st.session_state["data_portal_login_name"] = login_user
    st.session_state["data_portal_db_user"] = login_user
    st.session_state["data_portal_db_password"] = str(password or "")
    st.success("登录成功。")
    st.rerun()


@st.cache_resource(show_spinner=False)
def _cached_client(host: str, port: int, user: str, password: str) -> ReadOnlyClickHouse:
    client = ReadOnlyClickHouse(host=host, port=port, user=user, password=password)
    client.ping()
    return client


def _client() -> ReadOnlyClickHouse:
    user = st.session_state.get("data_portal_db_user", PORTAL_CH_USER)
    password = st.session_state.get("data_portal_db_password", PORTAL_CH_PASSWORD)
    return _cached_client(PORTAL_CH_HOST, PORTAL_CH_PORT, user, password)


def _render_sidebar(client: ReadOnlyClickHouse) -> None:
    with st.sidebar:
        st.markdown("### 连接信息")
        st.text(f"Host: {PORTAL_CH_HOST}:{PORTAL_CH_PORT}")
        st.text(f"User: {client.user}")
        st.caption("密码不会在页面显示。")

        if st.button("退出登录", use_container_width=True):
            st.session_state.pop("data_portal_authenticated", None)
            st.session_state.pop("data_portal_login_name", None)
            st.session_state.pop("data_portal_db_user", None)
            st.session_state.pop("data_portal_db_password", None)
            st.rerun()

        st.divider()
        st.markdown("### 只读边界")
        st.markdown(
            """
            - 不提供自由 SQL 输入。
            - 只生成 `SHOW` / `DESCRIBE` / `SELECT`。
            - 表名和列名来自数据库元数据选择。
            - 查询使用 ClickHouse `readonly=1` 设置。
            """
        )


@st.cache_data(show_spinner=False, ttl=600)
def _latest_dates(_client: ReadOnlyClickHouse, database: str, tables: tuple[str, ...]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for table_name in tables:
        try:
            latest[table_name] = _client.latest_time(database, table_name)
        except Exception:
            latest[table_name] = ""
    return latest
def _render_browser(client: ReadOnlyClickHouse) -> None:
    try:
        databases = client.databases()
    except Exception as exc:
        st.error(f"读取数据库列表失败: {exc}")
        return

    visible_databases = [
        db for db in databases if db.lower() not in {"system", "information_schema", "information_schema_compat"}
    ]
    if not visible_databases:
        st.warning("当前账号未读取到可显示的数据库。")
        return

    top_col_1, top_col_2, top_col_3 = st.columns([1.1, 1.1, 1.8])
    default_database_index = visible_databases.index("futures_data") if "futures_data" in visible_databases else 0
    database = top_col_1.selectbox("数据库", visible_databases, index=default_database_index, key="data_portal_database")

    try:
        tables = client.tables(database)
    except Exception as exc:
        st.error(f"读取表列表失败: {exc}")
        return

    grouped_tables = {
        group: [table_name for table_name in tables if table_data_group(database, table_name) == group]
        for group in DATA_GROUP_ORDER
    }
    data_groups = [group for group in DATA_GROUP_ORDER if grouped_tables.get(group)]
    if not data_groups:
        st.warning("当前数据库没有可展示的数据表。")
        return

    default_group_index = data_groups.index("行情数据") if "行情数据" in data_groups else 0
    data_group = top_col_2.selectbox("数据类别", data_groups, index=default_group_index, key="data_portal_data_group")
    filtered_tables = grouped_tables.get(data_group, [])
    if not filtered_tables:
        st.warning("当前类别下没有表。")
        return

    default_table = "day1_data" if data_group == "行情数据" and "day1_data" in filtered_tables else filtered_tables[0]
    table_index = filtered_tables.index(default_table) if default_table in filtered_tables else 0
    table = top_col_3.selectbox(
        "数据表",
        filtered_tables,
        index=table_index,
        key=f"data_portal_table_{database}_{data_group}",
    )

    latest_dates = _latest_dates(client, database, tuple(filtered_tables))

    st.markdown("#### 数据表清单")
    table_rows = []
    for table_name in filtered_tables:
        note = table_note_dict(database, table_name)
        table_rows.append(
            {
                "数据库": database,
                "数据类别": note["data_group"],
                "数据表": table_name,
                "细分类别": note["category"],
                "最新日期": latest_dates.get(table_name, ""),
                "备注": note["description"],
            }
        )
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

    try:
        columns = client.describe_table(database, table)
    except Exception as exc:
        st.error(f"读取表结构失败: {exc}")
        return

    st.markdown(f"#### 当前表: `{database}.{table}`")
    current_note = table_note_dict(database, table)
    current_latest = latest_dates.get(table) or client.latest_time(database, table)
    st.info(
        f"数据类别：{current_note['data_group']}\n\n"
        f"细分类别：{current_note['category']}\n\n"
        f"最新日期：{current_latest or '-'}\n\n"
        f"备注：{current_note['description']}"
    )
    schema_df = pd.DataFrame([column.__dict__ for column in columns])
    with st.expander("查看字段结构", expanded=True):
        st.dataframe(schema_df, use_container_width=True, hide_index=True)

    _render_query_panel(client, database, table, columns)

def _render_query_panel(client: ReadOnlyClickHouse, database: str, table: str, columns) -> None:
    column_names = [column.name for column in columns]
    column_types = {column.name: column.type for column in columns}
    time_columns = detect_time_columns(columns)
    symbol_columns = detect_symbol_columns(columns)

    st.markdown("#### 查询与下载")
    field_col_1, field_col_2 = st.columns([1, 2])
    all_columns = field_col_1.checkbox("选择全部字段", value=True, key="data_portal_all_columns")
    default_columns = column_names if len(column_names) <= 40 else column_names[:40]
    selected_columns = tuple(column_names if all_columns else field_col_2.multiselect(
        "字段",
        column_names,
        default=default_columns,
        key="data_portal_selected_columns",
    ))

    filter_col_1, filter_col_2, filter_col_3 = st.columns([1, 1, 1])
    time_column = filter_col_1.selectbox(
        "时间字段",
        [""] + time_columns,
        index=1 if time_columns else 0,
        key="data_portal_time_column",
    ) or None

    default_start = date(date.today().year, 1, 1)
    start_date = filter_col_2.date_input("开始日期", value=default_start, key="data_portal_start_date")
    end_date = filter_col_3.date_input("结束日期", value=date.today(), key="data_portal_end_date")

    start_time = None
    end_time = None
    if time_column:
        time_type = column_types.get(time_column, "")
        start_time = _format_time_filter_value(start_date, time_type, is_end=False)
        end_time = _format_time_filter_value(end_date, time_type, is_end=True)

    sym_col_1, sym_col_2, sym_col_3 = st.columns([1, 2, 1])
    symbol_options = [""] + symbol_columns
    default_symbol_index = symbol_options.index("symbol") if "symbol" in symbol_options else (1 if symbol_columns else 0)
    symbol_column = sym_col_1.selectbox(
        "品种/合约字段",
        symbol_options,
        index=default_symbol_index,
        key=f"data_portal_symbol_column_{database}_{table}",
    ) or None
    symbol_text = sym_col_2.text_area(
        "品种/合约过滤",
        value="",
        height=80,
        key=f"data_portal_symbol_values_{database}_{table}",
        placeholder="例如: rb, hc, au2608。多个值可用逗号、分号或换行分隔；留空表示不过滤。",
    )
    symbol_filter_mode = sym_col_3.selectbox(
        "品种匹配方式",
        ["exact", "prefix", "contains"],
        index=0,
        key=f"data_portal_symbol_filter_mode_{database}_{table}",
        format_func=lambda value: {"exact": "精确匹配", "prefix": "前缀匹配", "contains": "包含匹配"}[value],
    )
    sym_col_3.caption("精确=完全相等；前缀=输入 rb 可匹配 rb2601；包含=任意位置包含。")

    if parse_value_list(symbol_text) and not symbol_column:
        st.warning("已填写品种/合约过滤，但未选择品种/合约字段；当前不会按品种过滤。")

    row_col_1, row_col_2, row_col_3 = st.columns([1, 1, 1])
    preview_limit = int(row_col_1.number_input("预览行数", min_value=1, max_value=100000, value=1000, step=100, key="data_portal_preview_limit"))
    download_limit = int(row_col_2.number_input("下载行数上限 (0=全部)", min_value=0, value=100000, step=10000, key="data_portal_download_limit"))
    output_format = row_col_3.selectbox("下载格式", ["parquet", "csv"], key="data_portal_output_format")

    base_spec = QuerySpec(
        database=database,
        table=table,
        columns=selected_columns,
        time_column=time_column,
        start_time=start_time,
        end_time=end_time,
        symbol_column=symbol_column,
        symbol_values=parse_value_list(symbol_text),
        symbol_filter_mode=symbol_filter_mode,
        order_by_time=bool(time_column),
        limit=preview_limit,
    )

    action_col_1, action_col_2, action_col_3 = st.columns([1, 1, 2])
    if action_col_1.button("预览数据", type="primary", use_container_width=True, key="data_portal_preview_button"):
        _preview_data(client, base_spec)

    if action_col_2.button("统计行数", use_container_width=True, key="data_portal_count_button"):
        try:
            count = client.count_rows(base_spec)
            st.session_state["data_portal_last_count"] = count
        except Exception as exc:
            st.error(f"统计行数失败: {exc}")

    if "data_portal_last_count" in st.session_state:
        action_col_3.metric("当前过滤条件行数", f"{int(st.session_state['data_portal_last_count']):,}")

    if st.button("生成下载文件", use_container_width=True, key="data_portal_export_button"):
        export_limit = None if download_limit == 0 else download_limit
        export_spec = QuerySpec(**{**base_spec.__dict__, "limit": export_limit})
        _export_data(client, export_spec, output_format)

    _render_download_button()


def _preview_data(client: ReadOnlyClickHouse, spec: QuerySpec) -> None:
    try:
        with st.spinner("正在读取预览数据..."):
            df = client.preview_table(spec)
    except Exception as exc:
        st.error(f"预览失败: {exc}")
        return

    st.session_state["data_portal_preview_df"] = df
    st.success(f"预览完成，共返回 {len(df):,} 行。")
    st.dataframe(df, use_container_width=True, hide_index=True)


def _export_data(client: ReadOnlyClickHouse, spec: QuerySpec, output_format: str) -> None:
    if not spec.columns:
        st.error("请至少选择一个字段。")
        return

    suffix = "parquet" if output_format == "parquet" else "csv"
    file_name = f"{spec.database}_{spec.table}_{timestamp_for_filename()}.{suffix}"
    output_path = DOWNLOAD_DIR / file_name
    try:
        with st.spinner("正在生成下载文件..."):
            path = client.export_query(spec, output_path, output_format)
    except Exception as exc:
        st.error(f"导出失败: {exc}")
        return

    st.session_state["data_portal_download_path"] = str(path)
    st.success(f"文件已生成：{path.name}。请点击下方按钮下载。")


def _render_download_button() -> None:
    path_text = st.session_state.get("data_portal_download_path")
    if not path_text:
        return

    path = Path(path_text)
    if not path.exists():
        st.warning("下载文件已不存在，请重新生成。")
        return

    size = path.stat().st_size
    st.caption(f"文件大小: {size / 1024 / 1024:.2f} MB")

    if size > MAX_DOWNLOAD_BYTES:
        st.warning(f"文件超过 {MAX_DOWNLOAD_BYTES / 1024 / 1024:.0f} MB，页面无法直接下载。请缩小日期、品种或行数范围后重新生成。")
        return

    mime = "text/csv" if path.suffix.lower() == ".csv" else "application/octet-stream"
    st.download_button(
        "下载文件",
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime,
        use_container_width=True,
        key="data_portal_download_button",
    )



def _format_time_filter_value(value: date, clickhouse_type: str, *, is_end: bool) -> str:
    """按 ClickHouse 字段类型生成过滤值。

    Date / Date32 只能接收 YYYY-MM-DD；DateTime / DateTime64 才使用完整时间。
    """

    type_text = str(clickhouse_type or "").lower()
    if type_text.startswith("date") and not type_text.startswith("datetime"):
        return value.isoformat()
    suffix = "23:59:59" if is_end else "00:00:00"
    return f"{value.isoformat()} {suffix}"


if __name__ == "__main__":
    main()
