"""
WebSocket客户端模块
"""

import json
import time
import threading
import websocket
import hmac
import hashlib
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
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.ws_lock = threading.Lock()
        self.running = False
        self.connected = False
        self.last_heartbeat = 0
        
        # 订单簿数据
        self.orderbook: Dict[str, List[List[float]]] = {
            "bids": [],  # [[price, quantity], ...]
            "asks": []   # [[price, quantity], ...]
        }
        self.bid_price: float = 0
        self.ask_price: float = 0
        self.last_price: float = 0
        
        # 订阅管理
        self.subscriptions: List[Dict] = []
        
        # 回调函数
        self.on_message_callback: Optional[Callable] = None
        
    def _generate_signature(self, params: dict) -> str:
        """生成签名"""
        sorted_params = sorted(params.items())
        signature_payload = '&'.join([f"{key}={value}" for key, value in sorted_params])
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            signature_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
        
    def _authenticate(self):
        """发送认证消息"""
        if not self.api_key or not self.secret_key:
            logger.info("未提供API密钥，跳过认证")
            return
            
        timestamp = str(int(time.time() * 1000))
        params = {
            "timestamp": timestamp,
            "apiKey": self.api_key
        }
        signature = self._generate_signature(params)
        auth_message = {
            "op": "auth",
            "data": {
                "apiKey": self.api_key,
                "timestamp": timestamp,
                "signature": signature
            }
        }
        self.ws.send(json.dumps(auth_message))
        
    def connect(self):
        """建立WebSocket连接"""
        if self.connected:
            return
            
        self.running = True
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                if "success" in data and data["success"] and data.get("op") == "auth":
                    logger.info("WebSocket认证成功")
                    # 重新订阅之前的频道
                    for sub in self.subscriptions:
                        self.ws.send(json.dumps(sub))
                elif "ping" in data:
                    # 响应ping消息
                    pong_msg = {"pong": data["ping"]}
                    self.ws.send(json.dumps(pong_msg))
                    self.last_heartbeat = time.time()
                else:
                    self._handle_message(data)
            except Exception as e:
                logger.error(f"处理WebSocket消息时出错: {e}")
            
        def on_error(ws, error):
            logger.error(f"WebSocket错误: {error}")
            self.connected = False
            self.last_heartbeat = 0  # 强制触发重连
            
        def on_close(ws, close_status_code, close_msg):
            self.connected = False
            logger.info(f"WebSocket连接关闭: {close_msg}")
            if self.auto_reconnect and self.running:
                threading.Thread(target=self.reconnect, daemon=True).start()
                
        def on_open(ws):
            self.connected = True
            logger.info("WebSocket连接已建立")
            self._authenticate()
            # 重新订阅之前的频道
            for sub in self.subscriptions:
                self.ws.send(json.dumps(sub))
                
        websocket.enableTrace(False)
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
        
        # 启动心跳检测线程
        if not self.heartbeat_thread or not self.heartbeat_thread.is_alive():
            self.heartbeat_thread = threading.Thread(
                target=self._heartbeat_check,
                daemon=True
            )
            self.heartbeat_thread.start()
            
    def _heartbeat_check(self):
        """心跳检测"""
        while self.running:
            if self.connected and time.time() - self.last_heartbeat > 30:
                logger.warning("心跳超时，准备重连...")
                self.reconnect()
            time.sleep(1)
            
    def reconnect(self):
        """重新连接"""
        with self.ws_lock:
            if not self.running:
                return
                
            logger.info("正在重新连接WebSocket...")
            if self.ws:
                try:
                    self.ws.close()
                except:
                    pass
                    
            time.sleep(1)  # 等待一秒后重连
            self.connect()
            
    def subscribe_orderbook(self, symbol: str):
        """订阅订单簿数据"""
        sub_message = {
            "op": "subscribe",
            "channel": f"depth.{symbol}"
        }
        if sub_message not in self.subscriptions:
            self.subscriptions.append(sub_message)
            if self.connected:
                self.ws.send(json.dumps(sub_message))
                
    def subscribe_bookticker(self, symbol: str):
        """订阅最优买卖价数据"""
        sub_message = {
            "op": "subscribe",
            "channel": f"bookTicker.{symbol}"
        }
        if sub_message not in self.subscriptions:
            self.subscriptions.append(sub_message)
            if self.connected:
                self.ws.send(json.dumps(sub_message))
                
    def _handle_message(self, data: dict):
        """处理WebSocket消息"""
        try:
            if "stream" in data and "data" in data:
                stream = data["stream"]
                event_data = data["data"]
                
                # 处理订单簿数据
                if stream.startswith("depth."):
                    self._update_orderbook(event_data)
                # 处理最优买卖价数据
                elif stream.startswith("bookTicker."):
                    if 'b' in event_data and 'a' in event_data:
                        self.bid_price = float(event_data['b'])
                        self.ask_price = float(event_data['a'])
                        self.last_price = (self.bid_price + self.ask_price) / 2
                
                # 调用用户回调函数
                if self.on_message_callback:
                    self.on_message_callback(stream, event_data)
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
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            try:
                self.heartbeat_thread.join(timeout=1)
            except:
                pass
                
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
                
        if self.ws_thread and self.ws_thread.is_alive():
            try:
                self.ws_thread.join(timeout=1)
            except:
                pass
                
        self.connected = False
        self.subscriptions = []
        logger.info("WebSocket连接已关闭") 