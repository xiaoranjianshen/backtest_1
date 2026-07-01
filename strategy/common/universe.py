# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import pandas as pd

from config import pure_product_code


@dataclass(frozen=True)
class UniverseSelectionEntry:
    """
    单个选品结果。

    选品模型只负责回答“某个交易日哪些品种可以进入策略池”，不直接下单。
    - symbol: 品种或合约代码，统一转为纯品种小写代码。
    - rank: 当天横截面排名，1 代表最靠前。
    - score: 模型分数或规则分数，数值越大通常代表越优先。
    - weight: 组合权重，可用于分配仓位；只做过滤时可以填 1.0。
    - side: 方向建议，1 做多，-1 做空，0 表示只选品不判断方向。
    - reason: 选入原因，进入信号诊断和交易日志。
    - model_name: 产生该结果的选品模型名称。
    - meta: 其他指标，例如预测概率、IC 分组、成交额、行业等。
    """

    symbol: str
    rank: int = 0
    score: float = 0.0
    weight: float = 1.0
    side: int = 0
    reason: str = "selected_by_universe_model"
    model_name: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_symbol(self) -> str:
        return normalize_universe_symbol(self.symbol)


class UniverseSelector:
    """所有动态选品模型的最小通用接口。"""

    name = "universe_selector"

    def entries_for(self, trade_date) -> dict[str, UniverseSelectionEntry]:
        raise NotImplementedError

    def entry(self, trade_date, symbol: str) -> UniverseSelectionEntry | None:
        entries = self.entries_for(trade_date)
        symbol_key = normalize_universe_symbol(symbol)
        product_key = pure_product_code(symbol_key).lower()
        return entries.get(symbol_key) or entries.get(product_key)

    def contains(self, trade_date, symbol: str) -> bool:
        return self.entry(trade_date, symbol) is not None

    def selected_symbols(self, trade_date) -> list[str]:
        return list(self.entries_for(trade_date))

    def selected_days(self) -> int:
        return 0


class MappingUniverseSelector(UniverseSelector):
    """
    基于 date -> symbol -> entry 的选品器。

    这是回测里最常用的形式：模型可以提前或在线生成“每日选品表”，策略只按
    当前交易日查询选品结果。这里的“提前生成”只能使用当时可见的数据，不能用
    未来标签或未来行情。
    """

    def __init__(self, selector_by_date: Mapping | None = None, name: str = "mapping_selector"):
        self.name = str(name)
        self._selector = normalize_selector_map(selector_by_date or {}, model_name=self.name)

    def entries_for(self, trade_date) -> dict[str, UniverseSelectionEntry]:
        return self._selector.get(normalize_selector_date(trade_date), {})

    def selected_days(self) -> int:
        return len(self._selector)

    def as_legacy_map(self) -> dict[str, dict[str, dict]]:
        output: dict[str, dict[str, dict]] = {}
        for trade_date, entries in self._selector.items():
            output[trade_date] = {
                symbol: {
                    "rank": entry.rank,
                    "score": entry.score,
                    "weight": entry.weight,
                    "side": entry.side,
                    "reason": entry.reason,
                    "model_name": entry.model_name,
                    **entry.meta,
                }
                for symbol, entry in entries.items()
            }
        return output


class DataFrameUniverseSelector(MappingUniverseSelector):
    """
    从 DataFrame 构造选品器。

    标准列：
    - trade_date: 生效交易日
    - symbol: 品种或合约代码
    - rank / score / weight / side / reason / model_name: 可选
    额外列会进入 meta，方便信号诊断展示。
    """

    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        date_col: str = "trade_date",
        symbol_col: str = "symbol",
        name: str = "dataframe_selector",
    ):
        selector = selector_map_from_frame(frame, date_col=date_col, symbol_col=symbol_col, model_name=name)
        super().__init__(selector, name=name)


class NullUniverseSelector(UniverseSelector):
    """空选品器：任何日期都不选中任何品种。"""

    name = "null_selector"

    def entries_for(self, trade_date) -> dict[str, UniverseSelectionEntry]:
        return {}


def ensure_universe_selector(value, *, name: str = "mapping_selector") -> UniverseSelector:
    if isinstance(value, UniverseSelector):
        return value
    if value is None:
        return MappingUniverseSelector({}, name=name)
    if isinstance(value, pd.DataFrame):
        return DataFrameUniverseSelector(value, name=name)
    if isinstance(value, Mapping):
        return MappingUniverseSelector(value, name=name)
    raise TypeError(f"Unsupported universe selector type: {type(value).__name__}")


def normalize_selector_map(raw_selector: Mapping, *, model_name: str = "") -> dict[str, dict[str, UniverseSelectionEntry]]:
    normalized: dict[str, dict[str, UniverseSelectionEntry]] = {}
    for date_key, entries in (raw_selector or {}).items():
        date_text = normalize_selector_date(date_key)
        normalized[date_text] = {}
        for symbol, value in (entries or {}).items():
            entry = coerce_selection_entry(symbol, value, model_name=model_name)
            normalized[date_text][entry.normalized_symbol] = entry
    return normalized


def selector_map_from_frame(
    frame: pd.DataFrame,
    *,
    date_col: str = "trade_date",
    symbol_col: str = "symbol",
    model_name: str = "",
) -> dict[str, dict[str, UniverseSelectionEntry]]:
    if frame is None or frame.empty:
        return {}
    if date_col not in frame.columns or symbol_col not in frame.columns:
        raise ValueError(f"Selector frame must contain {date_col!r} and {symbol_col!r}")

    selector: dict[str, dict[str, UniverseSelectionEntry]] = {}
    reserved = {date_col, symbol_col, "rank", "score", "weight", "side", "reason", "model_name"}
    for row in frame.itertuples(index=False):
        raw = row._asdict()
        trade_date = normalize_selector_date(raw[date_col])
        symbol = normalize_universe_symbol(raw[symbol_col])
        meta = {key: value for key, value in raw.items() if key not in reserved}
        entry = UniverseSelectionEntry(
            symbol=symbol,
            rank=int(_safe_number(raw.get("rank"), 0)),
            score=float(_safe_number(raw.get("score"), 0.0)),
            weight=float(_safe_number(raw.get("weight"), 1.0)),
            side=int(_safe_number(raw.get("side"), 0)),
            reason=str(raw.get("reason") or "selected_by_universe_model"),
            model_name=str(raw.get("model_name") or model_name),
            meta=meta,
        )
        selector.setdefault(trade_date, {})[symbol] = entry
    return selector


def coerce_selection_entry(symbol: str, value, *, model_name: str = "") -> UniverseSelectionEntry:
    symbol_key = normalize_universe_symbol(symbol)
    if isinstance(value, UniverseSelectionEntry):
        if value.normalized_symbol == symbol_key:
            return value
        return UniverseSelectionEntry(
            symbol=symbol_key,
            rank=value.rank,
            score=value.score,
            weight=value.weight,
            side=value.side,
            reason=value.reason,
            model_name=value.model_name or model_name,
            meta=dict(value.meta),
        )
    if isinstance(value, Mapping):
        meta = {
            key: val
            for key, val in value.items()
            if key not in {"rank", "score", "weight", "side", "reason", "model_name"}
        }
        return UniverseSelectionEntry(
            symbol=symbol_key,
            rank=int(_safe_number(value.get("rank"), 0)),
            score=float(_safe_number(value.get("score"), 0.0)),
            weight=float(_safe_number(value.get("weight"), 1.0)),
            side=int(_safe_number(value.get("side"), 0)),
            reason=str(value.get("reason") or "selected_by_universe_model"),
            model_name=str(value.get("model_name") or model_name),
            meta=meta,
        )
    if isinstance(value, (int, float)):
        return UniverseSelectionEntry(symbol=symbol_key, score=float(value), weight=1.0, model_name=model_name)
    return UniverseSelectionEntry(symbol=symbol_key, model_name=model_name)


def selection_metrics(entry: UniverseSelectionEntry | None, prefix: str = "selector") -> dict[str, Any]:
    if entry is None:
        return {
            f"{prefix}_selected": False,
            f"{prefix}_rank": None,
            f"{prefix}_score": None,
            f"{prefix}_weight": 0.0,
            f"{prefix}_side": 0,
            f"{prefix}_model": None,
            "indicator_selector_weight": 0.0,
        }
    metrics = {
        f"{prefix}_selected": True,
        f"{prefix}_rank": entry.rank,
        f"{prefix}_score": entry.score,
        f"{prefix}_weight": entry.weight,
        f"{prefix}_side": entry.side,
        f"{prefix}_model": entry.model_name,
        "indicator_selector_weight": entry.weight,
    }
    for key, value in entry.meta.items():
        metrics[f"{prefix}_{key}"] = value
    return metrics


def normalize_selector_date(value) -> str:
    return pd.Timestamp(value).date().isoformat()


def normalize_universe_symbol(symbol: object) -> str:
    text = str(symbol).strip()
    if "(" in text and ")" in text:
        text = text.split("(", 1)[1].split(")", 1)[0]
    if "@" in text:
        text = text.split("@", 1)[1]
    if "." in text:
        text = text.split(".")[-1]
    return text.lower()


def selected_symbols_from_selector(selector: UniverseSelector, dates: Iterable) -> list[str]:
    symbols: set[str] = set()
    for trade_date in dates:
        symbols.update(selector.selected_symbols(trade_date))
    return sorted(symbols)


def _safe_number(value, default: float) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
