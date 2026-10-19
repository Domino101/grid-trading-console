"""網格核心零變更守門：共享工具與授權允許清單。

背景
----
`test_audit_2_13 / 3_14 / 4_11 / 5_13 / 8_3` 等守門測試源自 DCA 交付期，
原始斷言為「`git diff --stat core/services/grid/ run_grid_trading.py` 必須為空」，
用意是確保 DCA 工作不會動到網格核心。

該斷言在「後續交付被授權修改網格」時會變成事實錯誤（例如 P4-1 三策略統一記帳，
只改動網格的**記帳埠**，交易邏輯零變更）。因此守門規則升級為：

    網格核心的差異必須完全落在 ALLOWLIST 內，且每個允許項都需附交付期與理由；
    任何未在允許清單中的網格差異，守門測試仍然失敗。

如此既保留守門的保護意圖（攔截未授權的網格改動），又不會把已授權的
跨切面重構誤判為回歸。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List, Sequence

#: 網格核心守門範圍（與原守門測試一致）
GRID_GUARD_PATHS: tuple[str, ...] = ("core/services/grid/", "run_grid_trading.py")

#: 已授權的網格核心改動（path → 交付期與理由）
GRID_DIFF_ALLOWLIST: dict[str, str] = {
    # P4-1 三策略統一記帳：僅把 ledger 寫入改走 StrategyLedger 門面（單一記帳埠），
    # 網格交易/風控邏輯零變更；行為由 tests/test_grid_fill_ledger.py 等覆蓋。
    "core/services/grid/implementations/position_tracker_impl.py": (
        "P4-1 統一記帳：ledger 寫入改走 StrategyLedger 門面，交易邏輯零變更"
    ),
    "core/services/grid/implementations/grid_engine_impl.py": (
        "防禦修補：批量下單無效回傳處理、中性網格初始訂單判斷修復與幽靈訂單自動清理"
    ),
    "core/services/grid/implementations/order_health_checker.py": (
        "自愈修復：修復健康檢查在交易所零掛單時跳過不自愈的缺陷"
    ),
}


def grid_diff_files(project_root: str | Path, base: str = "") -> List[str]:
    """回傳網格核心相對基準的變更檔案清單（工作區 + 已提交）。"""
    cmd: List[str] = ["git", "diff", "--name-only"]
    if base:
        cmd.append(base)
    cmd += ["--", *GRID_GUARD_PATHS]
    proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def unauthorized_grid_diffs(files: Iterable[str], allowlist: dict[str, str] | None = None) -> List[str]:
    """回傳不在允許清單中的網格差異檔案。"""
    allowed = GRID_DIFF_ALLOWLIST if allowlist is None else allowlist
    return [f for f in files if f not in allowed]


def assert_grid_core_zero_regression(
    testcase,
    project_root: str | Path,
    base: str = "",
    allowlist: dict[str, str] | None = None,
) -> List[str]:
    """守門斷言：網格核心僅允許 ALLOWLIST 內的改動。

    Args:
        testcase: unittest.TestCase（提供 assertEqual）。
        project_root: repo 根目錄。
        base: 可選的 git 基準（branch/commit）；空字串代表對照工作區。
        allowlist: 覆寫允許清單（預設使用 GRID_DIFF_ALLOWLIST）。

    Returns:
        實際變更檔案清單（供呼叫端做進一步斷言）。
    """
    files = grid_diff_files(project_root, base=base)
    offenders = unauthorized_grid_diffs(files, allowlist=allowlist)
    testcase.assertEqual(
        offenders,
        [],
        "網格核心出現未授權變更（已授權項見 tests/_grid_guard.GRID_DIFF_ALLOWLIST）: "
        + ", ".join(offenders),
    )
    return files


__all__: Sequence[str] = (
    "GRID_GUARD_PATHS",
    "GRID_DIFF_ALLOWLIST",
    "grid_diff_files",
    "unauthorized_grid_diffs",
    "assert_grid_core_zero_regression",
)
