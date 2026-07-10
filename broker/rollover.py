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

    def __init__(self):
        self._processed_events = set()

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
        使用数据层提供的旧合约收盘价与新合约开盘价执行换月。

        last_close_prices 仅为兼容旧调用签名保留，不再作为价格回退来源。
        """
        rolled_symbols = 0
        account = broker.account

        for sym, bar in bar_data.items():
            if not bool(bar.get('is_fresh', True)):
                continue
            if not self._is_month_change(bar):
                continue

            old_contract_value = bar.get('previous_underlying_symbol')
            new_contract_value = bar.get('underlying_symbol')
            old_contract = (
                '' if old_contract_value is None or pd.isna(old_contract_value)
                else str(old_contract_value).strip()
            )
            new_contract = (
                '' if new_contract_value is None or pd.isna(new_contract_value)
                else str(new_contract_value).strip()
            )
            old_close_price = bar.get('roll_old_close')
            roll_open_price = bar.get('roll_new_open')

            valid_contracts = (
                old_contract
                and new_contract
                and old_contract.lower() != new_contract.lower()
            )
            try:
                valid_prices = all(
                    value is not None and not pd.isna(value) and float(value) > 0
                    for value in (old_close_price, roll_open_price)
                )
            except (TypeError, ValueError):
                valid_prices = False
            if not valid_contracts or not valid_prices:
                print(
                    f"[Rollover Warning] {current_time} | {sym} | "
                    "换月事件缺少有效的旧/新合约或真实价格，已跳过。"
                )
                continue

            old_close_price = float(old_close_price)
            roll_open_price = float(roll_open_price)
            event_key = (sym, current_time, old_contract.lower(), new_contract.lower())
            if event_key in self._processed_events:
                continue

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
                    old_close_price, roll_open_price, current_time,
                    old_contract=old_contract,
                    new_contract=new_contract,
                )
                sym_rolled = True

            if sym_rolled:
                self._processed_events.add(event_key)
                rolled_symbols += 1
                print(
                    f"[Rollover] {current_time} | {old_contract} -> {new_contract} | "
                    f"旧合约收盘:{old_close_price} -> 新合约开盘:{roll_open_price}"
                )

        return rolled_symbols
