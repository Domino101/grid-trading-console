"""
單元測試：TradingView 決策執行橋接器 (tests/test_tv_execution_bridge.py)
"""

import unittest
from unittest.mock import patch, MagicMock
from tools.tv_execution_bridge import TradingViewExecutionBridge


class TestTradingViewExecutionBridge(unittest.TestCase):
    """測試 TradingView 執行橋接客戶端與安全守門機制"""

    def setUp(self):
        self.bridge = TradingViewExecutionBridge(
            base_url="http://testserver:8001",
            token="test-token-123",
            source="tv_bridge"
        )

    def test_reason_formatting(self):
        reason = self.bridge._format_decision_reason(
            action_name="create_dca_long",
            user_reason="trend breakout",
            tv_symbol="BINANCE:SOLUSDC.P",
            tv_metrics={"adx": 28.5, "rsi": 55.2}
        )
        self.assertIn("[TV_BRIDGE:CREATE_DCA_LONG]", reason)
        self.assertIn("trend breakout", reason)
        self.assertIn("tv_symbol=BINANCE:SOLUSDC.P", reason)
        self.assertIn("adx=28.5,rsi=55.2", reason)

    def test_live_grid_gate_blocks_without_confirm_live(self):
        """安全守門：LIVE 模式未帶 confirm_live=True 必須拋出 PermissionError"""
        with self.assertRaises(PermissionError) as ctx:
            self.bridge.create_grid(
                tv_symbol="BINANCE:SOLUSDC.P",
                lower_price=90.0,
                upper_price=120.0,
                grid_interval=0.5,
                order_amount=0.1,
                account_mode="live",
                confirm_live=False
            )
        self.assertIn("confirm_live=True", str(ctx.exception))

    def test_live_dca_gate_blocks_without_confirm_live(self):
        """安全守門：LIVE DCA 未帶 confirm_live=True 必須拋出 PermissionError"""
        with self.assertRaises(PermissionError) as ctx:
            self.bridge.create_dca(
                tv_symbol="BINANCE:SOLUSDC.P",
                base_order_amount=10.0,
                account_mode="live",
                confirm_live=False
            )
        self.assertIn("confirm_live=True", str(ctx.exception))

    @patch("httpx.Client.post")
    def test_create_grid_paper_payload(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {"grid_id": "grid-sol-1", "status": "created"}
        mock_post.return_value = mock_resp

        res = self.bridge.create_grid(
            tv_symbol="BINANCE:SOLUSDC.P",
            lower_price=90.0,
            upper_price=110.0,
            grid_interval=0.5,
            order_amount=0.1,
            grid_type="neutral",
            account_mode="paper",
            auto_start=False,
            tv_metrics={"adx": 16.2}
        )

        self.assertEqual(res["grid_id"], "grid-sol-1")
        self.assertTrue(mock_post.called)
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs["params"]["source"], "tv_bridge")
        self.assertEqual(call_kwargs["json"]["symbol"], "SOL/USDC:USDC")
        self.assertEqual(call_kwargs["json"]["exchange"], "binance")
        self.assertEqual(call_kwargs["json"]["account_mode"], "paper")

    @patch("httpx.Client.post")
    def test_create_dca_paper_payload(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {"bot_id": "dca-sol-1", "status": "created"}
        mock_post.return_value = mock_resp

        res = self.bridge.create_dca(
            tv_symbol="HYPERLIQUID:BTCUSDC.P",
            base_order_amount=25.0,
            direction="long",
            account_mode="paper",
            auto_start=False
        )

        self.assertEqual(res["bot_id"], "dca-sol-1")
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs["json"]["symbol"], "BTC/USDC:USDC")
        self.assertEqual(call_kwargs["json"]["exchange"], "hyperliquid")
        self.assertEqual(call_kwargs["json"]["account_mode"], "paper")

    @patch("httpx.Client.patch")
    def test_pause_and_resume_dca_reentry(self, mock_patch):
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {"bot_id": "dca-1", "status": "armed"}
        mock_patch.return_value = mock_resp

        # 暫停重新進場
        self.bridge.pause_dca_reentry("dca-1", reason="FOMC coming")
        call_kwargs = mock_patch.call_args[1]
        self.assertEqual(call_kwargs["json"]["repeat_after_take_profit"], False)
        self.assertIn("FOMC coming", call_kwargs["params"]["reason"])

        # 恢復重新進場
        self.bridge.resume_dca_reentry("dca-1", reason="FOMC passed")
        call_kwargs2 = mock_patch.call_args[1]
        self.assertEqual(call_kwargs2["json"]["repeat_after_take_profit"], True)
        self.assertIn("FOMC passed", call_kwargs2["params"]["reason"])

    @patch("httpx.Client.post")
    def test_stop_bot(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {"bot_id": "dca-1", "action": "stop", "status": "accepted"}
        mock_post.return_value = mock_resp

        res = self.bridge.stop_bot("dca", "dca-1", reason="trend reversal")
        self.assertEqual(res["status"], "accepted")
        self.assertIn("/api/v1/dca/dca-1/stop", mock_post.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
