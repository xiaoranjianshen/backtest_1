# ==========================================
# 全局静态配置中心 (Config)
# ==========================================
import os
import re

# ------------------------------------------
# 1. 基础设施配置 (Infrastructure)
# ------------------------------------------
CH_HOST = 'localhost'
# 192.168.100.23
CH_USER = 'default'
CH_PASS = ''

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'cache_data')


# ------------------------------------------
# 2. 核心枚举字典 (Options / Enums)
# ⚠️ 极其重要：外部调用 data_provider 时，必须从以下列表中选择参数！
# ------------------------------------------

# 【可选频率 freq】
# 'tick' : 逐笔/切片数据 (包含买卖五档)
# '1m'   : 1分钟线
# '5m'   : 5分钟线 (未来扩展)
# '15m'  : 15分钟线 (未来扩展)
# '1d'   : 日线数据

# 【可选数据形态 data_type】
# 'main'     : 真实的主力连续合约 (未复权，包含换月跳空的真实绝对价格)
# 'main_adj' : 等比复权主力连续合约 (消除跳空，用于计算均线、Z-Score等信号)
# 'index'    : 指数连续合约 (KQ.i，按持仓量加权的平滑曲线)
# 'all'      : 全市场所有明细合约 (如 RB2605，最底层的真实单月合约)
# 'spot'     : 现货基差数据 (预留扩展)
# 'options'  : 期权隐含波动率/希腊字母数据 (预留扩展)

# 【可选交易所 exchange】
# 'SHFE'  : 上海期货交易所 (上期所)
# 'INE'   : 上海国际能源交易中心 (能源中心)
# 'DCE'   : 大连商品交易所 (大商所)
# 'CZCE'  : 郑州商品交易所 (郑商所)
# 'GFEX'  : 广州期货交易所 (广期所)
# 'CFFEX' : 中国金融期货交易所 (中金所)

# 【可选板块分类 sector】
# '黑色', '有色', '贵金属', '化工', '能源', '油脂油料', '软商品', '生鲜', '建材', '股指', '国债', '航运', '新能源'


# ------------------------------------------
# 3. 数据表智能路由字典 (Data Routing Dictionary)
# ------------------------------------------
# 结构: freq -> data_type -> {'db': 数据库名, 'table': 表名}
DB_ROUTING_MAP = {
    'tick': {
        'main': {'db': 'main_contract_tick_data', 'table': 'main_tick'},
        'all':  {'db': 'tick_data', 'table': 'tick_all_data'}
    },
    '1m': {
        'main':     {'db': 'adjusted_im_1_5_min', 'table': 'futures_main_1m'},
        'main_adj': {'db': 'adjusted_im_1_5_min', 'table': 'futures_main_1m_adj'},
        'index':    {'db': 'adjusted_im_1_5_min', 'table': 'futures_index_1m'},
        'all':      {'db': 'adjusted_im_1_5_min', 'table': 'futures_all_1m'}
    },
    '5m': {
        'main':     {'db': 'adjusted_im_1_5_min', 'table': 'futures_main_5m'},
        'index':    {'db': 'adjusted_im_1_5_min', 'table': 'futures_index_5m'},
        'all':      {'db': 'adjusted_im_1_5_min', 'table': 'futures_all_5m'}
    },
    '1d': {
        'main':  {'db': 'i_contract_daily_data', 'table': 'm_daily'},
        'index': {'db': 'i_contract_daily_data', 'table': 'i_daily'},
        'all':   {'db': 'large_timeframe_data', 'table': 'day1_data'}
    }
}


# ------------------------------------------
# 4. 品种静态属性字典 (Symbol MetaData)
# ------------------------------------------
# 格式: '代码': [合约乘数, 最小变动价位, '交易所', '板块']
SYMBOL_DICT = {
    # --- 郑商所 (CZCE) ---
    'AP': [10, 1.0, 'CZCE', '生鲜'],     'CF': [5, 5.0, 'CZCE', '软商品'],
    'CJ': [5, 5.0, 'CZCE', '生鲜'],      'CY': [5, 5.0, 'CZCE', '软商品'],
    'FG': [20, 1.0, 'CZCE', '建材'],     'MA': [10, 1.0, 'CZCE', '化工'],
    'OI': [10, 1.0, 'CZCE', '油脂油料'], 'PF': [5, 2.0, 'CZCE', '化工'],
    'PK': [5, 2.0, 'CZCE', '生鲜'],      'PL': [20, 1.0, 'CZCE', '化工'],
    'PR': [15, 2.0, 'CZCE', '化工'],     'PX': [5, 2.0, 'CZCE', '化工'],
    'RM': [10, 1.0, 'CZCE', '油脂油料'], 'SA': [20, 1.0, 'CZCE', '建材'],
    'SF': [5, 2.0, 'CZCE', '黑色'],      'SH': [30, 1.0, 'CZCE', '化工'],
    'SM': [5, 2.0, 'CZCE', '黑色'],      'SR': [10, 1.0, 'CZCE', '软商品'],
    'TA': [5, 2.0, 'CZCE', '化工'],      'UR': [20, 1.0, 'CZCE', '化工'],

    # --- 大商所 (DCE) ---
    'a': [10, 1.0, 'DCE', '油脂油料'],   'b': [10, 1.0, 'DCE', '油脂油料'],
    'c': [10, 1.0, 'DCE', '软商品'],     'cs': [10, 1.0, 'DCE', '软商品'],
    'eb': [5, 1.0, 'DCE', '化工'],       'eg': [10, 1.0, 'DCE', '化工'],
    'i': [100, 0.5, 'DCE', '黑色'],      'j': [100, 0.5, 'DCE', '黑色'],
    'jd': [10, 1.0, 'DCE', '生鲜'],      'jm': [60, 0.5, 'DCE', '黑色'],
    'l': [5, 1.0, 'DCE', '化工'],        'lg': [90, 0.5, 'DCE', '建材'],
    'lh': [16, 5.0, 'DCE', '生鲜'],      'm': [10, 1.0, 'DCE', '油脂油料'],
    'p': [10, 2.0, 'DCE', '油脂油料'],   'pg': [20, 1.0, 'DCE', '能源'],
    'pp': [5, 1.0, 'DCE', '化工'],       'rr': [10, 1.0, 'DCE', '软商品'],
    'v': [5, 1.0, 'DCE', '化工'],        'y': [10, 2.0, 'DCE', '油脂油料'],
    'bz': [30, 1.0, 'DCE', '化工'],

    # --- 上期所 & 能源中心 (SHFE & INE) ---
    'ad': [10, 5.0, 'SHFE', '有色'],     'ag': [15, 1.0, 'SHFE', '贵金属'],
    'al': [5, 5.0, 'SHFE', '有色'],      'ao': [20, 1.0, 'SHFE', '有色'],
    'au': [1000, 0.02, 'SHFE', '贵金属'], 'bc': [5, 10.0, 'INE', '有色'],
    'br': [5, 5.0, 'SHFE', '化工'],      'bu': [10, 1.0, 'SHFE', '能源'],
    'cu': [5, 10.0, 'SHFE', '有色'],     'fu': [10, 1.0, 'SHFE', '能源'],
    'hc': [10, 1.0, 'SHFE', '黑色'],     'lu': [10, 1.0, 'INE', '能源'],
    'ni': [1, 10.0, 'SHFE', '有色'],     'nr': [10, 5.0, 'INE', '化工'],
    'op': [40, 2.0, 'SHFE', '软商品'],   'pb': [5, 5.0, 'SHFE', '有色'],
    'rb': [10, 1.0, 'SHFE', '黑色'],     'ru': [10, 5.0, 'SHFE', '化工'],
    'sc': [1000, 0.1, 'INE', '能源'],    'sn': [1, 10.0, 'SHFE', '有色'],
    'sp': [10, 2.0, 'SHFE', '软商品'],   'ss': [5, 5.0, 'SHFE', '黑色'],
    'zn': [5, 5.0, 'SHFE', '有色'],      'ec': [50, 0.1, 'INE', '航运'],

    # --- 广期所 (GFEX) ---
    'si': [5, 5.0, 'GFEX', '新能源'],    'ps': [3, 5.0, 'GFEX', '新能源'],
    'lc': [1, 20.0, 'GFEX', '新能源'],   'pd': [1000, 0.05, 'GFEX', '贵金属'],
    'pt': [1000, 0.05, 'GFEX', '贵金属'],

    # --- 中金所 (CFFEX) ---
    'IC': [200, 0.2, 'CFFEX', '股指'],   'IF': [300, 0.2, 'CFFEX', '股指'],
    'IH': [300, 0.2, 'CFFEX', '股指'],   'IM': [200, 0.2, 'CFFEX', '股指'],
    'T':  [10000, 0.005, 'CFFEX', '国债'], 'TF': [10000, 0.005, 'CFFEX', '国债'],
    'TL': [10000, 0.01, 'CFFEX', '国债'],  'TS': [20000, 0.002, 'CFFEX', '国债']
}

NAME_TO_CODE = {
    '苹果': 'AP', '棉花': 'CF', '红枣': 'CJ', '棉纱': 'CY', '玻璃': 'FG', '甲醇': 'MA',
    '菜籽油': 'OI', '短纤': 'PF', '花生': 'PK', '丙烯': 'PL', '瓶片': 'PR', '对二甲苯': 'PX',
    '菜粕': 'RM', '纯碱': 'SA', '硅铁': 'SF', '烧碱': 'SH', '锰硅': 'SM',
    '白糖': 'SR', 'PTA': 'TA', '尿素': 'UR', '豆一': 'a', '豆二': 'b',
    '玉米': 'c', '淀粉': 'cs', '苯乙烯': 'eb', '乙二醇': 'eg', '铁矿石': 'i',
    '焦炭': 'j', '鸡蛋': 'jd', '焦煤': 'jm', '塑料': 'l', '原木': 'lg', '生猪': 'lh', '豆粕': 'm', '棕榈油': 'p',
    '液化气': 'pg', '聚丙烯': 'pp', '粳米': 'rr', 'PVC': 'v', '豆油': 'y', '纯苯': 'bz', '铸造铝合金': 'ad', '液化石油气': 'pg', '玉米淀粉': 'cs',
    '白银': 'ag', '铝': 'al', '氧化铝': 'ao', '黄金': 'au', '国际铜': 'bc', '合成橡胶': 'br', '丁二烯橡胶': 'br',
    '沥青': 'bu', '铜': 'cu', '燃料油': 'fu', '热轧卷板': 'hc', '低硫燃料油': 'lu', '镍': 'ni', '20号胶': 'nr',
    '胶版纸': 'op', '双胶纸': 'op', '铅': 'pb', '螺纹钢': 'rb', '天然橡胶': 'ru', '原油': 'sc', '锡': 'sn',
    '纸浆': 'sp', '不锈钢': 'ss', '锌': 'zn', '集装箱运价指数': 'ec', '集运指数': 'ec',
    '工业硅': 'si', '多晶硅': 'ps', '碳酸锂': 'lc', '钯': 'pd', '铂': 'pt',
    '中证500股指': 'IC', '沪深300股指': 'IF', '上证50股指': 'IH', '中证1000股指': 'IM',
    '10年期国债': 'T', '5年期国债': 'TF', '30年期国债': 'TL', '2年期国债': 'TS'
}
# ==========================================
# 5. 期货品种完全体费率与保证金规则字典 (Fee & Margin MetaData)
# ==========================================
# 说明：
# fee_type: 'ratio' (按名义本金比例，如万分之一), 'fixed' (按固定金额，如3元/手)
# margin_rate: 交易所基准保证金比例 (如 0.09 代表 9%)
# fee_open: 开仓费率/金额
# fee_close_history: 平昨仓费率/金额
# fee_close_today: 平今仓费率/金额

FEE_DICT = {
    # ================= 🟢 已精准核对：上海期货交易所 (SHFE) =================
    'rb': { 'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.09, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0001 },
    'hc': { 'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.09, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0001 },
    'cu': { 'multiplier': 5, 'tick_size': 10.0, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.00005, 'fee_close_history': 0.00005, 'fee_close_today': 0.0001 }, # ⚠️ 铜平今加倍
    'al': { 'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.12, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0 },
    'zn': { 'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.12, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0 },
    'pb': { 'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.00004, 'fee_close_history': 0.00004, 'fee_close_today': 0.00004 },
    'ao': { 'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0001 },
    'ss': { 'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 2.0, 'fee_close_history': 2.0, 'fee_close_today': 2.0 },
    'ni': { 'multiplier': 1, 'tick_size': 10.0, 'margin_rate': 0.14, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0 },
    'sn': { 'multiplier': 1, 'tick_size': 10.0, 'margin_rate': 0.14, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0 },
    'au': { 'multiplier': 1000, 'tick_size': 0.02, 'margin_rate': 0.16, 'fee_type': 'fixed', 'fee_open': 20.0, 'fee_close_history': 20.0, 'fee_close_today': 20.0 }, # ⚠️ 使用标准合约费率
    'ag': { 'multiplier': 15, 'tick_size': 1.0, 'margin_rate': 0.21, 'fee_type': 'ratio', 'fee_open': 0.00005, 'fee_close_history': 0.00005, 'fee_close_today': 0.00005 },
    'ru': { 'multiplier': 10, 'tick_size': 5.0, 'margin_rate': 0.11, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0 },
    'bu': { 'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.11, 'fee_type': 'ratio', 'fee_open': 0.00005, 'fee_close_history': 0.00005, 'fee_close_today': 0.00005 },
    'sp': { 'multiplier': 10, 'tick_size': 2.0, 'margin_rate': 0.09, 'fee_type': 'ratio', 'fee_open': 0.00005, 'fee_close_history': 0.00005, 'fee_close_today': 0.00005 },
    'br': { 'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.11, 'fee_type': 'ratio', 'fee_open': 0.00002, 'fee_close_history': 0.00002, 'fee_close_today': 0.00002 },
    'ad': { 'multiplier': 10, 'tick_size': 5.0, 'margin_rate': 0.07, 'fee_type': 'ratio', 'fee_open': 0.00005, 'fee_close_history': 0.00005, 'fee_close_today': 0.00005 },
    'op': { 'multiplier': 40, 'tick_size': 2.0, 'margin_rate': 0.09, 'fee_type': 'ratio', 'fee_open': 0.00005, 'fee_close_history': 0.00005, 'fee_close_today': 0.00005 },
    'fu': { 'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.11, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0003 }, # ⚠️ 燃油平今惩罚三倍

    # ----------------- 🟡 精准核对：上海国际能源交易中心 (INE, 严格小写) -----------------
    'nr': {'multiplier': 10, 'tick_size': 5.0, 'margin_rate': 0.11, 'fee_type': 'ratio', 'fee_open': 0.00002,'fee_close_history': 0.00002, 'fee_close_today': 0.00002},
    'bc': {'multiplier': 5, 'tick_size': 10.0, 'margin_rate': 0.09, 'fee_type': 'ratio', 'fee_open': 0.00001,'fee_close_history': 0.00001, 'fee_close_today': 0.00001},
    'sc': {'multiplier': 1000, 'tick_size': 0.1, 'margin_rate': 0.11, 'fee_type': 'fixed', 'fee_open': 40.0, 'fee_close_history': 40.0, 'fee_close_today': 240.0},  # ⚠️ 原油平今惩罚高达240元
    'lu': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.11, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0003},  # ⚠️ 低硫燃料油平今三倍
    'ec': {'multiplier': 50, 'tick_size': 0.1, 'margin_rate': 0.17, 'fee_type': 'ratio', 'fee_open': 0.0006,'fee_close_history': 0.0006, 'fee_close_today': 0.0012},  # ⚠️ 集运欧线平今翻倍

    # ----------------- 🟡 精准核对：郑州商品交易所 (CZCE, 严格大写) -----------------
    'CF': {'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.07, 'fee_type': 'fixed', 'fee_open': 4.3,'fee_close_history': 4.3, 'fee_close_today': 4.3},
    'CY': {'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.08, 'fee_type': 'fixed', 'fee_open': 1.0,'fee_close_history': 1.0, 'fee_close_today': 1.0},
    'SR': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.08, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'TA': {'multiplier': 5, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'PF': {'multiplier': 5, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 2.0,'fee_close_history': 2.0, 'fee_close_today': 2.0},
    'FG': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.12, 'fee_type': 'fixed', 'fee_open': 6.0,'fee_close_history': 6.0, 'fee_close_today': 6.0},  # ⚠️ 玻璃更新为6元
    'MA': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'SA': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.0002,'fee_close_history': 0.0002, 'fee_close_today': 0.0002},
    'RM': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 1.5, 'fee_close_history': 1.5, 'fee_close_today': 1.5},
    'OI': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 2.0,'fee_close_history': 2.0, 'fee_close_today': 2.0},
    'ZC': {'multiplier': 100, 'tick_size': 0.2, 'margin_rate': 0.50, 'fee_type': 'fixed', 'fee_open': 150.0,'fee_close_history': 150.0, 'fee_close_today': 150.0},  # 动力煤
    'WH': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.15, 'fee_type': 'fixed', 'fee_open': 30.0, 'fee_close_history': 30.0, 'fee_close_today': 30.0},  # 强麦
    'PM': {'multiplier': 50, 'tick_size': 1.0, 'margin_rate': 0.15, 'fee_type': 'fixed', 'fee_open': 30.0, 'fee_close_history': 30.0, 'fee_close_today': 30.0},  # 普麦
    'RI': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.15, 'fee_type': 'fixed', 'fee_open': 2.5,'fee_close_history': 2.5, 'fee_close_today': 2.5},  # 早籼稻
    'LR': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.15, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0},  # 晚籼稻
    'JR': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.15, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},  # 粳稻
    'AP': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.12, 'fee_type': 'fixed', 'fee_open': 5.0,'fee_close_history': 5.0, 'fee_close_today': 20.0},  # ⚠️ 苹果平今惩罚高达20元
    'UR': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'PK': {'multiplier': 5, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 4.0,'fee_close_history': 4.0, 'fee_close_today': 4.0},
    'RS': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.20, 'fee_type': 'fixed', 'fee_open': 2.0, 'fee_close_history': 2.0, 'fee_close_today': 2.0},  # 油菜籽
    'SM': {'multiplier': 5, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'SF': {'multiplier': 5, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'SH': {'multiplier': 30, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0002,'fee_close_history': 0.0002, 'fee_close_today': 0.0002},
    'PR': {'multiplier': 15, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.00005, 'fee_close_history': 0.00005, 'fee_close_today': 0.00005},
    'PL': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'CJ': {'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.12, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'PX': {'multiplier': 5, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0001},

    # ----------------- 🟡 精准核对：大连商品交易所 (DCE, 严格小写) -----------------
    'a': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.09, 'fee_type': 'fixed', 'fee_open': 2.0,'fee_close_history': 2.0, 'fee_close_today': 2.0},
    'b': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.09, 'fee_type': 'fixed', 'fee_open': 1.0,'fee_close_history': 1.0, 'fee_close_today': 1.0},
    'm': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.09, 'fee_type': 'fixed', 'fee_open': 1.5,'fee_close_history': 1.5, 'fee_close_today': 1.5},
    'y': {'multiplier': 10, 'tick_size': 2.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 2.5,'fee_close_history': 2.5, 'fee_close_today': 2.5},
    'p': {'multiplier': 10, 'tick_size': 2.0, 'margin_rate': 0.11, 'fee_type': 'fixed', 'fee_open': 2.5,'fee_close_history': 2.5, 'fee_close_today': 2.5},
    'jm': {'multiplier': 60, 'tick_size': 0.5, 'margin_rate': 0.14, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'j': {'multiplier': 100, 'tick_size': 0.5, 'margin_rate': 0.20, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'i': {'multiplier': 100, 'tick_size': 0.5, 'margin_rate': 0.13, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'c': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.09, 'fee_type': 'fixed', 'fee_open': 1.2,'fee_close_history': 1.2, 'fee_close_today': 1.2},
    'cs': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.08, 'fee_type': 'fixed', 'fee_open': 1.5,'fee_close_history': 1.5, 'fee_close_today': 1.5},
    'eg': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.11, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'eb': {'multiplier': 5, 'tick_size': 1.0, 'margin_rate': 0.11, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'pg': {'multiplier': 20, 'tick_size': 1.0, 'margin_rate': 0.11, 'fee_type': 'fixed', 'fee_open': 6.0,'fee_close_history': 6.0, 'fee_close_today': 12.0},  # ⚠️ PG平今翻倍为12元
    'rr': {'multiplier': 10, 'tick_size': 1.0, 'margin_rate': 0.08, 'fee_type': 'fixed', 'fee_open': 1.0,'fee_close_history': 1.0, 'fee_close_today': 1.0},
    'bb': {'multiplier': 500, 'tick_size': 0.05, 'margin_rate': 0.15, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'fb': {'multiplier': 10, 'tick_size': 0.05, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'lg': {'multiplier': 90, 'tick_size': 0.5, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'bz': {'multiplier': 30, 'tick_size': 1.0, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'jd': {'multiplier': 5, 'tick_size': 1.0, 'margin_rate': 0.09, 'fee_type': 'ratio', 'fee_open': 0.00015,'fee_close_history': 0.00015, 'fee_close_today': 0.00015},
    'lh': {'multiplier': 16, 'tick_size': 5.0, 'margin_rate': 0.10, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'l': {'multiplier': 5, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 1.0,'fee_close_history': 1.0, 'fee_close_today': 1.0},
    'v': {'multiplier': 5, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 1.0,'fee_close_history': 1.0, 'fee_close_today': 1.0},
    'pp': {'multiplier': 5, 'tick_size': 1.0, 'margin_rate': 0.10, 'fee_type': 'fixed', 'fee_open': 1.0,'fee_close_history': 1.0, 'fee_close_today': 1.0},

    # ----------------- 🟡 精准核对：广州期货交易所 (GFEX, 严格小写) -----------------
    'si': {'multiplier': 5, 'tick_size': 5.0, 'margin_rate': 0.13, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'lc': {'multiplier': 1, 'tick_size': 20.0, 'margin_rate': 0.17, 'fee_type': 'ratio', 'fee_open': 0.00032,'fee_close_history': 0.00032, 'fee_close_today': 0.00032},
    'ps': {'multiplier': 3, 'tick_size': 5.0, 'margin_rate': 0.15, 'fee_type': 'ratio', 'fee_open': 0.0005,'fee_close_history': 0.0005, 'fee_close_today': 0.0005},
    'pt': {'multiplier': 1000, 'tick_size': 0.05, 'margin_rate': 0.19, 'fee_type': 'ratio', 'fee_open': 0.0001,'fee_close_history': 0.0001, 'fee_close_today': 0.0001},
    'pd': {'multiplier': 1000, 'tick_size': 0.05, 'margin_rate': 0.19, 'fee_type': 'ratio', 'fee_open': 0.0001, 'fee_close_history': 0.0001, 'fee_close_today': 0.0001},

    # ----------------- 🟡 精准核对：中国金融期货交易所 (CFFEX, 严格大写) -----------------
    'IF': {'multiplier': 300, 'tick_size': 0.2, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.000023, 'fee_close_history': 0.000023, 'fee_close_today': 0.00023},  # ⚠️ 股指平今10倍惩罚
    'IH': {'multiplier': 300, 'tick_size': 0.2, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.000023,'fee_close_history': 0.000023, 'fee_close_today': 0.00023},  # ⚠️ 股指平今10倍惩罚
    'IC': {'multiplier': 200, 'tick_size': 0.2, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.000023, 'fee_close_history': 0.000023, 'fee_close_today': 0.00023},  # ⚠️ 股指平今10倍惩罚
    'IM': {'multiplier': 200, 'tick_size': 0.2, 'margin_rate': 0.12, 'fee_type': 'ratio', 'fee_open': 0.000023, 'fee_close_history': 0.000023, 'fee_close_today': 0.00023},  # ⚠️ 股指平今10倍惩罚
    'TS': {'multiplier': 20000, 'tick_size': 0.002, 'margin_rate': 0.005, 'fee_type': 'fixed', 'fee_open': 3.0, 'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'TF': {'multiplier': 10000, 'tick_size': 0.005, 'margin_rate': 0.012, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'T': {'multiplier': 10000, 'tick_size': 0.005, 'margin_rate': 0.02, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0},
    'TL': {'multiplier': 10000, 'tick_size': 0.01, 'margin_rate': 0.035, 'fee_type': 'fixed', 'fee_open': 3.0,'fee_close_history': 3.0, 'fee_close_today': 3.0}
}


# ------------------------------------------
# 6. 查询代码构建 (ClickHouse Symbol Builder)
# ------------------------------------------
_UPPERCASE_EXCHANGES = frozenset({'CZCE', 'CFFEX'})


def canonical_symbol_code(pure_letters: str) -> str:
    """从 SYMBOL_DICT 取 canonical 写法 (如 ta -> TA, rb -> rb)"""
    for code in SYMBOL_DICT:
        if code.lower() == pure_letters.lower():
            return code
    return pure_letters


def product_code_for_exchange(canonical: str, exchange: str) -> str:
    if exchange in _UPPERCASE_EXCHANGES:
        return canonical.upper()
    return canonical.lower()


def build_query_symbol(user_input: str, data_type: str) -> str | None:
    """
    将用户极简输入 (rb / ta / TA605) 转为 ClickHouse 可识别的完整 symbol。
    """
    raw_lower = user_input.strip().lower()
    lower_dict = {k.lower(): v for k, v in SYMBOL_DICT.items()}

    month_match = re.match(r"^([a-z]+)(\d+)$", raw_lower)
    if month_match:
        pure = month_match.group(1)
        digits = month_match.group(2)
        if pure not in lower_dict:
            return None
        exchange = lower_dict[pure][2]
        product = product_code_for_exchange(canonical_symbol_code(pure), exchange) + digits
        return f"{exchange}.{product}"

    if raw_lower not in lower_dict:
        return None

    exchange = lower_dict[raw_lower][2]
    product = product_code_for_exchange(canonical_symbol_code(raw_lower), exchange)

    if data_type == 'index':
        prefix = "KQ.i@"
    elif data_type in ('main', 'main_adj'):
        prefix = "KQ.m@"
    else:
        prefix = ""
    return f"{prefix}{exchange}.{product}"


def pure_product_code(symbol: str) -> str:
    """
    从任意 symbol 提取纯品种字母码，供 FEE_DICT / 风控查表。
    """
    raw = symbol.split('.')[-1]
    match = re.match(r'^([a-zA-Z]+)', raw)
    return match.group(1).lower() if match else raw.lower()