"""
WebSocket客户端模块
"""

import json
import time
import threading
import websocket
from typing import Dict, List, Optional, Callable
from decimal import Decimal
from logger import setup_logger

logger = setup_logger("ws_client")

class BackpackWSClient:
    def __init__(self, 
                 api_key: str = "", 
                 secret_key: str = "",
                 auto_reconnect: bool = True):
        """
        初始化WebSocket客户端
        """
        self.ws_url = "wss://ws.backpack.exchange"
        self.api_key = api_key
        self.secret_key = secret_key
        self.auto_reconnect = auto_reconnect
        
        # WebSocket连接相关
        self.ws: Optional[websocket.WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.ws_lock = threading.Lock()
        self.running = False
        self.connected = False
        
        # 订单簿数据
        self.orderbook: Dict[str, List[List[float]]] = {
            "bids": [],  # [[price, quantity], ...]
            "asks": []   # [[price, quantity], ...]
        }
        self.bid_price: float = 0
        self.ask_price: float = 0
        self.last_price: float = 0
        
        # 订阅管理
        self.subscriptions: List[str] = []
        
        # 回调函数
        self.on_message_callback: Optional[Callable] = None
        
    def connect(self):
        """建立WebSocket连接"""
        if self.connected:
            return
            
        self.running = True
        
        def on_message(ws, message):
            self._handle_message(message)
            
        def on_error(ws, error):
            logger.error(f"WebSocket错误: {error}")
            self.connected = False
            
        def on_close(ws, close_status_code, close_msg):
            self.connected = False
            logger.info(f"WebSocket连接关闭: {close_msg}")
            if self.auto_reconnect and self.running:
                self.reconnect()
                
        def on_open(ws):
            self.connected = True
            logger.info("WebSocket连接已建立")
            # 重新订阅之前的频道
            for sub in self.subscriptions:
                self._send_subscribe(sub)
                
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        self.ws_thread = threading.Thread(
            target=self.ws.run_forever,
            daemon=True
        )
        self.ws_thread.start()
        
    def reconnect(self):
        """重新连接"""
        with self.ws_lock:
            if self.ws:
                self.ws.close()
            time.sleep(1)  # 等待1秒后重连
            self.connect()
            
    def subscribe_orderbook(self, symbol: str):
        """订阅订单簿数据"""
        channel = f"depth.{symbol}"
        if channel not in self.subscriptions:
            self.subscriptions.append(channel)
            if self.connected:
                self._send_subscribe(channel)
                
    def _send_subscribe(self, channel: str):
        """发送订阅请求"""
        sub_message = {
            "type": "subscribe",
            "channel": channel,
        }
        self.ws.send(json.dumps(sub_message))
        
    def _handle_message(self, message: str):
        """处理WebSocket消息"""
        try:
            data = json.loads(message)
            if "type" not in data:
                return
                
            if data["type"] == "depth":
                self._update_orderbook(data)
                
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            
    def _update_orderbook(self, data: Dict):
        """更新订单簿"""
        if "bids" in data:
            for bid in data["bids"]:
                price = float(bid[0])
                quantity = float(bid[1])
                
                if quantity == 0:
                    self.orderbook["bids"] = [b for b in self.orderbook["bids"] if b[0] != price]
                else:
                    # 查找是否存在相同价位
                    found = False
                    for i, b in enumerate(self.orderbook["bids"]):
                        if b[0] == price:
                            self.orderbook["bids"][i] = [price, quantity]
                            found = True
                            break
                    
                    if not found:
                        self.orderbook["bids"].append([price, quantity])
                        
            # 按价格降序排序
            self.orderbook["bids"] = sorted(
                self.orderbook["bids"], 
                key=lambda x: x[0], 
                reverse=True
            )
            
        if "asks" in data:
            for ask in data["asks"]:
                price = float(ask[0])
                quantity = float(ask[1])
                
                if quantity == 0:
                    self.orderbook["asks"] = [a for a in self.orderbook["asks"] if a[0] != price]
                else:
                    # 查找是否存在相同价位
                    found = False
                    for i, a in enumerate(self.orderbook["asks"]):
                        if a[0] == price:
                            self.orderbook["asks"][i] = [price, quantity]
                            found = True
                            break
                    
                    if not found:
                        self.orderbook["asks"].append([price, quantity])
                        
            # 按价格升序排序
            self.orderbook["asks"] = sorted(
                self.orderbook["asks"], 
                key=lambda x: x[0]
            )
            
        # 更新最优价格
        if self.orderbook["bids"] and self.orderbook["asks"]:
            self.bid_price = self.orderbook["bids"][0][0]
            self.ask_price = self.orderbook["asks"][0][0]
            self.last_price = (self.bid_price + self.ask_price) / 2
            
    def get_orderbook(self) -> Dict[str, List[List[float]]]:
        """获取当前订单簿"""
        return self.orderbook
        
    def get_best_bid_ask(self) -> tuple[float, float]:
        """获取最优买卖价"""
        return self.bid_price, self.ask_price
        
    def get_mid_price(self) -> float:
        """获取中间价"""
        return self.last_price
        
    def close(self):
        """关闭WebSocket连接"""
        self.running = False
        if self.ws:
            self.ws.close()
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=1) 