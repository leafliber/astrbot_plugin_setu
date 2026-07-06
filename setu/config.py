"""配置解析与默认值兜底。

AstrBot 把 ``_conf_schema.json`` 解析为 ``AstrBotConfig``（继承自 ``dict``）传入插件。
本模块统一把扁平/嵌套的配置字典规整为强类型的 :class:`SetuConfig` dataclass，
对缺失字段填入默认值，避免业务代码到处写 ``config.get(...)`` 与 ``KeyError`` 隐患。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .rate_limiter import LimitRule, parse_window

# ---- 各分组默认值 ----

API_DEFAULTS: dict[str, Any] = {
    "base_url": "https://api.lolicon.app/setu/v2",
    "r18": 0,
    "num": 1,
    "size": ["original"],
    "image_proxy": "i.pixiv.re",
    "keyword": "",
    "tag": [],
    "uid": [],
    "excludeAI": False,
    "dsc": False,
    "aspectRatio": "",
    "dateAfter": 0,
    "dateBefore": 0,
    "show_metadata": False,
}

NETWORK_DEFAULTS: dict[str, Any] = {
    "http_proxy": "",
    "timeout": 15,
}

CACHE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "max_count": 500,
    "ttl_hours": 168,
}

RATE_LIMIT_DEFAULTS: dict[str, Any] = {
    "global_per_minute": 30,
    "umo_per_minute": 5,
    "global_rules": "",
    "umo_rules": "",
}

SESSION_DEFAULTS: dict[str, Any] = {
    "default_enabled": True,
    "admin_only_toggle": True,
}

TOOL_DEFAULTS: dict[str, Any] = {
    "enabled": True,
}


def _merge(group: str, defaults: dict[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    """合并某分组：以 defaults 为基底，raw 中存在的同名字段覆盖。"""
    merged = dict(defaults)
    section = raw.get(group, {}) or {}
    if not isinstance(section, Mapping):
        return merged
    for key, _default_val in defaults.items():
        if key in section and section[key] is not None:
            merged[key] = section[key]
    return merged


def _parse_rules_str(s: str, per_minute_fallback: int) -> list[LimitRule]:
    """解析规则字符串为 LimitRule 列表。

    格式：``1h:200,1d:1000``（逗号分隔，窗口:数量）。
    始终包含 per_minute_fallback 转换的 1m 规则作为基线。
    """
    rules: list[LimitRule] = [LimitRule(window_seconds=60.0, max_count=per_minute_fallback)]
    s = (s or "").strip()
    if not s:
        return rules
    for part in s.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        window_str, _, max_str = part.partition(":")
        try:
            window_sec = parse_window(window_str.strip())
            max_count = int(max_str.strip())
            if max_count > 0 and window_sec > 0:
                # 避免与 1m 基线重复
                if window_sec != 60.0:
                    rules.append(LimitRule(window_seconds=window_sec, max_count=max_count))
        except (ValueError, TypeError):
            continue
    return rules


@dataclass
class SetuConfig:
    """规整后的插件配置。"""

    trigger_words: list[str] = field(default_factory=lambda: ["色图", "来点色图", "setu"])
    api: dict[str, Any] = field(default_factory=lambda: dict(API_DEFAULTS))
    network: dict[str, Any] = field(default_factory=lambda: dict(NETWORK_DEFAULTS))
    cache: dict[str, Any] = field(default_factory=lambda: dict(CACHE_DEFAULTS))
    rate_limit: dict[str, Any] = field(default_factory=lambda: dict(RATE_LIMIT_DEFAULTS))
    session: dict[str, Any] = field(default_factory=lambda: dict(SESSION_DEFAULTS))
    tool: dict[str, Any] = field(default_factory=lambda: dict(TOOL_DEFAULTS))

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any] | None) -> SetuConfig:
        """从 AstrBotConfig 原始字典构建，缺失字段自动补默认值。"""
        raw = raw or {}

        # 触发词列表：去空格 + 去重（保留首次出现顺序）
        tw = raw.get("trigger_words", None)
        if not isinstance(tw, list) or not tw:
            trigger_words = ["色图", "来点色图", "setu"]
        else:
            trigger_words = list(dict.fromkeys(str(w).strip() for w in tw if str(w).strip()))

        return cls(
            trigger_words=trigger_words,
            api=_merge("api", API_DEFAULTS, raw),
            network=_merge("network", NETWORK_DEFAULTS, raw),
            cache=_merge("cache", CACHE_DEFAULTS, raw),
            rate_limit=_merge("rate_limit", RATE_LIMIT_DEFAULTS, raw),
            session=_merge("session", SESSION_DEFAULTS, raw),
            tool=_merge("tool", TOOL_DEFAULTS, raw),
        )

    # ---- 便捷访问器 ----

    @property
    def http_proxy(self) -> str | None:
        """返回代理字符串，空则返回 None（httpx 不使用代理）。"""
        p = str(self.network.get("http_proxy", "") or "").strip()
        return p or None

    @property
    def timeout(self) -> float:
        return float(self.network.get("timeout", 15))

    @property
    def cache_enabled(self) -> bool:
        return bool(self.cache.get("enabled", True))

    @property
    def cache_dir_name(self) -> str:
        return "astrbot_plugin_setu"

    @property
    def base_url(self) -> str:
        return str(self.api.get("base_url", API_DEFAULTS["base_url"]))

    @property
    def image_proxy(self) -> str:
        return str(self.api.get("image_proxy", API_DEFAULTS["image_proxy"]))

    @property
    def show_metadata(self) -> bool:
        return bool(self.api.get("show_metadata", False))

    @property
    def tool_enabled(self) -> bool:
        return bool(self.tool.get("enabled", True))

    @property
    def global_limit_rules(self) -> list[LimitRule]:
        """全局限流规则列表（含 per_minute 基线 + 配置的额外窗口）。"""
        gpm = int(self.rate_limit.get("global_per_minute", 30))
        return _parse_rules_str(str(self.rate_limit.get("global_rules", "")), gpm)

    @property
    def umo_default_limit_rules(self) -> list[LimitRule]:
        """分会话默认限流规则列表。"""
        upm = int(self.rate_limit.get("umo_per_minute", 5))
        return _parse_rules_str(str(self.rate_limit.get("umo_rules", "")), upm)
