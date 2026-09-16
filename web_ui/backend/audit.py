"""
操作審計日誌模組 (SQLite 實作)

記錄所有 Hermes Agent 與外部對網格生命週期的寫操作。
"""

import os
import json
import logging
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

from web_ui.backend.lifecycle import connect_sqlite

logger = logging.getLogger("GridAPI.Audit")


class AuditLogger:
    """操作審計日誌類別 (Thread-Safe SQLite，per-project-root 註冊表)

    0.3.6 修正（統一記帳稽核 L10）：
    * **單例忽視 project_root 的缺陷**：過去 ``get_instance(root_b)`` 會回傳
      先前以 ``root_a`` 建立的實例，導致稽核日誌寫進錯誤的 DB；當該路徑位於
      已被清除的暫存目錄時，所有寫入以 ``no such table: audit_log`` 靜默失敗。
      現改為以 abspath 為鍵的實例註冊表。
    * **自我修復 schema**：寫入前確認 ``audit_log`` 存在；若 DB 檔被刪除/重建，
      自動重新 ``_init_db()`` 並重試一次，不再靜默丟失稽核軌跡。
    * **可觀測降級**：寫入失敗累計計數，可由 ``total_write_failures()`` 與
      ``GET /api/v1/system/ledger-audit``（L10）觀察。
    """

    _instance: Optional["AuditLogger"] = None          # 相容舊呼叫（最近一次使用的實例）
    _instances: Dict[str, "AuditLogger"] = {}
    _instances_lock = threading.Lock()
    _write_failures: int = 0

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.db_dir = os.path.join(project_root, "data")
        self.db_path = os.path.join(self.db_dir, "grid_audit.db")
        self._schema_ok = False
        self._local_failures = 0

        # 確保 data 目錄存在
        os.makedirs(self.db_dir, exist_ok=True)
        self._init_db()

    @classmethod
    def get_instance(cls, project_root: Optional[str] = None) -> "AuditLogger":
        if project_root is None:
            # 預設為專案根目錄
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
        key = os.path.abspath(str(project_root))
        with cls._instances_lock:
            instance = cls._instances.get(key)
            if instance is None:
                instance = cls(key)
                cls._instances[key] = instance
            cls._instance = instance
            return instance

    @classmethod
    def reset_instances(cls) -> None:
        """測試隔離用：清空 per-root 實例註冊表與失敗計數。"""
        with cls._instances_lock:
            cls._instances.clear()
            cls._instance = None
            cls._write_failures = 0

    @classmethod
    def total_write_failures(cls) -> int:
        """全進程累計稽核寫入失敗次數（0 = 稽核功能健康）。"""
        return cls._write_failures

    def health(self) -> Dict[str, Any]:
        return {
            "db_path": self.db_path,
            "schema_ok": bool(self._schema_ok),
            "write_failures": self._local_failures,
            "global_write_failures": type(self)._write_failures,
        }

    def _get_connection(self):
        return connect_sqlite(self.db_path)

    def _ensure_schema(self, force: bool = False) -> bool:
        """確認 audit_log 表存在；DB 檔被移除/重建時自動自我修復。"""
        if self._schema_ok and not force and os.path.exists(self.db_path):
            return True
        self._init_db()
        return bool(self._schema_ok)

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
                        action      TEXT NOT NULL,        -- 'create_grid' / 'start' / 'stop' / 'delete' / 'run_dca_backtest' / 'run_dca_sweep'
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
                self._schema_ok = True
                logger.info(f"✅ 審計資料庫初始化成功: {self.db_path}")
        except Exception as e:
            self._schema_ok = False
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
            self._ensure_schema()

            for attempt in (1, 2):
                try:
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
                    return
                except Exception as exc:
                    # 自我修復：DB 檔被移除/重建導致 schema 不存在時，重新初始化後重試一次
                    if attempt == 1 and ("no such table" in str(exc).lower() or not os.path.exists(self.db_path)):
                        logger.warning(f"⚠️ 審計日誌 schema 缺損，嘗試自我修復後重試: {exc}")
                        self._schema_ok = False
                        self._ensure_schema(force=True)
                        continue
                    raise
        except Exception as e:
            type(self)._write_failures += 1
            self._local_failures += 1
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
            self._ensure_schema()
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
