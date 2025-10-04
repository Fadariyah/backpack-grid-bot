"""
โมดูลหลักสำหรับกลยุทธ์ทำตลาดด้วย Bollinger Band
"""

import time
from typing import List, Dict, Tuple, Optional
from decimal import Decimal
import random
import threading
import logging
import queue
import os
from datetime import datetime

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
    QUOTE_ORDER_SIZE,
    LONG_BOLL_INTERVAL,
    SHORT_BOLL_INTERVAL,
    DYNAMIC_SPREAD,
    SPREAD_MIN,
    SPREAD_MAX,
    TREND_SKEW,
    UPTREND_SKEW,
    DOWNTREND_SKEW,
    STOP_LOSS_ACTIVATION,
    STOP_LOSS_RATIO,
    TAKE_PROFIT_RATIO,
    GRID_LEVELS_PER_SIDE,
    GRID_STEP,
    GRID_SIDE_BUDGET_RATIO,
)
from utils.indicators import BollingerBands
from utils.database import PositionDB
from logger import setup_logger

logger = setup_logger("grid_bot")

class BollMakerBot:
    def __init__(self, config: dict):
        """เริ่มต้นหุ่นยนต์ทำตลาดแบบกริด/Bollinger"""
        self.config = config
        self.api_key = config["API_KEY"]
        self.secret_key = config["SECRET_KEY"]
        self.symbol = config["SYMBOL"]
        
        # สร้างโฟลเดอร์ data หากยังไม่มี
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        
        # เริ่มต้นฐานข้อมูล
        db_path = os.path.join(data_dir, "positions.db")
        self.db = PositionDB(db_path)
        self.logger = logging.getLogger(__name__)
        
        # ตั้งค่าคิวและอีเวนต์สำหรับงานฐานข้อมูล
        self.db_queue = queue.Queue()
        self.position_event = threading.Event()
        self.position_cache = None
        self.last_position_update = 0
        self.position_update_interval = 1
        self.cache_lock = threading.Lock()
        
        # การควบคุมการส่งออเดอร์ (rate-limit ของฝั่งเราเอง)
        self.last_order_time = 0
        self.order_interval = 120  # 120 วินาที (2 นาที)
        
        # เริ่มต้น REST/WS API client
        self.rest_client = BackpackClient(self.api_key, self.secret_key)
        self.ws_client = BackpackWSClient(
            self.api_key, 
            self.secret_key, 
            self.symbol,
            on_message_callback=self._handle_ws_message
        )
        
        # ใช้เฉพาะล็อกที่จำเป็น
        self.boll_lock = threading.Lock()  # ล็อกสำหรับข้อมูล Bollinger
        
        # อีเวนต์บ่งชี้ว่าเริ่มต้นข้อมูลครบแล้ว
        self.init_complete = threading.Event()
        
        # การอัปเดต Kline
        self.kline_update_thread = None
        self.kline_update_interval = 60  # อัปเดตทุก 60 วินาที
        
        # สถานะภายใน
        self.running = False
        self.current_orders = {}
        self.last_price = 0
        self.last_update_time = 0
        
        # ตั้งค่า Bollinger เริ่มต้น
        self.long_boll = BollingerBands(period=LONG_BOLL_PERIOD, num_std=LONG_BOLL_STD)
        self.short_boll = BollingerBands(period=SHORT_BOLL_PERIOD, num_std=SHORT_BOLL_STD)
        self.long_klines = {}
        self.short_klines = {}

    def setup_boll_data(self, prices: List[float], long_period: int = None, short_period: int = None):
        """
        ตั้งค่าข้อมูล Bollinger เพื่อการทดสอบ
        
        Args:
            prices: รายการราคา
            long_period: คาบของ Bollinger ระยะยาว (ถ้ามี)
            short_period: คาบของ Bollinger ระยะสั้น (ถ้ามี)
        """
        if long_period:
            self.long_boll = BollingerBands(period=long_period, num_std=LONG_BOLL_STD)
        if short_period:
            self.short_boll = BollingerBands(period=short_period, num_std=SHORT_BOLL_STD)
            
        # สร้างข้อมูล Kline จำลอง
        current_time = int(time.time())
        for i, price in enumerate(prices):
            timestamp = current_time - (len(prices) - i) * 60  # 1 แท่ง/นาที
            kline_data = {
                "close": price,
                "timestamp": timestamp
            }
            
            # อัปเดตข้อมูล Kline ระยะยาว/สั้น
            self.long_klines[timestamp] = kline_data
            self.short_klines[timestamp] = kline_data
            
        # คำนวณ Bollinger
        with self.boll_lock:
            # เรียงตามเวลา
            sorted_long_klines = sorted(self.long_klines.values(), key=lambda x: x["timestamp"])
            sorted_short_klines = sorted(self.short_klines.values(), key=lambda x: x["timestamp"])
            
            # ดึงราคาปิด
            long_closes = [float(k["close"]) for k in sorted_long_klines[-self.long_boll.period:]]
            short_closes = [float(k["close"]) for k in sorted_short_klines[-self.short_boll.period:]]
            
            # อัปเดต Bollinger
            if len(long_closes) >= self.long_boll.period:
                for price in long_closes:
                    self.long_boll.update(price)
            if len(short_closes) >= self.short_boll.period:
                for price in short_closes:
                    self.short_boll.update(price)
                
        # อัปเดตราคาล่าสุด
        if prices:
            self.last_price = prices[-1]
            self.last_update_time = current_time

    def _process_db_queue(self):
        """ประมวลผลงานในคิวฐานข้อมูล"""
        try:
            while True:
                try:
                    # ดึงข้อความแบบไม่บล็อก
                    msg = self.db_queue.get_nowait()
                    action = msg.get('action')
                    data = msg.get('data')

                    if action == 'update_position':
                        try:
                            order_data = data
                            logger.info(f"เริ่มอัปเดตสถานะถือครอง: {order_data}")
                            
                            size, cost = self.db.get_position(self.symbol)
                            logger.info(f"สถานะถือครองปัจจุบัน: size={size}, cost={cost}")
                            
                            # คำนวณสถานะใหม่
                            order_size = Decimal(str(order_data['quantity']))
                            order_price = Decimal(str(order_data['price']))
                            
                            # ใช้ 'Bid' เพื่อบ่งชี้ฝั่งซื้อ
                            if order_data['side'].upper() == 'BID':
                                new_size = size + order_size
                                new_cost = cost + (order_size * order_price)
                                logger.info(f"อัปเดตซื้อ: +{order_size} @ {order_price}")
                            else:  # Ask
                                new_size = size - order_size
                                # ปรับต้นทุนเมื่อขายออก
                                if size > 0:
                                    cost_reduction = (order_size / size) * cost
                                    new_cost = cost - cost_reduction
                                else:
                                    new_cost = Decimal('0')
                                logger.info(f"อัปเดตขาย: -{order_size} @ {order_price}")
                            
                            # ป้องกันค่าติดลบ
                            new_size = max(Decimal('0'), new_size)
                            new_cost = max(Decimal('0'), new_cost)
                            
                            # อัปเดตฐานข้อมูล (แปลงเป็น float)
                            self.db.update_position(self.symbol, float(new_size), float(new_cost))
                            logger.info(f"อัปเดตสถานะสำเร็จ: new_size={new_size}, new_cost={new_cost}")
                            
                            # บันทึกประวัติการเทรด
                            self.db.add_trade(
                                self.symbol,
                                order_data['side'],
                                float(order_price),
                                float(order_size)
                            )
                            logger.info("บันทึกประวัติการเทรดสำเร็จ")
                        except Exception as e:
                            logger.error(f"เกิดข้อผิดพลาดระหว่างอัปเดตสถานะถือครอง: {e}")
                            logger.exception("รายละเอียดข้อผิดพลาด:")
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
            self.logger.error(f"เกิดข้อผิดพลาดระหว่างประมวลผลคิวฐานข้อมูล: {e}")
            if action == 'get_position':
                self.position_event.set()  # ให้แน่ใจว่าเซตอีเวนต์แม้เกิดข้อผิดพลาด

    def _get_cached_position(self):
        """ดึงสถานะถือครองจากแคช"""
        current_time = time.time()
        if self.position_cache is None or current_time - self.last_position_update > self.position_update_interval:
            # รีเซ็ตอีเวนต์
            self.position_event.clear()
            
            # ขอให้อัปเดตสถานะถือครอง
            self.db_queue.put({
                'action': 'get_position',
                'data': None
            })
            
            # รอการอัปเดต
            self.position_event.wait(timeout=1.0)
            
        return self.position_cache or {'size': 0, 'avg_price': 0}

    def update_position(self, order_data: dict):
        """อัปเดตสถานะถือครอง (เพิ่มงานลงคิว)"""
        try:
            self.db_queue.put({
                'action': 'update_position',
                'data': order_data
            })
        except Exception as e:
            self.logger.error(f"ไม่สามารถเพิ่มงานอัปเดตสถานะถือครองได้: {e}")
            raise

    def _handle_ws_message(self, stream, event_data):
        """จัดการข้อความที่ได้รับจาก WebSocket"""
        try:
            # ข้อมูลราคาท็อปออฟบุ๊ก
            if stream.startswith("bookTicker."):
                if 'b' in event_data and 'a' in event_data:
                    bid_price = float(event_data['b'])
                    ask_price = float(event_data['a'])
                    current_price = (bid_price + ask_price) / 2
                    current_time = time.time()
                    
                    # อัปเดตราคา (ไม่ล็อก ปล่อยให้มีการแข่งขันได้)
                    self.last_price = current_price
                    self.last_update_time = current_time
                    
                    # พิจารณาปรับคำสั่งตามช่วงเวลา
                    if current_time - self.last_order_time >= self.order_interval:
                        if not self.init_complete.is_set():
                            logger.debug("รอให้ข้อมูล Kline พร้อมก่อน...")
                            return
                            
                        position = self._get_cached_position()
                        position_cost = position["avg_price"] if position else 0
                        self._adjust_orders(current_price, position_cost)
                        self.last_order_time = current_time
                    
            # ข้อมูล depth
            elif stream.startswith("depth."):
                if 'b' in event_data and 'a' in event_data:
                    # สามารถเติมตรรกะการใช้ depth ได้ที่นี่
                    pass
                    
            # การอัปเดตสถานะคำสั่ง
            elif stream.startswith("account.orderUpdate."):
                event_type = event_data.get('e')
                
                # อีเวนต์คำสั่งถูกเติมเต็มบางส่วน/ทั้งหมด
                if event_type == 'orderFill':
                    order_id = event_data.get('i')
                    side = event_data.get('S')
                    quantity = float(event_data.get('l', '0'))
                    price = float(event_data.get('p', '0'))
                    
                    # อัปเดตสถานะถือครอง
                    order_data = {
                        'side': side,
                        'quantity': quantity,
                        'price': price
                    }
                    self.update_position(order_data)
                    
        except Exception as e:
            self.logger.error(f"จัดการข้อความ WebSocket ล้มเหลว: {str(e)}")

    def _monitor_price(self):
        """เฝ้าติดตามการเปลี่ยนแปลงของราคา"""
        while self.running:
            try:
                # อัปเดตแคชสถานะถือครองเป็นระยะ
                self._get_cached_position()
                time.sleep(1)
            except Exception as e:
                logger.error(f"การเฝ้าติดตามราคาเกิดข้อผิดพลาด: {str(e)}")
                time.sleep(5)

    def _calculate_dynamic_spread(self, current_price):
        """
        คำนวณสเปรดแบบไดนามิก
        :param current_price: ราคาปัจจุบัน
        :return: (ask_spread, bid_spread)
        """
        try:
            if not self.config["DYNAMIC_SPREAD"]:
                return self.config["SPREAD"], self.config["SPREAD"]

            # ดึง Bollinger ระยะสั้น
            short_upper, _, short_lower = self.short_boll.get_bands()
            
            # ใช้ความกว้างของแบนด์เทียบกับราคาเป็นตัวแทนความผันผวน
            volatility = abs(short_upper - short_lower) / current_price

            # บันทึกเพื่อดีบัก
            self.logger.debug(
                f"คำนวณความผันผวน - บน: {short_upper}, ล่าง: {short_lower}, "
                + f"ราคา: {current_price}, ผันผวน: {volatility:.4%}"
            )

            # กำหนด base spread ตามระดับความผันผวน
            if volatility <= 0.0025:  # ผันผวนต่ำ (0.25%)
                base_spread = self.config["SPREAD_MIN"]
            elif volatility >= 0.05:  # ผันผวนสูง (5%)
                base_spread = self.config["SPREAD_MAX"]
            else:
                # เชิงเส้นระหว่างช่วง
                spread_range = self.config["SPREAD_MAX"] - self.config["SPREAD_MIN"]
                normalized_vol = (volatility - 0.0025) / (0.05 - 0.0025)
                base_spread = self.config["SPREAD_MIN"] + spread_range * normalized_vol

            # ปรับตามแนวโน้ม
            if self.config["TREND_SKEW"]:
                sma = self.short_boll.get_sma()
                if current_price > sma:  # ขาขึ้น
                    # ขาขึ้น: ลดสเปรดฝั่งขายให้ติดตลาดขึ้น และเพิ่มสเปรดฝั่งซื้อเพื่อคุมความเสี่ยง
                    ask_spread = base_spread * self.config["UPTREND_SKEW"]  # < 1
                    bid_spread = base_spread * (2 - self.config["UPTREND_SKEW"])  # รวมคงเดิม
                else:  # ขาลง
                    # ขาลง: เพิ่มสเปรดฝั่งขายเพื่อคุมความเสี่ยง และลดสเปรดฝั่งซื้อให้ติดตลาดขึ้น
                    ask_spread = base_spread * self.config["DOWNTREND_SKEW"]  # > 1
                    bid_spread = base_spread * (2 - self.config["DOWNTREND_SKEW"])  # รวมคงเดิม
            else:
                ask_spread = bid_spread = base_spread

            # บังคับให้อยู่ในช่วงที่ตั้งค่าไว้
            ask_spread = max(min(ask_spread, self.config["SPREAD_MAX"]), self.config["SPREAD_MIN"])
            bid_spread = max(min(bid_spread, self.config["SPREAD_MAX"]), self.config["SPREAD_MIN"])

            # บันทึกผลสุดท้ายเพื่อดีบัก
            self.logger.debug(
                f"คำนวณสเปรด - ฐาน: {base_spread:.4f}, "
                + f"ขาย: {ask_spread:.4f}, ซื้อ: {bid_spread:.4f}"
            )

            return ask_spread, bid_spread

        except Exception as e:
            self.logger.error(f"คำนวณสเปรดแบบไดนามิกล้มเหลว: {str(e)}")
            return self.config["SPREAD"], self.config["SPREAD"]

    def _check_risk_control(self, current_price: float, position_cost: float) -> bool:
        """ตรวจสอบเงื่อนไขความเสี่ยง (SL/TP)"""
        try:
            if position_cost <= 0:
                return True
            
            # ผลตอบแทนเมื่อเทียบทุน
            roi = (current_price - position_cost) / position_cost
            
            # เงื่อนไข Stop Loss
            if abs(roi) >= self.config["STOP_LOSS_ACTIVATION"]:
                if roi < 0 and abs(roi) >= self.config["STOP_LOSS_RATIO"]:
                    logger.warning(f"ทริกเกอร์ Stop Loss: ROI={roi:.2%}, ราคา={current_price}, ต้นทุน={position_cost}")
                    # ปิดสถานะด้วย Market
                    self._close_position()
                    return False
            
            # เงื่อนไข Take Profit
            if roi >= self.config["TAKE_PROFIT_RATIO"]:
                logger.info(f"ทริกเกอร์ Take Profit: ROI={roi:.2%}, ราคา={current_price}, ต้นทุน={position_cost}")
                self._close_position()
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"ตรวจสอบเงื่อนไขความเสี่ยงล้มเหลว: {str(e)}")
            return True

    def _close_position(self):
        """ปิดสถานะทั้งหมดด้วย Market"""
        try:
            position = self._get_cached_position()
            if position and position["size"] > 0:
                order_details = {
                    "symbol": self.symbol,
                    "side": "Ask",
                    "orderType": "Market",
                    "quantity": str(position["size"]),
                    "timeInForce": "IOC"
                }
                order = self.rest_client.place_order(order_details)
                if order and "id" in order:
                    logger.info(f"ปิดสถานะด้วย Market สำเร็จ: จำนวน={position['size']}")
                
        except Exception as e:
            logger.error(f"ปิดสถานะด้วย Market ล้มเหลว: {str(e)}")

    def _adjust_orders(self, current_price: float, position_cost: float):
        """ปรับคำสั่งซื้อขายตามราคาปัจจุบันและต้นทุนถือครอง"""
        try:
            # ตรวจความเสี่ยง
            if not self._check_risk_control(current_price, position_cost):
                return
            
            # ดึงแบนด์อย่างปลอดภัยภายใต้ล็อก แล้วคำนวณนอกล็อก
            with self.boll_lock:
                long_bands = self.long_boll.get_bands()
                short_bands = self.short_boll.get_bands()
            
            long_upper, long_middle, long_lower = long_bands
            short_upper, short_middle, short_lower = short_bands
            
            # คำนวณสเกลของสถานะเป้าหมาย (ยังคงใช้ได้ ถ้าจะปรับสูตรแจ้งได้)
            position_scale = self._calculate_position_scale(
                current_price,
                long_upper, long_lower,
                short_upper, short_lower
            )

            # --- เดิม: ยกเลิกทั้งหมด + วาง 1 ชั้น ---
            # # PATCH: ยกเลิกคำสั่งเดิมทั้งหมด (คงไว้ก่อน เพื่อลดคำสั่งค้าง)
            # self.rest_client.cancel_all_orders(self.symbol)
            # self.current_orders = {}
            # ... (วาง 1 buy / 1 sell) ...
            # --- จบของเดิม ---

            # PATCH: Multi-level Grid (หลายชั้น)
            # ยกเลิกทั้งหมดก่อน เพื่อความง่าย/ชัดเจน (ถ้าต้องการ "reconcile รายคำสั่ง" แจ้งได้)
            self.rest_client.cancel_all_orders(self.symbol)
            self.current_orders = {}

            levels = int(self.config.get("GRID_LEVELS_PER_SIDE", 6))
            step = float(self.config.get("GRID_STEP", 0.006))  # 0.6% ต่อขั้น
            price_prec = int(self.config["PRICE_PRECISION"])
            qty_prec = int(self.config["QUANTITY_PRECISION"])

            # เงื่อนไขเปิดออเดอร์ตาม band/SMA เดิม
            can_buy = True
            can_sell = True
            if self.config["TRADE_IN_BAND"]:
                can_buy = self.short_boll.is_price_in_band(current_price, short_upper, short_lower)
                can_sell = self.short_boll.is_price_in_band(current_price, short_upper, short_lower)
            if self.config["BUY_BELOW_SMA"]:
                can_buy = can_buy and current_price < short_middle

            # กำหนดงบต่อฝั่ง
            side_ratio = float(self.config.get("GRID_SIDE_BUDGET_RATIO", 0.5))
            total_budget = float(self.config.get("GRID_TOTAL_INVESTMENT", 200))
            buy_usdc_cap = total_budget * side_ratio                  # กันงบฝั่งซื้อเป็น USDC
            sell_base_cap = (total_budget * side_ratio) / current_price  # กันจำนวนหน่วย SOL ฝั่งขาย

            # ขนาดคำสั่งพื้นฐาน (ต่อชั้น)
            base_order_size = float(self.config["BASE_ORDER_SIZE"])   # หน่วยเป็น SOL
            # หมายเหตุ: ถ้าอยากกำกับด้วย USDC ต่อชั้นแทน แจ้งได้ครับ จะสลับไปใช้ QUOTE_ORDER_SIZE

            # ตัวนับงบที่ใช้จริง
            used_buy_usdc = 0.0
            used_sell_base = 0.0

            # วนสร้างราคากริดหลายชั้น
            # ฝั่งซื้อ: ต่ำกว่าราคากลาง 1*step, 2*step, ..., levels*step
            # ฝั่งขาย: สูงกว่าราคากลาง 1*step, 2*step, ..., levels*step
            buy_prices = []
            sell_prices = []
            for i in range(1, levels + 1):
                bp = round(current_price * (1 - step * i), price_prec)
                ap = round(current_price * (1 + step * i), price_prec)
                # guard: ราคา > 0
                if bp > 0:
                    buy_prices.append(bp)
                if ap > 0:
                    sell_prices.append(ap)

            # เงื่อนไขกำไรขั้นต่ำเทียบต้นทุน (สำหรับฝั่งขาย)
            min_sell_price = None
            if position_cost > 0:
                min_sell_price = round(position_cost * (1 + self.config["MIN_PROFIT_SPREAD"]), price_prec)

            # ส่งออเดอร์เป็นบันได
            # ฝั่งซื้อ
            if can_buy:
                for bp in buy_prices:
                    notional = bp * base_order_size
                    if used_buy_usdc + notional > buy_usdc_cap:
                        break  # เกินงบฝั่งซื้อแล้วหยุด
                    qty = round(base_order_size, qty_prec)
                    if qty <= 0:
                        continue
                    try:
                        order_details = {
                            "symbol": self.symbol,
                            "side": "Bid",
                            "orderType": "Limit",
                            "quantity": str(qty),
                            "price": format(bp, f".{price_prec}f"),
                            "timeInForce": "GTC",
                            "postOnly": True
                        }
                        order = self.rest_client.place_order(order_details)
                        if order and "id" in order:
                            self.current_orders[order["id"]] = order
                            used_buy_usdc += notional
                            logger.info(f"ส่ง BUY ชั้นราคา {bp} จำนวน {qty} (ใช้ {used_buy_usdc:.2f}/{buy_usdc_cap:.2f} USDC)")
                    except Exception as e:
                        logger.error(f"ส่งออเดอร์ BUY@{bp} ล้มเหลว: {str(e)}")

            # ฝั่งขาย
            if can_sell:
                for ap in sell_prices:
                    # ถ้ามีต้นทุน ให้ขายเฉพาะที่มากกว่า min_sell_price
                    if (min_sell_price is not None) and (ap <= min_sell_price):
                        continue
                    qty = round(base_order_size, qty_prec)
                    if used_sell_base + qty > sell_base_cap:
                        break  # เกินหน่วยสินทรัพย์ที่กันไว้แล้วหยุด
                    if qty <= 0:
                        continue
                    try:
                        order_details = {
                            "symbol": self.symbol,
                            "side": "Ask",
                            "orderType": "Limit",
                            "quantity": str(qty),
                            "price": format(ap, f".{price_prec}f"),
                            "timeInForce": "GTC",
                            "postOnly": True
                        }
                        order = self.rest_client.place_order(order_details)
                        if order and "id" in order:
                            self.current_orders[order["id"]] = order
                            used_sell_base += qty
                            logger.info(f"ส่ง SELL ชั้นราคา {ap} จำนวน {qty} (ใช้ {used_sell_base:.4f}/{sell_base_cap:.4f} {self.symbol.split('_')[0]})")
                    except Exception as e:
                        logger.error(f"ส่งออเดอร์ SELL@{ap} ล้มเหลว: {str(e)}")

        except Exception as e:
            logger.error(f"ปรับคำสั่งล้มเหลว (multi-grid): {str(e)}")

            
    def _calculate_position_scale(self, current_price: float,
                                long_upper: float, long_lower: float,
                                short_upper: float, short_lower: float) -> float:
        """คำนวณสเกลของสถานะเป้าหมาย"""
        try:
            # ใช้สำเนาข้อมูล ไม่มีการล็อกที่นี่
            if not all([long_upper, long_lower, short_upper, short_lower]):
                logger.warning("ข้อมูล Bollinger ยังไม่พร้อมครบ ส่งค่ากลาง (1.0)")
                return 1.0

            # ตรวจช่วงแบนด์ไม่ให้เล็กเกินไป
            long_range = long_upper - long_lower
            short_range = short_upper - short_lower
            
            min_range = 0.0001
            
            if long_range <= min_range or short_range <= min_range:
                logger.warning("ช่วงของ Bollinger แคบเกินไป ส่งค่ากลาง (1.0)")
                return 1.0
            
            # ตำแหน่งของราคาในแบนด์ระยะยาว/สั้น (0..1)
            long_position = (current_price - long_lower) / long_range
            short_position = (current_price - short_lower) / short_range
            
            long_position = max(0.0, min(1.0, long_position))
            short_position = max(0.0, min(1.0, short_position))
            
            # กลับด้าน: ราคาต่ำ → ต้องการถือมาก
            long_position = 1.0 - long_position
            short_position = 1.0 - short_position
            
            # เฉลี่ยระยะยาว/สั้น
            position_scale = (long_position + short_position) / 2
            
            # นำไปแมปเข้าช่วงที่กำหนดไว้
            min_scale = self.config["MIN_POSITION_SCALE"]
            max_scale = self.config["MAX_POSITION_SCALE"]
            position_scale = min_scale + (max_scale - min_scale) * position_scale
            
            logger.debug(
                f"คำนวณสัดส่วนถือครอง - ราคา: {current_price}, ระยะยาว: {long_position:.4f}, "
                + f"ระยะสั้น: {short_position:.4f}, สุดท้าย: {position_scale:.4f}"
            )
            
            return position_scale
                
        except Exception as e:
            logger.error(f"คำนวณสัดส่วนถือครองล้มเหลว: {str(e)}")
            return 1.0
            
    def subscribe_order_updates(self):
        """สมัครรับสตรีมอัปเดตคำสั่งซื้อขาย (private stream)"""
        if not self.ws_client or not self.ws_client.is_connected():
            logger.warning("ไม่สามารถสมัครรับอัปเดตคำสั่งได้: การเชื่อมต่อ WebSocket ไม่พร้อม")
            return False
        
        # พยายามสมัครรับอัปเดต
        stream = f"account.orderUpdate.{self.symbol}"
        if stream not in self.ws_client.subscriptions:
            retry_count = 0
            max_retries = 3
            success = False
            
            while retry_count < max_retries and not success:
                try:
                    success = self.ws_client.private_subscribe(stream)
                    if success:
                        logger.info(f"สมัครรับอัปเดตคำสั่งสำเร็จ: {stream}")
                        return True
                    else:
                        logger.warning(f"สมัครรับอัปเดตคำสั่งไม่สำเร็จ กำลังลองใหม่... ({retry_count+1}/{max_retries})")
                except Exception as e:
                    logger.error(f"เกิดข้อยกเว้นระหว่างสมัครรับอัปเดตคำสั่ง: {e}")
                
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(1)  # หน่วงก่อนลองใหม่
            
            if not success:
                logger.error(f"พยายาม {max_retries} ครั้งแล้วยังสมัครรับอัปเดตคำสั่งไม่สำเร็จ")
                return False
        else:
            logger.info(f"สมัครรับอัปเดตคำสั่งไว้แล้ว: {stream}")
            return True

    def _update_kline_data(self):
        """ดึง/อัปเดตข้อมูล Kline และคำนวณ Bollinger"""
        try:
            # ดึง Kline (ไม่ต้องล็อก)
            long_klines = self.rest_client.get_klines(
                self.symbol,
                interval=self.config["LONG_BOLL_INTERVAL"],
                limit=self.config["LONG_BOLL_PERIOD"] * 2
            )
            
            short_klines = self.rest_client.get_klines(
                self.symbol,
                interval=self.config["SHORT_BOLL_INTERVAL"],
                limit=self.config["SHORT_BOLL_PERIOD"] * 2
            )
            
            # ตรวจสอบข้อมูล
            if not long_klines or not short_klines:
                logger.error(
                    f"ดึง Kline ไม่สำเร็จ - ระยะยาว: {len(long_klines) if long_klines else 0} แท่ง, "
                    + f"ระยะสั้น: {len(short_klines) if short_klines else 0} แท่ง"
                )
                return
                
            # บันทึกตัวอย่างรูปแบบข้อมูลเพื่อดีบัก
            logger.debug(f"ตัวอย่าง Kline ระยะยาว: {type(long_klines)}, ตัวอย่าง: {long_klines[:1]}")
            logger.debug(f"ตัวอย่าง Kline ระยะสั้น: {type(short_klines)}, ตัวอย่าง: {short_klines[:1]}")
                
            # เตรียมข้อมูล
            if long_klines:
                try:
                    if isinstance(long_klines, dict):
                        long_klines = [long_klines]
                    
                    # เรียงตามเวลาเริ่มต้น
                    sorted_long_klines = sorted(long_klines, key=lambda x: x['start'] if isinstance(x, dict) else x[0])
                    recent_long_klines = sorted_long_klines[-self.config["LONG_BOLL_PERIOD"]:]
                    
                    # ดึงราคาปิด
                    long_closes = []
                    for k in recent_long_klines:
                        if isinstance(k, dict):
                            long_closes.append(float(k['close']))
                        else:
                            long_closes.append(float(k[4]))  # index 4 = close
                    
                    # อัปเดต Bollinger ระยะยาว
                    with self.boll_lock:
                        self.long_boll = BollingerBands(
                            period=self.config["LONG_BOLL_PERIOD"],
                            num_std=self.config["LONG_BOLL_STD"]
                        )
                        for price in long_closes:
                            self.long_boll.update(price)
                        
                    logger.debug(f"อัปเดต Bollinger ระยะยาวด้วย {len(long_closes)} แท่ง")
                except Exception as e:
                    logger.error(f"ประมวลผล Kline ระยะยาวล้มเหลว: {str(e)}")
            
            if short_klines:
                try:
                    if isinstance(short_klines, dict):
                        short_klines = [short_klines]
                    
                    sorted_short_klines = sorted(short_klines, key=lambda x: x['start'] if isinstance(x, dict) else x[0])
                    recent_short_klines = sorted_short_klines[-self.config["SHORT_BOLL_PERIOD"]:]
                    
                    short_closes = []
                    for k in recent_short_klines:
                        if isinstance(k, dict):
                            short_closes.append(float(k['close']))
                        else:
                            short_closes.append(float(k[4]))
                    
                    with self.boll_lock:
                        self.short_boll = BollingerBands(
                            period=self.config["SHORT_BOLL_PERIOD"],
                            num_std=self.config["SHORT_BOLL_STD"]
                        )
                        for price in short_closes:
                            self.short_boll.update(price)
                        
                    logger.debug(f"อัปเดต Bollinger ระยะสั้นด้วย {len(short_closes)} แท่ง")
                except Exception as e:
                    logger.error(f"ประมวลผล Kline ระยะสั้นล้มเหลว: {str(e)}")
            
            # อัปเดตราคาล่าสุดจาก Kline ล่าสุด
            if short_klines and len(short_klines) > 0:
                latest_kline = short_klines[-1]
                try:
                    if isinstance(latest_kline, dict):
                        latest_time = datetime.strptime(latest_kline['end'], '%Y-%m-%d %H:%M:%S').timestamp()
                        latest_price = float(latest_kline['close'])
                    else:
                        latest_time = float(latest_kline[0])
                        latest_price = float(latest_kline[4])
                        
                    if latest_time > self.last_update_time:
                        self.last_price = latest_price
                        self.last_update_time = latest_time
                        logger.debug(f"อัปเดตราคาล่าสุดจาก Kline: {self.last_price}")
                except Exception as e:
                    logger.error(f"อัปเดตราคาล่าสุดล้มเหลว: {str(e)}")
                    if not isinstance(e, str):
                        import traceback
                        logger.error(f"รายละเอียดข้อผิดพลาด: {traceback.format_exc()}")
            
            # ทำเครื่องหมายว่าเตรียมข้อมูลเรียบร้อยแล้ว
            if not self.init_complete.is_set():
                with self.boll_lock:
                    long_ready = self.long_boll.is_ready()
                    short_ready = self.short_boll.is_ready()
                
                if long_ready and short_ready:
                    self.init_complete.set()
                    logger.info("เตรียมข้อมูล Kline เรียบร้อย")
                else:
                    logger.debug(
                        f"ข้อมูล Bollinger ยังไม่พร้อม - ระยะยาว: {len(self.long_boll.prices)}/{self.config['LONG_BOLL_PERIOD']}, "
                        + f"ระยะสั้น: {len(self.short_boll.prices)}/{self.config['SHORT_BOLL_PERIOD']}"
                    )
                
        except Exception as e:
            logger.error(f"อัปเดต Kline ล้มเหลว: {str(e)}")
            if not isinstance(e, str):
                import traceback
                logger.error(f"รายละเอียดข้อผิดพลาด: {traceback.format_exc()}")

    def _initialize_websocket(self):
        """รอให้ WebSocket เชื่อมต่อ และสมัครรับสตรีมที่จำเป็น"""
        wait_time = 0
        max_wait_time = 30  # เพิ่มเพดานเวลารอเป็น 30 วินาที
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                wait_time = 0
                while not self.ws_client.connected and wait_time < max_wait_time:
                    time.sleep(1)
                    wait_time += 1
                    
                if self.ws_client.connected:
                    logger.info("เชื่อมต่อ WebSocket แล้ว กำลังเริ่มสตรีมข้อมูล...")
                    
                    # สร้างสแน็ปช็อตของออเดอร์บุ๊ก
                    orderbook_initialized = self.ws_client.initialize_orderbook()
                    
                    # สมัครรับ depth และ bookTicker
                    if orderbook_initialized:
                        depth_subscribed = self.ws_client.subscribe_depth()
                        ticker_subscribed = self.ws_client.subscribe_bookTicker()
                        
                        if depth_subscribed and ticker_subscribed:
                            logger.info("สมัครรับสตรีมตลาดสำเร็จ!")
                            
                        # สมัครรับสตรีมคำสั่งส่วนตัว
                        if self.subscribe_order_updates():
                            logger.info("สมัครรับสตรีมอัปเดตคำสั่งสำเร็จ!")
                            return True
                        else:
                            logger.error("สมัครรับสตรีมอัปเดตคำสั่งไม่สำเร็จ")
                    else:
                        logger.error("สร้างสแน็ปช็อตออเดอร์บุ๊กไม่สำเร็จ")
                
                retry_count += 1
                if retry_count < max_retries:
                    logger.warning(f"การตั้งค่า WebSocket ล้มเหลว กำลังลองครั้งที่ {retry_count + 1}...")
                    self.ws_client.reconnect()
                    time.sleep(5)
                    
            except Exception as e:
                logger.error(f"เกิดข้อผิดพลาดระหว่างตั้งค่า WebSocket: {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(5)
                    
        logger.error(f"ลอง {max_retries} ครั้งแล้วยังตั้งค่า WebSocket ไม่สำเร็จ")
        return False

    def _check_and_reconnect_ws(self):
        """ตรวจสอบสถานะ WebSocket และเชื่อมต่อใหม่เมื่อจำเป็น"""
        if not self.ws_client.connected:
            logger.warning("ตรวจพบว่า WebSocket หลุด กำลังพยายามเชื่อมต่อใหม่...")
            retry_count = 0
            max_retries = 3
            
            while retry_count < max_retries and not self.ws_client.connected:
                try:
                    self.ws_client.reconnect()
                    time.sleep(2)
                    
                    if self.ws_client.connected:
                        success = self._initialize_websocket()
                        if success:
                            logger.info("เชื่อมต่อ WebSocket ใหม่สำเร็จพร้อมตั้งค่าสตรีม")
                            return True
                            
                except Exception as e:
                    logger.error(f"ความพยายามเชื่อมต่อ WebSocket ใหม่ล้มเหลว: {str(e)}")
                    
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = 5 * (retry_count + 1)
                    logger.info(f"รอ {wait_time} วินาที ก่อนลองครั้งที่ {retry_count + 1}...")
                    time.sleep(wait_time)
            
            if not self.ws_client.connected:
                logger.error("เชื่อมต่อ WebSocket ใหม่ไม่สำเร็จ จะลองอีกครั้งในรอบถัดไป")
                return False
                
        return True

    def _kline_update_worker(self):
        """เธรดทำงานอัปเดต Kline"""
        logger.info("เริ่มเธรดอัปเดต Kline")
        while self.running:
            try:
                self._update_kline_data()
                time.sleep(self.kline_update_interval)
            except Exception as e:
                logger.error(f"เธรดอัปเดต Kline เกิดข้อผิดพลาด: {str(e)}")
                time.sleep(5)

    def start(self):
        """เริ่มหุ่นยนต์เทรด"""
        try:
            self.running = True
            
            # เชื่อมต่อ WebSocket และตั้งค่าสตรีม
            self.ws_client.connect()
            if not self._initialize_websocket():
                logger.error("ตั้งค่า WebSocket ไม่สำเร็จ ไม่สามารถเริ่มหุ่นยนต์ได้")
                self.stop()
                return
            
            # สตาร์ทเธรดอัปเดต Kline
            self.kline_update_thread = threading.Thread(
                target=self._kline_update_worker,
                daemon=True
            )
            self.kline_update_thread.start()
            
            # รอข้อมูลพร้อม
            if not self.init_complete.wait(timeout=30):
                logger.error("เตรียมข้อมูล Kline เกินเวลา")
                self.stop()
                return
            
            logger.info("หุ่นยนต์เทรดเริ่มทำงานแล้ว")
            
            last_ws_check = time.time()
            ws_check_interval = 30  # ตรวจเช็ค WebSocket ทุก 30 วินาที
            
            # ลูปหลัก
            while self.running:
                try:
                    current_time = time.time()
                    
                    # ประมวลผลงานฐานข้อมูล
                    self._process_db_queue()
                    
                    # ตรวจสอบ WebSocket เป็นระยะ
                    if current_time - last_ws_check >= ws_check_interval:
                        if not self._check_and_reconnect_ws():
                            ws_check_interval = min(ws_check_interval * 2, 300)
                        else:
                            ws_check_interval = 30
                        last_ws_check = current_time
                    
                    time.sleep(0.1)
                    
                except KeyboardInterrupt:
                    logger.info("ได้รับสัญญาณหยุด กำลังปิดหุ่นยนต์...")
                    self.stop()
                    break
                except Exception as e:
                    logger.error(f"ลูปหลักเกิดข้อผิดพลาด: {str(e)}")
                    time.sleep(5)
                    
        except Exception as e:
            logger.error(f"เริ่มทำงานล้มเหลว: {str(e)}")
            self.stop()

    def stop(self):
        """หยุดหุ่นยนต์เทรด"""
        try:
            self.running = False
            
            # รอเธรด Kline จบ
            if self.kline_update_thread and self.kline_update_thread.is_alive():
                try:
                    self.kline_update_thread.join(timeout=5)
                except Exception as e:
                    logger.error(f"เกิดข้อผิดพลาดขณะรอเธรด Kline: {str(e)}")
            
            # ยกเลิกคำสั่งทั้งหมด
            self.rest_client.cancel_all_orders(self.symbol)
            
            # ปิด WebSocket
            if self.ws_client:
                self.ws_client.close()
            
            logger.info("หยุดหุ่นยนต์เทรดเรียบร้อย")
        except Exception as e:
            logger.error(f"หยุดทำงานล้มเหลว: {str(e)}")

    def __del__(self):
        """คืนทรัพยากร"""
        if hasattr(self, 'db'):
            self.db.close()

    def _calculate_total_balance(self, include_borrow_positions=True) -> Tuple[float, float, float, Optional[str]]:
        """
        คำนวณมูลค่ารวมของพอร์ต รวม Spot และสถานะกู้ยืม (ถ้ามี)

        Args:
            include_borrow_positions (bool): รวมสถานะกู้ยืมหรือไม่ (ค่าเริ่มต้น True)

        Returns:
            Tuple[float, float, float, Optional[str]]: 
                - base_balance: ยอดคงเหลือของสินทรัพย์ฐาน (รวม Spot+กู้ยืม)
                - quote_balance: ยอดคงเหลือของสินทรัพย์อ้างอิง (รวม Spot+กู้ยืม)
                - total_value_in_quote: มูลค่ารวมคิดเป็นสกุลอ้างอิง
                - error_message: ข้อความผิดพลาด (None ถ้าไม่มี)
        """
        try:
            # ดึงยอดคงเหลือ Spot
            balances = self.rest_client.get_balance()
            logger.info(f"ข้อมูลยอดคงเหลือจาก API: {balances}")
            
            # ตรวจข้อผิดพลาดจาก API
            if isinstance(balances, dict) and "error" in balances:
                error_msg = f"API ยอดคงเหลือส่งข้อผิดพลาด: {balances['error']}"
                logger.error(error_msg)
                return 0.0, 0.0, 0.0, error_msg
            
            # แยกสกุล Base/Quote จากสัญลักษณ์
            base_currency, quote_currency = self.symbol.split('_')
            
            # ยอด Spot เริ่มต้น
            spot_base_balance = 0
            spot_quote_balance = 0
            
            # รวมยอด (available + locked)
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
                        logger.warning(f"รูปแบบข้อมูลยอดของสินทรัพย์ {asset} ไม่ถูกต้อง: {details}")
            else:
                logger.warning(f"รูปแบบข้อมูลยอดคงเหลือไม่ถูกต้อง: {balances}")
            
            # ปรับด้วยสถานะกู้ยืม (ถ้าต้องการ)
            borrow_lend_base_adjustment = 0
            borrow_lend_quote_adjustment = 0
            
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
                                        logger.debug(f"ปรับด้วยกู้ยืม - สินทรัพย์ฐาน ({base_currency}): {net_quantity}")
                                    elif position_symbol == quote_currency:
                                        borrow_lend_quote_adjustment = net_quantity
                                        logger.debug(f"ปรับด้วยกู้ยืม - สินทรัพย์อ้างอิง ({quote_currency}): {net_quantity}")
                                except ValueError as e:
                                    logger.error(f"ประมวลผลข้อมูลกู้ยืมล้มเหลว ({position_symbol}): {e}")
                                    continue
                except Exception as e:
                    logger.warning(f"ดึงสถานะกู้ยืมไม่สำเร็จ: {str(e)}")
                    # ดำเนินต่อโดยไม่รวมกู้ยืม
            
            # รวมยอดสุดท้าย (Spot + ปรับด้วยกู้ยืม)
            base_balance = spot_base_balance + borrow_lend_base_adjustment
            quote_balance = spot_quote_balance + borrow_lend_quote_adjustment
            
            # ประเมินมูลค่ารวมด้วยราคาล่าสุด
            current_price = self.last_price
            if not current_price:
                logger.warning("ไม่พบราคาปัจจุบัน ค่ามูลค่ารวมอาจคลาดเคลื่อน")
                total_value_in_quote = quote_balance
            else:
                total_value_in_quote = (base_balance * current_price) + quote_balance
            
            logger.info(
                f"หลังคำนวณยอด - ฐาน({base_currency}): {base_balance:.8f} (Spot: {spot_base_balance:.8f}, กู้ยืม: {borrow_lend_base_adjustment:.8f})"
            )
            logger.info(
                f"หลังคำนวณยอด - อ้างอิง({quote_currency}): {quote_balance:.8f} (Spot: {spot_quote_balance:.8f}, กู้ยืม: {borrow_lend_quote_adjustment:.8f})"
            )
            logger.info(f"มูลค่ารวม: {total_value_in_quote:.8f} {quote_currency}")
            
            return base_balance, quote_balance, total_value_in_quote, None
            
        except Exception as e:
            error_msg = f"คำนวณมูลค่ารวมล้มเหลว: {str(e)}"
            logger.error(error_msg)
            import traceback
            traceback.print_exc()
            return 0.0, 0.0, 0.0, error_msg

if __name__ == "__main__":
    # สร้างดิกชันนารีคอนฟิก
    config = {
        "GRID_LEVELS_PER_SIDE": GRID_LEVELS_PER_SIDE,
        "GRID_STEP": GRID_STEP,
        "GRID_SIDE_BUDGET_RATIO": GRID_SIDE_BUDGET_RATIO,
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
        "QUOTE_ORDER_SIZE": QUOTE_ORDER_SIZE,
        "LONG_BOLL_INTERVAL": LONG_BOLL_INTERVAL,
        "SHORT_BOLL_INTERVAL": SHORT_BOLL_INTERVAL,
        "DYNAMIC_SPREAD": DYNAMIC_SPREAD,
        "SPREAD_MIN": SPREAD_MIN,
        "SPREAD_MAX": SPREAD_MAX,
        "TREND_SKEW": TREND_SKEW,
        "UPTREND_SKEW": UPTREND_SKEW,
        "DOWNTREND_SKEW": DOWNTREND_SKEW,
        "STOP_LOSS_ACTIVATION": STOP_LOSS_ACTIVATION,
        "STOP_LOSS_RATIO": STOP_LOSS_RATIO,
        "TAKE_PROFIT_RATIO": TAKE_PROFIT_RATIO
    }
    
    bot = None
    try:
        # เริ่มและทำงาน
        bot = BollMakerBot(config)
        bot.start()
        
        # ลูปหลักเพื่อคงโปรเซสไว้
        while bot.running:
            try:
                time.sleep(1)
                
                # ถ้า WS หลุด พยายามเชื่อมใหม่
                if bot.running and not bot.ws_client.connected:
                    logger.warning("WebSocket หลุด กำลังเชื่อมต่อใหม่...")
                    bot.ws_client.reconnect()
                    
            except KeyboardInterrupt:
                logger.info("ได้รับสัญญาณหยุด กำลังปิดหุ่นยนต์...")
                if bot:
                    bot.stop()
                break
            except Exception as e:
                logger.error(f"ลูปหลักเกิดข้อผิดพลาด: {str(e)}")
                time.sleep(5)
                
    except KeyboardInterrupt:
        logger.info("ผู้ใช้ยกเลิกการทำงาน")
    except Exception as e:
        logger.error(f"โปรแกรมเกิดข้อผิดพลาด: {str(e)}")
    finally:
        if bot:
            bot.stop()
        logger.info("โปรแกรมสิ้นสุดการทำงาน")
