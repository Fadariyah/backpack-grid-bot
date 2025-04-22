"""
布林带做市策略主模块
"""

import time
from typing import List, Dict, Tuple, Optional
from decimal import Decimal
import random
import threading
import logging
import queue
import os

from api.backpack_client import BackpackClient
from api.backpack_ws_client import BackpackWSClient
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
    BUY_BELOW_SMA,
    BASE_ORDER_SIZE,
    QUOTE_ORDER_SIZE
)
from utils.indicators import BollingerBands
from utils.database import PositionDB
from logger import setup_logger

logger = setup_logger("grid_bot")

class BollMakerBot:
    def __init__(self, config: dict):
        """初始化网格交易机器人"""
        self.config = config
        self.api_key = config["API_KEY"]
        self.secret_key = config["SECRET_KEY"]
        self.symbol = config["SYMBOL"]
        
        # 确保data目录存在
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        
        # 初始化数据库
        db_path = os.path.join(data_dir, "positions.db")
        self.db = PositionDB(db_path)
        self.logger = logging.getLogger(__name__)
        
        # 初始化消息队列和事件
        self.db_queue = queue.Queue()
        self.position_event = threading.Event()
        self.position_cache = None
        self.last_position_update = 0
        self.position_update_interval = 1
        self.cache_lock = threading.Lock()
        
        # 添加订单控制
        self.last_order_time = 0
        self.order_interval = 120  # 120秒，即2分钟
        
        # 初始化API客户端
        self.rest_client = BackpackClient(self.api_key, self.secret_key)
        self.ws_client = BackpackWSClient(
            self.api_key, 
            self.secret_key, 
            self.symbol,
            on_message_callback=self._handle_ws_message
        )
        
        # 初始化Bollinger Bands指标
        self.long_boll = BollingerBands(
            period=config["LONG_BOLL_PERIOD"],
            num_std=config["LONG_BOLL_STD"]
        )
        self.short_boll = BollingerBands(
            period=config["SHORT_BOLL_PERIOD"],
            num_std=config["SHORT_BOLL_STD"]
        )
        
        # 初始化状态变量
        self.running = False
        self.current_orders = {}
        self.last_price = 0

    def _process_db_queue(self):
        """处理数据库操作队列"""
        try:
            while True:
                try:
                    # 非阻塞方式获取消息
                    msg = self.db_queue.get_nowait()
                    action = msg.get('action')
                    data = msg.get('data')

                    if action == 'update_position':
                        try:
                            order_data = data
                            logger.info(f"开始处理持仓更新: {order_data}")
                            
                            size, cost = self.db.get_position(self.symbol)
                            logger.info(f"当前持仓: size={size}, cost={cost}")
                            
                            # 计算新的持仓
                            order_size = Decimal(str(order_data['quantity']))
                            order_price = Decimal(str(order_data['price']))
                            
                            if order_data['side'].lower() == 'buy':
                                new_size = size + order_size
                                new_cost = cost + (order_size * order_price)
                            else:  # sell
                                new_size = size - order_size
                                new_cost = cost * (new_size / size) if size != 0 else Decimal('0')
                            
                            # 更新数据库 - 转换为 float
                            self.db.update_position(self.symbol, float(new_size), float(new_cost))
                            logger.info(f"持仓更新成功: new_size={new_size}, new_cost={new_cost}")
                            
                            # 记录交易历史 - 转换为 float
                            self.db.add_trade(
                                self.symbol,
                                order_data['side'],
                                float(order_price),  # 转换为 float
                                float(order_size)    # 转换为 float
                            )
                            logger.info("交易历史记录添加成功")
                        except Exception as e:
                            logger.error(f"处理持仓更新时发生错误: {e}")
                            logger.exception("详细错误信息：")  # 添加详细的错误堆栈信息
                    elif action == 'get_position':
                        size, cost = self.db.get_position(self.symbol)
                        with self.cache_lock:
                            self.position_cache = {
                                'size': float(size),
                                'avg_price': float(cost) / float(size) if float(size) != 0 else 0
                            }
                            self.last_position_update = time.time()
                        self.position_event.set()

                except queue.Empty:
                    break
                    
        except Exception as e:
            self.logger.error(f"处理数据库队列时发生错误: {e}")
            if action == 'get_position':
                self.position_event.set()  # 确保在错误情况下也设置事件

    def _get_cached_position(self):
        """获取缓存的持仓信息"""
        current_time = time.time()
        if self.position_cache is None or current_time - self.last_position_update > self.position_update_interval:
            # 重置事件
            self.position_event.clear()
            
            # 请求更新持仓信息
            self.db_queue.put({
                'action': 'get_position',
                'data': None
            })
            
            # 等待更新完成
            self.position_event.wait(timeout=1.0)  # 设置超时以防止死锁
            
        return self.position_cache or {'size': 0, 'avg_price': 0}

    def update_position(self, order_data: dict):
        """更新持仓信息"""
        try:
            # 将更新操作放入队列
            self.db_queue.put({
                'action': 'update_position',
                'data': order_data
            })
        except Exception as e:
            self.logger.error(f"添加更新持仓任务失败: {e}")
            raise

    def _handle_ws_message(self, stream, event_data):
        """处理WebSocket消息回调"""
        try:
            # 处理行情数据
            if stream.startswith("bookTicker."):
                if 'b' in event_data and 'a' in event_data:
                    bid_price = float(event_data['b'])
                    ask_price = float(event_data['a'])
                    current_price = (bid_price + ask_price) / 2
                    
                    # 更新价格
                    self.last_price = current_price
                    
                    # 更新Bollinger Bands
                    self.long_boll.update(current_price)
                    self.short_boll.update(current_price)
                    
                    # 检查是否需要调整订单（每分钟一次）
                    current_time = time.time()
                    if current_time - self.last_order_time >= self.order_interval:
                        # 使用缓存的持仓信息
                        position = self._get_cached_position()
                        position_cost = position["avg_price"] if position else 0
                        
                        # 检查是否需要调整订单
                        self._adjust_orders(current_price, position_cost)
                        self.last_order_time = current_time
                    
            # 处理深度数据
            elif stream.startswith("depth."):
                if 'b' in event_data and 'a' in event_data:
                    # 这里可以添加处理深度数据的逻辑
                    pass
                    
            # 处理订单更新
            elif stream.startswith("account.orderUpdate."):
                event_type = event_data.get('e')
                
                # 处理订单成交事件
                if event_type == 'orderFill':
                    order_id = event_data.get('i')
                    side = event_data.get('S')
                    quantity = float(event_data.get('l', '0'))
                    price = float(event_data.get('p', '0'))
                    
                    # 更新持仓信息
                    order_data = {
                        'side': side,
                        'quantity': quantity,
                        'price': price
                    }
                    self.update_position(order_data)
                    
        except Exception as e:
            self.logger.error(f"处理WebSocket消息失败: {str(e)}")

    def _monitor_price(self):
        """监控价格变化"""
        while self.running:
            try:
                # 定期更新持仓缓存
                self._get_cached_position()
                time.sleep(1)
            except Exception as e:
                logger.error(f"监控价格失败: {str(e)}")
                time.sleep(5)  # 发生错误时等待一段时间再继续

    def _adjust_orders(self, current_price: float, position_cost: float):
        """根据当前价格和持仓成本调整订单"""
        try:
            # 获取Bollinger Bands数据
            long_upper, long_middle, long_lower = self.long_boll.update(current_price)
            short_upper, short_middle, short_lower = self.short_boll.update(current_price)
            
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
            base_balance, quote_balance, total_value, error = self._calculate_total_balance()
            if error:
                logger.error(f"获取余额失败: {error}")
                return
            if base_balance <= 0 or quote_balance <= 0:
                logger.warning("余额不足，跳过下单")
                return
                
            # 计算买卖价格
            spread = self.config["GRID_SPREAD"]
            price_precision = self.config["PRICE_PRECISION"]
            
            # 先格式化当前价格，避免后续计算产生过多小数位
            current_price = round(current_price, price_precision)
            
            # 计算买卖价格并立即控制精度
            buy_price = round(current_price * (1 - spread), price_precision)
            sell_price = round(current_price * (1 + spread), price_precision)
            
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
                min_sell_price = round(position_cost * (1 + self.config["MIN_PROFIT_SPREAD"]), price_precision)
                can_sell = can_sell and sell_price > min_sell_price
                
            # 计算订单数量
            base_order_size = self.config["BASE_ORDER_SIZE"]
            quote_order_size = self.config["QUOTE_ORDER_SIZE"]
            
            # 根据持仓比例调整订单大小，添加最大订单限制
            max_order_size = base_order_size * 5  # 最大订单限制为基础订单的5倍
            buy_size = min(max(base_order_size * position_scale, base_order_size), max_order_size)
            sell_size = min(max(base_order_size * (1 + (1 - position_scale)), base_order_size), max_order_size)
            
            # 格式化数量到指定精度（确保不超过3位小数）
            precision = self.config["QUANTITY_PRECISION"]
            buy_size = round(buy_size, precision)
            sell_size = round(sell_size, precision)
            
            # 确保数量大于0
            if buy_size <= 0 or sell_size <= 0:
                logger.warning(f"订单数量异常: buy_size={buy_size}, sell_size={sell_size}, position_scale={position_scale}")
                return
            
            # 下买单
            if can_buy:
                try:
                    order_details = {
                        "symbol": self.symbol,
                        "side": "Bid",
                        "orderType": "Limit",
                        "quantity": str(buy_size),
                        "price": format(buy_price, f".{self.config['PRICE_PRECISION']}f"),
                        "timeInForce": "GTC",
                        "postOnly": True
                    }
                    order = self.rest_client.place_order(order_details)
                    if order and "id" in order:
                        self.current_orders[order["id"]] = order
                        logger.info(f"下买单成功: 价格={buy_price}, 数量={buy_size}")
                except Exception as e:
                    logger.error(f"下买单失败: {str(e)}")
                    
            # 下卖单
            if can_sell:
                try:
                    order_details = {
                        "symbol": self.symbol,
                        "side": "Ask",
                        "orderType": "Limit",
                        "quantity": str(sell_size),
                        "price": format(sell_price, f".{self.config['PRICE_PRECISION']}f"),
                        "timeInForce": "GTC",
                        "postOnly": True
                    }
                    order = self.rest_client.place_order(order_details)
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
            # 检查布林带是否初始化完成
            if not all([long_upper, long_lower, short_upper, short_lower]):
                logger.warning("布林带数据未完全初始化，返回中性仓位")
                return 1.0

            # 检查分母是否为零或接近零
            long_range = long_upper - long_lower
            short_range = short_upper - short_lower
            
            min_range = 0.0001  # 定义最小有效范围
            
            if long_range <= min_range or short_range <= min_range:
                logger.warning("布林带范围过小，返回中性仓位")
                return 1.0
            
            # 计算价格在长期和短期布林带中的位置
            long_position = (current_price - long_lower) / long_range
            short_position = (current_price - short_lower) / short_range
            
            # 限制position值在[0,1]范围内
            long_position = max(0.0, min(1.0, long_position))
            short_position = max(0.0, min(1.0, short_position))
            
            # 综合长期和短期指标
            position_scale = (long_position + short_position) / 2
            
            # 应用配置的仓位范围
            min_scale = self.config["MIN_POSITION_SCALE"]
            max_scale = self.config["MAX_POSITION_SCALE"]
            
            # 将position_scale映射到配置的范围
            position_scale = min_scale + (max_scale - min_scale) * position_scale
            
            logger.debug(f"计算仓位比例 - 当前价格: {current_price}, 长期位置: {long_position:.4f}, " +
                      f"短期位置: {short_position:.4f}, 最终比例: {position_scale:.4f}")
            
            return position_scale
            
        except Exception as e:
            logger.error(f"计算持仓比例失败: {str(e)}")
            return 1.0  # 发生错误时返回中性仓位
            
    def subscribe_order_updates(self):
        """订阅订单更新流"""
        if not self.ws_client or not self.ws_client.is_connected():
            logger.warning("无法订阅订单更新：WebSocket连接不可用")
            return False
        
        # 尝试订阅订单更新流
        stream = f"account.orderUpdate.{self.symbol}"
        if stream not in self.ws_client.subscriptions:
            retry_count = 0
            max_retries = 3
            success = False
            
            while retry_count < max_retries and not success:
                try:
                    success = self.ws_client.private_subscribe(stream)
                    if success:
                        logger.info(f"成功订阅订单更新: {stream}")
                        return True
                    else:
                        logger.warning(f"订阅订单更新失败，尝试重试... ({retry_count+1}/{max_retries})")
                except Exception as e:
                    logger.error(f"订阅订单更新时发生异常: {e}")
                
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(1)  # 重试前等待
            
            if not success:
                logger.error(f"在 {max_retries} 次尝试后仍无法订阅订单更新")
                return False
        else:
            logger.info(f"已经订阅了订单更新: {stream}")
            return True

    def _initialize_websocket(self):
        """等待WebSocket连接建立并进行初始化订阅"""
        wait_time = 0
        max_wait_time = 10
        while not self.ws_client.connected and wait_time < max_wait_time:
            time.sleep(0.5)
            wait_time += 0.5
            
        if self.ws_client.connected:
            logger.info("WebSocket连接已建立，初始化数据流...")
            
            # 初始化订单簿
            orderbook_initialized = self.ws_client.initialize_orderbook()
            
            # 订阅深度流和行情数据
            if orderbook_initialized:
                depth_subscribed = self.ws_client.subscribe_depth()
                ticker_subscribed = self.ws_client.subscribe_bookTicker()
                
                if depth_subscribed and ticker_subscribed:
                    logger.info("行情数据流订阅成功!")
                    
                # 订阅私有订单更新流
                if self.subscribe_order_updates():
                    logger.info("订单更新流订阅成功!")
                else:
                    logger.error("订单更新流订阅失败")
            else:
                logger.error("订单簿初始化失败")
        else:
            logger.warning("WebSocket连接建立超时，将在运行过程中继续尝试连接")

    def start(self):
        """启动交易机器人"""
        try:
            self.running = True
            
            # 连接WebSocket并初始化数据流
            self.ws_client.connect()
            self._initialize_websocket()
            
            if not self.ws_client.connected:
                logger.error("WebSocket连接失败，无法启动机器人")
                self.stop()
                return
            
            logger.info("交易机器人已启动")
            
            # 主循环
            while self.running:
                try:
                    # 处理数据库操作队列
                    self._process_db_queue()
                    
                    # 检查WebSocket连接状态
                    if not self.ws_client.connected:
                        logger.warning("WebSocket连接断开，尝试重新连接...")
                        self.ws_client.reconnect()
                    
                    time.sleep(0.1)  # 控制循环频率
                    
                except KeyboardInterrupt:
                    logger.info("收到退出信号，正在停止机器人...")
                    self.stop()
                    break
                except Exception as e:
                    logger.error(f"主循环发生错误: {str(e)}")
                    time.sleep(5)  # 发生错误时等待一段时间再继续
                    
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
            if self.ws_client:
                self.ws_client.close()
            
            logger.info("交易机器人已停止")
        except Exception as e:
            logger.error(f"停止失败: {str(e)}")

    def __del__(self):
        """清理资源"""
        if hasattr(self, 'db'):
            self.db.close()

    def _calculate_total_balance(self, include_borrow_positions=True) -> Tuple[float, float, float, Optional[str]]:
        """
        计算账户总余额，包括现货和借贷仓位

        Args:
            include_borrow_positions (bool): 是否包含借贷仓位，默认为True

        Returns:
            Tuple[float, float, float, Optional[str]]: 
                - base_balance (float): 基础货币余额（包括现货和借贷）
                - quote_balance (float): 报价货币余额（包括现货和借贷）
                - total_value_in_quote (float): 总资产价值（以报价货币计算）
                - error_message (Optional[str]): 错误信息，如果没有错误则为None
        """
        try:
            # 获取现货余额
            balances = self.rest_client.get_balance()
            logger.info(f"API返回的余额数据: {balances}")  # 添加日志查看数据格式
            
            # 检查返回的数据是否有错误
            if isinstance(balances, dict) and "error" in balances:
                error_msg = f"获取余额API返回错误: {balances['error']}"
                logger.error(error_msg)
                return 0.0, 0.0, 0.0, error_msg
            
            # 从交易对中获取基础货币和计价货币
            base_currency, quote_currency = self.symbol.split('_')
            
            # 初始化现货余额
            spot_base_balance = 0
            spot_quote_balance = 0
            
            # 计算现货总余额（可用 + 锁定）
            if isinstance(balances, dict):
                for asset, details in balances.items():
                    if isinstance(details, dict):
                        available = float(details.get('available', 0))
                        locked = float(details.get('locked', 0))
                        total = available + locked
                        
                        if asset == base_currency:
                            spot_base_balance = total
                        elif asset == quote_currency:
                            spot_quote_balance = total
                    else:
                        logger.warning(f"资产 {asset} 的余额数据格式不正确: {details}")
            else:
                logger.warning(f"余额数据格式不正确: {balances}")
            
            # 初始化借贷调整
            borrow_lend_base_adjustment = 0
            borrow_lend_quote_adjustment = 0
            
            # 获取并处理借贷仓位
            if include_borrow_positions:
                try:
                    borrow_positions = self.rest_client.get_borrow_lend_positions()
                    if isinstance(borrow_positions, list):
                        for position in borrow_positions:
                            position_symbol = position.get('symbol')
                            net_quantity_str = position.get('netQuantity')
                            
                            if position_symbol and net_quantity_str:
                                try:
                                    net_quantity = float(net_quantity_str)
                                    if position_symbol == base_currency:
                                        borrow_lend_base_adjustment = net_quantity
                                        logger.debug(f"借贷调整 - 基础资产 ({base_currency}): {net_quantity}")
                                    elif position_symbol == quote_currency:
                                        borrow_lend_quote_adjustment = net_quantity
                                        logger.debug(f"借贷调整 - 报价资产 ({quote_currency}): {net_quantity}")
                                except ValueError as e:
                                    logger.error(f"处理借贷仓位数据时出错 ({position_symbol}): {e}")
                                    continue
                except Exception as e:
                    logger.warning(f"获取借贷仓位失败: {str(e)}")
                    # 继续执行，但不包括借贷仓位
            
            # 计算最终余额（现货余额 + 借贷调整）
            base_balance = spot_base_balance + borrow_lend_base_adjustment
            quote_balance = spot_quote_balance + borrow_lend_quote_adjustment
            
            # 获取当前价格用于计算总价值
            current_price = self.last_price  # 使用最新价格
            if not current_price:
                logger.warning("无法获取当前价格，总资产价值可能不准确")
                total_value_in_quote = quote_balance  # 假设基础资产价值为0
            else:
                total_value_in_quote = (base_balance * current_price) + quote_balance
            
            logger.info(f"计算后余额 - 基础({base_currency}): {base_balance:.8f} (现货: {spot_base_balance:.8f}, 借贷: {borrow_lend_base_adjustment:.8f})")
            logger.info(f"计算后余额 - 报价({quote_currency}): {quote_balance:.8f} (现货: {spot_quote_balance:.8f}, 借贷: {borrow_lend_quote_adjustment:.8f})")
            logger.info(f"计算后总价值: {total_value_in_quote:.8f} {quote_currency}")
            
            return base_balance, quote_balance, total_value_in_quote, None
            
        except Exception as e:
            error_msg = f"计算总余额时发生错误: {str(e)}"
            logger.error(error_msg)
            import traceback
            traceback.print_exc()
            return 0.0, 0.0, 0.0, error_msg

if __name__ == "__main__":
    # 构建配置字典
    config = {
        "API_KEY": API_KEY,
        "SECRET_KEY": SECRET_KEY,
        "SYMBOL": SYMBOL,
        "ORDER_AMOUNT": ORDER_AMOUNT,
        "GRID_TOTAL_INVESTMENT": GRID_TOTAL_INVESTMENT,
        "PRICE_PRECISION": PRICE_PRECISION,
        "QUANTITY_PRECISION": QUANTITY_PRECISION,
        "GRID_SPREAD": SPREAD,
        "LONG_BOLL_PERIOD": LONG_BOLL_PERIOD,
        "LONG_BOLL_STD": LONG_BOLL_STD,
        "SHORT_BOLL_PERIOD": SHORT_BOLL_PERIOD,
        "SHORT_BOLL_STD": SHORT_BOLL_STD,
        "MAX_POSITION_SCALE": MAX_POSITION_SCALE,
        "MIN_POSITION_SCALE": MIN_POSITION_SCALE,
        "MIN_PROFIT_SPREAD": MIN_PROFIT_SPREAD,
        "TRADE_IN_BAND": TRADE_IN_BAND,
        "BUY_BELOW_SMA": BUY_BELOW_SMA,
        "BASE_ORDER_SIZE": BASE_ORDER_SIZE,
        "QUOTE_ORDER_SIZE": QUOTE_ORDER_SIZE
    }
    
    bot = None
    try:
        # 初始化并启动机器人
        bot = BollMakerBot(config)
        bot.start()
        
        # 主循环，保持程序运行
        while bot.running:
            try:
                time.sleep(1)
                
                # 检查WebSocket连接状态
                if bot.running and not bot.ws_client.connected:
                    logger.warning("WebSocket连接断开，尝试重新连接...")
                    bot.ws_client.reconnect()
                    
            except KeyboardInterrupt:
                logger.info("收到退出信号，正在停止机器人...")
                if bot:
                    bot.stop()
                break
            except Exception as e:
                logger.error(f"主循环发生错误: {str(e)}")
                time.sleep(5)  # 发生错误时等待一段时间再继续
                
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序发生错误: {str(e)}")
    finally:
        if bot:
            bot.stop()
        logger.info("程序已退出") 