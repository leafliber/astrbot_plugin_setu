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

    def test_show_metadata_property(self, base_config, custom_config):
        assert base_config.show_metadata is False
        cfg = SetuConfig.from_raw({"api": {"show_metadata": True}})
        assert cfg.show_metadata is True

    def test_non_mapping_section_ignored(self):
        """分组值非 dict 时回退全部默认值，不应抛错。"""
        cfg = SetuConfig.from_raw({"api": "not-a-dict"})
        assert cfg.api["r18"] == 0

    def test_global_limit_rules_default(self, base_config):
        """默认只有 1m 基线规则。"""
        rules = base_config.global_limit_rules
        assert len(rules) == 1
        assert rules[0].window_seconds == 60.0
        assert rules[0].max_count == 30

    def test_umo_default_limit_rules_default(self, base_config):
        rules = base_config.umo_default_limit_rules
        assert len(rules) == 1
        assert rules[0].max_count == 5

    def test_global_limit_rules_with_extra(self):
        cfg = SetuConfig.from_raw({
            "rate_limit": {
                "global_per_minute": 10,
                "global_rules": "1h:200,1d:1000",
            }
        })
        rules = cfg.global_limit_rules
        labels = {r.label for r in rules}
        assert "1m" in labels
        assert "1h" in labels
        assert "1d" in labels
        r_1h = next(r for r in rules if r.label == "1h")
        assert r_1h.max_count == 200

    def test_umo_default_limit_rules_with_extra(self):
        cfg = SetuConfig.from_raw({
            "rate_limit": {
                "umo_per_minute": 3,
                "umo_rules": "12h:20",
            }
        })
        rules = cfg.umo_default_limit_rules
        labels = {r.label for r in rules}
        assert "1m" in labels
        assert "12h" in labels

    def test_rules_invalid_entries_ignored(self):
        """无效规则条目被忽略，不影响有效条目。"""
        cfg = SetuConfig.from_raw({
            "rate_limit": {
                "global_per_minute": 10,
                "global_rules": "1h:200,abc,5x:10,1d:500",
            }
        })
        rules = cfg.global_limit_rules
        labels = {r.label for r in rules}
        assert "1m" in labels
        assert "1h" in labels
        assert "1d" in labels
        assert "abc" not in labels

    def test_rules_dedup_with_1m(self):
        """1h:30 不与 1m 重复。"""
        cfg = SetuConfig.from_raw({
            "rate_limit": {
                "global_per_minute": 10,
                "global_rules": "1m:5,1h:200",
            }
        })
        rules = cfg.global_limit_rules
        # 1m 来自 global_rules 的会被忽略（因为 60s == 60.0 重复）
        # 基线 1m:10 保留，额外 1h:200 保留
        assert len(rules) == 2
