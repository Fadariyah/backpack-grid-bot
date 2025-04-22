# Backpack 布林带做市策略

这是一个基于 Backpack Exchange 的改进版网格交易策略，使用双重布林带来控制仓位和趋势判断，能更好地适应不同市场环境。

## 策略特点

- 双重布林带控制
  - 长周期布林带用于仓位管理（默认1小时）
  - 短周期布林带用于趋势判断（默认5分钟）
- 动态仓位管理
  - 价格越低，买入仓位越大
  - 价格越高，买入仓位越小
  - 基于布林带位置动态调整订单大小
- 趋势适应
  - 可选择只在均线下方买入
  - 设置最小获利价差，避免过早卖出
  - 在强趋势中自动暂停交易
- 动态价差管理
  - 根据市场波动率自动调整买卖价差
  - 支持趋势偏移调整
  - 上涨趋势时降低卖出价差
  - 下跌趋势时降低买入价差
- 完整的风险控制
  - 实时跟踪持仓成本
  - 动态调整仓位大小
  - 支持借贷仓位管理
  - 订单大小限制保护
  - 止盈止损保护机制

## 安装要求

- Python 3.10+
- 依赖包：
  - requests
  - numpy
  - python-dotenv（用于管理环境变量）
  - sqlite3（用于本地数据存储）
  - websocket-client（用于实时数据订阅）

## 快速开始

1. 克隆代码库：
```bash
git clone https://github.com/defi-maker/backpack-grid.git
cd backpack-grid
```

2. 安装依赖：
```bash
uv sync
```
如果未安装uv，请先安装uv：
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. 配置API密钥：
   - 创建 `.env` 文件
   - 填入您的 Backpack API密钥：
     ```
     BACKPACK_API_KEY=您的API密钥
     BACKPACK_SECRET_KEY=您的密钥
     ```

4. 配置策略参数：
   - 在 `config.py` 中设置：
     ```python
     # 交易配置
     SYMBOL = "SOL_USDC"  # 交易对
     ORDER_AMOUNT = 4  # 单次下单金额(USDC)
     GRID_TOTAL_INVESTMENT = 400  # 总投资额(USDC)
     
     # 布林带参数
     LONG_BOLL_INTERVAL = "1h"  # 长周期布林带时间周期
     LONG_BOLL_PERIOD = 21  # 长周期布林带K线数量
     LONG_BOLL_STD = 2.0  # 长周期标准差倍数
     
     SHORT_BOLL_INTERVAL = "5m"  # 短周期布林带时间周期
     SHORT_BOLL_PERIOD = 21  # 短周期布林带K线数量
     SHORT_BOLL_STD = 2.0  # 短周期标准差倍数
     
     # 仓位控制
     MAX_POSITION_SCALE = 10.0  # 最大仓位倍数
     MIN_POSITION_SCALE = 1.0  # 最小仓位倍数
     MIN_PROFIT_SPREAD = 0.001  # 最小获利价差(0.1%)
     TRADE_IN_BAND = True  # 是否只在布林带内交易
     BUY_BELOW_SMA = True  # 是否只在均线下方买入
     
     # 动态价差配置
     DYNAMIC_SPREAD = True  # 是否启用动态价差
     SPREAD_MIN = 0.001  # 最小价差 0.1%
     SPREAD_MAX = 0.002  # 最大价差 0.2%
     
     # 趋势偏移配置
     TREND_SKEW = True  # 是否启用趋势偏移
     UPTREND_SKEW = 0.8  # 上涨趋势时的偏移系数
     DOWNTREND_SKEW = 1.2  # 下跌趋势时的偏移系数
     
     # 风控参数
     STOP_LOSS_ACTIVATION = 0.02  # 止损触发比例 2%
     STOP_LOSS_RATIO = 0.03  # 止损比例 3%
     TAKE_PROFIT_RATIO = 0.07  # 止盈比例 7%
     
     # 订单精度
     PRICE_PRECISION = 2  # 价格精度
     QUANTITY_PRECISION = 2  # 数量精度
     
     # 订单大小
     BASE_ORDER_SIZE = 0.02  # 基础订单大小(SOL)
     QUOTE_ORDER_SIZE = 4  # 基础订单大小(USDC)
     ```

5. 运行策略：
```bash
uv run grid_bot.py
```

## 参数说明

### 布林带参数

1. 长周期布林带 (`LONG_BOLL_INTERVAL`, `LONG_BOLL_PERIOD`, `LONG_BOLL_STD`)
   - 用于控制仓位大小
   - 21根K线，1小时周期，适合中长期趋势判断
   - 标准差倍数2.0，可根据市场波动调整

2. 短周期布林带 (`SHORT_BOLL_INTERVAL`, `SHORT_BOLL_PERIOD`, `SHORT_BOLL_STD`)
   - 用于判断短期趋势和交易机会
   - 21根K线，5分钟周期，适合短期市场波动捕捉
   - 使用相同的窗口大小(21)，保持指标的一致性
   - 当价格超出短期布林带时暂停交易

### 仓位控制参数

1. 仓位倍数 (`MAX_POSITION_SCALE`, `MIN_POSITION_SCALE`)
   - 控制在不同价格位置的买入数量
   - 最大持仓为基础订单的10倍，降低风险
   - 最小持仓为基础订单大小，保持市场活跃度

2. 获利控制 (`MIN_PROFIT_SPREAD`)
   - 最小获利价差，设为0.1%
   - 在保证盈利的同时提高成交概率
   - 避免过大的价差影响做市效率

3. 交易控制 (`TRADE_IN_BAND`, `BUY_BELOW_SMA`)
   - TRADE_IN_BAND: 只在布林带内交易，降低风险
   - BUY_BELOW_SMA: 只在均线下方买入，避免追高

4. 订单控制
   - BASE_ORDER_SIZE: 基础订单大小（基础货币）
   - QUOTE_ORDER_SIZE: 基础订单大小（计价货币）
   - 订单大小会根据仓位倍数动态调整，但不超过基础大小的5倍

### 动态价差参数

1. 价差控制 (`DYNAMIC_SPREAD`, `SPREAD_MIN`, `SPREAD_MAX`)
   - 根据市场波动率自动调整买卖价差
   - 低波动率时使用最小价差(0.1%)
   - 高波动率时使用最大价差(0.2%)
   - 中间波动率线性插值计算价差

2. 趋势偏移 (`TREND_SKEW`, `UPTREND_SKEW`, `DOWNTREND_SKEW`)
   - 根据趋势动态调整买卖价差的分配
   - 上涨趋势时降低卖出价差(0.8)，提高买入价差
   - 下跌趋势时降低买入价差(1.2)，提高卖出价差
   - 通过偏移系数控制调整力度

### 风控参数

1. 止损控制 (`STOP_LOSS_ACTIVATION`, `STOP_LOSS_RATIO`)
   - 当亏损达到2%时激活止损监控
   - 当亏损达到3%时执行强制止损
   - 使用市价单快速止损出场

2. 止盈控制 (`TAKE_PROFIT_RATIO`)
   - 当盈利达到7%时执行止盈
   - 使用市价单锁定盈利
   - 避免过度追求利润导致回撤

## 策略逻辑

1. 仓位计算
   - 结合长短期布林带位置计算目标仓位
   - 动态调整订单大小，最大不超过基础订单的5倍
   - 考虑当前持仓成本进行买卖决策

2. 交易条件
   - 买入条件：
     * 价格在短期布林带内（如启用）
     * 价格在长期均线下方（如启用）
     * 有足够的计价货币余额
   - 卖出条件：
     * 价格高于持仓成本加最小获利价差
     * 价格在布林带范围内（如启用）
     * 有足够的基础货币余额

3. 风险控制
   - 实时跟踪持仓成本和数量
   - 订单大小限制保护
   - 在强趋势中自动暂停交易
   - 考虑借贷仓位进行余额计算
   - 止盈止损保护机制

## 数据存储

策略使用SQLite数据库存储：
- 持仓信息
- 交易历史
- 成本计算

数据库文件位于 `data/positions.db`

## 适用场景

1. 震荡市场
   - 通过做市策略赚取价差
   - 动态调整仓位降低风险
   - 利用波动率自适应价差

2. 下跌市场
   - 逐步建仓，降低持仓成本
   - 反弹时部分止盈
   - 止损保护避免深度回撤

3. 上涨市场
   - 保持小仓位持续做市
   - 设置最小获利空间
   - 止盈策略锁定收益

## 风险提示

- 本项目仅供学习和研究使用
- 请在实盘交易前充分测试
- 注意控制风险，合理设置参数
- 市场有风险，投资需谨慎

## 许可证

MIT License
