# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
import importlib
from collections import OrderedDict
from datetime import date
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


UI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = UI_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

loaded_config = sys.modules.get("config")
if loaded_config is not None:
    loaded_path = Path(getattr(loaded_config, "__file__", "") or "")
    if loaded_path.name == "config.py" and loaded_path.parent == UI_DIR:
        del sys.modules["config"]

from config import SYMBOL_DICT
from data_manager import BacktestDataManager
from labels import (
    DATA_TYPE_LABELS,
    FREQ_LABELS,
    LIMIT_MODE_LABELS,
    ORDER_TYPE_LABELS,
    SIZING_LABELS,
    format_with_mapping,
)
import run_from_config as run_config_module
from ui_config import ACTIVE_REPORT_CONFIG_PATH, APP_ICON, APP_TITLE, CUSTOM_CSS, LAYOUT, PROJECT_ROOT


run_config_module = importlib.reload(run_config_module)
STRATEGY_SPECS = run_config_module.STRATEGY_SPECS
available_strategy_specs = run_config_module.available_strategy_specs

SELECTED_SYMBOLS_KEY = "selected_symbols"
ACTIVE_CONFIG_SOURCE_MTIME_KEY = "active_config_source_mtime"
SECTOR_ORDER = [
    "黑色",
    "有色",
    "贵金属",
    "化工",
    "能源",
    "油脂油料",
    "软商品",
    "生鲜",
    "建材",
    "股指",
    "国债",
    "航运",
    "新能源",
    "未分类",
]

# config.py 当前部分中文字段曾被错误编码；这里先在 UI 层标准化显示名。
SECTOR_NAME_FIXES = {
    "榛戣壊": "黑色",
    "鏈夎壊": "有色",
    "璐甸噾灞?": "贵金属",
    "璐甸噾灞": "贵金属",
    "鍖栧伐": "化工",
    "鑳芥簮": "能源",
    "娌硅剛娌规枡": "油脂油料",
    "杞晢鍝?": "软商品",
    "杞晢鍝": "软商品",
    "鐢熼矞": "生鲜",
    "寤烘潗": "建材",
    "鑲℃寚": "股指",
    "鍥藉€?": "国债",
    "鍥藉€": "国债",
    "鑸繍": "航运",
    "鏂拌兘婧?": "新能源",
    "鏂拌兘婧": "新能源",
}


st.set_page_config(page_title=APP_TITLE, page_icon=APP_ICON, layout=LAYOUT)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def _query_flag(name: str) -> bool:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        value = ""
    return str(value).lower() in {"1", "true", "yes"}


def _is_embedded() -> bool:
    context = getattr(st, "context", None)
    return bool(getattr(context, "is_embedded", False)) or _query_flag("embed")


def _strategy_label(key: str) -> str:
    spec = STRATEGY_SPECS.get(key)
    return spec.label if spec else key


def _parse_symbol_text(value: str) -> list[str]:
    text = (
        str(value or "")
        .replace("，", ",")
        .replace("、", ",")
        .replace("；", ",")
        .replace(";", ",")
        .replace("\n", ",")
        .replace(" ", ",")
    )
    return [item.strip().lower() for item in text.split(",") if item.strip()]


def _coerce_symbols(value) -> list[str]:
    if isinstance(value, str):
        return _parse_symbol_text(value)
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return []


def _symbol_key(code: str) -> str:
    return str(code).strip().lower()


def _normalize_sector_name(raw_sector) -> str:
    raw_text = str(raw_sector or "未分类").strip()
    return SECTOR_NAME_FIXES.get(raw_text, raw_text) or "未分类"


def _symbol_groups() -> OrderedDict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for code, attrs in SYMBOL_DICT.items():
        sector = _normalize_sector_name(attrs[3] if len(attrs) > 3 else "未分类")
        grouped.setdefault(sector, []).append(str(code))

    rank = {sector: index for index, sector in enumerate(SECTOR_ORDER)}
    ordered = OrderedDict()
    for sector in sorted(grouped, key=lambda item: (rank.get(item, 999), item)):
        ordered[sector] = sorted(grouped[sector], key=lambda item: item.lower())
    return ordered


def _ordered_symbols(symbols) -> list[str]:
    incoming = {_symbol_key(item) for item in symbols if str(item).strip()}
    ordered = []
    for codes in _symbol_groups().values():
        for code in codes:
            key = _symbol_key(code)
            if key in incoming and key not in ordered:
                ordered.append(key)

    leftovers = sorted(incoming.difference(ordered))
    return ordered + leftovers


def _active_report_symbols() -> tuple[float, list[str]]:
    if not ACTIVE_REPORT_CONFIG_PATH.exists():
        return 0.0, []

    try:
        config = json.loads(ACTIVE_REPORT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0, []

    symbols = _coerce_symbols(config.get("symbols"))
    if symbols:
        return ACTIVE_REPORT_CONFIG_PATH.stat().st_mtime, _ordered_symbols(symbols)
    return 0.0, []


def _selected_symbols() -> list[str]:
    active_mtime, active_symbols = _active_report_symbols()
    loaded_mtime = float(st.session_state.get(ACTIVE_CONFIG_SOURCE_MTIME_KEY, -1.0))
    if SELECTED_SYMBOLS_KEY not in st.session_state or active_mtime > loaded_mtime:
        st.session_state[SELECTED_SYMBOLS_KEY] = active_symbols
        st.session_state[ACTIVE_CONFIG_SOURCE_MTIME_KEY] = active_mtime
    return list(st.session_state[SELECTED_SYMBOLS_KEY])


def _set_selected_symbols(symbols):
    st.session_state[SELECTED_SYMBOLS_KEY] = _ordered_symbols(symbols)


def _toggle_symbol(code: str):
    selected = set(_selected_symbols())
    key = _symbol_key(code)
    if key in selected:
        selected.remove(key)
    else:
        selected.add(key)
    _set_selected_symbols(selected)
    st.rerun()


def _select_sector(codes: list[str]):
    selected = set(_selected_symbols())
    selected.update(_symbol_key(code) for code in codes)
    _set_selected_symbols(selected)
    st.rerun()


def _clear_sector(codes: list[str]):
    selected = set(_selected_symbols())
    selected.difference_update(_symbol_key(code) for code in codes)
    _set_selected_symbols(selected)
    st.rerun()


def _select_all_symbols():
    all_codes = [code for codes in _symbol_groups().values() for code in codes]
    _set_selected_symbols(all_codes)
    st.rerun()


def _clear_all_symbols():
    _set_selected_symbols([])
    st.rerun()


def _symbol_pool_selector() -> list[str]:
    st.markdown("##### 品种池 (Universe)")
    selected = set(_selected_symbols())
    all_groups = _symbol_groups()
    all_count = sum(len(codes) for codes in all_groups.values())

    summary_col, all_col, clear_col = st.columns([5, 1, 1])
    summary_col.caption(f"已选择 {len(selected)} / {all_count} 个品种。按钮点亮表示该品种会加入本次回测。")
    if all_col.button("全品种", use_container_width=True):
        _select_all_symbols()
    if clear_col.button("清空", use_container_width=True):
        _clear_all_symbols()

    for sector, codes in all_groups.items():
        sector_keys = {_symbol_key(code) for code in codes}
        selected_in_sector = len(selected.intersection(sector_keys))
        header_col, select_col, clear_sector_col = st.columns([5, 1, 1])
        header_col.markdown(f"**{sector} ({selected_in_sector}/{len(codes)})**")
        if select_col.button("全选", key=f"select_sector_{sector}", use_container_width=True):
            _select_sector(codes)
        if clear_sector_col.button("清除", key=f"clear_sector_{sector}", use_container_width=True):
            _clear_sector(codes)

        per_row = 8
        for start in range(0, len(codes), per_row):
            row_codes = codes[start:start + per_row]
            columns = st.columns(per_row)
            for index, code in enumerate(row_codes):
                key = _symbol_key(code)
                button_type = "primary" if key in selected else "secondary"
                if columns[index].button(
                    code,
                    key=f"toggle_symbol_{key}",
                    type=button_type,
                    use_container_width=True,
                ):
                    _toggle_symbol(code)

    ordered_selected = _selected_symbols()
    st.caption("当前品种池: " + (", ".join(ordered_selected) if ordered_selected else "未选择品种"))
    return ordered_selected


def _parse_dashboard_url(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("DASHBOARD_URL:"):
            return line.split(":", 1)[1].strip()
    return ""


def _post_report_update(url: str):
    if not url:
        return
    embedded = _is_embedded()
    if not embedded:
        webbrowser.open(url)
    components.html(
        f"""
        <script>
            const message = {{type: 'backtest-report-updated', url: {json.dumps(url)}}};
            window.parent.postMessage(message, '*');
            window.top.postMessage(message, '*');
        </script>
        """,
        height=0,
    )


def _run_backtest(dm: BacktestDataManager, config: dict, timeout_seconds: int):
    config_path = dm.write_config(config)
    command = dm.config_command(config_path)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    timeout = timeout_seconds if timeout_seconds > 0 else None
    with st.status("正在运行回测...", expanded=False) as status:
        st.write("正在调用回测引擎，完成后会生成最新 HTML 报告。")
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        st.session_state["last_run_output"] = output[-12000:]

        if proc.returncode != 0:
            status.update(label="回测失败", state="error")
            st.error(f"回测进程退出码: {proc.returncode}")
            return ""

        dashboard_url = _parse_dashboard_url(proc.stdout or "")
        status.update(label="回测完成", state="complete")
        return dashboard_url


def _market_fields():
    st.markdown("#### 市场与区间 (Market)")
    symbols = _symbol_pool_selector()

    cap_col, freq_col, data_col, start_col, end_col = st.columns([1.3, 1, 1.2, 1, 1])
    initial_capital = cap_col.number_input(
        "初始资金",
        min_value=10_000.0,
        value=5_000_000.0,
        step=50_000.0,
    )
    freq = freq_col.selectbox("周期", ["1d", "5m", "1m", "tick"], format_func=format_with_mapping(FREQ_LABELS))
    data_type = data_col.selectbox(
        "数据类型",
        ["main", "main_adj", "index", "all"],
        format_func=format_with_mapping(DATA_TYPE_LABELS),
    )
    start_date = start_col.date_input("开始日期", value=date(2021, 1, 1))
    end_date = end_col.date_input("结束日期", value=date(2022, 1, 1))

    return {
        "symbols": symbols,
        "freq": freq,
        "data_type": data_type,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "initial_capital": float(initial_capital),
    }


def _general_signal_fields():
    st.markdown("#### 仓位规则 (Sizing)")
    size_1, size_2, size_3, size_4 = st.columns(4)
    sizing_mode = size_1.selectbox(
        "开仓方式",
        list(SIZING_LABELS),
        index=list(SIZING_LABELS).index("equity_pct"),
        format_func=format_with_mapping(SIZING_LABELS),
    )
    default_value = 0.03 if sizing_mode.endswith("_pct") else 1.0
    sizing_value = size_2.number_input("参数值", min_value=0.0, value=float(default_value), step=0.01, format="%.4f")
    min_volume = size_3.number_input("最小手数", min_value=0, value=1, step=1)
    max_volume_raw = size_4.number_input("最大手数 (0=不限)", min_value=0, value=0, step=1)

    st.markdown("#### 执行规则 (Execution)")
    exe_1, exe_2, exe_3, exe_4 = st.columns(4)
    order_type = exe_1.selectbox("订单类型", ["market", "limit"], format_func=format_with_mapping(ORDER_TYPE_LABELS))
    price_field = exe_2.selectbox("参考价格", ["close", "open", "high", "low"], index=0)
    slippage_ticks = exe_3.number_input("市价滑点 (跳)", min_value=0.0, value=0.5, step=0.5)
    limit_mode = exe_4.selectbox(
        "限价模式",
        ["at_close", "better_ticks", "worse_ticks"],
        format_func=format_with_mapping(LIMIT_MODE_LABELS),
    )
    limit_ticks = 0.0
    if order_type == "limit":
        limit_ticks = st.number_input("限价偏移跳数", min_value=0.0, value=0.0, step=0.5)
        st.caption("限价单本身不额外叠加成交滑点；这里的市价滑点只影响市价单。")

    st.markdown("#### 平仓规则 (Exit)")
    exit_1, exit_2, exit_3 = st.columns(3)
    close_pct = exit_1.slider("信号为 0 时平仓比例", min_value=0.0, max_value=1.0, value=1.0, step=0.05)
    allow_reverse = exit_2.checkbox("允许反手", value=True)
    respect_pending_orders = exit_3.checkbox("考虑未成交挂单", value=True)

    return {
        "sizing_mode": sizing_mode,
        "sizing_value": float(sizing_value),
        "min_volume": int(min_volume),
        "max_volume": None if int(max_volume_raw) <= 0 else int(max_volume_raw),
        "round_lot": 1,
        "order_type": order_type,
        "price_field": price_field,
        "slippage_ticks": float(slippage_ticks),
        "limit_mode": limit_mode,
        "limit_ticks": float(limit_ticks),
        "close_pct": float(close_pct),
        "allow_reverse": bool(allow_reverse),
        "respect_pending_orders": bool(respect_pending_orders),
        "record_signals": True,
    }


def _strategy_parameter_fields(strategy_key: str):
    st.markdown("#### 策略逻辑参数 (Strategy Logic)")

    if strategy_key in {"general_multi_ma", "dual_ma"}:
        col_1, col_2 = st.columns(2)
        return {
            "fast_window": int(col_1.number_input("快均线窗口", min_value=1, max_value=250, value=10, step=1)),
            "slow_window": int(col_2.number_input("慢均线窗口", min_value=2, max_value=500, value=30, step=1)),
        }

    if strategy_key == "breakout_pyramid":
        col_1, col_2, col_3, col_4 = st.columns(4)
        return {
            "lookback": int(col_1.number_input("突破回看窗口", min_value=2, value=20, step=1)),
            "add_scale": float(col_2.number_input("每次增仓强度", min_value=0.1, value=1.0, step=0.1, format="%.2f")),
            "max_position_scale": float(col_3.number_input("最大仓位强度", min_value=0.1, value=4.0, step=0.5, format="%.2f")),
            "allow_short": bool(col_4.checkbox("允许做空", value=True)),
        }

    if strategy_key in {"composite_factor", "cross_momentum"}:
        col_1, col_2, col_3 = st.columns(3)
        return {
            "rebalance_period": int(col_1.number_input("调仓周期", min_value=1, value=5, step=1)),
            "top_k": int(col_2.number_input("多空数量", min_value=1, value=2, step=1)),
            "signal_scale": float(col_3.number_input("单腿信号强度", min_value=0.1, value=1.0, step=0.1, format="%.2f")),
        }

    st.caption("该策略没有额外逻辑参数。")
    return {}


def _guide():
    st.markdown(
        """
### 使用路径 (Usage)

- 推荐新策略继承 `GeneralSignalStrategy`，策略只负责输出方向或目标仓位信号。
- 策略输出统一使用 `signal intent`：`signal` 表示方向，`position_mode` 表示目标仓位、增减仓或全平。
- 仓位由 `sizing` 控制：固定手数、固定保证金、固定名义金额、初始资金比例、当前权益比例、可用资金比例。
- 执行由 `execution` 控制：市价单使用 `slippage_ticks`，限价单使用 `limit_mode` 和 `ticks`。
- 平仓由 `exit` 控制：`close_pct` 决定普通退出信号下平掉多少比例，策略也可以在高级信号里指定 `close_volume`。
- 新策略接入配置页时，优先编辑 `ui/run_from_config.py` 的 `STRATEGY_SPECS` 和对应 builder。
        """
    )


def main():
    dm = BacktestDataManager()
    if not _is_embedded():
        st.markdown(
            """
            <div class="main-header">
                <p class="main-title">回测配置中心 (Backtest Configuration)</p>
                <p class="main-caption">配置参数、运行回测，并打开生成的 HTML 报告。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    available = available_strategy_specs()
    if not available:
        st.error("未发现可运行策略，请检查 strategy 目录。")
        return

    config_tab, guide_tab = st.tabs(["参数配置 (Configuration)", "策略说明 (Guide)"])
    with config_tab:
        strategy_keys = [item["key"] for item in available]
        default_index = strategy_keys.index("general_multi_ma") if "general_multi_ma" in strategy_keys else 0

        strategy_key = st.selectbox(
            "策略",
            strategy_keys,
            index=default_index,
            format_func=_strategy_label,
        )

        config = {
            "strategy": strategy_key,
            **_market_fields(),
        }
        config.update(_strategy_parameter_fields(strategy_key))
        config.update(_general_signal_fields())

        col_1, col_2 = st.columns([1, 3])
        enable_main_rollover = col_1.checkbox("启用主力换月", value=True)
        timeout_seconds = int(col_2.number_input("运行超时秒数 (0=不限)", min_value=0, value=0, step=60))
        config["enable_main_rollover"] = bool(enable_main_rollover)

        can_run = bool(config["symbols"])
        submitted = st.button(
            "按当前参数运行回测",
            type="primary",
            use_container_width=True,
            disabled=not can_run,
        )

        if not can_run:
            st.warning("请至少选择一个品种。")

        if submitted:
            try:
                dashboard_url = _run_backtest(dm, config, timeout_seconds)
                if dashboard_url:
                    st.success("回测完成，报告已生成。")
                    st.markdown(f"[打开最新报告]({dashboard_url})")
                    _post_report_update(dashboard_url)
                else:
                    st.warning("回测完成但未获取到报告路径，请查看运行输出。")
            except subprocess.TimeoutExpired:
                st.error("回测超时，进程已终止。可增大超时秒数或设置为 0。")
            except Exception as exc:
                st.error(f"运行失败: {exc}")

        if st.session_state.get("last_run_output"):
            with st.expander("最近一次运行输出", expanded=False):
                st.code(st.session_state["last_run_output"])

    with guide_tab:
        _guide()


if __name__ == "__main__":
    main()
