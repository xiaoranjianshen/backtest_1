# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableNote:
    category: str
    description: str
    review_status: str = "待复核"


MARKET_DATA_CATEGORIES = {
    "指数数据",
    "期货行情",
    "Tick 行情",
    "主力换月/复权",
    "主力映射",
    "流动性指标",
    "筹码/成交分布",
    "期权波动率",
}

FUNDAMENTAL_DATA_CATEGORIES = {
    "宏观利率",
    "中国宏观",
    "海外宏观",
    "股票市场",
    "现货价格",
    "席位持仓",
}

DATA_GROUP_ORDER = ("行情数据", "基本面数据", "其他")


TABLE_NOTES: dict[tuple[str, str], TableNote] = {
    ("futures_data", "NHCI_all"): TableNote("指数数据", "南华商品指数及分项指数日度数据，包含工业品、农产品、金属、贵金属、黑色、油脂油料、有色金属等指数列。", "较确定"),

    ("futures_data", "chip_daily"): TableNote("筹码/成交分布", "按交易日、合约和价格聚合的成交筹码分布数据，chip_vol 表示该价格层的成交量或筹码量。", "需复核"),
    ("futures_data", "daily_liquidity_gap"): TableNote("流动性指标", "按日统计的合约流动性缺口和平均买卖价差指标，包含 avg_gap、avg_spread_bp、tick_count。", "较确定"),
    ("futures_data", "expert_trading_replication"): TableNote("交易复刻", "高手/专家交易复刻明细表，包含成交方向、价格、数量、名义金额、手续费、开平标记和毫秒级实时戳。", "需复核"),
    ("futures_data", "implied_volatility"): TableNote("期权波动率", "期权隐含波动率表，包含标的价格、历史波动率、平值看涨/看跌隐含波动率。", "较确定"),
    ("futures_data", "spot_prices_detailed"): TableNote("现货价格", "商品现货或相关指标价格明细，包含品种、价格、来源、国家、指标名称和 Wind 代码。", "较确定"),

    ("futures_data", "daily_adj_factor"): TableNote("主力换月/复权", "日频主力连续复权和换月辅助表，包含当前主力合约、换月标记 month_change 和 adjust_factor。", "较确定"),
    ("futures_data", "main_contract_mapping"): TableNote("主力映射", "日频主力合约映射表，记录品种在每个日期对应的主力明细合约。", "较确定"),
    ("futures_data", "m_daily_mapping"): TableNote("主力映射", "（旧）旧版主力合约日度映射表，main_code 为当天主力合约；当前回测换月逻辑不直接依赖该表。", "旧表"),
    ("futures_data", "m_daily_mapping_oi_1_2x"): TableNote("主力映射", "（旧）旧版持仓量主力映射实验表，疑似带 1.2 倍持仓切换条件；当前回测换月逻辑不直接依赖该表。", "旧表"),
    ("futures_data", "m_daily_mapping_oi_max"): TableNote("主力映射", "（旧）旧版最大持仓量主力映射表，可用于历史校验，但当前回测换月逻辑不直接依赖该表。", "旧表"),

    ("futures_data", "daily_data"): TableNote("期货行情", "（旧）旧版/样例日线 OHLCV 表，数据规模很小且缺少 open_interest；当前日线请优先使用 m_daily、i_daily、day1_data。", "旧表"),
    ("futures_data", "day1_data"): TableNote("期货行情", "全市场明细合约日线行情，包含 symbol、datetime、OHLC、volume、open_interest。", "较确定"),
    ("futures_data", "m_daily"): TableNote("期货行情", "主力连续未复权日线行情，回测 data_type=main、freq=1d 默认使用该表。", "较确定"),
    ("futures_data", "i_daily"): TableNote("期货行情", "期货指数连续日线行情，通常用于指数或加权连续价格分析。", "较确定"),
    ("futures_data", "min1_data"): TableNote("期货行情", "（旧）旧版 1 分钟 OHLCV 表，字段不含持仓量，当前 1m 请优先使用 futures_main_1m、futures_main_1m_adj、futures_index_1m、futures_all_1m。", "旧表"),
    ("futures_data", "min5_data"): TableNote("期货行情", "（旧）旧版 5 分钟 OHLCV 表，字段不含持仓量，当前 5m 请优先使用 futures_main_5m、futures_index_5m、futures_all_5m。", "旧表"),
    ("futures_data", "min15_data"): TableNote("期货行情", "（旧）旧版 15 分钟 OHLCV 表，字段不含持仓量，当前回测路由未接入该表；如需 15m 建议从新版分钟表重采样或重新生成。", "旧表"),
    ("futures_data", "min30_data"): TableNote("期货行情", "（旧）旧版 30 分钟 OHLCV 表，字段不含持仓量，当前回测路由未接入该表；如需 30m 建议从新版分钟表重采样或重新生成。", "旧表"),
    ("futures_data", "hour1_data"): TableNote("期货行情", "（旧）旧版 1 小时 OHLCV 表，字段不含持仓量，当前回测路由未接入该表；如需 1h 建议从新版分钟表重采样或重新生成。", "旧表"),
    ("futures_data", "hq_1m"): TableNote("期货行情", "（旧）旧版 1 分钟行情表，虽包含 open_interest，但当前回测路由已切到 futures_*_1m 系列表；只建议用于历史对账。", "旧表"),

    ("futures_data", "futures_all_1m"): TableNote("期货行情", "全市场明细合约 1 分钟 K 线，包含具体合约 symbol、OHLC、volume、open_interest。", "较确定"),
    ("futures_data", "futures_all_5m"): TableNote("期货行情", "全市场明细合约 5 分钟 K 线，包含具体合约 symbol、OHLC、volume、open_interest。", "较确定"),
    ("futures_data", "futures_main_1m"): TableNote("期货行情", "主力连续未复权 1 分钟 K 线，回测 freq=1m、data_type=main 默认使用该表。", "较确定"),
    ("futures_data", "futures_main_1m_adj"): TableNote("期货行情", "主力连续等比复权 1 分钟 K 线，包含 underlying_symbol、month_change、adjust_factor，用于均线/因子等连续价格计算。", "较确定"),
    ("futures_data", "futures_main_5m"): TableNote("期货行情", "主力连续未复权 5 分钟 K 线，回测 freq=5m、data_type=main 默认使用该表。", "较确定"),
    ("futures_data", "futures_index_1m"): TableNote("期货行情", "期货指数连续 1 分钟 K 线，包含指数合约 symbol、OHLC、volume、open_oi。", "较确定"),
    ("futures_data", "futures_index_5m"): TableNote("期货行情", "期货指数连续 5 分钟 K 线，包含指数合约 symbol、OHLC、volume、open_oi。", "较确定"),
    ("futures_data", "main_tick"): TableNote("Tick 行情", "主力连续 tick/盘口快照数据，包含 last_price、累计 volume、买一卖一价格和挂单量。", "较确定"),
    ("futures_data", "tick_all_data"): TableNote("Tick 行情", "全市场明细合约 tick/盘口快照数据，数据量很大，包含 last_price、累计 volume、open_interest、买一卖一价格和挂单量。", "较确定"),

    ("futures_data", "institution_positions"): TableNote("席位持仓", "机构/席位持仓统计表，按日期、席位、板块、品种记录 long_pos、short_pos、net_pos。", "较确定"),
    ("futures_data", "pos_9"): TableNote("席位持仓", "（旧）旧版席位持仓表，字段结构与 institution_positions 接近；当前席位持仓请优先看 institution_positions。", "旧表"),
    ("futures_data", "pos_his"): TableNote("席位持仓", "（旧）历史席位持仓表，包含 broker、sector、symbol、long_pos、short_pos、net_pos；当前新增席位数据请优先看 institution_positions。", "旧表"),

    ("futures_data", "cn_10y_yield"): TableNote("宏观利率", "中国 10 年期国债收益率时间序列。", "较确定"),
    ("futures_data", "cn_1y_ytm"): TableNote("宏观利率", "中国 1 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "cn_2y_ytm"): TableNote("宏观利率", "中国 2 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "cn_3y_ytm"): TableNote("宏观利率", "中国 3 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "cn_5y_ytm"): TableNote("宏观利率", "中国 5 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "cn_10y_ytm"): TableNote("宏观利率", "中国 10 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "cn_30y_ytm"): TableNote("宏观利率", "中国 30 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "cn_lpr_1y"): TableNote("宏观利率", "中国 1 年期 LPR 贷款市场报价利率。", "较确定"),
    ("futures_data", "cn_lpr_5y"): TableNote("宏观利率", "中国 5 年期以上 LPR 贷款市场报价利率。", "较确定"),
    ("futures_data", "cn_general_loan_rate"): TableNote("宏观利率", "中国一般贷款利率时间序列，频率和来源需复核。", "需复核"),

    ("futures_data", "cn_cpi_yoy"): TableNote("中国宏观", "中国 CPI 同比增速。", "较确定"),
    ("futures_data", "cn_ppi_yoy"): TableNote("中国宏观", "中国 PPI 同比增速。", "较确定"),
    ("futures_data", "cn_pmi"): TableNote("中国宏观", "中国制造业 PMI 或综合 PMI 指标，具体口径需复核。", "需复核"),
    ("futures_data", "cn_exports_yoy"): TableNote("中国宏观", "中国出口金额同比增速。", "较确定"),
    ("futures_data", "cn_retail_sales_yoy"): TableNote("中国宏观", "中国社会消费品零售总额同比增速。", "较确定"),
    ("futures_data", "cn_m2_absolute"): TableNote("中国宏观", "中国 M2 货币供应量绝对值。", "较确定"),
    ("futures_data", "cn_m2_yoy"): TableNote("中国宏观", "中国 M2 货币供应量同比增速。", "较确定"),
    ("futures_data", "cn_socfin_increment"): TableNote("中国宏观", "中国社会融资规模增量。", "较确定"),
    ("futures_data", "cn_socfin_yoy"): TableNote("中国宏观", "中国社会融资规模同比增速或存量同比，具体口径需复核。", "需复核"),
    ("futures_data", "cn_new_loans_hh_ml"): TableNote("中国宏观", "中国居民中长期新增贷款或住户中长期贷款指标。", "需复核"),
    ("futures_data", "cn_A_share_capital"): TableNote("股票市场", "中国 A 股总市值或市值类指标，具体口径需复核。", "需复核"),
    ("futures_data", "cn_all_a_pe_ttm"): TableNote("股票市场", "中国全 A 股 PE TTM 估值指标。", "较确定"),
    ("futures_data", "cn_margin_balance"): TableNote("股票市场", "中国 A 股融资融券余额或融资余额指标。", "较确定"),

    ("futures_data", "us_1y_ytm"): TableNote("海外宏观", "美国 1 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "us_2y_ytm"): TableNote("海外宏观", "美国 2 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "us_3y_ytm"): TableNote("海外宏观", "美国 3 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "us_5y_ytm"): TableNote("海外宏观", "美国 5 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "us_10y_ytm"): TableNote("海外宏观", "美国 10 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "us_30y_ytm"): TableNote("海外宏观", "美国 30 年期国债到期收益率时间序列。", "较确定"),
    ("futures_data", "us_10y_yield"): TableNote("海外宏观", "美国 10 年期国债收益率时间序列。", "较确定"),
    ("futures_data", "us_cpi_yoy"): TableNote("海外宏观", "美国 CPI 同比增速。", "较确定"),
    ("futures_data", "us_fed_funds_rate"): TableNote("海外宏观", "美国联邦基金目标利率或有效联邦基金利率。", "需复核"),
    ("futures_data", "us_nonfarm_added"): TableNote("海外宏观", "美国非农新增就业人数。", "较确定"),
    ("futures_data", "us_unemployment_rate"): TableNote("海外宏观", "美国失业率。", "较确定"),
    ("futures_data", "us_dollar_index"): TableNote("海外宏观", "美元指数时间序列。", "较确定"),
    ("futures_data", "us_vix"): TableNote("海外宏观", "VIX 恐慌指数时间序列。", "较确定"),
}


def get_table_note(database: str, table: str) -> TableNote:
    note = TABLE_NOTES.get((database, table))
    if note is not None:
        return note
    lower = table.lower()
    if lower.startswith("cn_"):
        return TableNote("中国宏观", "中国宏观或金融市场时间序列表，具体指标含义需根据表名和数据源复核。", "需复核")
    if lower.startswith("us_"):
        return TableNote("海外宏观", "美国或海外宏观金融时间序列表，具体指标含义需根据表名和数据源复核。", "需复核")
    if "tick" in lower:
        return TableNote("Tick 行情", "Tick 或盘口快照数据表，具体字段和合约口径需复核。", "需复核")
    if "daily" in lower or "day" in lower:
        return TableNote("日频数据", "日频数据表，具体口径需根据字段和数据源复核。", "需复核")
    if "min" in lower or "1m" in lower or "5m" in lower:
        return TableNote("分钟行情", "分钟级行情或衍生指标表，具体口径需复核。", "需复核")
    return TableNote("未分类", "暂未确认含义的数据表，请复核后补充说明。", "需复核")


def table_data_group(database: str, table: str) -> str:
    category = get_table_note(database, table).category
    if category in MARKET_DATA_CATEGORIES:
        return "行情数据"
    if category in FUNDAMENTAL_DATA_CATEGORIES:
        return "基本面数据"
    return "其他"


def table_note_dict(database: str, table: str) -> dict[str, str]:
    note = get_table_note(database, table)
    return {
        "category": note.category,
        "description": note.description,
        "review_status": note.review_status,
        "data_group": table_data_group(database, table),
    }
