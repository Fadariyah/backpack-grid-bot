"""
布林带做市策略主模块
"""

import time
from typing import List, Dict, Tuple
from decimal import Decimal

from api.client import (
    execute_order,
    get_balance,
    get_open_orders,
    cancel_all_orders,
    get_borrow_lend_positions,
    get_mid_price,
    init_ws_client
)
from config import (
    API_KEY,
    SECRET_KEY,
    SYMBOL,
    ORDER_AMOUNT,
    GRID_TOTAL_INVESTMENT,
    PRICE_PRECISION,
    QUANTITY_PRECISION,
    SPREAD,
    LONG_BOLL_PERIOD,
    LONG_BOLL_STD,
    SHORT_BOLL_PERIOD,
    SHORT_BOLL_STD,
    MAX_POSITION_SCALE,
    MIN_POSITION_SCALE,
    MIN_PROFIT_SPREAD,
    TRADE_IN_BAND,
    BUY_BELOW_SMA
)
from utils.indicators import BollingerBands
from utils.database import PositionDB
from logger import setup_logger

logger = setup_logger("grid_bot")

class BollMakerBot:
    def __init__(self):
        self.symbol = SYMBOL
        self.order_amount = Decimal(str(ORDER_AMOUNT))
        self.total_investment = Decimal(str(GRID_TOTAL_INVESTMENT))
        self.price_precision = PRICE_PRECISION
        self.quantity_precision = QUANTITY_PRECISION
        self.spread = Decimal(str(SPREAD))
        
        # 解析交易对
        self.base_asset, self.quote_asset = self.symbol.split('_')
        
        # 初始化布林带
        self.long_boll = BollingerBands(LONG_BOLL_PERIOD, LONG_BOLL_STD)
        self.short_boll = BollingerBands(SHORT_BOLL_PERIOD, SHORT_BOLL_STD)
        
        # 初始化数据库
        self.db = PositionDB()
        
        # 从数据库加载持仓信息
        self.position_size, self.position_cost = self.db.get_position(self.symbol)
        logger.info(f"从数据库加载持仓信息 - 数量: {self.position_size}, 成本: {self.position_cost}")
        
        # 初始化WebSocket客户端
        self.ws_client = init_ws_client(API_KEY, SECRET_KEY)
        self.ws_client.subscribe_orderbook(self.symbol)
        
    def _get_mid_price(self) -> Decimal:
        """获取当前中间价格"""
        mid_price = get_mid_price(self.symbol)
        if not mid_price:
            logger.error("获取中间价格失败")
            return None
        return mid_price
        
    def _check_balance(self) -> Tuple[Decimal, Decimal]:
        """
        检查账户余额
        Returns:
            (base_available, quote_available): 可用base和quote资产数量
        """
        # 获取现货余额
        balance = get_balance(API_KEY, SECRET_KEY)
        if "error" in balance:
            logger.error(f"获取账户余额失败: {balance['error']}")
            return None, None
            
        # 获取借贷仓位
        borrow_lend = get_borrow_lend_positions(API_KEY, SECRET_KEY)
        if "error" in borrow_lend:
            logger.error(f"获取借贷仓位失败: {borrow_lend['error']}")
            return None, None
            
        # 计算base资产余额
        base_available = Decimal('0')
        base_borrowed = Decimal('0')
        base_lent = Decimal('0')
        
        # 处理现货余额
        if isinstance(balance, list):
            for item in balance:
                if item.get('symbol') == self.base_asset:
                    base_available = Decimal(str(item.get('available', '0')))
                    break
                    
        # 处理借贷仓位
        if isinstance(borrow_lend, list):
            for position in borrow_lend:
                if position.get('symbol') == self.base_asset:
                    base_borrowed = Decimal(str(position.get('borrowed', '0')))
                    base_lent = Decimal(str(position.get('lent', '0')))
                    break
                    
        # 计算quote资产余额
        quote_available = Decimal('0')
        quote_borrowed = Decimal('0')
        quote_lent = Decimal('0')
        
        # 处理现货余额
        if isinstance(balance, list):
            for item in balance:
                if item.get('symbol') == self.quote_asset:
                    quote_available = Decimal(str(item.get('available', '0')))
                    break
                    
        # 处理借贷仓位
        if isinstance(borrow_lend, list):
            for position in borrow_lend:
                if position.get('symbol') == self.quote_asset:
                    quote_borrowed = Decimal(str(position.get('borrowed', '0')))
                    quote_lent = Decimal(str(position.get('lent', '0')))
                    break
                    
        # 计算实际可用余额
        base_total_available = base_available - base_borrowed + base_lent
        quote_total_available = quote_available - quote_borrowed + quote_lent
        
        return base_total_available, quote_total_available
        
    def _update_position_info(self, size: Decimal, price: Decimal, is_buy: bool):
        """更新持仓信息"""
        if is_buy:
            new_size = self.position_size + size
            new_cost = (self.position_cost * self.position_size + price * size) / new_size
            self.position_size = new_size
            self.position_cost = new_cost
        else:
            self.position_size -= size
            if self.position_size <= 0:
                self.position_size = Decimal('0')
                self.position_cost = Decimal('0')
                
        # 更新数据库中的持仓信息
        self.db.update_position(self.symbol, self.position_size, self.position_cost)
        
        # 记录交易历史
        side = "BUY" if is_buy else "SELL"
        self.db.add_trade(self.symbol, side, price, size)
                
    def _place_orders(self, mid_price: Decimal):
        """在中间价上下方挂单做市"""
        # 更新布林带
        price_float = float(mid_price)
        long_upper, long_middle, long_lower = self.long_boll.update(price_float)
        short_upper, short_middle, short_lower = self.short_boll.update(price_float)
        
        # 检查是否在短期布林带内
        if TRADE_IN_BAND and not self.short_boll.is_price_in_band(price_float, short_upper, short_lower):
            logger.info(f"价格超出短期布林带范围,暂停交易 @ {mid_price}")
            return
            
        # 获取仓位比例
        position_scale = self.long_boll.get_position_scale(
            price_float, long_upper, long_lower,
            MAX_POSITION_SCALE, MIN_POSITION_SCALE
        )
        
        # 计算买卖价格
        spread_amount = mid_price * self.spread
        buy_price = round(mid_price - spread_amount, self.price_precision)
        sell_price = round(mid_price + spread_amount, self.price_precision)
        
        # 计算下单数量
        quantity = round(self.order_amount / mid_price, self.quantity_precision)
        
        # 调整买卖数量
        quantity = quantity * Decimal(str(position_scale))
        
        # 获取账户余额
        base_available, quote_available = self._check_balance()
        if base_available is None or quote_available is None:
            return
            
        # 构建基础订单
        order_details = {
            "symbol": self.symbol,
            "quantity": str(quantity),
            "timeInForce": "GTC",
            "orderType": "LIMIT"
        }
        
        # 放置买单
        if not BUY_BELOW_SMA or price_float < long_middle:
            if quote_available >= quantity * buy_price:
                order_details.update({
                    "side": "BUY",
                    "price": str(buy_price)
                })
                result = execute_order(API_KEY, SECRET_KEY, order_details)
                if "error" in result:
                    logger.error(f"下买单失败 @ {buy_price}: {result['error']}")
                else:
                    logger.info(f"下买单成功 @ {buy_price}")
                    self._update_position_info(quantity, buy_price, True)
        
        # 放置卖单
        # 从数据库获取最新持仓成本
        current_size, current_cost = self.db.get_position(self.symbol)
        if current_size > 0 and mid_price > current_cost * (1 + MIN_PROFIT_SPREAD):
            if base_available >= quantity:
                order_details.update({
                    "side": "SELL",
                    "price": str(sell_price)
                })
                result = execute_order(API_KEY, SECRET_KEY, order_details)
                if "error" in result:
                    logger.error(f"下卖单失败 @ {sell_price}: {result['error']}")
                else:
                    logger.info(f"下卖单成功 @ {sell_price}")
                    self._update_position_info(quantity, sell_price, False)
                    
    def _monitor_and_adjust(self):
        """监控和调整订单"""
        while True:
            try:
                # 获取中间价
                mid_price = self._get_mid_price()
                if not mid_price:
                    continue
                    
                # 取消现有订单
                cancel_result = cancel_all_orders(API_KEY, SECRET_KEY, self.symbol)
                if "error" in cancel_result:
                    logger.error(f"取消订单失败: {cancel_result['error']}")
                    continue
                    
                # 放置新订单
                self._place_orders(mid_price)
                
                # 打印状态
                logger.info(f"当前中间价: {mid_price}, 持仓数量: {self.position_size}, 持仓成本: {self.position_cost}")
                
                time.sleep(5)  # 避免频繁请求
                
            except Exception as e:
                logger.error(f"监控过程发生错误: {str(e)}")
                time.sleep(5)
                
    def start(self):
        """启动交易机器人"""
        logger.info(f"启动 BollMaker 策略 - {self.symbol}")
        logger.info(f"单次下单金额: {self.order_amount} {self.quote_asset}")
        logger.info(f"总投资额: {self.total_investment} {self.quote_asset}")
        logger.info(f"价差比例: {self.spread * 100}%")
        
        # 开始监控
        self._monitor_and_adjust()
        
    def __del__(self):
        """析构时关闭数据库连接"""
        if hasattr(self, 'db'):
            self.db.close()

if __name__ == "__main__":
    bot = BollMakerBot()
    bot.start() 