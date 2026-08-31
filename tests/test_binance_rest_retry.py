import asyncio
import pytest
from unittest.mock import MagicMock

from core.adapters.exchanges.adapters.binance_rest import BinanceRest
from core.adapters.exchanges.interface import ExchangeConfig
from core.adapters.exchanges.models import ExchangeType


@pytest.mark.asyncio
async def test_binance_rest_execute_with_retry_kwargs():
    config = ExchangeConfig(
        exchange_id="binance",
        name="Binance",
        exchange_type=ExchangeType.PERPETUAL,
        api_key="mock",
        api_secret="mock",
    )
    rest = BinanceRest(config)

    def dummy_func(a, b, param_x=None):
        return f"result-{a}-{b}-{param_x}"

    res = await rest._execute_with_retry(dummy_func, 1, 2, param_x="test_value")
    assert res == "result-1-2-test_value"
