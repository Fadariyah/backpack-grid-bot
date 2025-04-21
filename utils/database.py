"""
数据库管理模块
"""

import sqlite3
from typing import Optional, Tuple
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timedelta
from logger import setup_logger

logger = setup_logger("database")

class PositionDB:
    def __init__(self, db_path: str = "data/positions.db", keep_days: int = 15):
        """
        初始化数据库连接
        Args:
            db_path: 数据库文件路径
            keep_days: 保留多少天的交易记录
        """
        # 确保数据目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        self.db_path = db_path
        self.keep_days = keep_days
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        
        # 创建持仓表
        self._create_tables()
        
        # 清理旧记录
        self._cleanup_old_trades()
        
    def _create_tables(self):
        """创建必要的数据表"""
        cursor = self.conn.cursor()
        
        # 持仓表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            size DECIMAL NOT NULL DEFAULT 0,
            cost DECIMAL NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # 交易历史表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            price DECIMAL NOT NULL,
            quantity DECIMAL NOT NULL,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol) REFERENCES positions(symbol)
        )
        """)
        
        self.conn.commit()
        
    def _cleanup_old_trades(self):
        """清理旧的交易记录"""
        try:
            cursor = self.conn.cursor()
            cutoff_date = datetime.now() - timedelta(days=self.keep_days)
            
            # 获取要删除的记录数
            cursor.execute("SELECT COUNT(*) FROM trades WHERE executed_at < ?", (cutoff_date,))
            count = cursor.fetchone()[0]
            
            if count > 0:
                # 删除旧记录
                cursor.execute("DELETE FROM trades WHERE executed_at < ?", (cutoff_date,))
                self.conn.commit()
                
                # 执行VACUUM以回收空间
                self.conn.execute("VACUUM")
                logger.info(f"已清理 {count} 条{self.keep_days}天前的交易记录")
        except Exception as e:
            logger.error(f"清理旧记录时发生错误: {str(e)}")
        
    def get_position(self, symbol: str) -> Tuple[Decimal, Decimal]:
        """
        获取当前持仓信息
        Returns:
            (size, cost): 持仓数量和成本
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT size, cost FROM positions WHERE symbol = ?",
            (symbol,)
        )
        row = cursor.fetchone()
        
        if row:
            return Decimal(str(row['size'])), Decimal(str(row['cost']))
        return Decimal('0'), Decimal('0')
        
    def update_position(self, symbol: str, size: Decimal, cost: Decimal):
        """更新持仓信息"""
        cursor = self.conn.cursor()
        cursor.execute("""
        INSERT INTO positions (symbol, size, cost) 
        VALUES (?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            size = ?,
            cost = ?,
            updated_at = CURRENT_TIMESTAMP
        """, (symbol, size, cost, size, cost))
        self.conn.commit()
        
    def add_trade(self, symbol: str, side: str, price: Decimal, quantity: Decimal):
        """记录交易历史"""
        cursor = self.conn.cursor()
        cursor.execute("""
        INSERT INTO trades (symbol, side, price, quantity)
        VALUES (?, ?, ?, ?)
        """, (symbol, side, price, quantity))
        self.conn.commit()
        
        # 每添加新记录时检查是否需要清理
        self._cleanup_old_trades()
        
    def get_recent_trades(self, symbol: str, limit: int = 100) -> list:
        """获取最近的交易记录"""
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT * FROM trades 
        WHERE symbol = ? 
        ORDER BY executed_at DESC 
        LIMIT ?
        """, (symbol, limit))
        return cursor.fetchall()
        
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            
    def __del__(self):
        """析构时确保关闭连接"""
        self.close() 