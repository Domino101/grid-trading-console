"""
操作審計日誌模組 (SQLite 實作)

記錄所有 Hermes Agent 與外部對網格生命週期的寫操作。
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from lifecycle import connect_sqlite

logger = logging.getLogger("GridAPI.Audit")


class AuditLogger:
    """操作審計日誌類別 (Thread-Safe SQLite 單例)"""

    _instance = None

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.db_dir = os.path.join(project_root, "data")
        self.db_path = os.path.join(self.db_dir, "grid_audit.db")

        # 確保 data 目錄存在
        os.makedirs(self.db_dir, exist_ok=True)
        self._init_db()

    @classmethod
    def get_instance(cls, project_root: Optional[str] = None) -> "AuditLogger":
        if cls._instance is None:
            if project_root is None:
                # 預設為專案根目錄
                project_root = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "../../")
                )
            cls._instance = cls(project_root)
        return cls._instance

    def _get_connection(self):
        return connect_sqlite(self.db_path)

    def _init_db(self) -> None:
        """初始化審計資料庫"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp   TEXT NOT NULL,
                        source      TEXT NOT NULL,        -- 'hermes' / 'dashboard' / 'manual'
                        action      TEXT NOT NULL,        -- 'create_grid' / 'start' / 'stop' / 'delete'
                        grid_id     TEXT,
                        params_json TEXT,                 -- 完整請求參數
                        result      TEXT NOT NULL,        -- 'success' / 'rejected' / 'error'
                        reason      TEXT,                 -- Hermes 的決策理由
                        error_msg   TEXT
                    );
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_source ON audit_log(source);"
                )
                conn.commit()
                logger.info(f"✅ 審計資料庫初始化成功: {self.db_path}")
        except Exception as e:
            logger.error(f"❌ 初始化審計資料庫失敗: {e}")

    def log(
        self,
        source: str,
        action: str,
        grid_id: Optional[str],
        params: Optional[dict],
        result: str,
        reason: Optional[str] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        """
        記錄一次操作。

        Args:
            source: 來源名稱 (hermes / dashboard / manual)
            action: 行為 (create_grid / start / stop / delete)
            grid_id: 網格識別碼
            params: 參數 dict
            result: 結果 (success / rejected / error)
            reason: 決策或操作原因
            error_msg: 錯誤訊息（若有）
        """
        try:
            params_json = (
                json.dumps(params, ensure_ascii=False) if params else None
            )
            timestamp = datetime.now().isoformat()

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO audit_log (timestamp, source, action, grid_id, params_json, result, reason, error_msg)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        timestamp,
                        source,
                        action,
                        grid_id,
                        params_json,
                        result,
                        reason,
                        error_msg,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"❌ 寫入審計日誌失敗: {e}")

    def get_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        獲取審計日誌列表。

        Args:
            limit: 限制數量
            offset: 偏移量
            source: 過濾特定來源

        Returns:
            日誌列表 dict 陣列
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                if source:
                    cursor.execute(
                        """
                        SELECT * FROM audit_log
                        WHERE source = ?
                        ORDER BY id DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (source, limit, offset),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT * FROM audit_log
                        ORDER BY id DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (limit, offset),
                    )

                rows = cursor.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    # 反序列化 params_json
                    if item.get("params_json"):
                        try:
                            item["params"] = json.loads(item["params_json"])
                        except Exception:
                            item["params"] = None
                    else:
                        item["params"] = None

                    # 移除 params_json 欄位，由 params 取代
                    item.pop("params_json", None)
                    results.append(item)

                return results
        except Exception as e:
            logger.error(f"❌ 查詢審計日誌失敗: {e}")
            return []
