# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import webbrowser
import importlib
from collections import OrderedDict
from dataclasses import dataclass
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

from config import NAME_TO_CODE, SYMBOL_DICT
import data_manager as data_manager_module
from labels import (
    DATA_TYPE_LABELS,
    FREQ_LABELS,
    LIMIT_MODE_LABELS,
    ORDER_TYPE_LABELS,
    SIZING_LABELS,
    format_with_mapping,
)
import run_from_config as run_config_module
from agent_panel import render_agent_panel
from pulse_panel import render_pulse_panel
from ui_config import ACTIVE_REPORT_CONFIG_PATH, APP_ICON, APP_TITLE, CUSTOM_CSS, LAYOUT, PROJECT_ROOT


data_manager_module = importlib.reload(data_manager_module)
run_config_module = importlib.reload(run_config_module)
BacktestDataManager = data_manager_module.BacktestDataManager
STRATEGY_SPECS = run_config_module.STRATEGY_SPECS
available_strategy_specs = run_config_module.available_strategy_specs

SELECTED_SYMBOLS_KEY = "selected_symbols"
ACTIVE_CONFIG_SOURCE_MTIME_KEY = "active_config_source_mtime"
RUN_NOTICE_KEY = "run_notice"
CONFIG_ASSISTANT_DRAFT_KEY = "config_assistant_draft"
CONFIG_ASSISTANT_PROMPT_KEY = "config_assistant_prompt"
RUN_LOCK_PATH = UI_DIR / ".runtime" / "backtest_run.lock"
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


def _load_active_config() -> tuple[float, dict]:
    if not ACTIVE_REPORT_CONFIG_PATH.exists():
        return 0.0, {}

    try:
        config = json.loads(ACTIVE_REPORT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0, {}

    try:
        mtime = ACTIVE_REPORT_CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    return mtime, config if isinstance(config, dict) else {}


def _date_from_config(config: dict, key: str, default: date) -> date:
    text = str(config.get(key) or "").strip()
    if not text:
        return default
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return default


def _option_index(options: list, value, default: int = 0) -> int:
    try:
        return options.index(value)
    except ValueError:
        return default


def _bool_from_config(config: dict, key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _strategy_form_defaults(strategy_key: str, active_config: dict) -> dict:
    defaults = dict(active_config)
    if strategy_key == "tick_anomaly_scalping":
        same_strategy = str(active_config.get("strategy", "")).strip().lower() == strategy_key
        defaults["freq"] = "tick"
        defaults["data_type"] = "main"
        defaults["order_type"] = "opponent"
        defaults["price_field"] = active_config.get("price_field", "mid_price") if same_strategy else "mid_price"
        defaults.setdefault("scalp_mode", "reversal")
        defaults.setdefault("sizing_mode", "fixed_volume")
        defaults.setdefault("sizing_value", 1.0)
        defaults.setdefault("min_volume", 1)
        defaults.setdefault("slippage_ticks", 0.0)
    if strategy_key == "utbot_stc_hull":
        defaults["freq"] = "5m"
        defaults["data_type"] = "main"
        defaults["order_type"] = "market"
        defaults["price_field"] = "close"
        defaults.setdefault("sizing_mode", "fixed_volume")
        defaults.setdefault("sizing_value", 1.0)
        defaults.setdefault("min_volume", 1)
        defaults.setdefault("max_volume", 1)
        defaults.setdefault("slippage_ticks", 0.5)
    if strategy_key == "vwap_band_reversion":
        defaults["freq"] = "5m"
        defaults["data_type"] = "main"
        defaults["order_type"] = "market"
        defaults["price_field"] = "close"
        defaults.setdefault("sizing_mode", "fixed_volume")
        defaults.setdefault("sizing_value", 1.0)
        defaults.setdefault("min_volume", 1)
        defaults.setdefault("max_volume", 1)
        defaults.setdefault("slippage_ticks", 0.5)
    if strategy_key == "donchian_atr_breakout":
        defaults["freq"] = "5m"
        defaults["data_type"] = "main"
        defaults["order_type"] = "market"
        defaults["price_field"] = "close"
        defaults.setdefault("sizing_mode", "equity_pct")
        defaults.setdefault("sizing_value", 0.10)
        defaults.setdefault("min_volume", 1)
        defaults.setdefault("max_volume", None)
        defaults.setdefault("slippage_ticks", 0.5)
    if strategy_key == "opening_range_acd":
        defaults["freq"] = "5m"
        defaults["data_type"] = "main"
        defaults["order_type"] = "market"
        defaults["price_field"] = "close"
        defaults.setdefault("sizing_mode", "equity_pct")
        defaults.setdefault("sizing_value", 0.10)
        defaults.setdefault("min_volume", 1)
        defaults.setdefault("max_volume", None)
        defaults.setdefault("slippage_ticks", 0.5)
    if strategy_key == "abs_ret_rolling_validation":
        defaults["freq"] = "1d"
        defaults["data_type"] = "all"
        defaults["order_type"] = "market"
        defaults["price_field"] = "close"
        defaults.setdefault("sizing_mode", "fixed_volume")
        defaults.setdefault("sizing_value", 1.0)
        defaults.setdefault("min_volume", 0)
        defaults.setdefault("max_volume", None)
        defaults.setdefault("slippage_ticks", 0.5)
        defaults.setdefault("close_pct", 1.0)
        defaults.setdefault("allow_reverse", True)
        defaults.setdefault("respect_pending_orders", True)
        defaults.setdefault("prediction_mode", "online_model")
        defaults.setdefault("model_available_date", "2025-07-01")
        defaults.setdefault("validation_mode", "monthly_prior")
        defaults.setdefault("absret_universe_mode", "all_predictions")
        defaults.setdefault("min_signal_confidence", 0.60)
        defaults.setdefault("daily_signal_cutoff_hour", 21)
    return defaults


@dataclass(frozen=True)
class ConfigAssistantResult:
    config: dict
    summary: list[str]
    warnings: list[str]


NAME_TO_SYMBOL_HINTS = {
    "黄金": "au",
    "沪金": "au",
    "白银": "ag",
    "沪银": "ag",
    "螺纹": "rb",
    "螺纹钢": "rb",
    "热卷": "hc",
    "铁矿": "i",
    "焦煤": "jm",
    "焦炭": "j",
    "铜": "cu",
    "铝": "al",
    "锌": "zn",
    "原油": "sc",
    "橡胶": "ru",
    "PTA": "ta",
    "pta": "ta",
    "甲醇": "ma",
    "纯碱": "sa",
    "玻璃": "fg",
    "豆粕": "m",
    "棕榈": "p",
    "苹果": "ap",
}

CONFIG_ASSISTANT_IGNORED_TOKENS = {
    "atr",
    "csv",
    "data",
    "json",
    "main",
    "tick",
    "vwap",
    "zscore",
}


def build_backtest_config_draft(
    prompt: str,
    active_config: dict,
    available_keys: set[str],
    *,
    today: date | None = None,
) -> ConfigAssistantResult:
    today = today or date.today()
    text = str(prompt or "").strip()
    lower_text = text.lower()
    config = dict(active_config or {})
    summary: list[str] = []
    warnings: list[str] = []

    strategy = _infer_strategy_key(lower_text, available_keys, config.get("strategy"))
    config["strategy"] = strategy
    summary.append(f"策略：{_strategy_label(strategy)}")

    symbols = _infer_prompt_symbols(text)
    if symbols:
        config["symbols"] = symbols
        summary.append(f"品种：{', '.join(_symbol_selection_label(symbol) for symbol in symbols)}")
    elif not _coerce_symbols(config.get("symbols")):
        config["symbols"] = ["au"]
        warnings.append("没有识别到品种，暂时使用 au。")
        summary.append(f"品种：{_symbol_selection_label('au')}")
    else:
        inherited_symbols = _coerce_symbols(config.get("symbols"))
        summary.append(
            "品种：沿用当前表单 "
            + ", ".join(_symbol_selection_label(symbol) for symbol in inherited_symbols)
        )
        warnings.append("没有从描述中识别到新的品种，草案会沿用当前表单已选品种。")

    freq = _infer_prompt_freq(lower_text) or str(config.get("freq") or "1d")
    config["freq"] = freq
    summary.append(f"周期：{freq}")

    data_type = _infer_prompt_data_type(lower_text, config.get("symbols"), freq)
    if data_type:
        config["data_type"] = data_type
    else:
        config["data_type"] = "main"
    summary.append(f"数据类型：{config['data_type']}")

    start_date, end_date = _infer_prompt_dates(text, lower_text, today=today)
    if start_date:
        config["start_date"] = start_date
    elif not config.get("start_date"):
        config["start_date"] = f"{today.year}-01-01"
        warnings.append("没有识别到开始日期，暂时使用今年年初。")
    if end_date:
        config["end_date"] = end_date
    elif not config.get("end_date"):
        config["end_date"] = today.isoformat()
        warnings.append("没有识别到结束日期，暂时使用今天。")
    summary.append(f"区间：{config['start_date']} 到 {config['end_date']}")

    config.setdefault("initial_capital", 5_000_000.0)
    config.setdefault("enable_main_rollover", True)
    _apply_profile_defaults(config, lower_text)
    _apply_strategy_defaults(config, strategy, lower_text)

    if _date_from_config(config, "start_date", today) > _date_from_config(config, "end_date", today):
        warnings.append("识别出的开始日期晚于结束日期，请手动检查。")

    return ConfigAssistantResult(config=config, summary=summary, warnings=warnings)


def _infer_strategy_key(text: str, available_keys: set[str], current_value) -> str:
    candidates: list[tuple[list[str], str]] = [
        (["zscore", "z-score", "均值回归", "反转"], "zscore_reversal"),
        (["双均线", "dual ma", "dual_ma"], "dual_ma"),
        (["均线", "ma"], "general_multi_ma"),
        (["donchian", "atr", "突破"], "donchian_atr_breakout"),
        (["vwap"], "vwap_band_reversion"),
        (["opening", "acd", "开盘区间"], "opening_range_acd"),
        (["tick", "scalp", "剥头皮", "异常"], "tick_anomaly_scalping"),
        (["factor", "因子", "截面"], "composite_factor"),
    ]
    for tokens, key in candidates:
        if key in available_keys and any(token in text for token in tokens):
            return key
    current = str(current_value or "").strip().lower()
    if current in available_keys:
        return current
    return "general_multi_ma" if "general_multi_ma" in available_keys else sorted(available_keys)[0]


def _infer_prompt_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for name, symbol in NAME_TO_SYMBOL_HINTS.items():
        if name in text and symbol not in symbols:
            symbols.append(symbol)

    for match in re.findall(r"\b([A-Za-z]{1,4}\d{3,4}|[A-Za-z]{1,4})\b", text):
        symbol = match.lower()
        if symbol in CONFIG_ASSISTANT_IGNORED_TOKENS:
            continue
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _infer_prompt_freq(text: str) -> str | None:
    if any(token in text for token in ["tick", "逐笔", "分笔"]):
        return "tick"
    if any(token in text for token in ["5m", "5min", "5分钟", "5分"]):
        return "5m"
    if any(token in text for token in ["1m", "1min", "1分钟", "1分"]):
        return "1m"
    if any(token in text for token in ["1d", "daily", "日线", "日频"]):
        return "1d"
    return None


def _infer_prompt_data_type(text: str, symbols, freq: str) -> str | None:
    if freq == "tick":
        return "main"
    if "指数" in text:
        return "index"
    if "复权" in text and freq == "1m":
        return "main_adj"
    symbol_list = _coerce_symbols(symbols)
    if symbol_list and any(re.search(r"\d", symbol) for symbol in symbol_list):
        return "all"
    if "单合约" in text or "具体合约" in text:
        return "all"
    return "main"


def _infer_prompt_dates(text: str, lower_text: str, *, today: date) -> tuple[str | None, str | None]:
    iso_dates = re.findall(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    normalized = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in iso_dates]
    if len(normalized) >= 2:
        return normalized[0], normalized[1]
    if len(normalized) == 1:
        return normalized[0], today.isoformat() if _mentions_current_time(lower_text) else None

    compact_dates = re.findall(r"\b(20\d{6})\b", text)
    compact = [f"{value[:4]}-{value[4:6]}-{value[6:8]}" for value in compact_dates]
    if len(compact) >= 2:
        return compact[0], compact[1]

    year_to_now = re.search(r"(20\d{2})\s*(?:年)?\s*(?:到|至|~|-)\s*(?:现在|今天|now|today)", text, re.I)
    if year_to_now:
        return f"{int(year_to_now.group(1)):04d}-01-01", today.isoformat()

    year_only = re.search(r"(20\d{2})\s*年", text)
    if year_only:
        year = int(year_only.group(1))
        return f"{year:04d}-01-01", today.isoformat() if _mentions_current_time(lower_text) else f"{year:04d}-12-31"

    if "今年" in text:
        return f"{today.year}-01-01", today.isoformat()
    if "去年" in text:
        year = today.year - 1
        return f"{year}-01-01", f"{year}-12-31"
    return None, None


def _mentions_current_time(text: str) -> bool:
    return any(token in text for token in ["now", "today", "现在", "今天", "当前", "至今", "到现在"])


def _apply_profile_defaults(config: dict, text: str) -> None:
    conservative = any(token in text for token in ["保守", "稳健", "小仓位", "低风险"])
    aggressive = any(token in text for token in ["激进", "高风险", "大仓位"])
    config.setdefault("sizing_mode", "equity_pct")
    config.setdefault("min_volume", 1)
    config.setdefault("order_type", "market")
    config.setdefault("price_field", "close")
    config.setdefault("slippage_ticks", 0.5)
    config.setdefault("limit_mode", "at_close")
    config.setdefault("close_pct", 1.0)
    config.setdefault("allow_reverse", True)
    config.setdefault("respect_pending_orders", True)

    if conservative:
        config["sizing_value"] = 0.02
        config["max_volume"] = 1
    elif aggressive:
        config["sizing_value"] = 0.08
        config["max_volume"] = None
    else:
        config.setdefault("sizing_value", 0.03)
        config.setdefault("max_volume", None)


def _apply_strategy_defaults(config: dict, strategy: str, text: str) -> None:
    conservative = any(token in text for token in ["保守", "稳健", "小仓位", "低风险"])
    aggressive = any(token in text for token in ["激进", "高风险", "大仓位"])
    if strategy in {"general_multi_ma", "dual_ma"}:
        config.setdefault("fast_window", 10 if not conservative else 20)
        config.setdefault("slow_window", 30 if not conservative else 60)
    elif strategy == "zscore_reversal":
        config.setdefault("lookback", 20 if conservative else 10)
        config.setdefault("entry_z", 2.6 if conservative else (1.8 if aggressive else 2.1))
        config.setdefault("first_exit_z", 0.0)
        config.setdefault("final_exit_z", 1.0)
    elif strategy == "donchian_atr_breakout":
        config.setdefault("donchian_window", 200 if conservative else 144)
        config.setdefault("atr_period", 72)
        config.setdefault("trend_window", 240)
        config.setdefault("breakout_buffer_ticks", 3.0 if conservative else 2.0)


def _render_config_assistant(dm: BacktestDataManager, active_config: dict, available_keys: set[str]) -> dict | None:
    st.markdown("#### Agent 配置助手（试验）")
    st.caption("先把自然语言变成当前表单配置草案；确认后再应用，不会自动运行回测。")

    prompt = st.text_area(
        "描述你想测试的回测",
        key=CONFIG_ASSISTANT_PROMPT_KEY,
        height=72,
        placeholder="例如：我想测黄金日线，2024 到现在，ZScore 反转策略，保守一点",
    )
    action_col, clear_col = st.columns([1, 4])
    if action_col.button("生成配置草案", type="primary", use_container_width=True):
        if not str(prompt or "").strip():
            st.session_state.pop(CONFIG_ASSISTANT_DRAFT_KEY, None)
            st.warning("请先在文本框里输入你的需求。灰色示例只是占位提示，不会自动作为输入。")
        else:
            result = build_backtest_config_draft(prompt, active_config, available_keys)
            st.session_state[CONFIG_ASSISTANT_DRAFT_KEY] = {
                "config": result.config,
                "summary": result.summary,
                "warnings": result.warnings,
            }
    if clear_col.button("清空配置草案", use_container_width=True):
        st.session_state.pop(CONFIG_ASSISTANT_DRAFT_KEY, None)
        st.rerun()

    draft = st.session_state.get(CONFIG_ASSISTANT_DRAFT_KEY)
    if not isinstance(draft, dict):
        return None
    for item in draft.get("summary", []):
        st.markdown(f"- {item}")
    for item in draft.get("warnings", []):
        st.warning(item)
    with st.expander("查看配置草案 JSON", expanded=False):
        st.json(draft.get("config", {}))
    apply_col, _ = st.columns([1, 4])
    if apply_col.button("应用到当前表单", use_container_width=True):
        config = draft.get("config", {})
        if isinstance(config, dict):
            active_path = dm.write_active_config(config)
            try:
                source_mtime = active_path.stat().st_mtime
            except OSError:
                source_mtime = None
            _set_selected_symbols(_coerce_symbols(config.get("symbols")), source_mtime=source_mtime)
            st.session_state.pop(CONFIG_ASSISTANT_DRAFT_KEY, None)
            st.success("Agent 配置草案已应用到当前表单，请检查后再运行回测。")
            return config
    return None


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


def _symbol_display_name(code: str) -> str:
    key = _symbol_key(code)
    matches = [
        str(name)
        for name, mapped_code in NAME_TO_CODE.items()
        if _symbol_key(mapped_code) == key
    ]
    if not matches:
        return ""
    return sorted(matches, key=lambda item: (len(item), item))[0]


def _symbol_button_label(code: str) -> str:
    name = _symbol_display_name(code)
    return f"{name} {code}" if name else str(code)


def _symbol_selection_label(code: str) -> str:
    return _symbol_button_label(code)


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
    mtime, config = _load_active_config()
    symbols = _coerce_symbols(config.get("symbols"))
    if symbols:
        return mtime, _ordered_symbols(symbols)
    return 0.0, []


def _selected_symbols() -> list[str]:
    active_mtime, active_symbols = _active_report_symbols()
    loaded_mtime = float(st.session_state.get(ACTIVE_CONFIG_SOURCE_MTIME_KEY, -1.0))
    if SELECTED_SYMBOLS_KEY not in st.session_state or active_mtime > loaded_mtime:
        st.session_state[SELECTED_SYMBOLS_KEY] = active_symbols
        st.session_state[ACTIVE_CONFIG_SOURCE_MTIME_KEY] = active_mtime
    return list(st.session_state[SELECTED_SYMBOLS_KEY])


def _set_selected_symbols(symbols, source_mtime: float | None = None):
    st.session_state[SELECTED_SYMBOLS_KEY] = _ordered_symbols(symbols)
    if source_mtime is not None:
        st.session_state[ACTIVE_CONFIG_SOURCE_MTIME_KEY] = float(source_mtime)


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
                    _symbol_button_label(code),
                    key=f"toggle_symbol_{key}",
                    type=button_type,
                    use_container_width=True,
                ):
                    _toggle_symbol(code)

    ordered_selected = _selected_symbols()
    selected_labels = [_symbol_selection_label(code) for code in ordered_selected]
    st.caption("当前品种池: " + (", ".join(selected_labels) if selected_labels else "未选择品种"))
    return ordered_selected


def _parse_dashboard_url(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("DASHBOARD_URL:"):
            return line.split(":", 1)[1].strip()
    return ""


def _read_run_lock(max_age_seconds: int = 24 * 60 * 60) -> dict:
    if not RUN_LOCK_PATH.exists():
        return {}
    try:
        age = time.time() - RUN_LOCK_PATH.stat().st_mtime
    except OSError:
        return {}
    if age > max_age_seconds:
        try:
            RUN_LOCK_PATH.unlink()
        except OSError:
            pass
        return {}
    try:
        data = json.loads(RUN_LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _write_run_lock(
    pid: int,
    config_path: Path,
    command: list[str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
):
    RUN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUN_LOCK_PATH.write_text(
        json.dumps(
            {
                "pid": int(pid),
                "config_path": str(config_path),
                "command": command,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "timeout_seconds": int(timeout_seconds),
                "started_at_epoch": time.time(),
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _clear_run_lock(pid: int | None = None):
    if not RUN_LOCK_PATH.exists():
        return
    if pid is not None:
        lock = _read_run_lock()
        if lock and int(lock.get("pid", -1)) != int(pid):
            return
    try:
        RUN_LOCK_PATH.unlink()
    except OSError:
        pass


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0 and str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _run_output_paths(config_path: Path) -> tuple[Path, Path]:
    stem = config_path.with_suffix("")
    return (
        stem.with_name(stem.name + "_stdout.log"),
        stem.with_name(stem.name + "_stderr.log"),
    )


def _read_run_output(lock: dict) -> str:
    chunks = []
    for key in ("stdout_path", "stderr_path"):
        path_text = lock.get(key)
        if not path_text:
            continue
        path = Path(path_text)
        if path.exists():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(chunks)


def _stop_locked_backtest(lock: dict) -> tuple[bool, str]:
    try:
        pid = int(lock.get("pid", 0))
    except (TypeError, ValueError):
        _clear_run_lock()
        return False, "运行锁中的 PID 无效，已清理运行锁。"

    if pid <= 0:
        _clear_run_lock()
        return False, "运行锁中的 PID 无效，已清理运行锁。"

    if not _pid_is_running(pid):
        _clear_run_lock(pid)
        return True, f"回测进程 PID={pid} 已不存在，已清理运行锁。"

    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        if result.returncode != 0:
            return False, f"停止失败，PID={pid}。{output}"
    else:
        try:
            os.kill(pid, 15)
        except OSError as exc:
            return False, f"停止失败，PID={pid}。{exc}"

    _clear_run_lock(pid)
    return True, f"已停止当前回测进程，PID={pid}。"


def _finish_completed_run(lock: dict) -> str:
    output = _read_run_output(lock)
    if output:
        st.session_state["last_run_output"] = output[-12000:]
    try:
        pid = int(lock.get("pid", 0))
    except (TypeError, ValueError):
        pid = None
    _clear_run_lock(pid)
    return _parse_dashboard_url(output)


def _start_backtest(dm: BacktestDataManager, config: dict, timeout_seconds: int) -> tuple[bool, str]:
    existing_lock = _read_run_lock()
    if existing_lock:
        return False, f"已有回测进程正在运行，PID={existing_lock.get('pid', '-')}。"

    dm.write_active_config(config)
    config_path = dm.write_config(config)
    command = dm.config_command(config_path)
    stdout_path, stderr_path = _run_output_paths(config_path)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    stdout = open(stdout_path, "w", encoding="utf-8", errors="replace")
    stderr = open(stderr_path, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=stdout,
            stderr=stderr,
            env=env,
        )
    finally:
        stdout.close()
        stderr.close()

    _write_run_lock(proc.pid, config_path, command, stdout_path, stderr_path, timeout_seconds)
    return True, f"回测已启动，PID={proc.pid}。运行期间可以刷新状态或停止当前回测。"


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


def _start_hidden_run_status_poll(interval_seconds: int = 3):
    """Poll run status in a hidden iframe so the visible configuration page stays stable."""
    interval_ms = max(1, int(interval_seconds)) * 1000
    components.html(
        f"""
        <script>
            window.setTimeout(() => {{
                let baseUrl = 'http://localhost:8501/?embed=1';
                try {{
                    const parentUrl = new URL(window.parent.location.href);
                    if (parentUrl.protocol === 'http:' || parentUrl.protocol === 'https:') {{
                        baseUrl = parentUrl.origin + parentUrl.pathname + '?embed=1';
                    }}
                }} catch (error) {{
                    baseUrl = 'http://localhost:8501/?embed=1';
                }}
                const url = new URL(baseUrl);
                url.searchParams.set('poll', '1');
                url.searchParams.set('ts', Date.now().toString());
                const frame = document.createElement('iframe');
                frame.src = url.toString();
                frame.style.display = 'none';
                frame.style.width = '0';
                frame.style.height = '0';
                frame.style.border = '0';
                frame.setAttribute('aria-hidden', 'true');
                document.body.appendChild(frame);
            }}, {interval_ms});
        </script>
        """,
        height=0,
    )


def _schedule_hidden_poll_reload(interval_seconds: int = 3):
    """Reload only the hidden polling iframe while the backtest process is alive."""
    interval_ms = max(1, int(interval_seconds)) * 1000
    components.html(
        f"""
        <script>
            window.setTimeout(() => {{
                try {{
                    window.parent.location.reload();
                }} catch (error) {{
                    window.location.reload();
                }}
            }}, {interval_ms});
        </script>
        """,
        height=0,
    )


def _render_poll_response():
    """Minimal hidden-frame endpoint used to detect background backtest completion."""
    running_lock = _read_run_lock()
    if not running_lock:
        return

    try:
        running_pid = int(running_lock.get("pid", 0))
    except (TypeError, ValueError):
        running_pid = 0

    if _pid_is_running(running_pid):
        _schedule_hidden_poll_reload(3)
        return

    dashboard_url = _finish_completed_run(running_lock)
    if dashboard_url:
        _post_report_update(dashboard_url)


def _market_fields(active_config: dict):
    st.markdown("#### 市场与区间 (Market)")
    symbols = _symbol_pool_selector()

    cap_col, freq_col, data_col, start_col, end_col = st.columns([1.3, 1, 1.2, 1, 1])
    initial_capital = cap_col.number_input(
        "初始资金",
        min_value=10_000.0,
        value=float(active_config.get("initial_capital", 5_000_000.0)),
        step=50_000.0,
    )
    freq_options = ["1d", "5m", "1m", "tick"]
    freq = freq_col.selectbox(
        "周期",
        freq_options,
        index=_option_index(freq_options, active_config.get("freq", "1d")),
        format_func=format_with_mapping(FREQ_LABELS),
    )
    data_type_options = ["main"] if freq == "tick" else ["main", "main_adj", "index", "all"]
    data_type = data_col.selectbox(
        "数据类型",
        data_type_options,
        index=_option_index(data_type_options, active_config.get("data_type", "main")),
        format_func=format_with_mapping(DATA_TYPE_LABELS),
    )
    start_date = start_col.date_input(
        "开始日期",
        value=_date_from_config(active_config, "start_date", date(2021, 1, 1)),
        key="backtest_start_date",
    )
    end_date = end_col.date_input(
        "结束日期",
        value=_date_from_config(active_config, "end_date", date(2022, 1, 1)),
        key="backtest_end_date",
    )

    return {
        "symbols": symbols,
        "freq": freq,
        "data_type": data_type,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "initial_capital": float(initial_capital),
    }


def _general_signal_fields(active_config: dict, freq: str | None = None):
    st.markdown("#### 仓位规则 (Sizing)")
    size_1, size_2, size_3, size_4 = st.columns(4)
    sizing_options = list(SIZING_LABELS)
    sizing_mode = size_1.selectbox(
        "开仓方式",
        sizing_options,
        index=_option_index(sizing_options, active_config.get("sizing_mode", "equity_pct")),
        format_func=format_with_mapping(SIZING_LABELS),
    )
    default_value = float(active_config.get("sizing_value", 0.03 if sizing_mode.endswith("_pct") else 1.0))
    sizing_value = size_2.number_input("参数值", min_value=0.0, value=float(default_value), step=0.01, format="%.4f")
    min_volume = size_3.number_input("最小手数", min_value=0, value=int(active_config.get("min_volume", 1)), step=1)
    max_volume_default = active_config.get("max_volume")
    max_volume_default = 0 if max_volume_default in (None, "", "None") else int(max_volume_default)
    max_volume_raw = size_4.number_input("最大手数 (0=不限)", min_value=0, value=max_volume_default, step=1)

    st.markdown("#### 执行规则 (Execution)")
    exe_1, exe_2, exe_3, exe_4 = st.columns(4)
    is_tick_freq = str(freq or active_config.get("freq", "1d")).lower() == "tick"
    order_type_options = ["market", "opponent", "limit"] if is_tick_freq else ["market", "limit"]
    order_type = exe_1.selectbox(
        "订单类型",
        order_type_options,
        index=_option_index(order_type_options, active_config.get("order_type", "market")),
        format_func=format_with_mapping(ORDER_TYPE_LABELS),
    )
    if is_tick_freq:
        price_field_options = ["close", "last_price", "mid_price", "bid_price_1", "ask_price_1"]
    else:
        price_field_options = ["close", "open", "high", "low"]
    price_field = exe_2.selectbox(
        "参考价格",
        price_field_options,
        index=_option_index(price_field_options, active_config.get("price_field", "close")),
    )
    slippage_ticks = exe_3.number_input(
        "市价滑点 (跳)",
        min_value=0.0,
        value=float(active_config.get("slippage_ticks", 0.5)),
        step=0.5,
    )
    limit_mode_options = ["at_close", "better_ticks", "worse_ticks"]
    limit_mode = exe_4.selectbox(
        "限价模式",
        limit_mode_options,
        index=_option_index(limit_mode_options, active_config.get("limit_mode", "at_close")),
        format_func=format_with_mapping(LIMIT_MODE_LABELS),
    )
    limit_ticks = 0.0
    if order_type == "limit":
        limit_ticks = st.number_input(
            "限价偏移跳数",
            min_value=0.0,
            value=float(active_config.get("limit_ticks", 0.0)),
            step=0.5,
        )
        st.caption("限价单本身不额外叠加成交滑点；这里的市价滑点只影响市价单。")

    st.markdown("#### 平仓规则 (Exit)")
    exit_1, exit_2, exit_3 = st.columns(3)
    close_pct = exit_1.slider(
        "信号为 0 时平仓比例",
        min_value=0.0,
        max_value=1.0,
        value=float(active_config.get("close_pct", 1.0)),
        step=0.05,
    )
    allow_reverse = exit_2.checkbox("允许反手", value=_bool_from_config(active_config, "allow_reverse", True))
    respect_pending_orders = exit_3.checkbox(
        "考虑未成交挂单",
        value=_bool_from_config(active_config, "respect_pending_orders", True),
    )

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


def _strategy_parameter_fields(strategy_key: str, active_config: dict):
    st.markdown("#### 策略逻辑参数 (Strategy Logic)")

    if strategy_key in {"general_multi_ma", "dual_ma"}:
        col_1, col_2 = st.columns(2)
        return {
            "fast_window": int(col_1.number_input(
                "快均线窗口",
                min_value=1,
                max_value=250,
                value=int(active_config.get("fast_window", 10)),
                step=1,
            )),
            "slow_window": int(col_2.number_input(
                "慢均线窗口",
                min_value=2,
                max_value=500,
                value=int(active_config.get("slow_window", 30)),
                step=1,
            )),
        }

    if strategy_key == "breakout_pyramid":
        col_1, col_2, col_3, col_4 = st.columns(4)
        return {
            "lookback": int(col_1.number_input(
                "突破回看窗口",
                min_value=2,
                value=int(active_config.get("lookback", 20)),
                step=1,
            )),
            "add_scale": float(col_2.number_input(
                "每次增仓强度",
                min_value=0.1,
                value=float(active_config.get("add_scale", 1.0)),
                step=0.1,
                format="%.2f",
            )),
            "max_position_scale": float(col_3.number_input(
                "最大仓位强度",
                min_value=0.1,
                value=float(active_config.get("max_position_scale", 4.0)),
                step=0.5,
                format="%.2f",
            )),
            "allow_short": bool(col_4.checkbox(
                "允许做空",
                value=_bool_from_config(active_config, "allow_short", True),
            )),
        }

    if strategy_key == "zscore_reversal":
        col_1, col_2, col_3, col_4 = st.columns(4)
        return {
            "lookback": int(col_1.number_input(
                "回看窗口",
                min_value=2,
                value=int(active_config.get("lookback", 10)),
                step=1,
            )),
            "entry_z": float(col_2.number_input(
                "开仓 Z 值",
                min_value=0.1,
                value=float(active_config.get("entry_z", 2.1)),
                step=0.1,
                format="%.2f",
            )),
            "first_exit_z": float(col_3.number_input(
                "半平 Z 值",
                value=float(active_config.get("first_exit_z", 0.0)),
                step=0.1,
                format="%.2f",
            )),
            "final_exit_z": float(col_4.number_input(
                "全平 Z 值",
                min_value=0.1,
                value=float(active_config.get("final_exit_z", 1.0)),
                step=0.1,
                format="%.2f",
            )),
        }

    if strategy_key == "tick_anomaly_scalping":
        mode_options = ["reversal", "fade", "follow", "fade_after_pause"]
        col_1, col_2, col_3, col_4 = st.columns(4)
        col_5, col_6, col_7, col_8 = st.columns(4)
        col_9, col_10, col_11, col_12 = st.columns(4)
        return {
            "scalp_mode": col_1.selectbox(
                "Mode",
                mode_options,
                index=_option_index(mode_options, active_config.get("scalp_mode", "reversal")),
            ),
            "shock_window_seconds": float(col_2.number_input(
                "Shock Window Seconds",
                min_value=0.5,
                value=float(active_config.get("shock_window_seconds", 3.0)),
                step=0.5,
                format="%.2f",
            )),
            "lookback_days": float(col_3.number_input(
                "Lookback Days",
                min_value=0.1,
                value=float(active_config.get("lookback_days", 10.0)),
                step=0.5,
                format="%.2f",
            )),
            "tail_prob": float(col_4.number_input(
                "Tail Probability",
                min_value=0.0001,
                max_value=0.2,
                value=float(active_config.get("tail_prob", 0.001)),
                step=0.0005,
                format="%.4f",
            )),
            "min_move_bps": float(col_5.number_input(
                "Min Move Bps",
                min_value=0.0,
                value=float(active_config.get("min_move_bps", 4.0)),
                step=0.5,
                format="%.2f",
            )),
            "hold_seconds": float(col_6.number_input(
                "Hold Seconds",
                min_value=0.1,
                value=float(active_config.get("hold_seconds", 20.0)),
                step=0.5,
                format="%.2f",
            )),
            "take_profit_ticks": float(col_7.number_input(
                "Take Profit Ticks",
                min_value=0.0,
                value=float(active_config.get("take_profit_ticks", 8.0)),
                step=0.5,
                format="%.2f",
            )),
            "stop_loss_ticks": float(col_8.number_input(
                "Stop Loss Ticks",
                min_value=0.0,
                value=float(active_config.get("stop_loss_ticks", 5.0)),
                step=0.5,
                format="%.2f",
            )),
            "reversal_confirm_seconds": float(col_9.number_input(
                "Confirm Seconds",
                min_value=0.1,
                value=float(active_config.get("reversal_confirm_seconds", 1.5)),
                step=0.5,
                format="%.2f",
            )),
            "reversal_retrace_ratio": float(col_10.number_input(
                "Retrace Ratio",
                min_value=0.0,
                max_value=1.0,
                value=float(active_config.get("reversal_retrace_ratio", 0.4)),
                step=0.05,
                format="%.2f",
            )),
            "warmup_days": float(col_11.number_input(
                "Warmup Days",
                min_value=0.0,
                value=float(active_config.get("warmup_days", 10.0)),
                step=1.0,
                format="%.1f",
            )),
            "min_history_samples": int(col_12.number_input(
                "Min Samples",
                min_value=0,
                value=int(active_config.get("min_history_samples", 5000)),
                step=500,
            )),
        }

    if strategy_key == "utbot_stc_hull":
        col_1, col_2, col_3, col_4 = st.columns(4)
        col_5, col_6, col_7, col_8 = st.columns(4)
        col_9, col_10, col_11, col_12 = st.columns(4)
        col_13, col_14, col_15 = st.columns(3)
        return {
            "hma_length": int(col_1.number_input(
                "HMA Length",
                min_value=2,
                value=int(active_config.get("hma_length", 55)),
                step=1,
            )),
            "atr_period": int(col_2.number_input(
                "UT ATR Period",
                min_value=1,
                value=int(active_config.get("atr_period", 10)),
                step=1,
            )),
            "ut_key_value": float(col_3.number_input(
                "UT Key Value",
                min_value=0.1,
                value=float(active_config.get("ut_key_value", 1.0)),
                step=0.1,
                format="%.2f",
            )),
            "stc_length": int(col_4.number_input(
                "STC Length",
                min_value=1,
                value=int(active_config.get("stc_length", 12)),
                step=1,
            )),
            "stc_fast": int(col_5.number_input(
                "STC Fast",
                min_value=1,
                value=int(active_config.get("stc_fast", 26)),
                step=1,
            )),
            "stc_slow": int(col_6.number_input(
                "STC Slow",
                min_value=1,
                value=int(active_config.get("stc_slow", 50)),
                step=1,
            )),
            "stc_factor": float(col_7.number_input(
                "STC Factor",
                min_value=0.01,
                max_value=1.0,
                value=float(active_config.get("stc_factor", 0.5)),
                step=0.05,
                format="%.2f",
            )),
            "stc_long_max": float(col_8.number_input(
                "STC Long Max",
                min_value=0.0,
                max_value=100.0,
                value=float(active_config.get("stc_long_max", 35.0)),
                step=1.0,
                format="%.1f",
            )),
            "stc_short_min": float(col_9.number_input(
                "STC Short Min",
                min_value=0.0,
                max_value=100.0,
                value=float(active_config.get("stc_short_min", 65.0)),
                step=1.0,
                format="%.1f",
            )),
            "take_profit_ticks": float(col_10.number_input(
                "Take Profit Ticks",
                min_value=0.0,
                value=float(active_config.get("take_profit_ticks", 24.0)),
                step=1.0,
                format="%.1f",
            )),
            "stop_loss_ticks": float(col_11.number_input(
                "Stop Loss Ticks",
                min_value=0.0,
                value=float(active_config.get("stop_loss_ticks", 16.0)),
                step=1.0,
                format="%.1f",
            )),
            "max_hold_bars": int(col_12.number_input(
                "Max Hold Bars",
                min_value=1,
                value=int(active_config.get("max_hold_bars", 36)),
                step=1,
            )),
            "cooldown_bars": int(col_13.number_input(
                "Cooldown Bars",
                min_value=0,
                value=int(active_config.get("cooldown_bars", 3)),
                step=1,
            )),
            "max_entries_per_symbol_per_day": int(col_14.number_input(
                "Max Entries/Day",
                min_value=1,
                value=int(active_config.get("max_entries_per_symbol_per_day", 20)),
                step=1,
            )),
            "require_price_above_hull": bool(st.checkbox(
                "Require price on Hull side",
                value=_bool_from_config(active_config, "require_price_above_hull", True),
            )),
            "exit_on_opposite_signal": bool(st.checkbox(
                "Exit on opposite signal",
                value=_bool_from_config(active_config, "exit_on_opposite_signal", True),
            )),
        }

    if strategy_key == "vwap_band_reversion":
        col_1, col_2, col_3, col_4 = st.columns(4)
        col_5, col_6, col_7, col_8 = st.columns(4)
        col_9, col_10, col_11, col_12 = st.columns(4)
        col_13 = st.columns(1)[0]
        return {
            "std_window": int(col_1.number_input(
                "Std Window",
                min_value=3,
                value=int(active_config.get("std_window", 48)),
                step=1,
            )),
            "entry_z": float(col_2.number_input(
                "Entry Z",
                min_value=0.1,
                value=float(active_config.get("entry_z", 2.0)),
                step=0.1,
                format="%.2f",
            )),
            "exit_z": float(col_3.number_input(
                "Exit Z",
                min_value=0.0,
                value=float(active_config.get("exit_z", 0.25)),
                step=0.05,
                format="%.2f",
            )),
            "min_bars_in_session": int(col_4.number_input(
                "Min Session Bars",
                min_value=1,
                value=int(active_config.get("min_bars_in_session", 12)),
                step=1,
            )),
            "min_std_ticks": float(col_5.number_input(
                "Min Std Ticks",
                min_value=0.0,
                value=float(active_config.get("min_std_ticks", 4.0)),
                step=0.5,
                format="%.1f",
            )),
            "max_vwap_slope_ticks": float(col_6.number_input(
                "Max VWAP Slope Ticks",
                min_value=0.0,
                value=float(active_config.get("max_vwap_slope_ticks", 6.0)),
                step=0.5,
                format="%.1f",
            )),
            "slope_window": int(col_7.number_input(
                "Slope Window",
                min_value=1,
                value=int(active_config.get("slope_window", 6)),
                step=1,
            )),
            "take_profit_ticks": float(col_8.number_input(
                "Take Profit Ticks",
                min_value=0.0,
                value=float(active_config.get("take_profit_ticks", 28.0)),
                step=1.0,
                format="%.1f",
            )),
            "stop_loss_ticks": float(col_9.number_input(
                "Stop Loss Ticks",
                min_value=0.0,
                value=float(active_config.get("stop_loss_ticks", 18.0)),
                step=1.0,
                format="%.1f",
            )),
            "max_hold_bars": int(col_10.number_input(
                "Max Hold Bars",
                min_value=1,
                value=int(active_config.get("max_hold_bars", 24)),
                step=1,
            )),
            "cooldown_bars": int(col_11.number_input(
                "Cooldown Bars",
                min_value=0,
                value=int(active_config.get("cooldown_bars", 3)),
                step=1,
            )),
            "max_entries_per_symbol_per_day": int(col_12.number_input(
                "Max Entries/Day",
                min_value=1,
                value=int(active_config.get("max_entries_per_symbol_per_day", 20)),
                step=1,
            )),
            "session_start_hour": int(col_13.number_input(
                "Session Start Hour",
                min_value=0,
                max_value=23,
                value=int(active_config.get("session_start_hour", 21)),
                step=1,
            )),
        }

    if strategy_key == "donchian_atr_breakout":
        col_1, col_2, col_3, col_4 = st.columns(4)
        col_5, col_6, col_7, col_8 = st.columns(4)
        col_9, col_10, col_11, col_12 = st.columns(4)
        return {
            "donchian_window": int(col_1.number_input(
                "Donchian Window",
                min_value=2,
                value=int(active_config.get("donchian_window", 144)),
                step=1,
            )),
            "atr_period": int(col_2.number_input(
                "ATR Period",
                min_value=2,
                value=int(active_config.get("atr_period", 72)),
                step=1,
            )),
            "trend_window": int(col_3.number_input(
                "Trend EMA Window",
                min_value=2,
                value=int(active_config.get("trend_window", 240)),
                step=1,
            )),
            "breakout_buffer_ticks": float(col_4.number_input(
                "Breakout Buffer Ticks",
                min_value=0.0,
                value=float(active_config.get("breakout_buffer_ticks", 2.0)),
                step=0.5,
                format="%.1f",
            )),
            "min_channel_atr": float(col_5.number_input(
                "Min Channel ATR",
                min_value=0.0,
                value=float(active_config.get("min_channel_atr", 1.8)),
                step=0.1,
                format="%.2f",
            )),
            "max_extension_atr": float(col_6.number_input(
                "Max Extension ATR",
                min_value=0.1,
                value=float(active_config.get("max_extension_atr", 1.5)),
                step=0.1,
                format="%.2f",
            )),
            "atr_stop_mult": float(col_7.number_input(
                "ATR Stop Mult",
                min_value=0.1,
                value=float(active_config.get("atr_stop_mult", 3.2)),
                step=0.1,
                format="%.2f",
            )),
            "max_hold_bars": int(col_8.number_input(
                "Max Hold Bars",
                min_value=1,
                value=int(active_config.get("max_hold_bars", 384)),
                step=1,
            )),
            "cooldown_bars": int(col_9.number_input(
                "Cooldown Bars",
                min_value=0,
                value=int(active_config.get("cooldown_bars", 18)),
                step=1,
            )),
            "max_entries_per_symbol_per_day": int(col_10.number_input(
                "Max Entries/Day",
                min_value=1,
                value=int(active_config.get("max_entries_per_symbol_per_day", 3)),
                step=1,
            )),
            "allowed_entry_hours": str(col_11.text_input(
                "Allowed Entry Hours",
                value=str(active_config.get("allowed_entry_hours", "9,10,13,21,22,23,0,1,2")),
            )),
            "exit_on_midline": bool(col_12.checkbox(
                "Exit on Midline",
                value=_bool_from_config(active_config, "exit_on_midline", True),
            )),
        }

    if strategy_key == "opening_range_acd":
        col_1, col_2, col_3, col_4 = st.columns(4)
        col_5, col_6, col_7, col_8 = st.columns(4)
        col_9, col_10, col_11, col_12 = st.columns(4)
        col_13, col_14 = st.columns(2)
        return {
            "opening_range_bars": int(col_1.number_input(
                "Opening Range Bars",
                min_value=1,
                value=int(active_config.get("opening_range_bars", 3)),
                step=1,
            )),
            "atr_period": int(col_2.number_input(
                "ATR Period",
                min_value=2,
                value=int(active_config.get("atr_period", 48)),
                step=1,
            )),
            "trend_window": int(col_3.number_input(
                "Trend EMA Window",
                min_value=2,
                value=int(active_config.get("trend_window", 144)),
                step=1,
            )),
            "breakout_buffer_ticks": float(col_4.number_input(
                "Breakout Buffer Ticks",
                min_value=0.0,
                value=float(active_config.get("breakout_buffer_ticks", 1.0)),
                step=0.5,
                format="%.1f",
            )),
            "min_range_atr": float(col_5.number_input(
                "Min Range ATR",
                min_value=0.0,
                value=float(active_config.get("min_range_atr", 0.5)),
                step=0.1,
                format="%.2f",
            )),
            "max_extension_atr": float(col_6.number_input(
                "Max Extension ATR",
                min_value=0.1,
                value=float(active_config.get("max_extension_atr", 1.2)),
                step=0.1,
                format="%.2f",
            )),
            "atr_stop_mult": float(col_7.number_input(
                "ATR Stop Mult",
                min_value=0.1,
                value=float(active_config.get("atr_stop_mult", 3.0)),
                step=0.1,
                format="%.2f",
            )),
            "trail_atr_mult": float(col_8.number_input(
                "Trail ATR Mult",
                min_value=0.1,
                value=float(active_config.get("trail_atr_mult", 4.0)),
                step=0.1,
                format="%.2f",
            )),
            "take_profit_atr": float(col_9.number_input(
                "Take Profit ATR (0=off)",
                min_value=0.0,
                value=float(active_config.get("take_profit_atr", 0.0)),
                step=0.5,
                format="%.2f",
            )),
            "max_hold_bars": int(col_10.number_input(
                "Max Hold Bars",
                min_value=1,
                value=int(active_config.get("max_hold_bars", 384)),
                step=1,
            )),
            "cooldown_bars": int(col_11.number_input(
                "Cooldown Bars",
                min_value=0,
                value=int(active_config.get("cooldown_bars", 18)),
                step=1,
            )),
            "max_entries_per_symbol_per_day": int(col_12.number_input(
                "Max Entries/Day",
                min_value=1,
                value=int(active_config.get("max_entries_per_symbol_per_day", 2)),
                step=1,
            )),
            "session_start_hours": str(col_13.text_input(
                "Session Start Hours",
                value=str(active_config.get("session_start_hours", "9,21")),
            )),
            "allowed_entry_hours": str(col_14.text_input(
                "Allowed Entry Hours",
                value=str(active_config.get("allowed_entry_hours", "9,10,21,22,23,0,1")),
            )),
            "exit_on_range_reentry": bool(st.checkbox(
                "Exit on Range Re-entry",
                value=_bool_from_config(active_config, "exit_on_range_reentry", False),
            )),
        }

    if strategy_key == "abs_ret_rolling_validation":
        col_1, col_2, col_3, col_4 = st.columns(4)
        col_5, col_6, col_7, col_8 = st.columns(4)
        col_9, col_10, col_11, col_12 = st.columns(4)
        validation_mode_options = ["monthly_prior", "aggregate"]
        edge_mode_options = ["rolling", "static", "none"]
        time_column_options = ["end_datetime", "start_datetime"]
        signal_frequency_options = ["daily", "intraday"]
        daily_policy_options = ["strongest", "last", "first"]
        universe_mode_options = ["all_predictions", "validated_products"]
        prediction_mode_options = ["online_model", "replay_csv"]
        st.caption(
            "AbsRet runs as a daily strategy on concrete contracts. Online mode loads the saved model and feature cache to generate predictions during backtest setup; replay mode is only for checking an existing prediction CSV."
        )
        return {
            "model_name": str(col_1.text_input(
                "Model Name",
                value=str(active_config.get("model_name", "hybrid_product")),
            )),
            "min_validation_hit_rate": float(col_2.number_input(
                "Min Validation Hit Rate",
                min_value=0.0,
                max_value=1.0,
                value=float(active_config.get("min_validation_hit_rate", 0.60)),
                step=0.01,
                format="%.2f",
            )),
            "max_total_margin_pct": float(col_3.number_input(
                "Max Total Margin %",
                min_value=0.0,
                max_value=1.0,
                value=float(active_config.get("max_total_margin_pct", 0.30)),
                step=0.05,
                format="%.2f",
            )),
            "edge_quantile": float(col_4.number_input(
                "Edge Quantile",
                min_value=0.0,
                max_value=1.0,
                value=float(active_config.get("edge_quantile", 0.90)),
                step=0.01,
                format="%.2f",
            )),
            "validation_mode": col_5.selectbox(
                "Validation Mode",
                validation_mode_options,
                index=_option_index(validation_mode_options, active_config.get("validation_mode", "monthly_prior")),
            ),
            "edge_threshold_mode": col_6.selectbox(
                "Edge Threshold Mode",
                edge_mode_options,
                index=_option_index(edge_mode_options, active_config.get("edge_threshold_mode", "rolling")),
            ),
            "signal_time_column": col_7.selectbox(
                "Signal Time Column",
                time_column_options,
                index=_option_index(time_column_options, active_config.get("signal_time_column", "end_datetime")),
            ),
            "max_positions": int(col_8.number_input(
                "Max Positions (0=unlimited)",
                min_value=0,
                value=int(active_config.get("max_positions", 0) or 0),
                step=1,
            )),
            "validation_lookback_months": int(col_9.number_input(
                "Validation Lookback Months",
                min_value=1,
                value=int(active_config.get("validation_lookback_months", 3)),
                step=1,
            )),
            "edge_threshold_lookback": int(col_10.number_input(
                "Edge Lookback Rows",
                min_value=0,
                value=int(active_config.get("edge_threshold_lookback", 5000)),
                step=500,
            )),
            "min_threshold_history": int(col_11.number_input(
                "Min Edge History",
                min_value=1,
                value=int(active_config.get("min_threshold_history", 200)),
                step=50,
            )),
            "absret_max_symbols": int(col_12.number_input(
                "Max Contracts (0=all matched)",
                min_value=0,
                value=int(active_config.get("absret_max_symbols", 0) or 0),
                step=1,
            )),
            "absret_universe_mode": st.selectbox(
                "Universe Mode",
                universe_mode_options,
                index=_option_index(universe_mode_options, active_config.get("absret_universe_mode", "all_predictions")),
            ),
            "prediction_mode": st.selectbox(
                "Prediction Mode",
                prediction_mode_options,
                index=_option_index(prediction_mode_options, active_config.get("prediction_mode", "online_model")),
            ),
            "model_available_date": str(st.text_input(
                "Model Available Date",
                value=str(active_config.get("model_available_date", "2025-07-01")),
            )),
            "min_signal_confidence": float(st.number_input(
                "Min Signal Confidence",
                min_value=0.50,
                max_value=1.00,
                value=float(active_config.get("min_signal_confidence", 0.60)),
                step=0.01,
                format="%.2f",
            )),
            "daily_signal_cutoff_hour": int(st.number_input(
                "Daily Signal Cutoff Hour",
                min_value=0,
                max_value=23,
                value=int(active_config.get("daily_signal_cutoff_hour", 21)),
                step=1,
            )),
            "signal_frequency": st.selectbox(
                "Signal Frequency",
                signal_frequency_options,
                index=_option_index(signal_frequency_options, active_config.get("signal_frequency", "daily")),
            ),
            "daily_signal_policy": st.selectbox(
                "Daily Signal Policy",
                daily_policy_options,
                index=_option_index(daily_policy_options, active_config.get("daily_signal_policy", "strongest")),
            ),
            "one_contract_per_product": bool(st.checkbox(
                "One contract per product",
                value=_bool_from_config(active_config, "one_contract_per_product", True),
            )),
            "close_on_failed_signal": bool(st.checkbox(
                "Close when signal drops below filter",
                value=_bool_from_config(active_config, "close_on_failed_signal", True),
            )),
            "signal_path": str(st.text_input(
                "Signal CSV path (blank=default)",
                value=str(active_config.get("signal_path", "")),
            )),
            "validation_path": str(st.text_input(
                "Validation CSV path (blank=default)",
                value=str(active_config.get("validation_path", "")),
            )),
            "monthly_validation_path": str(st.text_input(
                "Monthly validation CSV path (blank=default)",
                value=str(active_config.get("monthly_validation_path", "")),
            )),
        }

    if strategy_key in {"composite_factor", "cross_momentum"}:
        col_1, col_2, col_3 = st.columns(3)
        return {
            "rebalance_period": int(col_1.number_input(
                "调仓周期",
                min_value=1,
                value=int(active_config.get("rebalance_period", 5)),
                step=1,
            )),
            "top_k": int(col_2.number_input(
                "多空数量",
                min_value=1,
                value=int(active_config.get("top_k", 2)),
                step=1,
            )),
            "signal_scale": float(col_3.number_input(
                "单腿信号强度",
                min_value=0.1,
                value=float(active_config.get("signal_scale", 1.0)),
                step=0.1,
                format="%.2f",
            )),
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
- 执行由 `execution` 控制：市价单使用 `slippage_ticks`，Tick 对价单吃对手盘，限价单使用 `limit_mode` 和 `ticks`。
- 平仓由 `exit` 控制：`close_pct` 决定普通退出信号下平掉多少比例，策略也可以在高级信号里指定 `close_volume`。
- 新策略接入配置页时，优先编辑 `ui/run_from_config.py` 的 `STRATEGY_SPECS` 和对应 builder。
        """
    )


def main():
    if _query_flag("poll"):
        _render_poll_response()
        return

    dm = BacktestDataManager()
    _, active_config = _load_active_config()
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

    config_tab, pulse_tab, agent_tab, guide_tab = st.tabs([
        "参数配置 (Configuration)",
        "脉冲发现 (Pulse Discovery)",
        "Agent 助手",
        "策略说明 (Guide)",
    ])
    with config_tab:
        notice = st.session_state.pop(RUN_NOTICE_KEY, None)
        if isinstance(notice, dict):
            level = notice.get("level")
            message = notice.get("message", "")
            if level == "success":
                st.success(message)
            elif level == "error":
                st.error(message)
            else:
                st.info(message)

        strategy_keys = [item["key"] for item in available]
        applied_config = _render_config_assistant(dm, active_config, set(strategy_keys))
        if isinstance(applied_config, dict):
            active_config = applied_config

        active_strategy = str(active_config.get("strategy", "general_multi_ma")).strip().lower()
        if active_strategy not in strategy_keys:
            active_strategy = "general_multi_ma" if "general_multi_ma" in strategy_keys else strategy_keys[0]
        default_index = strategy_keys.index(active_strategy)

        strategy_key = st.selectbox(
            "策略",
            strategy_keys,
            index=default_index,
            format_func=_strategy_label,
        )

        form_active_config = _strategy_form_defaults(strategy_key, active_config)

        config = {
            "strategy": strategy_key,
            **_market_fields(form_active_config),
        }
        config.update(_strategy_parameter_fields(strategy_key, form_active_config))
        config.update(_general_signal_fields(form_active_config, config.get("freq")))

        col_1, col_2 = st.columns([1, 3])
        enable_main_rollover = col_1.checkbox(
            "启用主力换月",
            value=_bool_from_config(active_config, "enable_main_rollover", True),
        )
        timeout_seconds = int(col_2.number_input("运行超时秒数 (0=不限)", min_value=0, value=0, step=60))
        config["enable_main_rollover"] = bool(enable_main_rollover)

        can_run = bool(config["symbols"]) or strategy_key == "abs_ret_rolling_validation"
        running_lock = _read_run_lock()
        if running_lock:
            try:
                running_pid = int(running_lock.get("pid", 0))
            except (TypeError, ValueError):
                running_pid = 0
            if not _pid_is_running(running_pid):
                dashboard_url = _finish_completed_run(running_lock)
                running_lock = {}
                if dashboard_url:
                    st.success("回测完成，报告已生成。")
                    st.markdown(f"[打开最新报告]({dashboard_url})")
                    _post_report_update(dashboard_url)
                else:
                    st.warning("回测进程已结束，但未获取到报告路径。请查看最近一次运行输出。")
            else:
                _start_hidden_run_status_poll(3)
                timeout_limit = int(running_lock.get("timeout_seconds") or 0)
                started_at = float(running_lock.get("started_at_epoch") or 0.0)
                if timeout_limit > 0 and started_at > 0 and time.time() - started_at > timeout_limit:
                    ok, message = _stop_locked_backtest(running_lock)
                    running_lock = {}
                    if ok:
                        st.error(f"回测超过 {timeout_limit} 秒，已自动停止。{message}")
                    else:
                        st.error(f"回测超过 {timeout_limit} 秒，但自动停止失败。{message}")

        if config.get("freq") == "tick" and len(config.get("symbols", [])) > 2:
            st.warning(
                f"Tick 回测当前选择了 {len(config.get('symbols', []))} 个品种，"
                "可以运行，但耗时会明显变长。运行期间请不要重复点击按钮。"
            )
        if running_lock:
            st.warning(f"已有回测进程正在运行，PID={running_lock.get('pid', '-')}。")
            run_col_1, run_col_2 = st.columns([1, 1])
            if run_col_1.button("刷新运行状态", use_container_width=True):
                st.rerun()
            if run_col_2.button("停止当前回测", use_container_width=True):
                ok, message = _stop_locked_backtest(running_lock)
                st.session_state[RUN_NOTICE_KEY] = {
                    "level": "success" if ok else "error",
                    "message": message,
                }
                st.rerun()

        submitted = st.button(
            "按当前参数运行回测",
            type="primary",
            use_container_width=True,
            disabled=(not can_run) or bool(running_lock),
        )

        if not can_run:
            st.warning("请至少选择一个品种。")

        if submitted:
            try:
                ok, message = _start_backtest(dm, config, timeout_seconds)
                if ok:
                    st.session_state[RUN_NOTICE_KEY] = {"level": "success", "message": message}
                    st.rerun()
                else:
                    st.error(message)
            except Exception as exc:
                st.error(f"运行失败: {exc}")

        if st.session_state.get("last_run_output"):
            with st.expander("最近一次运行输出", expanded=False):
                st.code(st.session_state["last_run_output"])

    with pulse_tab:
        render_pulse_panel()

    with agent_tab:
        render_agent_panel()

    with guide_tab:
        _guide()


if __name__ == "__main__":
    main()
