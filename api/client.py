"""
API请求客户端模块
"""

import json
import time
import requests
import hmac
import hashlib
from typing import Dict, Any, Optional, List, Union
from decimal import Decimal
from urllib.parse import urlencode
from .auth import create_signature
from config import API_URL, API_VERSION, DEFAULT_WINDOW
from .ws_client import BackpackWSClient

# 全局WebSocket客户端实例
ws_client: Optional[BackpackWSClient] = None

def init_ws_client(api_key: str = "", secret_key: str = ""):
    """初始化WebSocket客户端"""
    global ws_client
    if ws_client is None:
        ws_client = BackpackWSClient(api_key, secret_key)
        ws_client.connect()
    return ws_client

def get_orderbook(symbol: str) -> Dict:
    """
    获取订单簿数据
    优先使用WebSocket数据，如果未连接则使用REST API
    """
    global ws_client
    
    # 如果WebSocket已连接且有数据
    if ws_client and ws_client.connected:
        orderbook = ws_client.get_orderbook()
        if orderbook["bids"] and orderbook["asks"]:
            return orderbook
            
    # 否则使用REST API
    url = f"{API_URL}/api/{API_VERSION}/depth"
    params = {"symbol": symbol}
    
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"获取订单簿失败: {response.text}"}
    except Exception as e:
        return {"error": f"请求订单簿时出错: {str(e)}"}

def get_mid_price(symbol: str) -> Decimal:
    """获取中间价格"""
    global ws_client
    
    # 优先使用WebSocket数据
    if ws_client and ws_client.connected:
        return Decimal(str(ws_client.get_mid_price()))
        
    # 否则使用订单簿数据计算
    orderbook = get_orderbook(symbol)
    if "error" in orderbook:
        return None
        
    if orderbook["bids"] and orderbook["asks"]:
        best_bid = Decimal(str(orderbook["bids"][0][0]))
        best_ask = Decimal(str(orderbook["asks"][0][0]))
        return (best_bid + best_ask) / Decimal('2')
    
    return None

def make_request(method: str, endpoint: str, api_key=None, secret_key=None, instruction=None, 
                params=None, data=None, retry_count=3) -> Dict:
    """
    执行API请求，支持重试机制
    """
    url = f"{API_URL}{endpoint}"
    headers = {'Content-Type': 'application/json'}
    
    if api_key and secret_key and instruction:
        timestamp = str(int(time.time() * 1000))
        window = DEFAULT_WINDOW
        
        # 构建签名消息
        query_string = ""
        if params:
            sorted_params = sorted(params.items())
            query_string = "&".join([f"{k}={v}" for k, v in sorted_params])
            
        sign_message = f"instruction={instruction}"
        if query_string:
            sign_message += f"&{query_string}"
        sign_message += f"&timestamp={timestamp}&window={window}"
        
        signature = create_signature(secret_key, sign_message)
        if not signature:
            return {"error": "签名创建失败"}
            
        headers.update({
            'X-API-KEY': api_key,
            'X-SIGNATURE': signature,
            'X-TIMESTAMP': timestamp,
            'X-WINDOW': window
        })
    
    # 实施重试机制
    for attempt in range(retry_count):
        try:
            response = requests.request(method, url, headers=headers, params=params, json=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            if attempt < retry_count - 1:
                time.sleep(1)
                continue
            return {"error": "请求超时"}
        except requests.exceptions.HTTPError as e:
            return {"error": f"HTTP错误: {str(e)}"}
        except Exception as e:
            if attempt < retry_count - 1:
                time.sleep(1)
                continue
            return {"error": f"请求失败: {str(e)}"}
    
    return {"error": "达到最大重试次数"}

def get_ticker(symbol: str) -> Dict:
    """获取市场价格"""
    endpoint = f"/api/{API_VERSION}/ticker"
    params = {"symbol": symbol}
    return make_request("GET", endpoint, params=params)

def execute_order(api_key: str, secret_key: str, order_details: Dict) -> Dict:
    """执行订单"""
    endpoint = f"/api/{API_VERSION}/order"
    instruction = "orderExecute"
    
    # 提取所有参数用于签名
    params = {
        "orderType": order_details["orderType"],
        "price": order_details.get("price", "0"),
        "quantity": order_details["quantity"],
        "side": order_details["side"],
        "symbol": order_details["symbol"],
        "timeInForce": order_details.get("timeInForce", "GTC")
    }
    
    # 添加可选参数
    for key in ["postOnly", "reduceOnly", "clientId", "quoteQuantity", 
                "autoBorrow", "autoLendRedeem", "autoBorrowRepay", "autoLend"]:
        if key in order_details:
            params[key] = str(order_details[key]).lower() if isinstance(order_details[key], bool) else str(order_details[key])
            
    return make_request("POST", endpoint, api_key, secret_key, instruction, params, order_details)

def get_balance(api_key: str, secret_key: str) -> Dict:
    """获取账户余额"""
    endpoint = f"/api/{API_VERSION}/capital"
    instruction = "balanceQuery"
    return make_request("GET", endpoint, api_key, secret_key, instruction)

def get_open_orders(api_key: str, secret_key: str, symbol: str = None) -> Dict:
    """获取未成交订单"""
    endpoint = f"/api/{API_VERSION}/orders"
    instruction = "orderQueryAll"
    params = {}
    if symbol:
        params["symbol"] = symbol
    return make_request("GET", endpoint, api_key, secret_key, instruction, params)

def cancel_all_orders(api_key: str, secret_key: str, symbol: str) -> Dict:
    """取消所有订单"""
    endpoint = f"/api/{API_VERSION}/orders"
    instruction = "orderCancelAll"
    params = {"symbol": symbol}
    data = {"symbol": symbol}
    return make_request("DELETE", endpoint, api_key, secret_key, instruction, params, data)

def cancel_order(api_key: str, secret_key: str, order_id: str, symbol: str) -> Dict:
    """取消指定订单"""
    endpoint = f"/api/{API_VERSION}/order"
    instruction = "orderCancel"
    params = {"orderId": order_id, "symbol": symbol}
    data = {"orderId": order_id, "symbol": symbol}
    return make_request("DELETE", endpoint, api_key, secret_key, instruction, params, data)

def get_markets() -> Dict:
    """获取所有交易对信息"""
    endpoint = f"/api/{API_VERSION}/markets"
    return make_request("GET", endpoint)

def get_order_book(symbol: str, limit: int = 20) -> Dict:
    """获取市场深度"""
    endpoint = f"/api/{API_VERSION}/depth"
    params = {"symbol": symbol, "limit": str(limit)}
    return make_request("GET", endpoint, params=params)

def get_fill_history(api_key: str, secret_key: str, symbol: str = None, limit: int = 100) -> Dict:
    """获取历史成交记录"""
    endpoint = f"/wapi/{API_VERSION}/history/fills"
    instruction = "fillHistoryQueryAll"
    params = {"limit": str(limit)}
    if symbol:
        params["symbol"] = symbol
    return make_request("GET", endpoint, api_key, secret_key, instruction, params)

def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> Dict:
    """
    获取K线数据
    
    Args:
        symbol: 交易对
        interval: 时间间隔 (1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1month)
        limit: 获取数量
    """
    endpoint = f"/api/{API_VERSION}/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": str(limit)
    }
    return make_request("GET", endpoint, params=params)

def get_borrow_lend_positions(api_key: str, secret_key: str) -> Dict:
    """获取借贷仓位信息"""
    endpoint = f"/api/{API_VERSION}/borrowLend/positions"
    instruction = "borrowLendPositionQuery"
    return make_request("GET", endpoint, api_key, secret_key, instruction)