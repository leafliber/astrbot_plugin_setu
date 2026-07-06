"""多时间维度滑动窗口限流。

需求 4：支持全局限流与分 UMO（unified_msg_origin）限流。
管理员可对不同时间维度设置限制，所有限制叠加（AND 逻辑）：
请求必须通过全部规则才放行。

三层规则：
- **全局规则**（global_rules）：对所有 UMO 生效，来自配置
- **分会话默认规则**（umo_default_rules）：对每个 UMO 生效，来自配置
- **分会话自定义规则**（umo_custom_rules）：管理员通过指令为特定 UMO 设置，
  持久化到 KV 存储

每个规则是 ``(window_seconds, max_count)`` 的组合，滑动窗口内的时间戳超限则拒绝。
计数仅在内存中，进程重启清零（符合限流的瞬时性质）；自定义规则通过 KV 持久化。
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

# ---- 时间窗口解析与格式化 ----

_WINDOW_PATTERN = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_window(s: str) -> float:
    """解析时间窗口字符串为秒数。

    支持格式：``30s`` → 30, ``5m`` → 300, ``12h`` → 43200, ``7d`` → 604800。
    纯数字默认按分钟处理。
    """
    s = s.strip().lower()
    if not s:
        raise ValueError("时间窗口不能为空")
    m = _WINDOW_PATTERN.match(s)
    if m:
        return float(int(m.group(1)) * _UNIT_SECONDS[m.group(2)])
    # 纯数字 → 分钟
    if s.isdigit():
        return float(int(s) * 60)
    raise ValueError(f"无法解析时间窗口 '{s}'，支持格式如 30s/5m/12h/7d")


def format_window(seconds: float) -> str:
    """将秒数格式化为人类可读的时间窗口标签。"""
    secs = int(seconds)
    for unit in ("d", "h", "m", "s"):
        if secs % _UNIT_SECONDS[unit] == 0 and secs >= _UNIT_SECONDS[unit]:
            return f"{secs // _UNIT_SECONDS[unit]}{unit}"
    return f"{secs}s"


# ---- 数据类 ----


@dataclass(frozen=True)
class LimitRule:
    """单条限流规则。"""

    window_seconds: float
    max_count: int

    @property
    def label(self) -> str:
        """人类可读的时间窗口标签，如 ``1m``, ``12h``, ``7d``。"""
        return format_window(self.window_seconds)

    def to_dict(self) -> dict:
        """序列化为可持久化的字典。"""
        return {"window": self.label, "max": self.max_count}

    @classmethod
    def from_dict(cls, d: dict) -> LimitRule:
        """从字典反序列化。"""
        return cls(window_seconds=parse_window(d["window"]), max_count=int(d["max"]))


@dataclass
class WindowStatus:
    """单个时间窗口的检查状态。"""

    label: str
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def __str__(self) -> str:
        return f"{self.label}: {self.used}/{self.limit}"


@dataclass
class RateLimitResult:
    """限流检查结果。"""

    allowed: bool
    reason: str = ""
    global_windows: list[WindowStatus] = field(default_factory=list)
    umo_windows: list[WindowStatus] = field(default_factory=list)


@dataclass
class RateLimiter:
    """多时间维度滑动窗口限流器。

    :param global_rules: 全局规则列表，对所有 UMO 生效
    :param umo_default_rules: 分会话默认规则，对每个 UMO 生效
    """

    global_rules: list[LimitRule] = field(default_factory=list)
    umo_default_rules: list[LimitRule] = field(default_factory=list)
    # per-UMO 自定义规则（管理员设置）
    _umo_custom_rules: dict[str, list[LimitRule]] = field(default_factory=dict)
    # 全局计数器: {window_seconds: deque[timestamps]}
    _global_counters: dict[float, deque] = field(default_factory=dict)
    # 分会话计数器: {umo: {window_seconds: deque[timestamps]}}
    _umo_counters: dict[str, dict[float, deque]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 初始化全局计数器
        for rule in self.global_rules:
            self._global_counters.setdefault(rule.window_seconds, deque())

    # ------------------------------------------------------------------
    # 核心检查
    # ------------------------------------------------------------------

    def check(self, umo: str) -> RateLimitResult:
        """检查 ``umo`` 是否允许发起请求。允许时顺便记入全部窗口计数。

        所有规则叠加（AND 逻辑）：任一窗口超限即拒绝，且不记入任何计数。
        """
        now = time.monotonic()

        # 收集全部规则
        umo_rules = self.get_umo_rules(umo)

        # 检查全局窗口
        global_statuses: list[WindowStatus] = []
        for rule in self.global_rules:
            q = self._global_counters.setdefault(rule.window_seconds, deque())
            self._gc(q, now - rule.window_seconds)
            used = len(q)
            global_statuses.append(
                WindowStatus(label=rule.label, used=used, limit=rule.max_count),
            )
            if used >= rule.max_count:
                return RateLimitResult(
                    allowed=False,
                    reason=f"全局限流 {rule.label} 已达上限（{used}/{rule.max_count}），请稍后再试",
                    global_windows=global_statuses,
                    umo_windows=self._calc_umo_statuses(umo, umo_rules, now),
                )

        # 检查分会话窗口
        umo_statuses: list[WindowStatus] = []
        umo_counters = self._umo_counters.setdefault(umo, {})
        for rule in umo_rules:
            q = umo_counters.setdefault(rule.window_seconds, deque())
            self._gc(q, now - rule.window_seconds)
            used = len(q)
            umo_statuses.append(
                WindowStatus(label=rule.label, used=used, limit=rule.max_count),
            )
            if used >= rule.max_count:
                return RateLimitResult(
                    allowed=False,
                    reason=(
                        f"本会话限流 {rule.label} 已达上限"
                        f"（{used}/{rule.max_count}），请稍后再试"
                    ),
                    global_windows=global_statuses,
                    umo_windows=umo_statuses,
                )

        # 全部通过，记入所有窗口
        for rule in self.global_rules:
            self._global_counters[rule.window_seconds].append(now)
        for rule in umo_rules:
            umo_counters[rule.window_seconds].append(now)

        return RateLimitResult(
            allowed=True,
            global_windows=global_statuses,
            umo_windows=umo_statuses,
        )

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------

    def get_umo_rules(self, umo: str) -> list[LimitRule]:
        """获取某 UMO 的全部规则（默认 + 自定义）。"""
        defaults = list(self.umo_default_rules)
        customs = self._umo_custom_rules.get(umo, [])
        # 合并，去重（同窗口取更严格的）
        merged: dict[float, LimitRule] = {}
        for rule in defaults + customs:
            existing = merged.get(rule.window_seconds)
            if existing is None or rule.max_count < existing.max_count:
                merged[rule.window_seconds] = rule
        return list(merged.values())

    def get_umo_custom_rules(self, umo: str) -> list[LimitRule]:
        """获取某 UMO 的自定义规则（不含默认规则）。"""
        return list(self._umo_custom_rules.get(umo, []))

    def add_umo_rule(self, umo: str, rule: LimitRule) -> None:
        """为某 UMO 添加自定义规则。同窗口的旧规则会被覆盖。"""
        rules = self._umo_custom_rules.setdefault(umo, [])
        rules = [r for r in rules if r.window_seconds != rule.window_seconds]
        rules.append(rule)
        self._umo_custom_rules[umo] = rules

    def remove_umo_rule(self, umo: str, window_seconds: float) -> bool:
        """移除某 UMO 指定窗口的自定义规则。返回是否找到并移除。"""
        rules = self._umo_custom_rules.get(umo, [])
        new_rules = [r for r in rules if r.window_seconds != window_seconds]
        if len(new_rules) == len(rules):
            return False
        if new_rules:
            self._umo_custom_rules[umo] = new_rules
        else:
            self._umo_custom_rules.pop(umo, None)
        return True

    def clear_umo_rules(self, umo: str) -> int:
        """清空某 UMO 的全部自定义规则，返回清除数量。"""
        rules = self._umo_custom_rules.pop(umo, [])
        return len(rules)

    def set_umo_custom_rules(self, umo: str, rules: list[LimitRule]) -> None:
        """从持久化数据加载自定义规则（覆盖）。"""
        if rules:
            self._umo_custom_rules[umo] = list(rules)
        else:
            self._umo_custom_rules.pop(umo, None)

    def get_all_custom_rules_for_persist(self) -> dict[str, list[dict]]:
        """获取全部自定义规则，用于持久化。"""
        return {
            umo: [r.to_dict() for r in rules]
            for umo, rules in self._umo_custom_rules.items()
        }

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def global_status(self) -> list[WindowStatus]:
        """返回全局所有窗口的状态。"""
        now = time.monotonic()
        statuses: list[WindowStatus] = []
        for rule in self.global_rules:
            q = self._global_counters.get(rule.window_seconds, deque())
            self._gc(q, now - rule.window_seconds)
            statuses.append(
                WindowStatus(label=rule.label, used=len(q), limit=rule.max_count),
            )
        return statuses

    def umo_status(self, umo: str) -> list[WindowStatus]:
        """返回某 UMO 所有窗口的状态。"""
        now = time.monotonic()
        return self._calc_umo_statuses(umo, self.get_umo_rules(umo), now)

    def _calc_umo_statuses(
        self,
        umo: str,
        rules: list[LimitRule],
        now: float,
    ) -> list[WindowStatus]:
        """计算某 UMO 指定规则列表的窗口状态。"""
        counters = self._umo_counters.get(umo, {})
        statuses: list[WindowStatus] = []
        for rule in rules:
            q = counters.get(rule.window_seconds, deque())
            self._gc(q, now - rule.window_seconds)
            statuses.append(
                WindowStatus(label=rule.label, used=len(q), limit=rule.max_count),
            )
        return statuses

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _gc(queue: deque, cutoff: float) -> None:
        """丢弃窗口外（早于 cutoff）的时间戳。"""
        while queue and queue[0] < cutoff:
            queue.popleft()
