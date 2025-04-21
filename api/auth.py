"""
API认证模块
"""

import hmac
import hashlib

def create_signature(secret_key: str, message: str) -> str:
    """
    创建API请求签名
    
    Args:
        secret_key: API密钥
        message: 待签名消息
        
    Returns:
        str: 签名字符串
    """
    try:
        signature = hmac.new(
            secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    except Exception as e:
        print(f"创建签名失败: {str(e)}")
        return None
