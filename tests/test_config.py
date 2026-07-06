"""配置解析测试。"""

from __future__ import annotations

from setu.config import SetuConfig


class TestSetuConfig:
    def test_defaults_when_empty(self):
        cfg = SetuConfig.from_raw({})
        assert cfg.trigger_words == ["色图", "来点色图", "setu"]
        assert cfg.api["base_url"] == "https://api.lolicon.app/setu/v2"
        assert cfg.api["r18"] == 0
        assert cfg.api["num"] == 1
        assert cfg.api["size"] == ["original"]
        assert cfg.api["image_proxy"] == "i.pixiv.re"
        assert cfg.network["http_proxy"] == ""
        assert cfg.network["timeout"] == 15
        assert cfg.cache["enabled"] is True
        assert cfg.cache["max_count"] == 500
        assert cfg.rate_limit["global_per_minute"] == 30
        assert cfg.rate_limit["umo_per_minute"] == 5
        assert cfg.session["default_enabled"] is True
        assert cfg.tool["enabled"] is True

    def test_none_input(self):
        cfg = SetuConfig.from_raw(None)
        assert cfg.api["base_url"] == "https://api.lolicon.app/setu/v2"

    def test_custom_overrides(self, custom_config: SetuConfig):
        cfg = custom_config
        assert cfg.trigger_words == ["色图", "来点色图"]
        assert cfg.api["base_url"] == "https://mirror.example.com/setu/v2"
        assert cfg.api["r18"] == 2
        assert cfg.api["num"] == 3
        assert cfg.api["size"] == ["regular"]
        assert cfg.api["image_proxy"] == "i.example.re"
        assert cfg.api["excludeAI"] is True
        assert cfg.api["tag"] == ["白丝"]
        assert cfg.rate_limit["global_per_minute"] == 3
        assert cfg.session["default_enabled"] is False
        assert cfg.tool["enabled"] is False

    def test_partial_section_merge(self):
        """仅提供部分字段时，其余字段补默认值。"""
        cfg = SetuConfig.from_raw({"api": {"r18": 1}})
        assert cfg.api["r18"] == 1
        assert cfg.api["num"] == 1  # 未提供 -> 默认
        assert cfg.api["base_url"] == "https://api.lolicon.app/setu/v2"

    def test_trigger_words_filtered(self):
        cfg = SetuConfig.from_raw({"trigger_words": ["  ", "", "setu", "色图", "setu"]})
        assert cfg.trigger_words == ["setu", "色图"]

    def test_http_proxy_property(self, base_config, custom_config):
        assert base_config.http_proxy is None
        assert custom_config.http_proxy == "http://127.0.0.1:7890"

    def test_timeout_property(self, custom_config):
        assert custom_config.timeout == 10.0

    def test_cache_enabled_property(self, custom_config, base_config):
        assert base_config.cache_enabled is True
        cfg = SetuConfig.from_raw({"cache": {"enabled": False}})
        assert cfg.cache_enabled is False

    def test_tool_enabled_property(self, custom_config):
        assert custom_config.tool_enabled is False

    def test_base_url_and_image_proxy(self, custom_config):
        assert custom_config.base_url == "https://mirror.example.com/setu/v2"
        assert custom_config.image_proxy == "i.example.re"

    def test_non_mapping_section_ignored(self):
        """分组值非 dict 时回退全部默认值，不应抛错。"""
        cfg = SetuConfig.from_raw({"api": "not-a-dict"})
        assert cfg.api["r18"] == 0
