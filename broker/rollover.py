# -*- coding: utf-8 -*-
"""
主力连续合约换月处理器 (Main Contract Rollover)

换月逻辑 (T日收盘时):
  - 策略在T日正常交易，完整捕获T日盈亏
  - T日收盘后：用昨日收盘价(T-1日收盘) 平旧仓，用T日开盘价开新仓
  - 旧仓盈亏 = (昨收价 - 持仓均价) × 手数 × 乘数（正常结算）
  - 新仓开仓价 = T日开盘价（含滑点）
"""
from datetime import datetime
from typing import Dict

import pandas as pd

from broker.match_engine import MatchEngine


class MainContractRollover:
    """未复权主连换月执行器"""

    @staticmethod
    def is_enabled(data_type: str) -> bool:
        return data_type == 'main'

    @staticmethod
    def _is_month_change(bar: dict) -> bool:
        flag = bar.get('month_change', 0)
        if flag is None or pd.isna(flag):
            return False
        return int(flag) >= 1

    def process(self, broker: MatchEngine, current_time: datetime, bar_data: Dict[str, dict],
                last_close_prices: Dict[str, float]) -> int:
        """
        执行换月：
        - close_price: 昨日收盘价（用于平旧仓，结算T-1→T的真实盈亏）
        - roll_open_price: T日开盘价（用于开新仓）
        """
        rolled_symbols = 0
        account = broker.account

        for sym, bar in bar_data.items():
            if not self._is_month_change(bar):
                continue

            roll_open_price = bar.get('open')
            if roll_open_price is None or pd.isna(roll_open_price):
                continue

            # 昨日收盘价（用于结算旧仓）
            old_close_price = last_close_prices.get(sym, roll_open_price)

            sym_rolled = False
            from broker.order import Direction
            for pos_direction in (Direction.LONG, Direction.SHORT):
                pos_key = account._get_position_key(sym, pos_direction)
                pos = account.positions.get(pos_key)
                if not pos:
                    continue
                volume = account._position_volume(pos)
                if volume <= 0:
                    continue

                broker.execute_rollover(
                    sym, pos_direction, volume,
                    old_close_price, roll_open_price, current_time
                )
                sym_rolled = True

            if sym_rolled:
                rolled_symbols += 1
                print(f"🔄 [换月] {current_time} | {sym} | 昨收:{old_close_price} → 新开:{roll_open_price}")

        return rolled_symbols
