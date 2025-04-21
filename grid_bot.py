"""
布林带做市策略主模块
"""

import time
from typing import List, Dict, Tuple, Optional
from decimal import Decimal
import random
import threading

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
from api.backpack_client import BackpackClient, BackpackWebsocketClient

logger = setup_logger("grid_bot")

class BollMakerBot:
    def __init__(self, config: dict):
        """初始化网格交易机器人"""
        self.config = config
        self.api_key = config["API_KEY"]
        self.secret_key = config["SECRET_KEY"]
        self.symbol = config["SYMBOL"]
        
        # 初始化API客户端
        self.rest_client = BackpackClient(self.api_key, self.secret_key)
        self.ws_client = BackpackWebsocketClient(self.api_key, self.secret_key)
        
        # 初始化Bollinger Bands指标
        self.long_boll = BollingerBands(
            period=config["LONG_BOLL_PERIOD"],
            std_dev=config["LONG_BOLL_STD"]
        )
        self.short_boll = BollingerBands(
            period=config["SHORT_BOLL_PERIOD"],
            std_dev=config["SHORT_BOLL_STD"]
        )
        
        # 初始化数据库
        self.db = PositionDB()
        
        # 初始化状态变量
        self.running = False
        self.current_orders = {}
        self.last_price = 0
        
    def _monitor_price(self):
        """监控价格并更新指标"""
        try:
            while self.running:
                ticker = self.ws_client.ticker
                if ticker and "last" in ticker:
                    price = float(ticker["last"])
                    self.last_price = price
                    
                    # 更新Bollinger Bands
                    self.long_boll.update(price)
                    self.short_boll.update(price)
                    
                    # 获取当前持仓成本
                    position = self.db.get_position(self.symbol)
                    position_cost = position["avg_price"] if position else 0
                    
                    # 检查是否需要调整订单
                    self._adjust_orders(price, position_cost)
                    
                time.sleep(1)
        except Exception as e:
            logger.error(f"价格监控错误: {str(e)}")
            
    def _adjust_orders(self, current_price: float, position_cost: float):
        """根据当前价格和持仓成本调整订单"""
        try:
            # 获取Bollinger Bands数据
            long_upper, long_middle, long_lower = self.long_boll.get_bands()
            short_upper, short_middle, short_lower = self.short_boll.get_bands()
            
            # 计算目标持仓比例
            position_scale = self._calculate_position_scale(
                current_price,
                long_upper, long_lower,
                short_upper, short_lower
            )
            
            # 取消现有订单
            self.rest_client.cancel_all_orders(self.symbol)
            self.current_orders = {}
            
            # 检查是否可以下单
            base_balance, quote_balance = self._check_balance()
            if base_balance <= 0 or quote_balance <= 0:
                logger.warning("余额不足，跳过下单")
                return
                
            # 计算买卖价格
            buy_price = current_price * (1 - self.config["GRID_SPREAD"])
            sell_price = current_price * (1 + self.config["GRID_SPREAD"])
            
            # 检查是否满足交易条件
            can_buy = True
            can_sell = True
            
            if self.config["TRADE_IN_BAND"]:
                can_buy = current_price > short_lower
                can_sell = current_price < short_upper
                
            if self.config["BUY_BELOW_SMA"]:
                can_buy = can_buy and current_price < short_middle
                
            # 检查最小利润
            if position_cost > 0:
                min_sell_price = position_cost * (1 + self.config["MIN_PROFIT_SPREAD"])
                can_sell = can_sell and sell_price > min_sell_price
                
            # 计算订单数量
            base_order_size = self.config["BASE_ORDER_SIZE"]
            quote_order_size = self.config["QUOTE_ORDER_SIZE"]
            
            # 根据持仓比例调整订单大小
            buy_size = base_order_size * position_scale
            sell_size = base_order_size * (2 - position_scale)
            
            # 下买单
            if can_buy:
                try:
                    order = self.rest_client.place_order(
                        symbol=self.symbol,
                        side="buy",
                        order_type="limit",
                        quantity=buy_size,
                        price=buy_price
                    )
                    if order and "id" in order:
                        self.current_orders[order["id"]] = order
                        logger.info(f"下买单成功: 价格={buy_price}, 数量={buy_size}")
                except Exception as e:
                    logger.error(f"下买单失败: {str(e)}")
                    
            # 下卖单
            if can_sell:
                try:
                    order = self.rest_client.place_order(
                        symbol=self.symbol,
                        side="sell",
                        order_type="limit",
                        quantity=sell_size,
                        price=sell_price
                    )
                    if order and "id" in order:
                        self.current_orders[order["id"]] = order
                        logger.info(f"下卖单成功: 价格={sell_price}, 数量={sell_size}")
                except Exception as e:
                    logger.error(f"下卖单失败: {str(e)}")
                    
        except Exception as e:
            logger.error(f"调整订单失败: {str(e)}")
            
    def _calculate_position_scale(self, current_price: float,
                                long_upper: float, long_lower: float,
                                short_upper: float, short_lower: float) -> float:
        """计算目标持仓比例"""
        try:
            # 计算价格在长期和短期布林带中的位置
            long_position = (current_price - long_lower) / (long_upper - long_lower)
            short_position = (current_price - short_lower) / (short_upper - short_lower)
            
            # 综合长期和短期指标
            position_scale = (long_position + short_position) / 2
            
            # 限制在配置的范围内
            min_scale = self.config["MIN_POSITION_SCALE"]
            max_scale = self.config["MAX_POSITION_SCALE"]
            
            return max(min_scale, min(max_scale, position_scale))
        except Exception as e:
            logger.error(f"计算持仓比例失败: {str(e)}")
            return 0.5  # 发生错误时返回中性仓位
            
    def start(self):
        """启动交易机器人"""
        try:
            self.running = True
            
            # 连接WebSocket
            self.ws_client.connect()
            
            # 启动价格监控线程
            monitor_thread = threading.Thread(target=self._monitor_price)
            monitor_thread.daemon = True
            monitor_thread.start()
            
            logger.info("交易机器人已启动")
        except Exception as e:
            logger.error(f"启动失败: {str(e)}")
            self.stop()
            
    def stop(self):
        """停止交易机器人"""
        try:
            self.running = False
            
            # 取消所有订单
            self.rest_client.cancel_all_orders(self.symbol)
            
            # 关闭WebSocket连接
            self.ws_client.close()
            
            logger.info("交易机器人已停止")
        except Exception as e:
            logger.error(f"停止失败: {str(e)}")

if __name__ == "__main__":
    bot = BollMakerBot()
    bot.start() 