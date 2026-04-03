"""
数据库模块 - 对话持久化（SQLite）
"""

import json
import sqlite3
import os
from datetime import datetime


class ChatDatabase:
    """对话数据库管理"""

    def __init__(self, db_path: str = "./chat_history.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化数据库表结构"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedule_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_type TEXT NOT NULL,
                source_name TEXT,
                source_text TEXT NOT NULL,
                extracted_json TEXT,
                status TEXT NOT NULL,
                feishu_event_id TEXT,
                error_message TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def save_message(self, role: str, content: str):
        """
        保存一条消息
        :param role: 'user' 或 'assistant'
        :param content: 消息内容
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
            (role, content, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        """
        获取最近的消息记录
        :param limit: 最多返回条数（按轮次算，1轮 = user + assistant）
        :return: 消息列表 [{"role": ..., "content": ...}, ...]
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        # 取最近 limit*2 条（每轮包含 user 和 assistant 各一条）
        cursor.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (limit * 2,),
        )
        rows = cursor.fetchall()
        conn.close()
        # 反转为时间正序
        messages = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
        return messages

    def get_all_messages(self) -> list[dict]:
        """获取所有历史消息"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT role, content, timestamp FROM messages ORDER BY id ASC")
        rows = cursor.fetchall()
        conn.close()
        return [
            {"role": row["role"], "content": row["content"], "timestamp": row["timestamp"]}
            for row in rows
        ]

    def save_schedule_record(
        self,
        input_type: str,
        source_text: str,
        status: str,
        source_name: str = "",
        extracted_json: dict | str | None = None,
        feishu_event_id: str = "",
        error_message: str = "",
    ):
        """保存行程处理记录。"""
        if isinstance(extracted_json, dict):
            extracted_json = json.dumps(extracted_json, ensure_ascii=False, indent=2)

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO schedule_records (
                input_type, source_name, source_text, extracted_json,
                status, feishu_event_id, error_message, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                input_type,
                source_name,
                source_text,
                extracted_json,
                status,
                feishu_event_id,
                error_message,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()

    def get_recent_schedule_records(self, limit: int = 10) -> list[dict]:
        """获取最近的行程处理记录。"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT input_type, source_name, source_text, extracted_json,
                   status, feishu_event_id, error_message, timestamp
            FROM schedule_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def clear_history(self):
        """清空所有对话历史"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages")
        cursor.execute("DELETE FROM schedule_records")
        conn.commit()
        conn.close()

    def get_message_count(self) -> int:
        """获取消息总数"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM messages")
        count = cursor.fetchone()["cnt"]
        conn.close()
        return count

    def get_schedule_count(self) -> int:
        """获取行程处理记录总数"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM schedule_records")
        count = cursor.fetchone()["cnt"]
        conn.close()
        return count

