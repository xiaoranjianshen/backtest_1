# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


UI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = UI_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tick_algorithms.pulse_discovery import (
    PulseDiscoveryParams,
    frame_around_event,
    run_pulse_discovery,
)
from ui_config import ACTIVE_REPORT_CONFIG_PATH


def render_pulse_panel() -> None:
    """渲染 tick 脉冲发现研究页。"""

    st.markdown("### 行情脉冲发现 (Pulse Discovery)")
    st.caption(
        "用于从 tick 数据中找出极短时间内的异常单边跳变。当前版本为纯 Python 研究页，"
        "不影响回测引擎、成交撮合和策略信号逻辑。"
    )

    active_config = _load_active_config()
    symbols_default = _default_symbols(active_config)
    start_default = _date_from_text(active_config.get("start_date"), date.today() - timedelta(days=3))
    end_default = _date_from_text(active_config.get("end_date"), date.today())

    market_col_1, market_col_2, market_col_3 = st.columns([2.4, 1, 1])
    symbol_text = market_col_1.text_input(
        "品种或合约代码",
        value=", ".join(symbols_default),
        key="pulse_symbol_text",
        help="main 模式输入品种代码，例如 au,ag；all 模式可以输入具体合约，例如 au2608,ag2608。",
    )
    symbols = _parse_symbols(symbol_text)
    data_type = market_col_2.selectbox(
        "数据类型",
        ["main", "all"],
        index=0,
        key="pulse_data_type",
        help="main 为主力连续 tick；all 为明细合约 tick，当前页默认建议先用 main。",
    )
    force_refresh = market_col_3.checkbox("忽略脉冲缓存", value=False, key="pulse_force_refresh")

    date_col_1, date_col_2, p_col_1, p_col_2 = st.columns([1, 1, 1, 1])
    start_date = date_col_1.date_input("开始日期", value=start_default, key="pulse_start_date")
    end_date = date_col_2.date_input("结束日期", value=end_default, key="pulse_end_date")
    window_seconds = p_col_1.number_input(
        "窗口秒数",
        min_value=0.5,
        value=10.0,
        step=0.5,
        format="%.1f",
        key="pulse_window_seconds",
    )
    percentile_pct = p_col_2.number_input(
        "触发分位数 (%)",
        min_value=90.0,
        max_value=99.99,
        value=99.0,
        step=0.1,
        format="%.2f",
        key="pulse_percentile_pct",
    )

    p_col_3, p_col_4 = st.columns([1, 1])
    min_move_ticks = p_col_3.number_input(
        "最低跳数过滤",
        min_value=0.0,
        value=2.0,
        step=0.5,
        format="%.1f",
        key="pulse_min_move_ticks",
    )
    collapse_seconds = p_col_4.number_input(
        "事件合并秒数",
        min_value=0.0,
        value=30.0,
        step=1.0,
        format="%.1f",
        key="pulse_collapse_seconds",
    )

    run = st.button(
        "运行脉冲发现",
        type="primary",
        use_container_width=True,
        disabled=not symbols,
        key="pulse_run_button",
    )
    if not symbols:
        st.warning("请至少选择一个品种。")
        return
    if not run and "pulse_result" not in st.session_state:
        st.info("设置参数后点击运行。建议先选 1-3 个品种、1-3 天数据，确认速度和结果。")
        return

    if run:
        params = PulseDiscoveryParams(
            symbols=tuple(symbols),
            start_date=str(start_date),
            end_date=str(end_date),
            data_type=data_type,
            window_seconds=float(window_seconds),
            percentile=float(percentile_pct) / 100.0,
            min_move_ticks=float(min_move_ticks),
            collapse_seconds=float(collapse_seconds),
        )
        with st.spinner("正在加载 tick 数据并计算脉冲事件..."):
            st.session_state["pulse_result"] = run_pulse_discovery(params, force_refresh=force_refresh)

    result = st.session_state.get("pulse_result")
    if not isinstance(result, dict):
        return

    _render_result(result)


def _render_result(result: dict) -> None:
    events = result.get("events", pd.DataFrame())
    summary = result.get("summary", pd.DataFrame())
    raw = result.get("raw", pd.DataFrame())
    meta = result.get("meta", {})

    if meta.get("status") == "empty":
        st.error(meta.get("message", "未获取到数据。"))
        return

    cache_text = "命中缓存" if result.get("cache_hit") else "本次新计算"
    st.caption(f"Cache key: `{result.get('cache_key', '-')}` | {cache_text}")

    kpi_1, kpi_2, kpi_3, kpi_4 = st.columns(4)
    kpi_1.metric("原始 tick 行数", f"{int(meta.get('source_rows', 0)):,}")
    kpi_2.metric("脉冲事件数", f"{int(meta.get('event_count', len(events))):,}")
    kpi_3.metric("品种数", f"{int(meta.get('symbol_count', len(summary))):,}")
    kpi_4.metric("结果状态", str(meta.get("status", "-")))

    if summary.empty:
        st.warning("当前参数下没有可汇总的品种结果。")
        return

    st.markdown("#### 跨品种脉冲地图 (Cross-Asset Pulse Map)")
    st.plotly_chart(_cross_asset_map(summary), use_container_width=True)

    table_col_1, table_col_2 = st.columns([1, 1])
    with table_col_1:
        st.markdown("#### 脉冲强度阶梯 (Pulse Severity Ladder)")
        st.plotly_chart(_severity_ladder(events), use_container_width=True)
    with table_col_2:
        st.markdown("#### 品种指纹 (Pulse Fingerprint)")
        st.dataframe(_format_summary(summary), use_container_width=True, hide_index=True)

    st.markdown("#### 最大脉冲事件 (Largest Pulse Events)")
    if events.empty:
        st.info("当前阈值下没有触发事件。可以降低分位数或最低跳数过滤。")
        return

    display_events = _format_events(events.head(200))
    st.dataframe(display_events, use_container_width=True, hide_index=True)
    st.download_button(
        "下载脉冲事件 CSV",
        data=events.to_csv(index=False).encode("utf-8-sig"),
        file_name="pulse_events.csv",
        mime="text/csv",
        use_container_width=True,
        key="pulse_events_download",
    )

    st.markdown("#### 事件检查器 (Selected Event Inspector)")
    _event_inspector(raw, events)


def _cross_asset_map(summary: pd.DataFrame) -> go.Figure:
    fig = px.scatter(
        summary,
        x="p99_abs_move_ticks",
        y="pulses_per_hour",
        size="top20_avg_abs_move_ticks",
        color="quality_badge",
        hover_name="product",
        hover_data={
            "symbol": True,
            "pulse_count": True,
            "threshold_ticks": ":.2f",
            "avg_spread_ticks": ":.2f",
            "spread_coverage": ":.1%",
        },
        labels={
            "p99_abs_move_ticks": "P99 绝对跳变 (跳)",
            "pulses_per_hour": "每小时脉冲数",
            "top20_avg_abs_move_ticks": "Top20 平均强度",
            "quality_badge": "质量标记",
        },
    )
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=30, b=20))
    return fig


def _severity_ladder(events: pd.DataFrame) -> go.Figure:
    if events.empty:
        fig = go.Figure()
        fig.update_layout(height=320, annotations=[dict(text="No Pulse Events", x=0.5, y=0.5, showarrow=False)])
        return fig

    counts = (
        events.groupby(["product", "severity_band"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
    )
    fig = px.bar(
        counts,
        x="product",
        y="count",
        color="severity_band",
        barmode="stack",
        labels={"product": "品种", "count": "事件数", "severity_band": "强度"},
        category_orders={"severity_band": ["Trigger", "Strong", "Extreme"]},
    )
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=20, b=20), legend_orientation="h")
    return fig


def _event_inspector(raw: pd.DataFrame, events: pd.DataFrame) -> None:
    if raw.empty or events.empty:
        st.info("没有可检查的事件。")
        return

    top_events = events.head(200).copy()
    labels = [
        f"{row.datetime} | {str(row.product).upper()} | {row.direction} | {row.abs_move_ticks:.1f} ticks"
        for row in top_events.itertuples()
    ]
    selected_label = st.selectbox("选择事件", labels, key="pulse_event_selector")
    selected_idx = labels.index(selected_label)
    event = top_events.iloc[selected_idx]
    around = frame_around_event(raw, event, seconds_before=90, seconds_after=90)
    if around.empty:
        st.info("未找到事件附近的原始 tick。")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=around["datetime"], y=around["last_price"], name="last_price", mode="lines"))
    if "bid_price_1" in around.columns:
        fig.add_trace(go.Scatter(x=around["datetime"], y=around["bid_price_1"], name="bid_1", mode="lines"))
    if "ask_price_1" in around.columns:
        fig.add_trace(go.Scatter(x=around["datetime"], y=around["ask_price_1"], name="ask_1", mode="lines"))
    fig.add_vline(x=pd.to_datetime(event["datetime"]), line_color="#ef4444", line_dash="dash")
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=30, b=20), yaxis_title="价格")
    st.plotly_chart(fig, use_container_width=True)

    detail = event.to_dict()
    st.json({k: _json_safe(v) for k, v in detail.items()})


def _format_summary(summary: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "product", "pulse_count", "pulses_per_hour", "threshold_ticks",
        "p99_abs_move_ticks", "top20_avg_abs_move_ticks", "avg_spread_ticks",
        "spread_coverage", "quality_badge",
    ]
    result = summary[[col for col in cols if col in summary.columns]].copy()
    rename = {
        "product": "品种",
        "pulse_count": "事件数",
        "pulses_per_hour": "每小时事件",
        "threshold_ticks": "阈值(跳)",
        "p99_abs_move_ticks": "P99跳变",
        "top20_avg_abs_move_ticks": "Top20均值",
        "avg_spread_ticks": "平均价差(跳)",
        "spread_coverage": "盘口覆盖",
        "quality_badge": "质量",
    }
    result = result.rename(columns=rename)
    for col in ["每小时事件", "阈值(跳)", "P99跳变", "Top20均值", "平均价差(跳)"]:
        if col in result:
            result[col] = result[col].map(lambda x: f"{float(x):.2f}" if pd.notna(x) else "-")
    if "盘口覆盖" in result:
        result["盘口覆盖"] = result["盘口覆盖"].map(lambda x: f"{float(x):.1%}" if pd.notna(x) else "-")
    return result


def _format_events(events: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "datetime", "product", "direction", "last_price", "window_start_price",
        "move_ticks", "move_bps", "velocity_ticks_per_sec", "spread_ticks",
        "book_imbalance", "volume_delta", "threshold_ticks", "severity_band", "trigger_rows",
    ]
    result = events[[col for col in cols if col in events.columns]].copy()
    rename = {
        "datetime": "时间",
        "product": "品种",
        "direction": "方向",
        "last_price": "触发价",
        "window_start_price": "窗口起点价",
        "move_ticks": "跳变(跳)",
        "move_bps": "跳变(bp)",
        "velocity_ticks_per_sec": "速度(跳/秒)",
        "spread_ticks": "价差(跳)",
        "book_imbalance": "盘口不平衡",
        "volume_delta": "当笔量",
        "threshold_ticks": "阈值(跳)",
        "severity_band": "强度",
        "trigger_rows": "合并行数",
    }
    result = result.rename(columns=rename)
    for col in ["触发价", "窗口起点价", "跳变(跳)", "跳变(bp)", "速度(跳/秒)", "价差(跳)", "盘口不平衡", "阈值(跳)"]:
        if col in result:
            result[col] = result[col].map(lambda x: f"{float(x):.4f}" if pd.notna(x) else "-")
    return result


def _load_active_config() -> dict:
    try:
        if ACTIVE_REPORT_CONFIG_PATH.exists():
            config = json.loads(ACTIVE_REPORT_CONFIG_PATH.read_text(encoding="utf-8"))
            return config if isinstance(config, dict) else {}
    except Exception:
        return {}
    return {}


def _default_symbols(active_config: dict) -> list[str]:
    symbols = active_config.get("symbols")
    if isinstance(symbols, list) and symbols:
        return [str(item).lower() for item in symbols[:3]]
    if isinstance(symbols, str) and symbols.strip():
        return [symbols.strip().lower()]
    return ["au", "ag"]


def _parse_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for raw in str(text or "").replace("，", ",").split(","):
        item = raw.strip().lower()
        if item and item not in symbols:
            symbols.append(item)
    return symbols


def _date_from_text(value, default: date) -> date:
    try:
        if value:
            return date.fromisoformat(str(value)[:10])
    except ValueError:
        pass
    return default


def _json_safe(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value
