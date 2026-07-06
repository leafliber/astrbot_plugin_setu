"""分会话开关状态管理。

需求 4：支持分会话开启/关闭。状态以 ``unified_msg_origin``（UMO）为 key，
存入 AstrBot 的 KV 存储（``put_kv_data`` / ``get_kv_data``），重启不丢失。
缺省回退到配置的 ``session.default_enabled``。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

# KV 存储的 key 前缀
_KEY_PREFIX = "setu_enabled:"


class SessionManager:
    """管理每个会话的色图功能开关。

    通过注入的异步 KV 读写函数与 AstrBot 解耦，便于单元测试。
    """

    def __init__(
        self,
        default_enabled: bool,
        kv_getter: Callable[[str, object], Awaitable[object]],
        kv_setter: Callable[[str, object], Awaitable[None]],
    ) -> None:
        self.default_enabled = default_enabled
        self._get = kv_getter
        self._set = kv_setter

    async def is_enabled(self, umo: str) -> bool:
        """查询某会话是否开启。未显式设置时回退到默认值。"""
        val = await self._get(_KEY_PREFIX + umo, self.default_enabled)
        # KV 存储可能返回 None 或各种类型，统一转为 bool
        if val is None:
            return self.default_enabled
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "on")
        return bool(val)

    async def set_enabled(self, umo: str, enabled: bool) -> None:
        """显式设置某会话的开关状态。"""
        await self._set(_KEY_PREFIX + umo, bool(enabled))

    async def reset(self, umo: str) -> None:
        """清除某会话的显式设置，恢复到默认值。"""
        # 用默认值覆盖等价于"重置"，避免依赖 delete_kv_data 的存在性
        await self._set(_KEY_PREFIX + umo, self.default_enabled)

    @staticmethod
    def key_of(umo: str) -> str:
        """返回某 UMO 对应的 KV key，便于调试与测试。"""
        return _KEY_PREFIX + umo
