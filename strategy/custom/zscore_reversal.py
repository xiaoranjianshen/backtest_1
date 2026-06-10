# strategy/custom/zscore_reversal.py
# -*- coding: utf-8 -*-

import pandas as pd

from strategy.general_template import GeneralSignalStrategy


class ZScoreReversalStrategy(GeneralSignalStrategy):
    """
    多品种 z-score 均值回归策略。

    逻辑：
    - z >= entry_z：价格显著高于均值，做空
    - z <= -entry_z：价格显著低于均值，做多
    - 空头回到 z <= 0：平半仓
    - 空头回到 z <= -final_exit_z：平剩余仓位
    - 多头回到 z >= 0：平半仓
    - 多头回到 z >= final_exit_z：平剩余仓位
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        lookback=10,
        entry_z=2.1,
        first_exit_z=0.0,
        final_exit_z=1.0,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )

        self.lookback = int(lookback)
        self.entry_z = float(entry_z)
        self.first_exit_z = float(first_exit_z)
        self.final_exit_z = float(final_exit_z)

        if self.lookback < 2:
            raise ValueError("lookback 必须至少为 2")
        if self.entry_z <= 0:
            raise ValueError("entry_z 必须为正数")
        if self.final_exit_z <= 0:
            raise ValueError("final_exit_z 必须为正数")

        self.close_history = {sym: [] for sym in self.symbols}
        self.half_exit_done = {sym: False for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy ZScoreReversal] symbols={len(self.symbols)} | "
            f"lookback={self.lookback} | entry_z={self.entry_z:g} | "
            f"first_exit_z={self.first_exit_z:g} | final_exit_z={self.final_exit_z:g}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}

        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not bar:
                continue

            close_price = bar.get("close")
            if close_price is None or pd.isna(close_price):
                continue

            close_price = float(close_price)
            history = self.close_history.setdefault(sym, [])
            history.append(close_price)

            if len(history) > self.lookback:
                history.pop(0)

            current_net = self.get_net_position(sym)

            if current_net == 0:
                self.half_exit_done[sym] = False

            if len(history) < self.lookback:
                signals[sym] = {
                    "signal": None,
                    "reason": "warming_up",
                    "metrics": {
                        "close": close_price,
                        "history_len": len(history),
                    },
                }
                continue

            mean_price = sum(history) / len(history)
            variance = sum((price - mean_price) ** 2 for price in history) / len(history)
            sigma = variance ** 0.5

            if sigma <= 0:
                signals[sym] = {
                    "signal": None,
                    "reason": "zero_sigma",
                    "metrics": {
                        "close": close_price,
                        "mean": mean_price,
                        "sigma": sigma,
                        "zscore": 0.0,
                        "current_net": current_net,
                    },
                }
                continue

            zscore = (close_price - mean_price) / sigma

            signal = {
                "signal": None,
                "reason": "hold",
                "metrics": {
                    "close": close_price,
                    "mean": mean_price,
                    "sigma": sigma,
                    "zscore": zscore,
                    "current_net": current_net,
                    "half_exit_done": self.half_exit_done.get(sym, False),
                },
            }

            if current_net == 0:
                if zscore >= self.entry_z:
                    signal = {
                        "signal": -1,
                        "position_mode": "target",
                        "reason": "short_entry_high_zscore",
                        "metrics": {
                            "close": close_price,
                            "mean": mean_price,
                            "sigma": sigma,
                            "zscore": zscore,
                            "current_net": current_net,
                        },
                    }
                elif zscore <= -self.entry_z:
                    signal = {
                        "signal": 1,
                        "position_mode": "target",
                        "reason": "long_entry_low_zscore",
                        "metrics": {
                            "close": close_price,
                            "mean": mean_price,
                            "sigma": sigma,
                            "zscore": zscore,
                            "current_net": current_net,
                        },
                    }

            elif current_net < 0:
                if zscore <= -self.final_exit_z:
                    signal = {
                        "signal": 0,
                        "position_mode": "flat",
                        "reason": "short_final_exit",
                        "metrics": {
                            "close": close_price,
                            "mean": mean_price,
                            "sigma": sigma,
                            "zscore": zscore,
                            "current_net": current_net,
                        },
                    }
                elif (not self.half_exit_done.get(sym, False)) and zscore <= self.first_exit_z:
                    self.half_exit_done[sym] = True
                    signal = {
                        "signal": 0,
                        "position_mode": "reduce",
                        "close_pct": 0.5,
                        "reason": "short_half_exit",
                        "metrics": {
                            "close": close_price,
                            "mean": mean_price,
                            "sigma": sigma,
                            "zscore": zscore,
                            "current_net": current_net,
                        },
                    }

            elif current_net > 0:
                if zscore >= self.final_exit_z:
                    signal = {
                        "signal": 0,
                        "position_mode": "flat",
                        "reason": "long_final_exit",
                        "metrics": {
                            "close": close_price,
                            "mean": mean_price,
                            "sigma": sigma,
                            "zscore": zscore,
                            "current_net": current_net,
                        },
                    }
                elif (not self.half_exit_done.get(sym, False)) and zscore >= self.first_exit_z:
                    self.half_exit_done[sym] = True
                    signal = {
                        "signal": 0,
                        "position_mode": "reduce",
                        "close_pct": 0.5,
                        "reason": "long_half_exit",
                        "metrics": {
                            "close": close_price,
                            "mean": mean_price,
                            "sigma": sigma,
                            "zscore": zscore,
                            "current_net": current_net,
                        },
                    }

            signals[sym] = signal

        return signals