"""限流器测试。"""

from __future__ import annotations

from setu.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allows_under_limit(self):
        rl = RateLimiter(global_per_minute=3, umo_per_minute=2)
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True

    def test_umo_limit_blocks(self):
        rl = RateLimiter(global_per_minute=10, umo_per_minute=2)
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        r = rl.check("umo1")
        assert r.allowed is False
        assert "本会话" in r.reason

    def test_global_limit_blocks(self):
        rl = RateLimiter(global_per_minute=2, umo_per_minute=10)
        assert rl.check("umo1").allowed is True
        assert rl.check("umo2").allowed is True
        r = rl.check("umo3")
        assert r.allowed is False
        assert "全局" in r.reason

    def test_different_umo_independent(self):
        rl = RateLimiter(global_per_minute=10, umo_per_minute=2)
        assert rl.check("umo1").allowed is True  # umo1: 1/2
        assert rl.check("umo1").allowed is True  # umo1: 2/2
        assert rl.check("umo1").allowed is False  # umo1 已用尽
        # umo2 独立计数，仍可用
        assert rl.check("umo2").allowed is True  # umo2: 1/2
        assert rl.check("umo2").allowed is True  # umo2: 2/2

    def test_window_expiry(self, monkeypatch):
        """模拟时间流逝，窗口过期后计数清零。"""

        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = RateLimiter(global_per_minute=2, umo_per_minute=2, window_seconds=60.0)
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is True
        assert rl.check("umo1").allowed is False

        # 推进 61 秒，窗口过期
        fake_now[0] = 1061.0
        assert rl.check("umo1").allowed is True

    def test_status_methods(self, monkeypatch):
        fake_now = [1000.0]
        monkeypatch.setattr("setu.rate_limiter.time.monotonic", lambda: fake_now[0])

        rl = RateLimiter(global_per_minute=5, umo_per_minute=3)
        rl.check("umo1")
        rl.check("umo1")
        used, cap = rl.umo_status("umo1")
        assert (used, cap) == (2, 3)
        g_used, g_cap = rl.global_status()
        assert (g_used, g_cap) == (2, 5)

    def test_remaining_decrements(self):
        rl = RateLimiter(global_per_minute=5, umo_per_minute=3)
        r = rl.check("umo1")
        assert r.allowed is True
        assert r.umo_remaining == 2  # 用掉1个，剩2
        assert r.global_remaining == 4
