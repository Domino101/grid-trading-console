"""
O1Exchange 交易所基础模块

包含基础配置、网络管理、签名工具等共用功能
"""

import yaml
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
from decimal import Decimal, InvalidOperation
from datetime import datetime
from eth_account import Account
from eth_account.messages import encode_typed_data


class O1ExchangeBase:
    """O1Exchange 基础类"""

    # API 端点配置
    DEFAULT_REST_URL = "https://api.o1.exchange"
    API_VERSION = "v2"

    # 支持的网络 ID
    NETWORKS = {
        "base": 8453,
        "bsc": 56,
        "solana": 1399811149
    }

    # 默认网络
    DEFAULT_NETWORK = "base"

    # 支持的时间周期
    SUPPORTED_TIMEFRAMES = {
        '1m': '1m',
        '5m': '5m',
        '15m': '15m',
        '1h': '1h',
        '4h': '4h',
        '1d': '1d'
    }

    def __init__(self, config=None):
        """初始化基础配置"""
        self.config = config
        self.logger = None

        # 初始化 URL 配置
        self._setup_urls()

        # 加载配置
        self._load_config()

        # 支持的交易对缓存
        self._supported_symbols = []
        self._market_info = {}

        # 账户信息
        self._account = None
        self._wallet_address = None

    def _setup_urls(self):
        """设置 API URL"""
        if self.config:
            self.base_url = self.config.base_url or self.DEFAULT_REST_URL
        else:
            self.base_url = self.DEFAULT_REST_URL

        self.api_base = f"{self.base_url}/api/{self.API_VERSION}"

    def _load_config(self):
        """加载配置"""
        try:
            config_path = Path("config/exchanges/o1exchange_config.yaml")
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.o1_config = yaml.safe_load(f)

                # 解析网络配置
                network_config = self.o1_config.get('o1exchange', {}).get('network', {})
                self.default_network = network_config.get('default', self.DEFAULT_NETWORK)
                self.enabled_networks = network_config.get('enabled', list(self.NETWORKS.keys()))

                if self.logger:
                    self.logger.info(
                        f"✅ 加载 O1Exchange 配置: 默认网络={self.default_network}, "
                        f"启用网络={self.enabled_networks}"
                    )
            else:
                self._setup_default_config()
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 加载配置失败: {e}")
            self._setup_default_config()

    def _setup_default_config(self):
        """设置默认配置"""
        self.o1_config = {}
        self.default_network = self.DEFAULT_NETWORK
        self.enabled_networks = list(self.NETWORKS.keys())

    def initialize_account(self, private_key: str):
        """
        初始化钱包账户

        Args:
            private_key: 钱包私钥
        """
        try:
            self._account = Account.from_key(private_key)
            self._wallet_address = self._account.address
            if self.logger:
                self.logger.info(f"✅ 钱包账户已初始化: {self._wallet_address}")
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 初始化钱包账户失败: {e}")
            return False

    def sign_permit2(self, eip712_data: Dict[str, Any]) -> str:
        """
        签名 Permit2 EIP-712 数据

        Args:
            eip712_data: EIP-712 格式的数据

        Returns:
            签名结果 (0x 前缀的十六进制字符串)
        """
        if not self._account:
            raise ValueError("账户未初始化，请先调用 initialize_account")

        try:
            encoded_message = encode_typed_data(
                full_message=eip712_data
            )
            signed_message = self._account.sign_message(encoded_message)
            signature = signed_message.signature.hex()

            if not signature.startswith('0x'):
                signature = '0x' + signature

            return signature
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Permit2 签名失败: {e}")
            raise

    def sign_transaction(self, unsigned_tx: Dict[str, Any]) -> str:
        """
        签名交易

        Args:
            unsigned_tx: 未签名的交易对象

        Returns:
            签名后的交易 (0x 前缀的十六进制字符串)
        """
        if not self._account:
            raise ValueError("账户未初始化，请先调用 initialize_account")

        try:
            signed_tx = self._account.sign_transaction(unsigned_tx)
            return signed_tx.raw_transaction.hex()
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 交易签名失败: {e}")
            raise

    def get_network_id(self, network_name: Optional[str] = None) -> int:
        """
        获取网络 ID

        Args:
            network_name: 网络名称 (base, bsc, solana)

        Returns:
            网络 ID
        """
        network = network_name or self.default_network
        if network not in self.NETWORKS:
            raise ValueError(f"不支持的网络: {network}")
        return self.NETWORKS[network]

    def get_network_name(self, network_id: int) -> str:
        """
        根据网络 ID 获取网络名称

        Args:
            network_id: 网络 ID

        Returns:
            网络名称
        """
        for name, nid in self.NETWORKS.items():
            if nid == network_id:
                return name
        raise ValueError(f"不支持的网络 ID: {network_id}")

    # === 数据转换工具方法 ===

    def _safe_decimal(self, value: Any) -> Optional[Decimal]:
        """安全转换为 Decimal"""
        if value is None or value == "":
            return None
        try:
            if isinstance(value, str):
                if 'e' in value.lower():
                    return Decimal(value)
                value = value.replace(',', '')
            return Decimal(str(value))
        except (ValueError, TypeError, InvalidOperation):
            return None

    def _safe_float(self, value: Any) -> Optional[float]:
        """安全转换为 float"""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _safe_int(self, value: Any) -> Optional[int]:
        """安全转换为 int"""
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None

    def _safe_str(self, value: Any) -> str:
        """安全转换为 str"""
        if value is None:
            return ""
        return str(value)

    def _parse_timestamp(self, timestamp_value: Any) -> Optional[datetime]:
        """安全解析时间戳"""
        if timestamp_value is None:
            return datetime.now()

        if isinstance(timestamp_value, datetime):
            return timestamp_value

        try:
            if isinstance(timestamp_value, (int, float)):
                if timestamp_value > 10000000000:
                    return datetime.fromtimestamp(timestamp_value / 1000)
                else:
                    return datetime.fromtimestamp(timestamp_value)

            if isinstance(timestamp_value, str):
                timestamp_str = timestamp_value.replace('Z', '').split('+')[0]
                for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S']:
                    try:
                        return datetime.strptime(timestamp_str, fmt)
                    except ValueError:
                        continue

            return datetime.now()
        except Exception as e:
            if self.logger:
                self.logger.debug(f"解析时间戳失败: {timestamp_value}, 错误: {e}")
            return datetime.now()

    # === 配置访问方法 ===

    def get_supported_symbols(self) -> List[str]:
        """获取支持的交易对列表"""
        return self._supported_symbols.copy()

    def get_market_info(self) -> Dict[str, Any]:
        """获取市场信息"""
        return self._market_info.copy()

    def get_timeframe_mapping(self, timeframe: str) -> str:
        """获取时间周期映射"""
        return self.SUPPORTED_TIMEFRAMES.get(timeframe, timeframe)

    def is_timeframe_supported(self, timeframe: str) -> bool:
        """检查时间周期是否支持"""
        return timeframe in self.SUPPORTED_TIMEFRAMES

    def get_rest_url(self) -> str:
        """获取 REST API URL"""
        return self.base_url

    def get_wallet_address(self) -> Optional[str]:
        """获取钱包地址"""
        return self._wallet_address

    def set_logger(self, logger):
        """设置日志器"""
        self.logger = logger

    def get_logger(self):
        """获取日志器"""
        return self.logger
