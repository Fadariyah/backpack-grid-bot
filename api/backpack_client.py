"""
API请求客户端模块
"""

import json
import time
import requests
from typing import Dict, Any, Optional, List, Union
from decimal import Decimal
from urllib.parse import urlencode
from .auth import create_signature
from config import API_URL, API_VERSION, DEFAULT_WINDOW
from logger import setup_logger

logger = setup_logger("api_client")

class BackpackClient:
    """
    Backpack API客户端
    
    订单类型枚举:
    - Limit: 限价单
    - Market: 市价单
    - Ioc: 立即成交或取消
    - Fok: 全部成交或取消
    - Postonly: 只做挂单
    
    订单方向:
    - Bid: 买入
    - Ask: 卖出
    """

    ORDER_TYPES = {
        "LIMIT": "Limit",
        "MARKET": "Market",
        "IOC": "Ioc",
        "FOK": "Fok",
        "POST_ONLY": "Postonly"
    }

    ORDER_SIDES = {
        "BUY": "Bid",
        "SELL": "Ask"
    }

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key

    def make_request(self, method: str, endpoint: str, instruction=None, 
                    params=None, data=None, retry_count=3) -> Dict:
        """
        执行API请求，支持重试机制
        
        Args:
            method: HTTP方法 (GET, POST, DELETE)
            endpoint: API端点
            instruction: API指令
            params: 查询参数
            data: 请求体数据
            retry_count: 重试次数
            
        Returns:
            API响应数据
        """
        url = f"{API_URL}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        
        # 构建签名信息
        if instruction:
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
            
            signature = create_signature(self.secret_key, sign_message)
            if not signature:
                return {"error": "签名创建失败"}
            
            headers.update({
                'X-API-KEY': self.api_key,
                'X-SIGNATURE': signature,
                'X-TIMESTAMP': timestamp,
                'X-WINDOW': window
            })
        
        # 添加查询参数到URL
        if params and method.upper() in ['GET', 'DELETE']:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url += f"?{query_string}"
        
        # 实施重试机制
        for attempt in range(retry_count):
            try:
                if method.upper() == 'GET':
                    response = requests.get(url, headers=headers, timeout=10)
                elif method.upper() == 'POST':
                    response = requests.post(url, headers=headers, data=json.dumps(data) if data else None, timeout=10)
                elif method.upper() == 'DELETE':
                    response = requests.delete(url, headers=headers, data=json.dumps(data) if data else None, timeout=10)
                else:
                    return {"error": f"不支持的请求方法: {method}"}
                
                # 处理响应
                if response.status_code in [200, 201]:
                    return response.json() if response.text.strip() else {}
                elif response.status_code == 429:  # 速率限制
                    wait_time = 1 * (2 ** attempt)  # 指数退避
                    logger.warning(f"遇到速率限制，等待 {wait_time} 秒后重试")
                    time.sleep(wait_time)
                    continue
                else:
                    error_msg = f"状态码: {response.status_code}, 消息: {response.text}"
                    if attempt < retry_count - 1:
                        logger.warning(f"请求失败 ({attempt+1}/{retry_count}): {error_msg}")
                        time.sleep(1)  # 简单重试延迟
                        continue
                    return {"error": error_msg}
            
            except requests.exceptions.Timeout:
                if attempt < retry_count - 1:
                    logger.warning(f"请求超时 ({attempt+1}/{retry_count})，重试中...")
                    continue
                return {"error": "请求超时"}
            except requests.exceptions.ConnectionError:
                if attempt < retry_count - 1:
                    logger.warning(f"连接错误 ({attempt+1}/{retry_count})，重试中...")
                    time.sleep(2)  # 连接错误通常需要更长等待
                    continue
                return {"error": "连接错误"}
            except Exception as e:
                if attempt < retry_count - 1:
                    logger.warning(f"请求异常 ({attempt+1}/{retry_count}): {str(e)}，重试中...")
                    continue
                return {"error": f"请求失败: {str(e)}"}
        
        return {"error": "达到最大重试次数"}

    def get_ticker(self, symbol: str) -> Dict:
        """获取市场价格"""
        endpoint = f"/api/{API_VERSION}/ticker"
        params = {"symbol": symbol}
        return self.make_request("GET", endpoint, params=params)

    def place_order(self, order_details: dict) -> Dict:
        """
        执行订单
        
        Args:
            order_details: 订单详情，包含以下字段：
                - symbol: 交易对
                - side: 方向 (Bid/Ask)
                - orderType: 订单类型 (Limit/Market/Ioc/Fok/Postonly)
                - quantity: 数量
                - price: 价格
                - timeInForce: 有效期 (GTC)
                可选字段：
                - postOnly: 是否只做挂单
                - reduceOnly: 是否只减仓
                - clientId: 客户端订单ID
                - quoteQuantity: 报价币种数量
                - autoBorrow: 是否自动借币
                - autoLendRedeem: 是否自动赎回借出
                - autoBorrowRepay: 是否自动还币
                - autoLend: 是否自动出借
        """
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
        
        # 记录完整的请求参数
        logger.debug(f"下单参数: {params}")
        
        return self.make_request("POST", endpoint, instruction, params, order_details)

    def get_balance(self) -> Dict:
        """获取账户余额"""
        endpoint = f"/api/{API_VERSION}/capital"
        instruction = "balanceQuery"
        return self.make_request("GET", endpoint, instruction)

    def get_open_orders(self, symbol: str = None) -> Dict:
        """获取未成交订单"""
        endpoint = f"/api/{API_VERSION}/orders"
        instruction = "orderQueryAll"
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self.make_request("GET", endpoint, instruction, params)

    def cancel_all_orders(self, symbol: str) -> Dict:
        """取消所有订单"""
        endpoint = f"/api/{API_VERSION}/orders"
        instruction = "orderCancelAll"
        params = {"symbol": symbol}
        data = {"symbol": symbol}
        return self.make_request("DELETE", endpoint, instruction, params, data)

    def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """取消指定订单"""
        endpoint = f"/api/{API_VERSION}/order"
        instruction = "orderCancel"
        params = {"orderId": order_id, "symbol": symbol}
        return self.make_request("DELETE", endpoint, instruction, params)

    def get_markets(self) -> Dict:
        """获取所有交易对信息"""
        endpoint = f"/api/{API_VERSION}/markets"
        return self.make_request("GET", endpoint)

    def get_order_book(self, symbol: str, limit: int = 20) -> Dict:
        """获取市场深度"""
        endpoint = f"/api/{API_VERSION}/depth"
        params = {"symbol": symbol, "limit": str(limit)}
        return self.make_request("GET", endpoint, params=params)

    def get_fill_history(self, symbol: str = None, limit: int = 100) -> Dict:
        """获取历史成交记录"""
        endpoint = f"/wapi/{API_VERSION}/history/fills"
        instruction = "fillHistoryQueryAll"
        params = {"limit": str(limit)}
        if symbol:
            params["symbol"] = symbol
        return self.make_request("GET", endpoint, instruction, params)

    def get_klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> List:
        """
        获取K线数据
        
        Args:
            symbol: 交易对
            interval: 时间间隔 (1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1month)
            limit: 获取数量，默认500，最大1000
            
        Returns:
            List: K线数据列表，每个元素包含：
            [
                timestamp,  # 开盘时间（秒）
                open,      # 开盘价
                high,     # 最高价
                low,      # 最低价
                close,    # 收盘价
                volume,   # 成交量
                closeTime # 收盘时间（秒）
            ]
        """
        endpoint = f"/api/{API_VERSION}/klines"
        
        # 计算开始时间（当前时间减去 limit 个 interval 的时间）
        interval_seconds = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "6h": 21600,
            "8h": 28800,
            "12h": 43200,
            "1d": 86400,
            "3d": 259200,
            "1w": 604800,
            "1month": 2592000
        }
        
        # 获取间隔的秒数
        if interval not in interval_seconds:
            logger.error(f"不支持的时间间隔: {interval}")
            return []
            
        interval_secs = interval_seconds[interval]
        
        # 获取当前时间戳（秒）并向下取整到整分钟
        current_time = int(time.time())
        current_time = current_time - (current_time % 60)
        
        # 计算开始时间
        total_seconds = interval_secs * (limit + 1)  # 多获取一个周期的数据
        start_time = current_time - total_seconds
        
        logger.debug(f"获取K线数据 - 交易对: {symbol}, 间隔: {interval}, 数量: {limit}")
        logger.debug(f"时间范围 - 开始: {start_time} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))})")
        
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": str(limit),
            "startTime": str(start_time)
        }
        
        response = self.make_request("GET", endpoint, params=params)
        
        if isinstance(response, dict) and "error" in response:
            logger.error(f"获取K线数据失败: {response['error']}")
            return []
            
        return response if isinstance(response, list) else []

    def get_borrow_lend_positions(self) -> Dict:
        """获取借贷仓位信息"""
        endpoint = f"/api/{API_VERSION}/borrowLend/positions"
        instruction = "borrowLendPositionQuery"
        return self.make_request("GET", endpoint, instruction)