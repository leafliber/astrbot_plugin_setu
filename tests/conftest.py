"""测试共享夹具。"""

from __future__ import annotations

import pytest

from setu.config import SetuConfig


@pytest.fixture
def base_config() -> SetuConfig:
    """返回一个默认配置实例。"""
    return SetuConfig.from_raw({})


@pytest.fixture
def custom_config() -> SetuConfig:
    """返回带自定义参数的配置实例。"""
    return SetuConfig.from_raw(
        {
            "trigger_words": ["色图", "来点色图"],
            "api": {
                "base_url": "https://mirror.example.com/setu/v2",
                "r18": 2,
                "num": 3,
                "size": ["regular"],
                "image_proxy": "i.example.re",
                "tag": ["白丝"],
                "excludeAI": True,
            },
            "network": {"http_proxy": "http://127.0.0.1:7890", "timeout": 10},
            "cache": {"enabled": True, "max_count": 5, "ttl_hours": 24},
            "rate_limit": {"global_per_minute": 3, "umo_per_minute": 2},
            "session": {"default_enabled": False, "admin_only_toggle": False},
            "tool": {"enabled": False},
        }
    )
