"""
技术指标计算工具
"""

import numpy as np
from typing import Tuple, List
from decimal import Decimal

class BollingerBands:
    def __init__(self, period: int = 20, num_std: float = 2.0):
        """
        初始化布林带计算器
        
        Args:
            period: MA周期
            num_std: 标准差倍数
        """
        self.period = period
        self.num_std = num_std
        self.prices: List[float] = []
        
    def update(self, price: float) -> Tuple[float, float, float]:
        """
        更新价格并计算布林带
        
        Args:
            price: 最新价格
            
        Returns:
            (upper, middle, lower): 上轨、中轨、下轨
        """
        self.prices.append(price)
        
        # 保持固定周期
        if len(self.prices) > self.period:
            self.prices.pop(0)
            
        # 至少需要一个完整周期才能计算
        if len(self.prices) < self.period:
            return price, price, price
            
        # 计算布林带
        prices_array = np.array(self.prices)
        middle = prices_array.mean()
        std = prices_array.std()
        
        upper = middle + self.num_std * std
        lower = middle - self.num_std * std
        
        return upper, middle, lower
        
    def get_position_scale(self, price: float, upper: float, lower: float, 
                          max_scale: float = 100.0, min_scale: float = 1.0) -> float:
        """
        根据价格在布林带中的位置计算仓位比例
        
        Args:
            price: 当前价格
            upper: 上轨
            lower: 下轨
            max_scale: 最大仓位倍数
            min_scale: 最小仓位倍数
            
        Returns:
            float: 仓位比例
        """
        # 将价格标准化到[-1, 1]区间
        band_width = upper - lower
        if band_width == 0:
            return min_scale
            
        position = 2 * (price - lower) / band_width - 1
        position = max(-1.0, min(1.0, position))
        
        # 将[-1, 1]映射到[max_scale, min_scale]
        scale = (max_scale - min_scale) * (1 - position) / 2 + min_scale
        return scale
        
    def is_price_in_band(self, price: float, upper: float, lower: float) -> bool:
        """
        判断价格是否在布林带区间内
        """
        return lower <= price <= upper 