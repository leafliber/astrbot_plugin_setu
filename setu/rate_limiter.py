"""全局 + 分会话滑动窗口限流。

需求 4：支持全局限流与分 UMO（unified_msg_origin）限流。
采用滑动窗口计数：每个计数器维护一个时间戳 deque，仅保留窗口期内的时间戳，
超限则拒绝。计数仅在内存中，进程重启清零（符合限流的瞬时性质）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimitResult:
    """限流检查结果。"""

    allowed: bool
    reason: str = ""
    # 当前会话剩余配额（仅在不被全局拒绝时有意义）
    umo_remaining: int = 0
    global_remaining: int = 0


@dataclass
class RateLimiter:
    """两级滑动窗口限流器。

    :param global_per_minute: 全插件全局每分钟请求数上限
    :param umo_per_minute: 单个 UMO 每分钟请求数上限
    """

    global_per_minute: int = 30
    umo_per_minute: int = 5
    window_seconds: float = 60.0
    _global: deque[float] = field(default_factory=deque)
    _umo_counters: dict[str, deque[float]] = field(default_factory=dict)

    def check(self, umo: str) -> RateLimitResult:
        """检查 ``umo`` 是否允许发起请求。允许时顺便记入计数。"""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        self._gc(self._global, cutoff)
        umo_q = self._umo_counters.setdefault(umo, deque())
        self._gc(umo_q, cutoff)

        global_remaining = max(0, self.global_per_minute - len(self._global))
        umo_remaining = max(0, self.umo_per_minute - len(umo_q))

        if len(self._global) >= self.global_per_minute:
            return RateLimitResult(
                allowed=False,
                reason="全局请求已达上限，请稍后再试",
                umo_remaining=umo_remaining,
                global_remaining=0,
            )
        if len(umo_q) >= self.umo_per_minute:
            return RateLimitResult(
                allowed=False,
                reason="本会话请求已达上限，请稍后再试",
                umo_remaining=0,
                global_remaining=global_remaining,
            )

        # 计入本次请求
        self._global.append(now)
        umo_q.append(now)
        return RateLimitResult(
            allowed=True,
            umo_remaining=max(0, umo_remaining - 1),
            global_remaining=max(0, global_remaining - 1),
        )

    def umo_status(self, umo: str) -> tuple[int, int]:
        """返回 (该会话本窗口已用次数, 该会话上限)。"""
        now = time.monotonic()
        q = self._umo_counters.get(umo)
        if q is None:
            return 0, self.umo_per_minute
        self._gc(q, now - self.window_seconds)
        return len(q), self.umo_per_minute

    def global_status(self) -> tuple[int, int]:
        """返回 (全局本窗口已用次数, 全局上限)。"""
        now = time.monotonic()
        self._gc(self._global, now - self.window_seconds)
        return len(self._global), self.global_per_minute

    @staticmethod
    def _gc(queue: deque[float], cutoff: float) -> None:
        """丢弃窗口外（早于 cutoff）的时间戳。"""
        while queue and queue[0] < cutoff:
            queue.popleft()
