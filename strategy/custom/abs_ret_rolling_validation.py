# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import re
from bisect import bisect_right
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay

from config import pure_product_code
from strategy.general_template import GeneralSignalStrategy


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_DIR = WORKSPACE_ROOT / "distribution" / "outputs" / "abs_ret_hybrid_minute_t24_candidates"
DEFAULT_SIGNAL_PATH = DEFAULT_MODEL_DIR / "oos_predictions.csv"
DEFAULT_VALIDATION_PATH = DEFAULT_MODEL_DIR / "monthly_product_top10_aggregate.csv"
DEFAULT_MONTHLY_VALIDATION_PATH = DEFAULT_MODEL_DIR / "monthly_product_top10_metrics.csv"
DEFAULT_FEATURE_CACHE_PATH = DEFAULT_MODEL_DIR / "hybrid_features.parquet"
DEFAULT_ONLINE_MODEL_PATH = DEFAULT_MODEL_DIR / "hybrid_product_model.joblib"
DEFAULT_ONLINE_FEATURES_PATH = DEFAULT_MODEL_DIR / "hybrid_product_features.csv"
DEFAULT_MODEL_AVAILABLE_DATE = "2025-07-01"


class AbsRetRollingValidationStrategy(GeneralSignalStrategy):
    """
    Trade the abs_ret direction model only where prior validation is strong.

    By default this is a daily execution strategy: intraday abs_ret prediction
    rows are collapsed into one signal per contract per day, then the backtest
    engine submits the order after the daily bar and fills it on the next daily
    bar open.
    """

    def __init__(
        self,
        broker,
        account,
        symbol: str = "multi",
        target_symbols: Iterable[str] | None = None,
        prediction_mode: str = "online_model",
        signal_path: str | Path | None = None,
        feature_cache_path: str | Path | None = None,
        model_path: str | Path | None = None,
        model_features_path: str | Path | None = None,
        model_available_date: str | None = DEFAULT_MODEL_AVAILABLE_DATE,
        validation_path: str | Path | None = None,
        monthly_validation_path: str | Path | None = None,
        model_name: str = "hybrid_product",
        min_validation_hit_rate: float = 0.60,
        validation_mode: str = "monthly_prior",
        validation_lookback_months: int = 3,
        min_validation_rows: int = 30,
        allowed_products: Iterable[str] | None = None,
        signal_time_column: str = "end_datetime",
        signal_frequency: str = "daily",
        daily_signal_policy: str = "strongest",
        daily_signal_cutoff_hour: int = 21,
        trading_calendar: Iterable | None = None,
        min_signal_confidence: float = 0.60,
        edge_quantile: float = 0.90,
        edge_threshold_mode: str = "rolling",
        edge_threshold_lookback: int = 5000,
        min_threshold_history: int = 200,
        max_total_margin_pct: float = 0.30,
        max_positions: int | None = None,
        one_contract_per_product: bool = True,
        close_on_failed_signal: bool = True,
        sizing: dict | None = None,
        execution: dict | None = None,
        exit: dict | None = None,
        record_signals: bool = True,
        record_signal_holds: bool = False,
    ):
        # min_volume=0 avoids forcing a 1-lot trade when a contract's required
        # margin would already exceed its allocated portfolio slice.
        sizing = sizing or {"mode": "fixed_volume", "value": 1, "min_volume": 0}
        execution = execution or {"order_type": "market", "slippage_ticks": 1.0}
        exit = exit or {"close_pct": 1.0, "allow_reverse": True}
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            sizing=sizing,
            execution=execution,
            exit=exit,
            record_signals=record_signals,
            record_signal_holds=record_signal_holds,
        )

        self.prediction_mode = str(prediction_mode).strip().lower()
        self.signal_path = _resolve_path(signal_path, DEFAULT_SIGNAL_PATH)
        self.feature_cache_path = _resolve_path(feature_cache_path, DEFAULT_FEATURE_CACHE_PATH)
        self.model_path = _resolve_path(model_path, DEFAULT_ONLINE_MODEL_PATH)
        self.model_features_path = _resolve_path(model_features_path, DEFAULT_ONLINE_FEATURES_PATH)
        self.model_available_date = _optional_timestamp(model_available_date)
        self.validation_path = _resolve_path(validation_path, DEFAULT_VALIDATION_PATH)
        self.monthly_validation_path = _resolve_path(monthly_validation_path, DEFAULT_MONTHLY_VALIDATION_PATH)
        self.model_name = str(model_name)
        self.min_validation_hit_rate = float(min_validation_hit_rate)
        self.validation_mode = str(validation_mode).strip().lower()
        self.validation_lookback_months = max(1, int(validation_lookback_months))
        self.min_validation_rows = max(0, int(min_validation_rows))
        self.signal_time_column = str(signal_time_column)
        self.signal_frequency = str(signal_frequency).strip().lower()
        self.daily_signal_policy = str(daily_signal_policy).strip().lower()
        self.daily_signal_cutoff_hour = int(daily_signal_cutoff_hour)
        self.trading_calendar = _normalise_trading_calendar(trading_calendar)
        self.min_signal_confidence = min(max(float(min_signal_confidence), 0.0), 1.0)
        self.edge_quantile = min(max(float(edge_quantile), 0.0), 1.0)
        self.edge_threshold_mode = str(edge_threshold_mode).strip().lower()
        self.edge_threshold_lookback = int(edge_threshold_lookback)
        self.min_threshold_history = max(1, int(min_threshold_history))
        self.max_total_margin_pct = max(0.0, float(max_total_margin_pct))
        self.max_positions = int(max_positions or 0)
        self.one_contract_per_product = bool(one_contract_per_product)
        self.close_on_failed_signal = bool(close_on_failed_signal)
        self.allowed_products_override = (
            {_normalize_product(product) for product in allowed_products if str(product).strip()}
            if allowed_products is not None
            else None
        )

        self.validation_stats = self._load_validation_stats()
        self.allowed_products = self._resolve_allowed_products()
        self.monthly_allowed_by_period = self._load_monthly_validation() if self.validation_mode == "monthly_prior" else {}
        self.signal_frame, self.signals_by_time = self._load_signal_frame()
        self.active_targets: dict[str, dict] = {}

    def on_init(self):
        super().on_init()
        allowed = ",".join(sorted(self.allowed_products)) if self.allowed_products else "none"
        print(
            "[Strategy AbsRetRollingValidation] "
            f"mode={self.prediction_mode} | model={self.model_name} | validation_mode={self.validation_mode} | "
            f"signal_frequency={self.signal_frequency} | daily_policy={self.daily_signal_policy} | "
            f"allowed_products={allowed} | signal_rows={len(self.signal_frame):,} | "
            f"max_total_margin_pct={self.max_total_margin_pct:.2%}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        current_ts = self._signal_lookup_key(self.current_time)
        row_indexes = self.signals_by_time.get(current_ts)
        if not row_indexes:
            return {}

        signals: dict[str, dict] = {}
        old_targets = dict(self.active_targets)
        close_reasons: dict[str, str] = {}
        event_rows = self.signal_frame.loc[row_indexes].sort_values("edge", ascending=False)

        for row in event_rows.itertuples(index=False):
            sym = row.symbol_key
            if not _bar_is_tradeable(bar_data.get(sym)):
                continue

            if bool(row.passes_filter):
                self.active_targets[sym] = self._target_from_row(row)
                continue

            if self.close_on_failed_signal and sym in self.active_targets:
                self.active_targets.pop(sym, None)
                close_reasons[sym] = self._close_reason(row)

        removed_by_caps = self._enforce_position_caps()
        for sym in removed_by_caps:
            close_reasons.setdefault(sym, "absret_removed_by_portfolio_cap")

        stale_close_symbols = set()
        for sym in self.symbols:
            if sym in self.active_targets:
                continue
            if not _bar_is_tradeable(bar_data.get(sym)):
                continue
            if self.get_net_position(sym) != 0:
                close_reasons.setdefault(sym, "absret_stale_position_flat")
                stale_close_symbols.add(sym)

        active_count = max(1, len(self.active_targets))
        per_position_margin = self.max_total_margin_pct / active_count

        for sym, reason in close_reasons.items():
            if sym in self.active_targets:
                continue
            if not _bar_is_tradeable(bar_data.get(sym)):
                continue
            target = old_targets.get(sym, {})
            signals[sym] = {
                "signal": 0,
                "position_mode": "flat",
                "reason": reason,
                "metrics": self._metrics_from_target(target, per_position_margin=0.0, active_count=len(self.active_targets)),
            }

        if stale_close_symbols:
            return signals

        for sym, target in sorted(self.active_targets.items()):
            if not _bar_is_tradeable(bar_data.get(sym)):
                continue
            signals[sym] = {
                "signal": int(target["direction"]),
                "position_mode": "target",
                "target_margin_pct": per_position_margin,
                "signal_score": float(target["prob_up"]),
                "reason": "absret_model_edge_entry",
                "metrics": self._metrics_from_target(
                    target,
                    per_position_margin=per_position_margin,
                    active_count=len(self.active_targets),
                ),
            }

        return signals

    def _load_validation_stats(self) -> dict[str, dict]:
        if not self.validation_path.exists():
            raise FileNotFoundError(f"Validation file not found: {self.validation_path}")

        df = pd.read_csv(self.validation_path, encoding="utf-8-sig")
        model_col = _first_existing(df, ["experiment", "model"])
        if model_col is not None:
            df = df[df[model_col].astype(str) == self.model_name].copy()
        if df.empty:
            raise ValueError(f"No validation rows for model={self.model_name} in {self.validation_path}")

        rate_col = _first_existing(df, ["weighted_hit_rate", "hit_rate"])
        if rate_col is None:
            raise ValueError(f"Validation file has no hit-rate column: {self.validation_path}")

        rows_col = _first_existing(df, ["rows", "all_rows"])
        stats: dict[str, dict] = {}
        for row in df.itertuples(index=False):
            product = _normalize_product(getattr(row, "product"))
            hit_rate = _safe_float(getattr(row, rate_col), math.nan)
            rows = int(_safe_float(getattr(row, rows_col), 0.0)) if rows_col is not None else 0
            stats[product] = {
                "validation_hit_rate": hit_rate,
                "validation_rows": rows,
            }
        return stats

    def _resolve_allowed_products(self) -> set[str]:
        if self.allowed_products_override is not None:
            return set(self.allowed_products_override)
        return {
            product
            for product, stats in self.validation_stats.items()
            if _safe_float(stats.get("validation_hit_rate"), math.nan) > self.min_validation_hit_rate
        }

    def _load_monthly_validation(self) -> dict[pd.Period, dict[str, dict]]:
        if not self.monthly_validation_path.exists():
            print(f"[Strategy AbsRetRollingValidation] monthly validation file missing, fallback to aggregate: {self.monthly_validation_path}")
            return {}

        df = pd.read_csv(self.monthly_validation_path, encoding="utf-8-sig")
        model_col = _first_existing(df, ["experiment", "model"])
        if model_col is not None:
            df = df[df[model_col].astype(str) == self.model_name].copy()
        if "slice" in df.columns:
            df = df[df["slice"].astype(str) == "product_top10"].copy()
        if df.empty:
            return {}

        df["product_key"] = df["product"].map(_normalize_product)
        df["period"] = pd.to_datetime(df["month"], errors="coerce").dt.to_period("M")
        df["hit_rate"] = pd.to_numeric(df["hit_rate"], errors="coerce")
        df["rows"] = pd.to_numeric(df["rows"], errors="coerce").fillna(0.0)
        df = df.dropna(subset=["period", "hit_rate"]).sort_values(["product_key", "period"])

        allowed: dict[pd.Period, dict[str, dict]] = {}
        for period in sorted(df["period"].dropna().unique()):
            period_allowed: dict[str, dict] = {}
            for product, product_df in df.groupby("product_key", sort=False):
                past = product_df[product_df["period"] < period].tail(self.validation_lookback_months)
                total_rows = float(past["rows"].sum())
                if past.empty or total_rows < self.min_validation_rows:
                    continue
                weighted_hit = float((past["hit_rate"] * past["rows"]).sum() / total_rows)
                if weighted_hit > self.min_validation_hit_rate:
                    period_allowed[product] = {
                        "validation_hit_rate": weighted_hit,
                        "validation_rows": int(total_rows),
                        "validation_period": str(period),
                    }
            allowed[period] = period_allowed
        return allowed

    def _load_signal_frame(self) -> tuple[pd.DataFrame, dict[pd.Timestamp, list[int]]]:
        if self.prediction_mode in {"replay", "replay_csv", "csv", "prediction_csv"}:
            df = self._load_replay_prediction_rows()
        elif self.prediction_mode in {"online", "online_model", "model"}:
            df = self._build_online_prediction_rows()
        else:
            raise ValueError(f"Unsupported prediction_mode: {self.prediction_mode}")

        if self.signal_time_column not in df.columns:
            raise ValueError(f"signal_time_column={self.signal_time_column!r} not found in abs_ret prediction frame")

        df["signal_time"] = _coerce_time(df[self.signal_time_column])
        df["feature_start_time"] = _coerce_time(df["start_datetime"]) if "start_datetime" in df.columns else df["signal_time"]
        df["feature_end_time"] = _coerce_time(df["end_datetime"]) if "end_datetime" in df.columns else df["signal_time"]
        df = df.dropna(subset=["signal_time", "symbol", "prob_up"]).copy()
        df["signal_trade_date"] = self._signal_trade_dates(df["signal_time"])

        df["symbol_key"] = df["symbol"].map(_normalize_symbol)
        if "product" in df.columns:
            df["product_key"] = df["product"].map(_normalize_product)
        else:
            df["product_key"] = df["symbol_key"].map(_normalize_product)

        df["prob_up"] = pd.to_numeric(df["prob_up"], errors="coerce")
        if "edge" in df.columns:
            df["edge"] = pd.to_numeric(df["edge"], errors="coerce")
        else:
            df["edge"] = (df["prob_up"] - 0.5).abs()
        df["direction_confidence"] = df["prob_up"].map(lambda value: max(float(value), 1.0 - float(value)))

        if "direction" in df.columns:
            direction = pd.to_numeric(df["direction"], errors="coerce")
            df["direction_int"] = direction.map(lambda value: 1 if value > 0 else (-1 if value < 0 else 0))
        else:
            df["direction_int"] = df["prob_up"].map(lambda value: 1 if value >= 0.5 else -1)

        df = df.dropna(subset=["prob_up", "edge"]).copy()
        df = df.sort_values(["product_key", "signal_time", "symbol_key"]).reset_index(drop=True)
        df["edge_threshold"] = self._edge_thresholds(df)

        validation_time = df["signal_trade_date"] if self._is_daily_frequency() else df["signal_time"]

        if self.allowed_products_override is not None:
            df["validation_allowed"] = df["product_key"].isin(self.allowed_products_override)
        elif self.validation_mode == "monthly_prior" and self.monthly_allowed_by_period:
            df["validation_allowed"] = [
                self._monthly_product_allowed(product, ts)
                for product, ts in zip(df["product_key"], validation_time)
            ]
        else:
            df["validation_allowed"] = df["product_key"].isin(self.allowed_products)

        df["validation_hit_rate"] = [
            self._validation_hit_rate(product, ts)
            for product, ts in zip(df["product_key"], validation_time)
        ]
        df["passes_filter"] = (
            df["validation_allowed"]
            & df["edge_threshold"].notna()
            & (df["direction_confidence"] >= self.min_signal_confidence)
            & (df["edge"] >= df["edge_threshold"])
            & df["direction_int"].isin([1, -1])
        )

        if self._is_daily_frequency():
            df = self._collapse_daily_signals(df)
            group_key = "signal_date"
        elif self.signal_frequency in {"intraday", "minute", "raw"}:
            group_key = "signal_time"
        else:
            raise ValueError(f"Unsupported signal_frequency: {self.signal_frequency}")

        signals_by_time = {
            timestamp: indexes.tolist()
            for timestamp, indexes in df.groupby(group_key, sort=False).groups.items()
        }
        return df, signals_by_time

    def _load_replay_prediction_rows(self) -> pd.DataFrame:
        if not self.signal_path.exists():
            raise FileNotFoundError(f"Signal file not found: {self.signal_path}")

        header = pd.read_csv(self.signal_path, nrows=0, encoding="utf-8-sig").columns.tolist()
        wanted = [
            "symbol",
            "product",
            "start_datetime",
            "end_datetime",
            "prob_up",
            "edge",
            "direction",
            "side",
            "experiment",
            "model",
            "hit",
            "signed_next_ret",
            "next_log_ret",
            "y_up_next_bar",
        ]
        usecols = [col for col in wanted if col in header]
        df = pd.read_csv(self.signal_path, usecols=usecols, encoding="utf-8-sig")

        model_col = _first_existing(df, ["experiment", "model"])
        if model_col is not None:
            df = df[df[model_col].astype(str) == self.model_name].copy()
        if df.empty:
            raise ValueError(f"No replay signal rows for model={self.model_name} in {self.signal_path}")
        df["prediction_source"] = "replay_csv"
        return df

    def _build_online_prediction_rows(self) -> pd.DataFrame:
        if not self.model_path.exists():
            raise FileNotFoundError(
                "Online model file not found: "
                f"{self.model_path}. Run distribution/abs_ret_hybrid_minute_features.py first."
            )
        if not self.model_features_path.exists():
            raise FileNotFoundError(
                "Online model feature list not found: "
                f"{self.model_features_path}. Run distribution/abs_ret_hybrid_minute_features.py first."
            )
        if not self.feature_cache_path.exists():
            raise FileNotFoundError(f"Online feature cache not found: {self.feature_cache_path}")

        model_features_df = pd.read_csv(self.model_features_path, encoding="utf-8-sig")
        feature_col = "feature" if "feature" in model_features_df.columns else model_features_df.columns[0]
        model_features = [str(item) for item in model_features_df[feature_col].dropna().tolist()]
        if not model_features:
            raise ValueError(f"No model features listed in {self.model_features_path}")

        metadata_cols = ["symbol", "product", "start_datetime", "end_datetime"]
        usecols = list(dict.fromkeys(metadata_cols + model_features))
        try:
            df = pd.read_parquet(self.feature_cache_path, columns=usecols)
        except Exception as exc:
            raise ValueError(
                f"Unable to read required online model features from {self.feature_cache_path}"
            ) from exc
        if df.empty:
            raise ValueError(f"Feature cache is empty: {self.feature_cache_path}")

        df["symbol_key"] = df["symbol"].map(_normalize_symbol)
        selected_symbols = {_normalize_symbol(symbol) for symbol in self.symbols}
        if selected_symbols:
            df = df[df["symbol_key"].isin(selected_symbols)].copy()
        if df.empty:
            raise ValueError("No online feature rows matched target_symbols.")

        signal_times = _coerce_time(df[self.signal_time_column])
        if self.model_available_date is not None:
            df = df[signal_times >= self.model_available_date].copy()
            signal_times = _coerce_time(df[self.signal_time_column])
        if df.empty:
            raise ValueError(
                "No online feature rows remain after model_available_date filter: "
                f"{self.model_available_date}"
            )

        model = joblib.load(self.model_path)
        features = df[model_features].replace([np.inf, -np.inf], np.nan)
        if hasattr(model, "predict_proba"):
            prob_up = model.predict_proba(features)[:, 1]
        else:
            prob_up = model.predict(features)

        out = df[metadata_cols].copy()
        out["prob_up"] = pd.Series(prob_up, index=df.index).astype(float).to_numpy()
        out["edge"] = (out["prob_up"] - 0.5).abs()
        out["direction"] = np.where(out["prob_up"] >= 0.5, 1.0, -1.0)
        out["side"] = np.where(out["prob_up"] >= 0.5, "long", "short")
        out["experiment"] = self.model_name
        out["prediction_source"] = "online_model"
        return out

    def _signal_lookup_key(self, current_time) -> pd.Timestamp:
        timestamp = pd.Timestamp(current_time).floor("s")
        if self._is_daily_frequency():
            return timestamp.normalize()
        return timestamp

    def _is_daily_frequency(self) -> bool:
        return self.signal_frequency in {"daily", "1d", "day"}

    def _signal_trade_dates(self, signal_times: pd.Series) -> pd.Series:
        return signal_times.map(self._signal_trade_date)

    def _signal_trade_date(self, signal_time) -> pd.Timestamp:
        timestamp = pd.Timestamp(signal_time).floor("s")
        trade_date = timestamp.normalize()
        if timestamp.hour < self.daily_signal_cutoff_hour:
            return trade_date

        if len(self.trading_calendar) > 0:
            pos = bisect_right(self.trading_calendar, trade_date)
            if pos < len(self.trading_calendar):
                return self.trading_calendar[pos]

        # Fallback only applies when the engine did not pass an actual trading
        # calendar. The engine path passes df.index, so exchange holidays and
        # weekends are handled by the real backtest calendar.
        return (trade_date + BDay(1)).normalize()

    def _collapse_daily_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.daily_signal_policy not in {"strongest", "last", "first"}:
            raise ValueError(f"Unsupported daily_signal_policy: {self.daily_signal_policy}")

        daily = df.copy()
        daily["signal_date"] = daily["signal_trade_date"]
        if self.daily_signal_policy == "strongest":
            daily = daily.sort_values(
                ["symbol_key", "signal_date", "passes_filter", "edge", "signal_time"],
                ascending=[True, True, False, False, False],
            )
        elif self.daily_signal_policy == "last":
            daily = daily.sort_values(["symbol_key", "signal_date", "signal_time"], ascending=[True, True, False])
        else:
            daily = daily.sort_values(["symbol_key", "signal_date", "signal_time"], ascending=[True, True, True])

        return (
            daily
            .groupby(["symbol_key", "signal_date"], sort=False)
            .head(1)
            .sort_values(["signal_date", "product_key", "symbol_key"])
            .reset_index(drop=True)
        )

    def _edge_thresholds(self, df: pd.DataFrame) -> pd.Series:
        mode = self.edge_threshold_mode
        if mode in {"none", "off", "all"}:
            return pd.Series(0.0, index=df.index)
        if mode == "static":
            thresholds = df.groupby("product_key")["edge"].quantile(self.edge_quantile)
            return df["product_key"].map(thresholds)
        if mode != "rolling":
            raise ValueError(f"Unsupported edge_threshold_mode: {self.edge_threshold_mode}")

        def rolling_threshold(series: pd.Series) -> pd.Series:
            shifted = series.shift(1)
            if self.edge_threshold_lookback > 0:
                return shifted.rolling(
                    self.edge_threshold_lookback,
                    min_periods=self.min_threshold_history,
                ).quantile(self.edge_quantile)
            return shifted.expanding(min_periods=self.min_threshold_history).quantile(self.edge_quantile)

        return df.groupby("product_key", group_keys=False)["edge"].apply(rolling_threshold)

    def _monthly_product_allowed(self, product: str, signal_time: pd.Timestamp) -> bool:
        product = _normalize_product(product)
        period = pd.Timestamp(signal_time).to_period("M")
        return product in self.monthly_allowed_by_period.get(period, {})

    def _validation_hit_rate(self, product: str, signal_time: pd.Timestamp) -> float:
        product = _normalize_product(product)
        if self.validation_mode == "monthly_prior" and self.monthly_allowed_by_period:
            period = pd.Timestamp(signal_time).to_period("M")
            stats = self.monthly_allowed_by_period.get(period, {}).get(product)
            if stats is not None:
                return _safe_float(stats.get("validation_hit_rate"), math.nan)
        return _safe_float(self.validation_stats.get(product, {}).get("validation_hit_rate"), math.nan)

    def _target_from_row(self, row) -> dict:
        return {
            "symbol": row.symbol_key,
            "product": row.product_key,
            "direction": int(row.direction_int),
            "prob_up": float(row.prob_up),
            "direction_confidence": float(row.direction_confidence),
            "edge": float(row.edge),
            "edge_threshold": _safe_float(row.edge_threshold, math.nan),
            "validation_hit_rate": _safe_float(row.validation_hit_rate, math.nan),
            "signal_time": str(row.signal_time),
            "signal_trade_date": str(row.signal_trade_date),
            "feature_start_time": str(row.feature_start_time),
            "feature_end_time": str(row.feature_end_time),
            "model_name": self.model_name,
            "hit": getattr(row, "hit", None),
            "signed_next_ret": _safe_float(getattr(row, "signed_next_ret", math.nan), math.nan),
        }

    def _enforce_position_caps(self) -> set[str]:
        before = set(self.active_targets)

        if self.one_contract_per_product:
            best_by_product: dict[str, tuple[str, dict]] = {}
            for sym, target in self.active_targets.items():
                product = target["product"]
                current = best_by_product.get(product)
                if current is None or float(target["edge"]) > float(current[1]["edge"]):
                    best_by_product[product] = (sym, target)
            self.active_targets = {sym: target for sym, target in best_by_product.values()}

        if self.max_positions > 0 and len(self.active_targets) > self.max_positions:
            ranked = sorted(self.active_targets.items(), key=lambda item: float(item[1]["edge"]), reverse=True)
            self.active_targets = dict(ranked[: self.max_positions])

        return before - set(self.active_targets)

    def _close_reason(self, row) -> str:
        if not bool(row.validation_allowed):
            return "absret_validation_below_threshold"
        if pd.isna(row.edge_threshold):
            return "absret_edge_history_not_ready"
        if float(row.direction_confidence) < self.min_signal_confidence:
            return "absret_confidence_below_threshold"
        return "absret_edge_below_top_decile"

    def _metrics_from_target(self, target: dict, per_position_margin: float, active_count: int) -> dict:
        return {
            "model_name": self.model_name,
            "product": target.get("product"),
            "prob_up": target.get("prob_up"),
            "direction_confidence": target.get("direction_confidence"),
            "min_signal_confidence": self.min_signal_confidence,
            "edge": target.get("edge"),
            "edge_threshold": target.get("edge_threshold"),
            "validation_hit_rate": target.get("validation_hit_rate"),
            "target_margin_pct": per_position_margin,
            "portfolio_target_margin_pct": self.max_total_margin_pct,
            "active_count": active_count,
            "signal_time": target.get("signal_time"),
            "signal_trade_date": target.get("signal_trade_date"),
            "feature_start_time": target.get("feature_start_time"),
            "feature_end_time": target.get("feature_end_time"),
            "signed_next_ret": target.get("signed_next_ret"),
            "hit": target.get("hit"),
        }


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def _first_existing(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _coerce_time(series: pd.Series) -> pd.Series:
    values = pd.to_datetime(series, errors="coerce")
    if getattr(values.dt, "tz", None) is not None:
        values = values.dt.tz_localize(None)
    return values.dt.floor("s")


def _optional_timestamp(value: str | None) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.floor("s")


def _normalise_trading_calendar(calendar: Iterable | None) -> list[pd.Timestamp]:
    if calendar is None:
        return []
    values = pd.to_datetime(list(calendar), errors="coerce")
    if getattr(values, "tz", None) is not None:
        values = values.tz_localize(None)
    return sorted({pd.Timestamp(value).normalize() for value in values if not pd.isna(value)})


def _normalize_symbol(symbol: object) -> str:
    text = str(symbol).strip()
    match = re.search(r"\((.*?)\)", text)
    if match:
        text = match.group(1)
    text = text.split(".")[-1]
    return text.lower()


def _normalize_product(product: object) -> str:
    return pure_product_code(str(product).strip())


def _bar_is_tradeable(bar: dict | None) -> bool:
    if not bar:
        return False
    if not bool(bar.get("is_fresh", True)):
        return False
    close = bar.get("close")
    return close is not None and not pd.isna(close) and float(close) > 0


def _safe_float(value, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default
