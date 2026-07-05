"""
核心逻辑模組 - Core

包含 APR 計算、APR 報警等核心功能
"""

from .apr_calculator import APRCalculator

__all__ = ['APRCalculator']  # PriceMonitor / CycleDetector 已 deprecated,移除
