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

class BackpackWebsocketClient:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.ws = None
        self.last_heartbeat = time.time()
        self.heartbeat_interval = 30  # 30秒
        self.order_book = {"bids": [], "asks": []}
        self.ticker = {}
        self.connected = False
        self.reconnect_delay = 5  # 重连延迟5秒
        
    def _check_heartbeat(self):
        """检查心跳状态并在需要时重连"""
        while True:
            if self.ws and self.connected:
                if time.time() - self.last_heartbeat > self.heartbeat_interval:
                    logger.warning("心跳超时，尝试重新连接")
                    self.reconnect()
            time.sleep(5)
            
    def connect(self):
        """建立WebSocket连接"""
        try:
            self.ws = websocket.WebSocketApp(
                "wss://ws.backpack.exchange",
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open
            )
            
            # 启动心跳检查线程
            heartbeat_thread = threading.Thread(target=self._check_heartbeat)
            heartbeat_thread.daemon = True
            heartbeat_thread.start()
            
            self.ws.run_forever()
        except Exception as e:
            logger.error(f"WebSocket连接失败: {str(e)}")
            self.reconnect()
            
    def reconnect(self):
        """重新连接WebSocket"""
        try:
            if self.ws:
                self.ws.close()
            self.connected = False
            logger.info(f"等待{self.reconnect_delay}秒后重新连接...")
            time.sleep(self.reconnect_delay)
            self.connect()
        except Exception as e:
            logger.error(f"重新连接失败: {str(e)}")
            
    def _on_message(self, ws, message):
        """处理接收到的消息"""
        try:
            data = json.loads(message)
            
            if "type" in data:
                if data["type"] == "pong":
                    self.last_heartbeat = time.time()
                elif data["type"] == "auth":
                    if data.get("success"):
                        logger.info("认证成功")
                        self.connected = True
                        self._subscribe_channels()
                    else:
                        logger.error(f"认证失败: {data.get('message', '')}")
                elif data["type"] == "orderbook":
                    self.order_book["bids"] = data.get("bids", [])
                    self.order_book["asks"] = data.get("asks", [])
                elif data["type"] == "ticker":
                    self.ticker = data
        except json.JSONDecodeError:
            logger.error(f"JSON解析错误: {message}")
        except Exception as e:
            logger.error(f"处理消息时发生错误: {str(e)}")
            
    def _on_error(self, ws, error):
        """处理WebSocket错误"""
        logger.error(f"WebSocket错误: {str(error)}")
        
    def _on_close(self, ws, close_status_code, close_msg):
        """处理WebSocket关闭"""
        logger.warning(f"WebSocket连接关闭: {close_msg if close_msg else 'Unknown reason'}")
        self.connected = False
        
    def _on_open(self, ws):
        """处理WebSocket打开连接"""
        logger.info("WebSocket连接已建立")
        self._authenticate()
        
    def _authenticate(self):
        """发送认证消息"""
        timestamp = str(int(time.time() * 1000))
        sign_message = f"timestamp={timestamp}"
        signature = hmac.new(
            self.secret_key.encode(),
            sign_message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        auth_message = {
            "type": "auth",
            "args": {
                "apiKey": self.api_key,
                "signature": signature,
                "timestamp": timestamp
            }
        }
        self.ws.send(json.dumps(auth_message))
        
    def _subscribe_channels(self):
        """订阅频道"""
        channels = [
            {"type": "subscribe", "channel": "orderbook", "markets": ["SOL-USDC"]},
            {"type": "subscribe", "channel": "ticker", "markets": ["SOL-USDC"]}
        ]
        for channel in channels:
            self.ws.send(json.dumps(channel))
            
    def close(self):
        """关闭WebSocket连接"""
        if self.ws:
            self.ws.close()
            self.connected = False
        logger.info("WebSocket连接已关闭") 