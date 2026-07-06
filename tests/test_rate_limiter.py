"""限流器测试。"""

from __future__ import annotations

import pytest

from setu.rate_limiter import (
    LimitRule,
    RateLimiter,
    format_window,
    parse_window,
)


class TestWindowParsing:
    def test_parse_seconds(self):
        assert parse_window("30s") == 30.0

    def test_parse_minutes(self):
        assert parse_window("5m") == 300.0

    def test_parse_hours(self):
        assert parse_window("12h") == 43200.0

    def test_parse_days(self):
        assert parse_window("7d") == 604800.0

    def test_parse_pure_number_defaults_to_minutes(self):
        assert parse_window("5") == 300.0

    def test_parse_case_insensitive(self):
        assert parse_window("12H") == 43200.0

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            parse_window("abc")

    def test_parse_empty(self):
        with pytest.raises(ValueError):
            parse_window("")

    def test_format_seconds(self):
        assert format_window(30.0) == "30s"

    def test_format_minutes(self):
        assert format_window(300.0) == "5m"

    def test_format_hours(self):
        assert format_window(43200.0) == "12h"

    def test_format_days(self):
        assert format_window(604800.0) == "7d"

    def test_roundtrip(self):
        for s in ["30s", "5m", "12h", "7d"]:
            assert format_window(parse_window(s)) == s


class TestLimitRule:
    def test_label(self):
        assert LimitRule(43200.0, 3).label == "12h"

    def test_to_dict(self):
        d = LimitRule(43200.0, 3).to_dict()
        assert d == {"window": "12h", "max": 3}

    def test_from_dict(self):
        r = LimitRule.from_dict({"window": "12h", "max": 3})
        assert r.window_seconds == 43200.0
        assert r.max_count == 3

    def test_frozen(self):
        r = LimitRule(60.0, 5)
        with pytest.raises(AttributeError):
            r.max_count = 10  # type: ignore[misc]


class TestRateLimiterBasic:
    def _make(self, global_rules=None, umo_rules=None):
        return RateLimiter(
            global_rules=global_rules or [LimitRule(60.0, 3)],
            umo_default_rules=umo_rules or [LimitRule(60.0, 2)],
        )

    def test_allows_under_limit(self):
        rl = self._make()
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True

    def test_umo_limit_blocks(self):
        rl = self._make(global_rules=[LimitRule(60.0, 10)])
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        r = rl.check("umo1")
        assert r.allowed is False
        assert "本会话" in r.reason

    def test_global_limit_blocks(self):
        rl = self._make(umo_rules=[LimitRule(60.0, 10)])
        assert rl.check("umo1").allowed is True  # global: 1/3
        assert rl.check("umo2").allowed is True  # global: 2/3
        assert rl.check("umo3").allowed is True  # global: 3/3
        r = rl.check("umo4")  # 3 >= 3 → rejected
        assert r.allowed is False
        assert "全局" in r.reason

    def test_different_umo_independent(self):
        rl = self._make(global_rules=[LimitRule(60.0, 10)])
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is False
        assert rl.check("umo2").allowed is True
        assert rl.check("umo2").allowed is True

    def test_window_expiry(self, monkeypatch):
        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = self._make(global_rules=[LimitRule(60.0, 2)])
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is False

        fake_now[0] = 1061.0
        assert rl.check("umo1").allowed is True

    def test_no_partial_record_on_reject(self, monkeypatch):
        """任一窗口超限时，不应记入任何窗口的计数。"""
        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 10)],
            umo_default_rules=[LimitRule(60.0, 1), LimitRule(3600.0, 5)],
        )
        # 第一次请求通过，1m 和 1h 都记入
        assert rl.check("umo1").allowed is True
        # 第二次请求被 1m 拒绝（1 >= 1）
        r = rl.check("umo1")
        assert r.allowed is False
        # 1h 窗口应仍为 1（第一次记入的），未被第二次请求增加
        statuses = {w.label: w for w in rl.umo_status("umo1")}
        assert statuses["1h"].used == 1
        assert statuses["1m"].used == 1


class TestMultiWindowLimits:
    def test_multiple_windows_all_pass(self):
        """多窗口全部通过时放行。"""
        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 10), LimitRule(3600.0, 100)],
            umo_default_rules=[LimitRule(60.0, 5), LimitRule(3600.0, 20)],
        )
        r = rl.check("umo1")
        assert r.allowed is True
        assert len(r.global_windows) == 2
        assert len(r.umo_windows) == 2

    def test_shorter_window_blocks_first(self, monkeypatch):
        """短窗口先超限时拒绝。"""
        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 10)],
            umo_default_rules=[LimitRule(60.0, 2), LimitRule(3600.0, 100)],
        )
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        r = rl.check("umo1")
        assert r.allowed is False
        assert "1m" in r.reason

    def test_longer_window_blocks(self, monkeypatch):
        """长窗口超限时拒绝。"""
        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 100)],
            umo_default_rules=[LimitRule(60.0, 100), LimitRule(3600.0, 3)],
        )
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        r = rl.check("umo1")
        assert r.allowed is False
        assert "1h" in r.reason

    def test_global_and_umo_both_checked(self, monkeypatch):
        """全局和分会话窗口都被检查。"""
        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 2)],
            umo_default_rules=[LimitRule(60.0, 100)],
        )
        assert rl.check("umo1").allowed is True
        assert rl.check("umo2").allowed is True
        r = rl.check("umo3")
        assert r.allowed is False
        assert "全局" in r.reason


class TestCustomRules:
    def test_add_custom_rule(self):
        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 30)],
            umo_default_rules=[LimitRule(60.0, 5)],
        )
        rl.add_umo_rule("umo1", LimitRule(43200.0, 3))
        rules = rl.get_umo_rules("umo1")
        labels = {r.label for r in rules}
        assert "1m" in labels
        assert "12h" in labels

    def test_custom_rule_same_window_overrides(self):
        """同窗口的自定义规则覆盖默认规则（取更严格的）。"""
        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 30)],
            umo_default_rules=[LimitRule(60.0, 5)],
        )
        rl.add_umo_rule("umo1", LimitRule(60.0, 2))
        rules = rl.get_umo_rules("umo1")
        # 1m 窗口应取 max_count=2（更严格）
        rule_1m = next(r for r in rules if r.window_seconds == 60.0)
        assert rule_1m.max_count == 2

    def test_custom_rule_different_umo(self):
        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 30)],
            umo_default_rules=[LimitRule(60.0, 5)],
        )
        rl.add_umo_rule("umo1", LimitRule(43200.0, 3))
        # umo2 不受影响
        rules = rl.get_umo_rules("umo2")
        assert all(r.window_seconds != 43200.0 for r in rules)

    def test_remove_custom_rule(self):
        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 30)],
            umo_default_rules=[LimitRule(60.0, 5)],
        )
        rl.add_umo_rule("umo1", LimitRule(43200.0, 3))
        assert rl.remove_umo_rule("umo1", 43200.0) is True
        rules = rl.get_umo_custom_rules("umo1")
        assert len(rules) == 0

    def test_remove_nonexistent_rule(self):
        rl = RateLimiter()
        assert rl.remove_umo_rule("umo1", 43200.0) is False

    def test_clear_custom_rules(self):
        rl = RateLimiter()
        rl.add_umo_rule("umo1", LimitRule(43200.0, 3))
        rl.add_umo_rule("umo1", LimitRule(3600.0, 10))
        count = rl.clear_umo_rules("umo1")
        assert count == 2
        assert len(rl.get_umo_custom_rules("umo1")) == 0

    def test_custom_rule_enforced(self, monkeypatch):
        """自定义规则实际生效。"""
        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 100)],
            umo_default_rules=[LimitRule(60.0, 100)],
        )
        rl.add_umo_rule("umo1", LimitRule(43200.0, 2))

        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        r = rl.check("umo1")
        assert r.allowed is False
        assert "12h" in r.reason


class TestPersistSerialization:
    def test_get_all_for_persist(self):
        rl = RateLimiter()
        rl.add_umo_rule("umo1", LimitRule(43200.0, 3))
        rl.add_umo_rule("umo2", LimitRule(3600.0, 10))
        data = rl.get_all_custom_rules_for_persist()
        assert "umo1" in data
        assert {"window": "12h", "max": 3} in data["umo1"]
        assert "umo2" in data

    def test_set_from_persist(self):
        rl = RateLimiter()
        rules = [LimitRule.from_dict({"window": "12h", "max": 3})]
        rl.set_umo_custom_rules("umo1", rules)
        customs = rl.get_umo_custom_rules("umo1")
        assert len(customs) == 1
        assert customs[0].window_seconds == 43200.0
        assert customs[0].max_count == 3

    def test_set_empty_clears(self):
        rl = RateLimiter()
        rl.add_umo_rule("umo1", LimitRule(43200.0, 3))
        rl.set_umo_custom_rules("umo1", [])
        assert len(rl.get_umo_custom_rules("umo1")) == 0

    def test_roundtrip(self):
        rl1 = RateLimiter()
        rl1.add_umo_rule("umo1", LimitRule(43200.0, 3))
        rl1.add_umo_rule("umo1", LimitRule(3600.0, 10))
        data = rl1.get_all_custom_rules_for_persist()

        rl2 = RateLimiter()
        for umo, rules_data in data.items():
            rl2.set_umo_custom_rules(
                umo,
                [LimitRule.from_dict(d) for d in rules_data],
            )
        assert len(rl2.get_umo_custom_rules("umo1")) == 2


class TestStatusMethods:
    def test_global_status(self):
        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 5), LimitRule(3600.0, 100)],
        )
        rl.check("umo1")
        statuses = rl.global_status()
        assert len(statuses) == 2
        s_1m = next(s for s in statuses if s.label == "1m")
        assert s_1m.used == 1
        assert s_1m.limit == 5
        assert s_1m.remaining == 4

    def test_umo_status(self):
        rl = RateLimiter(
            global_rules=[LimitRule(60.0, 100)],
            umo_default_rules=[LimitRule(60.0, 3)],
        )
        rl.add_umo_rule("umo1", LimitRule(43200.0, 10))
        rl.check("umo1")
        statuses = rl.umo_status("umo1")
        assert len(statuses) == 2
        labels = {s.label for s in statuses}
        assert "1m" in labels
        assert "12h" in labels

    def test_status_empty_limiter(self):
        rl = RateLimiter()
        assert rl.global_status() == []
        assert rl.umo_status("umo1") == []


class TestWindowStatus:
    def test_remaining(self):
        from setu.rate_limiter import WindowStatus

        ws = WindowStatus(label="1m", used=3, limit=5)
        assert ws.remaining == 2

    def test_remaining_clamped(self):
        from setu.rate_limiter import WindowStatus

        ws = WindowStatus(label="1m", used=10, limit=5)
        assert ws.remaining == 0

    def test_str(self):
        from setu.rate_limiter import WindowStatus

        ws = WindowStatus(label="12h", used=2, limit=3)
        assert str(ws) == "12h: 2/3"
