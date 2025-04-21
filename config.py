"""
配置文件
"""

import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# API配置
API_URL = "https://api.backpack.exchange"
API_VERSION = "v1"
DEFAULT_WINDOW = "5000"

# 交易配置
SYMBOL = "SOL_USDC"  # 交易对
ORDER_AMOUNT = 4  # 单次下单金额(USDC)，约为总资金的1/100
GRID_TOTAL_INVESTMENT = 400  # 总投资额(USDC)，包含SOL和USDC的总价值
PRICE_PRECISION = 4  # 价格精度
QUANTITY_PRECISION = 4  # 数量精度
SPREAD = 0.0015  # 挂单距离中间价的价差(0.15%)，约为手续费的2倍

# 布林带参数
LONG_BOLL_PERIOD = 60  # 长周期布林带(用于仓位控制)，1小时
LONG_BOLL_STD = 2.0  # 长周期标准差倍数
SHORT_BOLL_PERIOD = 30  # 短周期布林带(用于趋势判断)，30分钟
SHORT_BOLL_STD = 2.0  # 短周期标准差倍数

# 仓位控制
MAX_POSITION_SCALE = 100.0  # 最大仓位倍数，布林带下轨可买入的最大倍数
MIN_POSITION_SCALE = 1.0  # 最小仓位倍数，布林带上轨的最小倍数
MIN_PROFIT_SPREAD = 0.002  # 最小获利价差(0.2%)
TRADE_IN_BAND = True  # 是否只在布林带内交易
BUY_BELOW_SMA = True  # 是否只在均线下方买入

# API密钥(请替换为您的密钥)
API_KEY = os.getenv("BACKPACK_API_KEY", "")
SECRET_KEY = os.getenv("BACKPACK_SECRET_KEY", "")

if not API_KEY or not SECRET_KEY:
    raise ValueError("请设置环境变量 BACKPACK_API_KEY 和 BACKPACK_SECRET_KEY")

# 日志配置
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

