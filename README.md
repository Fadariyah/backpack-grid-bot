# Backpack 布林带做市策略

这是一个基于 Backpack Exchange 的改进版网格交易策略，使用双重布林带来控制仓位和趋势判断，能更好地适应不同市场环境。

## 策略特点

- 双重布林带控制
  - 长周期布林带用于仓位管理
  - 短周期布林带用于趋势判断
- 动态仓位管理
  - 价格越低，买入仓位越大
  - 价格越高，买入仓位越小
- 趋势适应
  - 可选择只在均线下方买入
  - 设置最小获利价差，避免过早卖出
  - 在强趋势中自动暂停交易
- 完整的风险控制
  - 实时跟踪持仓成本
  - 动态调整仓位大小
  - 支持借贷仓位管理

## 安装要求

- Python 3.7+
- 依赖包：
  - requests
  - numpy
  - python-dotenv（可选，用于管理环境变量）

## 快速开始

1. 克隆代码库：
```bash
git clone https://github.com/defi-maker/backpack-grid.git
cd backpack-grid
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 配置API密钥：
   - 打开 `config.py`
   - 填入您的 Backpack API密钥和密钥

4. 配置策略参数：
   - 在 `config.py` 中设置：
     ```python
     # 交易配置
     SYMBOL = "SOL_USDC"  # 交易对
     GRID_NUM = 10  # 网格数量
     GRID_TOTAL_INVESTMENT = 1000  # 总投资额(USDC)
     
     # 布林带参数
     LONG_BOLL_PERIOD = 30  # 长周期布林带(用于仓位控制)
     LONG_BOLL_STD = 2.0  # 长周期标准差倍数
     SHORT_BOLL_PERIOD = 5  # 短周期布林带(用于趋势判断)
     SHORT_BOLL_STD = 2.0  # 短周期标准差倍数
     
     # 仓位控制
     MAX_POSITION_SCALE = 100.0  # 最大仓位倍数
     MIN_POSITION_SCALE = 1.0  # 最小仓位倍数
     MIN_PROFIT_SPREAD = 0.01  # 最小获利价差(1%)
     TRADE_IN_BAND = True  # 是否只在布林带内交易
     BUY_BELOW_SMA = True  # 是否只在均线下方买入
     ```

5. 运行策略：
```bash
python grid_bot.py
```

## 参数说明

### 布林带参数

1. 长周期布林带 (`LONG_BOLL_PERIOD`, `LONG_BOLL_STD`)
   - 用于控制仓位大小
   - 周期越长，仓位调整越平缓
   - 标准差倍数影响布林带宽度

2. 短周期布林带 (`SHORT_BOLL_PERIOD`, `SHORT_BOLL_STD`)
   - 用于判断短期趋势
   - 当价格超出短期布林带时暂停交易
   - 帮助避免在强趋势中频繁交易

### 仓位控制参数

1. 仓位倍数 (`MAX_POSITION_SCALE`, `MIN_POSITION_SCALE`)
   - 控制在不同价格位置的买入数量
   - 价格越低，买入倍数越接近 MAX_POSITION_SCALE
   - 价格越高，买入倍数越接近 MIN_POSITION_SCALE

2. 获利控制 (`MIN_PROFIT_SPREAD`)
   - 最小获利价差，例如 0.01 表示1%
   - 只有当价格超过持仓成本+价差时才会卖出
   - 避免频繁小额交易

3. 交易控制 (`TRADE_IN_BAND`, `BUY_BELOW_SMA`)
   - TRADE_IN_BAND: 是否只在布林带内交易
   - BUY_BELOW_SMA: 是否只在均线下方买入
   - 用于控制交易时机，降低风险

## 策略逻辑

1. 仓位计算
   - 使用长周期布林带将价格标准化到[-1, 1]区间
   - 将标准化后的价格映射到[MAX_SCALE, MIN_SCALE]
   - 据此动态调整每次交易的数量

2. 交易条件
   - 买入条件：
     * 价格在短期布林带内（如启用）
     * 价格在长期均线下方（如启用）
     * 有足够的quote资产
   - 卖出条件：
     * 持有仓位 > 0
     * 当前价格 > 持仓成本 * (1 + MIN_PROFIT_SPREAD)
     * 有足够的base资产

3. 风险控制
   - 实时跟踪持仓成本和数量
   - 动态调整买卖数量
   - 在强趋势中自动暂停交易
   - 考虑借贷仓位进行余额计算

## 适用场景

1. 震荡市场
   - 通过做市策略赚取价差
   - 动态调整仓位降低风险

2. 下跌市场
   - 逐步建仓，降低持仓成本
   - 反弹时部分止盈

3. 上涨市场
   - 保持小仓位持续做市
   - 设置最小获利空间

## 风险提示

- 本项目仅供学习和研究使用
- 请在实盘交易前充分测试
- 注意控制风险，合理设置参数
- 市场有风险，投资需谨慎

## 进阶使用

1. 参数优化
   - 可以通过回测优化布林带参数
   - 根据交易对特性调整仓位倍数
   - 根据手续费调整最小获利价差

2. 风险管理
   - 设置最大持仓限制
   - 设置单次交易限制
   - 设置止损条件

## 贡献指南

欢迎提交 Issue 和 Pull Request 来帮助改进这个项目。

## 许可证

MIT License

## 环境变量设置

在运行机器人之前，请先设置以下环境变量：

```bash
# Linux/Mac
export BACKPACK_API_KEY="您的API密钥"
export BACKPACK_SECRET_KEY="您的密钥"

# Windows (CMD)
set BACKPACK_API_KEY=您的API密钥
set BACKPACK_SECRET_KEY=您的密钥

# Windows (PowerShell)
$env:BACKPACK_API_KEY="您的API密钥"
$env:BACKPACK_SECRET_KEY="您的密钥"
```

## 其他配置说明
