"""会话开关状态管理测试。"""

from __future__ import annotations

import pytest

from setu.session_manager import SessionManager


class FakeKV:
    """内存 KV 存储，模拟 AstrBot 的 put/get_kv_data。"""

    def __init__(self):
        self.store: dict[str, object] = {}

    async def get(self, key: str, default=None):
        return self.store.get(key, default)

    async def set(self, key: str, value):
        self.store[key] = value


@pytest.fixture
def kv() -> FakeKV:
    return FakeKV()


@pytest.fixture
def manager(kv: FakeKV) -> SessionManager:
    return SessionManager(default_enabled=True, kv_getter=kv.get, kv_setter=kv.set)


class TestSessionManager:
    async def test_default_when_unset(self, manager: SessionManager):
        assert await manager.is_enabled("umo1") is True

    async def test_set_enabled_true(self, manager: SessionManager, kv: FakeKV):
        await manager.set_enabled("umo1", True)
        assert kv.store["setu_enabled:umo1"] is True
        assert await manager.is_enabled("umo1") is True

    async def test_set_enabled_false(self, manager: SessionManager):
        await manager.set_enabled("umo1", False)
        assert await manager.is_enabled("umo1") is False

    async def test_per_umo_independent(self, manager: SessionManager):
        await manager.set_enabled("umo1", False)
        assert await manager.is_enabled("umo1") is False
        assert await manager.is_enabled("umo2") is True  # 未设置 -> 默认

    async def test_string_value_parsed(self, kv: FakeKV, manager: SessionManager):
        # KV 存储可能返回字符串形式的布尔
        kv.store["setu_enabled:umo1"] = "false"
        assert await manager.is_enabled("umo1") is False
        kv.store["setu_enabled:umo1"] = "true"
        assert await manager.is_enabled("umo1") is True

    async def test_none_value_falls_back(self, kv: FakeKV, manager: SessionManager):
        kv.store["setu_enabled:umo1"] = None
        assert await manager.is_enabled("umo1") is True  # 回退默认

    async def test_reset(self, manager: SessionManager):
        await manager.set_enabled("umo1", False)
        await manager.reset("umo1")
        # reset 后回到默认值
        assert await manager.is_enabled("umo1") is True

    async def test_default_false(self, kv: FakeKV):
        m = SessionManager(default_enabled=False, kv_getter=kv.get, kv_setter=kv.set)
        assert await m.is_enabled("umo1") is False

    def test_key_of(self):
        assert SessionManager.key_of("umo1") == "setu_enabled:umo1"
