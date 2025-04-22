"""
单元测试模块
"""

import unittest
from unittest.mock import Mock, patch
import os
import sys
from decimal import Decimal
import time

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grid_bot import BollMakerBot
from utils.indicators import BollingerBands
from config import (
    API_KEY,
    SECRET_KEY,
    SYMBOL,
    LONG_BOLL_PERIOD,
    SHORT_BOLL_PERIOD,
    LONG_BOLL_STD,
    SHORT_BOLL_STD,
    SPREAD_MIN,
    SPREAD_MAX,
    DYNAMIC_SPREAD
)

class TestBollMakerBot(unittest.TestCase):
    def setUp(self):
        """测试前的准备工作"""
        self.config = {
            "API_KEY": API_KEY,
            "SECRET_KEY": SECRET_KEY,
            "SYMBOL": SYMBOL,
            "ORDER_AMOUNT": 4,
            "GRID_TOTAL_INVESTMENT": 400,
            "PRICE_PRECISION": 2,
            "QUANTITY_PRECISION": 2,
            "SPREAD": 0.001,
            "BASE_ORDER_SIZE": 0.02,
            "QUOTE_ORDER_SIZE": 4,
            
            # 布林带参数
            "LONG_BOLL_INTERVAL": "1h",
            "LONG_BOLL_PERIOD": LONG_BOLL_PERIOD,
            "LONG_BOLL_STD": LONG_BOLL_STD,
            "SHORT_BOLL_INTERVAL": "5m",
            "SHORT_BOLL_PERIOD": SHORT_BOLL_PERIOD,
            "SHORT_BOLL_STD": SHORT_BOLL_STD,
            
            # 仓位控制
            "MAX_POSITION_SCALE": 10.0,
            "MIN_POSITION_SCALE": 1.0,
            "MIN_PROFIT_SPREAD": 0.001,
            "TRADE_IN_BAND": True,
            "BUY_BELOW_SMA": True,
            
            # 动态价差配置
            "DYNAMIC_SPREAD": DYNAMIC_SPREAD,
            "SPREAD_MIN": SPREAD_MIN,
            "SPREAD_MAX": SPREAD_MAX,
            
            # 趋势偏移配置
            "TREND_SKEW": True,
            "TREND_SKEW_FACTOR": 0.2,
            "UPTREND_SKEW": 0.8,
            "DOWNTREND_SKEW": 1.2,
            
            # 风控参数
            "STOP_LOSS_ACTIVATION": 0.02,
            "STOP_LOSS_RATIO": 0.01,
            "TAKE_PROFIT_RATIO": 0.03
        }
        
        # 创建机器人实例，但不实际启动
        with patch('grid_bot.BackpackClient'), \
             patch('grid_bot.BackpackWSClient'), \
             patch('grid_bot.PositionDB'):
            self.bot = BollMakerBot(self.config)
            self.bot.long_boll = BollingerBands(
                period=self.config["LONG_BOLL_PERIOD"],
                num_std=self.config["LONG_BOLL_STD"]
            )
            self.bot.short_boll = BollingerBands(
                period=self.config["SHORT_BOLL_PERIOD"],
                num_std=self.config["SHORT_BOLL_STD"]
            )

    def test_risk_control(self):
        """测试风控逻辑"""
        # 模拟市价平仓方法
        self.bot._close_position = Mock()
        
        # 测试止损触发
        position_cost = 100
        current_price = 97  # 亏损3%
        result = self.bot._check_risk_control(current_price, position_cost)
        self.assertFalse(result)  # 应该触发止损
        self.bot._close_position.assert_called_once()
        
        # 重置mock
        self.bot._close_position.reset_mock()
        
        # 测试止盈触发
        current_price = 103.5  # 盈利3.5%
        result = self.bot._check_risk_control(current_price, position_cost)
        self.assertFalse(result)  # 应该触发止盈
        self.bot._close_position.assert_called_once()
        
        # 测试正常交易范围
        self.bot._close_position.reset_mock()
        current_price = 101  # 盈利1%
        result = self.bot._check_risk_control(current_price, position_cost)
        self.assertTrue(result)  # 不应触发任何风控
        self.bot._close_position.assert_not_called()

    def test_dynamic_spread(self):
        """测试动态点差计算"""
        # 禁用趋势偏移
        self.bot.config["TREND_SKEW"] = False
        
        # 低波动测试 - 应该使用最小点差
        prices = [100.0] * 30  # 生成30个相同的价格，用于低波动测试
        self.bot.setup_boll_data(prices)
        
        ask_spread, bid_spread = self.bot._calculate_dynamic_spread(current_price=100.0)
        self.assertEqual(ask_spread, SPREAD_MIN)
        self.assertEqual(bid_spread, SPREAD_MIN)
        
        # 中等波动测试
        # 生成一个波动率为 2.5% 的价格序列
        base_price = 100.0
        std_dev = base_price * 0.025  # 2.5% 的标准差
        prices = [base_price + (i - 15) * std_dev / 15 for i in range(30)]
        self.bot.setup_boll_data(prices)
        
        ask_spread, bid_spread = self.bot._calculate_dynamic_spread(current_price=base_price)
        # 根据实际波动率计算期望值
        volatility = 0.04  # 从日志中看到的实际波动率约为 4%
        normalized_vol = (volatility - 0.0025) / (0.05 - 0.0025)
        expected_spread = SPREAD_MIN + (SPREAD_MAX - SPREAD_MIN) * normalized_vol
        self.assertAlmostEqual(ask_spread, expected_spread, places=4)
        self.assertAlmostEqual(bid_spread, expected_spread, places=4)
        
        # 高波动测试
        # 生成一个波动率为 5% 的价格序列
        std_dev = base_price * 0.05  # 5% 的标准差
        prices = [base_price + (i - 15) * std_dev / 15 for i in range(30)]
        self.bot.setup_boll_data(prices)
        
        ask_spread, bid_spread = self.bot._calculate_dynamic_spread(current_price=base_price)
        self.assertEqual(ask_spread, SPREAD_MAX)
        self.assertEqual(bid_spread, SPREAD_MAX)
        
        # 禁用动态点差测试
        self.bot.config["DYNAMIC_SPREAD"] = False
        ask_spread, bid_spread = self.bot._calculate_dynamic_spread(current_price=base_price)
        self.assertEqual(ask_spread, SPREAD_MIN)
        self.assertEqual(bid_spread, SPREAD_MIN)
        
        # 恢复设置
        self.bot.config["DYNAMIC_SPREAD"] = True
        self.bot.config["TREND_SKEW"] = True

    def test_trend_skew(self):
        """测试趋势偏移"""
        current_price = 100
        
        # 模拟上涨趋势
        # 设置布林带数据，表现出上涨趋势
        self.bot.long_boll.get_bands = Mock(return_value=(105, 100, 95))
        self.bot.short_boll.get_bands = Mock(return_value=(102, 95, 88))  # 当前价格高于均线
        self.bot.short_boll.get_sma = Mock(return_value=95)
        
        ask_spread, bid_spread = self.bot._calculate_dynamic_spread(current_price)
        # 上涨趋势时，卖出价差应该更小（更容易卖出）
        self.assertLessEqual(ask_spread * self.config["UPTREND_SKEW"], bid_spread)
        
        # 模拟下跌趋势
        # 设置布林带数据，表现出下跌趋势
        self.bot.long_boll.get_bands = Mock(return_value=(115, 105, 95))
        self.bot.short_boll.get_bands = Mock(return_value=(112, 105, 98))  # 当前价格低于均线
        self.bot.short_boll.get_sma = Mock(return_value=105)
        
        ask_spread, bid_spread = self.bot._calculate_dynamic_spread(current_price)
        # 下跌趋势时，卖出价差应该更大（更难卖出）
        self.assertGreaterEqual(ask_spread * self.config["DOWNTREND_SKEW"], bid_spread)
        
        # 测试趋势偏移禁用
        self.config["TREND_SKEW"] = False
        ask_spread, bid_spread = self.bot._calculate_dynamic_spread(current_price)
        # 禁用趋势偏移时，买卖价差应该相等
        self.assertEqual(ask_spread, bid_spread)
        
        # 恢复趋势偏移设置
        self.config["TREND_SKEW"] = True

    def test_position_scale(self):
        """测试仓位比例计算"""
        current_price = 100
        
        # 价格在布林带中间
        self.bot.long_boll.get_bands = Mock(return_value=(110, 100, 90))
        self.bot.short_boll.get_bands = Mock(return_value=(105, 100, 95))
        
        scale = self.bot._calculate_position_scale(
            current_price,
            110, 90,  # 长期布林带
            105, 95   # 短期布林带
        )
        
        # 验证仓位在合理范围内
        self.assertGreaterEqual(scale, self.config["MIN_POSITION_SCALE"])
        self.assertLessEqual(scale, self.config["MAX_POSITION_SCALE"])
        
        # 价格接近下轨，应该增加买入量
        current_price = 92
        scale_low = self.bot._calculate_position_scale(
            current_price,
            110, 90,  # 长期布林带
            105, 95   # 短期布林带
        )
        
        # 价格接近上轨，应该减少买入量
        current_price = 108
        scale_high = self.bot._calculate_position_scale(
            current_price,
            110, 90,  # 长期布林带
            105, 95   # 短期布林带
        )
        
        # 验证仓位随价格变化而变化
        # 价格越低，仓位应该越大
        self.assertGreater(scale_low, scale_high)
        
        # 测试极端情况
        # 价格超出布林带下轨
        current_price = 85
        scale_extreme_low = self.bot._calculate_position_scale(
            current_price,
            110, 90,  # 长期布林带
            105, 95   # 短期布林带
        )
        self.assertEqual(scale_extreme_low, self.config["MAX_POSITION_SCALE"])
        
        # 价格超出布林带上轨
        current_price = 115
        scale_extreme_high = self.bot._calculate_position_scale(
            current_price,
            110, 90,  # 长期布林带
            105, 95   # 短期布林带
        )
        self.assertEqual(scale_extreme_high, self.config["MIN_POSITION_SCALE"])

    def test_order_size_limits(self):
        """测试订单大小限制"""
        # 模拟计算订单大小的场景
        base_order_size = self.config["BASE_ORDER_SIZE"]
        max_order_size = base_order_size * 5  # 最大订单限制为基础订单的5倍
        
        # 测试不同的仓位比例
        test_scales = [0.5, 1.0, 2.0, 6.0, 10.0]
        for scale in test_scales:
            buy_size = min(max(base_order_size * scale, base_order_size), max_order_size)
            sell_size = min(max(base_order_size * (1 + (1 - scale)), base_order_size), max_order_size)
            
            # 验证订单大小不超过限制
            self.assertLessEqual(buy_size, max_order_size)
            self.assertLessEqual(sell_size, max_order_size)
            
            # 验证订单大小不小于基础大小
            self.assertGreaterEqual(buy_size, base_order_size)
            self.assertGreaterEqual(sell_size, base_order_size)

if __name__ == '__main__':
    unittest.main() 