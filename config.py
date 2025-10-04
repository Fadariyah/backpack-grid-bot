"""
ไฟล์กำหนดค่า (Configuration)
"""

import os
from dotenv import load_dotenv

# โหลดตัวแปรสภาพแวดล้อมจากไฟล์ .env
load_dotenv()

# การตั้งค่า API
API_URL = "https://api.backpack.exchange"
WS_URL = "wss://ws.backpack.exchange"
API_VERSION = "v1"
DEFAULT_WINDOW = "5000"

# การตั้งค่าการเทรด
SYMBOL = "SOL_USDC_PERP"  # คู่เทรด
ORDER_AMOUNT = 2  # จำนวนเงินต่อออเดอร์ (USDC) ประมาณ 1/100 ของเงินทั้งหมด
GRID_TOTAL_INVESTMENT = 200  # มูลค่าลงทุนรวม (USDC) รวมทั้ง SOL และ USDC
PRICE_PRECISION = 2  # ความละเอียดของราคา
QUANTITY_PRECISION = 2  # ความละเอียดของปริมาณ
SPREAD = 0.00018  # ส่วนต่างพื้นฐาน (0.1%)
BASE_ORDER_SIZE = 0.1  # ขนาดออเดอร์พื้นฐาน (หน่วยเป็น SOL)
QUOTE_ORDER_SIZE = 4  # ขนาดออเดอร์พื้นฐาน (หน่วยเป็น USDC)

# === Grid (multi-level) ===
# PATCH: multi-level grid parameters
GRID_LEVELS_PER_SIDE = 6       # จำนวนชั้นต่อฝั่ง (ซื้อ 6 + ขาย 6)
GRID_STEP = 0.0002              # ระยะต่อชั้นเป็นสัดส่วน (0.6% ต่อขั้น)
GRID_SIDE_BUDGET_RATIO = 0.5   # กันงบต่อฝั่ง 50% ของ GRID_TOTAL_INVESTMENT

# พารามิเตอร์ Bollinger Band
LONG_BOLL_INTERVAL = "1h"  # ช่วงเวลาของ Bollinger Band ระยะยาว
LONG_BOLL_PERIOD = 21  # จำนวนแท่ง K สำหรับระยะยาว
LONG_BOLL_STD = 2.0  # ค่าคูณส่วนเบี่ยงเบนมาตรฐานระยะยาว

SHORT_BOLL_INTERVAL = "5m"  # ช่วงเวลาของ Bollinger Band ระยะสั้น
SHORT_BOLL_PERIOD = 21  # จำนวนแท่ง K สำหรับระยะสั้น
SHORT_BOLL_STD = 2.0  # ค่าคูณส่วนเบี่ยงเบนมาตรฐานระยะสั้น

# การควบคุมสถานะ (Position Control)
MAX_POSITION_SCALE = 10.0  # อัตราขยายสถานะสูงสุด (อ้างอิงสไตล์ BBGO)
MIN_POSITION_SCALE = 1.0   # อัตราขยายสถานะขั้นต่ำ
MIN_PROFIT_SPREAD = 0.0005  # ส่วนต่างกำไรขั้นต่ำ (0.1%)
TRADE_IN_BAND = True       # เทรดเฉพาะเมื่อราคาอยู่ในกรอบ Bollinger Band
BUY_BELOW_SMA = False       # ซื้อเฉพาะเมื่อราคาต่ำกว่าเส้นค่าเฉลี่ย (SMA)

# การตั้งค่า Spread แบบไดนามิก
DYNAMIC_SPREAD = True
SPREAD_MIN = 0.00022  # ส่วนต่างต่ำสุด 0.1%
SPREAD_MAX = 0.001  # ส่วนต่างสูงสุด 0.2%

# การชดเชยตามแนวโน้ม (Trend Skew)
TREND_SKEW = True
UPTREND_SKEW = 0.8   # ค่าสัมประสิทธิ์เมื่อเป็นขาขึ้น
DOWNTREND_SKEW = 1.2 # ค่าสัมประสิทธิ์เมื่อเป็นขาลง

# พารามิเตอร์ด้านความเสี่ยง
STOP_LOSS_ACTIVATION = 0.02  # จุดทริกเกอร์ตัดขาดทุน 2%
STOP_LOSS_RATIO = 0.03       # อัตราตัดขาดทุน 3%
TAKE_PROFIT_RATIO = 0.008     # อัตราทำกำไร 7%

# คีย์ API (กรุณาตั้งค่าให้ถูกต้อง)
API_KEY = os.getenv("BACKPACK_API_KEY", "")
SECRET_KEY = os.getenv("BACKPACK_SECRET_KEY", "")

# บังคับให้กำหนดตัวแปรสภาพแวดล้อมหากยังไม่ได้ตั้งค่า
if not API_KEY or not SECRET_KEY:
    raise ValueError("โปรดตั้งค่าตัวแปรสภาพแวดล้อม BACKPACK_API_KEY และ BACKPACK_SECRET_KEY")
