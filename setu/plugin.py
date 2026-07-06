"""Star 子类：装配各模块并注册 handler / LLM Tool。

这是插件的业务中枢，把触发词、限流、会话开关、API 客户端、图片缓存
串联成一条完整流程（见 PLAN.md 架构图）。触发词路径与 LLM Tool 路径
共用 :meth:`_handle_setu` 这条核心流程，区别仅在于入口与参数来源。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .api_client import ApiError, SetuApiClient
from .config import SetuConfig
from .image_cache import ImageCache
from .rate_limiter import RateLimiter
from .session_manager import SessionManager
from .tools import SETU_TOOL_NAME, build_params_from_tool_args
from .trigger import Trigger

PLUGIN_NAME = "astrbot_plugin_setu"


class SetuPlugin(Star):
    """色图插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.raw_config = config
        self.config = SetuConfig.from_raw(config)

        # 触发词匹配器
        self.trigger = Trigger(self.config.trigger_words)

        # 限流器：全局 + 分会话滑动窗口
        self.rate_limiter = RateLimiter(
            global_per_minute=int(self.config.rate_limit.get("global_per_minute", 30)),
            umo_per_minute=int(self.config.rate_limit.get("umo_per_minute", 5)),
        )

        # 会话开关状态：基于 AstrBot KV 存储
        self.session_manager = SessionManager(
            default_enabled=bool(self.config.session.get("default_enabled", True)),
            kv_getter=self.get_kv_data,
            kv_setter=self.put_kv_data,
        )

        # 共享 httpx 客户端（API 与图片缓存共用，统一走代理）
        self.api_client = SetuApiClient(self.config)

        # 图片缓存目录：data/plugin_data/astrbot_plugin_setu/cache/
        # 注意 get_astrbot_plugin_data_path() 返回 str，需用 Path 包装
        plugin_data = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.image_cache = ImageCache(
            self.config,
            cache_dir=plugin_data / "cache",
        )

        # LLM Tool：在 __init__ 中以闭包方式注册，捕获 self
        # 不能作为 class 方法用 @filter.llm_tool 装饰——装饰器存储的是未绑定函数，
        # AstrBot 调用时写 handler(event, **kwargs) 会把 event 填入 self 参数位置。
        setu_instance = self

        @filter.llm_tool(name=SETU_TOOL_NAME)
        async def get_setu(event: AstrMessageEvent, tag: str, num: int, r18: int):
            '''获取 Pixiv 色图并发送到当前会话。

            Args:
                tag(string): 标签关键词，多个用竖线 | 表示或关系，如 萝莉|少女
                num(int): 获取数量，1 到 20
                r18(int): 0 为非 R18，1 为 R18，2 为混合
            '''
            if not setu_instance.config.tool_enabled:
                yield event.plain_result("色图工具已被禁用。")
                return
            params = build_params_from_tool_args(tag=tag, num=num, r18=r18)
            async for result in setu_instance._handle_setu(event, params):
                yield result

        # 保存引用便于 terminate 等处访问
        self.get_setu = get_setu

        logger.info(
            "%s 已加载：触发词=%s, 镜像站=%s, 代理=%s, 缓存=%s",
            PLUGIN_NAME,
            self.config.trigger_words,
            self.config.base_url,
            self.config.http_proxy or "无",
            "开启" if self.config.cache_enabled else "关闭",
        )

    # ------------------------------------------------------------------
    # 核心流程：会话检查 → 限流 → 请求 → 缓存 → 发送
    # ------------------------------------------------------------------

    async def _handle_setu(
        self,
        event: AstrMessageEvent,
        params: Mapping[str, Any] | None,
    ) -> AsyncIterator:
        """触发词与 LLM Tool 共用的色图获取与发送流程。"""
        umo = event.unified_msg_origin

        # 1) 会话开关
        if not await self.session_manager.is_enabled(umo):
            yield event.plain_result("本会话未开启色图功能，可使用 /setu on 开启。")
            return

        # 2) 限流
        rl = self.rate_limiter.check(umo)
        if not rl.allowed:
            yield event.plain_result(rl.reason)
            return

        # 3) 请求 API
        try:
            data = await self.api_client.fetch(params)
        except ApiError as exc:
            yield event.plain_result(f"获取图片失败：{exc}")
            return

        if not data:
            yield event.plain_result("没有找到符合条件的图片。")
            return

        # 4) 逐条发送（命中缓存走本地，否则走 URL）
        size = self._primary_size()
        for item in data:
            chain = await self._build_chain(item, size)
            if chain is None:
                continue
            yield event.chain_result(chain)

    async def _build_chain(self, item: Mapping[str, Any], size: str) -> list | None:
        """为一条 setu 构造图片消息段（默认纯图片，可选附带元信息文本）。"""
        url = SetuApiClient.pick_url(item)
        image_comp = None

        if self.config.cache_enabled:
            local = await self.image_cache.get_local_path(item, size)
            if local:
                try:
                    image_comp = Comp.Image.fromFileSystem(local)
                except Exception:  # noqa: BLE001 - 本地图异常时回退 URL
                    image_comp = None

        if image_comp is None:
            if not url:
                return None
            image_comp = Comp.Image.fromURL(url)

        # 默认只发图片；show_metadata 开启时附带文字元信息
        if self.config.show_metadata:
            return [Comp.Plain(self._format_meta(item)), image_comp]
        return [image_comp]

    @staticmethod
    def _format_meta(item: Mapping[str, Any]) -> str:
        """格式化作品元信息文本。"""
        tags = item.get("tags") or []
        if isinstance(tags, list):
            tags_str = ", ".join(str(t) for t in tags[:8])
        else:
            tags_str = str(tags)
        return (
            f"标题：{item.get('title', '')}\n"
            f"作者：{item.get('author', '')} (uid:{item.get('uid', '')})\n"
            f"pid：{item.get('pid', '')}\n"
            f"标签：{tags_str}\n"
        )

    def _primary_size(self) -> str:
        sizes = self.config.api.get("size") or ["original"]
        return sizes[0] if isinstance(sizes, list) and sizes else "original"

    # ------------------------------------------------------------------
    # 入口一：触发词（无前缀）
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> AsyncIterator:
        """拦截全部消息，匹配触发词后走色图流程。"""
        msg = (event.message_str or "").strip()
        if not msg:
            return
        matched = self.trigger.match(msg)
        if not matched:
            return  # 未命中则放行，事件继续传播
        _word, rest = matched
        parsed = Trigger.parse_args(rest)
        params = parsed.to_api_params()
        async for result in self._handle_setu(event, params):
            yield result

    # ------------------------------------------------------------------
    # 入口三：管理员指令 /setu on|off|status|cache
    # ------------------------------------------------------------------

    @filter.command_group("setu")
    def setu_admin(self):
        """色图插件管理指令组。"""
        pass

    @setu_admin.command("on")
    async def setu_on(self, event: AstrMessageEvent) -> AsyncIterator:
        """开启当前会话的色图功能。"""
        if not await self._check_admin(event):
            return
        umo = event.unified_msg_origin
        await self.session_manager.set_enabled(umo, True)
        yield event.plain_result("已开启本会话的色图功能。")

    @setu_admin.command("off")
    async def setu_off(self, event: AstrMessageEvent) -> AsyncIterator:
        """关闭当前会话的色图功能。"""
        if not await self._check_admin(event):
            return
        umo = event.unified_msg_origin
        await self.session_manager.set_enabled(umo, False)
        yield event.plain_result("已关闭本会话的色图功能。")

    @setu_admin.command("status")
    async def setu_status(self, event: AstrMessageEvent) -> AsyncIterator:
        """查看当前会话状态与限流余量。"""
        umo = event.unified_msg_origin
        enabled = await self.session_manager.is_enabled(umo)
        used, cap = self.rate_limiter.umo_status(umo)
        g_used, g_cap = self.rate_limiter.global_status()
        cache = self.image_cache.stats() if self.config.cache_enabled else {"count": 0}
        yield event.plain_result(
            f"本会话色图功能：{'开启' if enabled else '关闭'}\n"
            f"分会话限流：{used}/{cap} 次/分钟\n"
            f"全局限流：{g_used}/{g_cap} 次/分钟\n"
            f"缓存图片数：{cache.get('count', 0)}"
        )

    @setu_admin.command("cache")
    async def setu_cache(self, event: AstrMessageEvent) -> AsyncIterator:
        """清理本地图片缓存。"""
        if not await self._check_admin(event):
            return
        count = 0
        if self.config.cache_enabled:
            count = self._clear_cache()
        yield event.plain_result(f"已清理 {count} 张缓存图片。")

    async def _check_admin(self, event: AstrMessageEvent) -> bool:
        """根据 admin_only_toggle 配置校验管理员权限。返回是否允许继续。"""
        if not bool(self.config.session.get("admin_only_toggle", True)):
            return True
        if event.is_admin():
            return True
        await event.send(event.plain_result("仅管理员可执行此操作。"))
        return False

    def _clear_cache(self) -> int:
        """删除全部缓存图片，返回删除数量。"""
        count = 0
        for f in self.image_cache.cache_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass
        return count

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def terminate(self):
        """插件卸载/停用时关闭 httpx 客户端。"""
        await self.api_client.close()
        await self.image_cache.close()
        logger.info("%s 已卸载", PLUGIN_NAME)
