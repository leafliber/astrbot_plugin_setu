"""AstrBot 插件入口。

所有 @filter 装饰的 handler 必须定义在本文件中：AstrBot 通过
``__init_subclass__`` 用 ``cls.__module__`` 注册 ``module_path``，
再用精确匹配 ``handler.__module__ == module_path`` 查找并绑定 handler。
若 handler 定义在 ``setu/plugin.py`` 等子模块中，``__module__`` 不匹配，
handler 会被注册到 registry 但永远不被绑定到插件实例。

业务逻辑（API 客户端、缓存、限流等）仍在 ``setu/`` 子包中，本文件仅
负责 handler 注册与流程编排。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .setu.api_client import ApiError, SetuApiClient
from .setu.config import SetuConfig
from .setu.image_cache import ImageCache
from .setu.rate_limiter import LimitRule, RateLimiter, parse_window
from .setu.session_manager import SessionManager
from .setu.tools import SETU_TOOL_NAME, build_params_from_tool_args
from .setu.trigger import Trigger

PLUGIN_NAME = "astrbot_plugin_setu"


@register(
    "astrbot_plugin_setu",
    "cassia",
    "基于 Lolicon API v2 的色图插件",
    "1.0.0",
    "https://github.com/cassia/astrbot_plugin_setu",
)
class Main(Star):
    """色图插件主类。handler 全部定义于此以确保模块路径匹配。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.raw_config = config
        self.config = SetuConfig.from_raw(config)

        # 触发词匹配器
        self.trigger = Trigger(self.config.trigger_words)

        # 限流器：多时间维度滑动窗口（全局 + 分会话默认 + 分会话自定义）
        self.rate_limiter = RateLimiter(
            global_rules=self.config.global_limit_rules,
            umo_default_rules=self.config.umo_default_limit_rules,
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

        # LLM Tool：在 __init__ 中以闭包方式注册。
        # star_manager 会对所有 handler（包括 llm_tool）执行
        # functools.partial(handler, star_cls) 绑定插件实例，
        # 因此闭包的首参数 _self 用于接收 star_cls（与 setu_instance 是同一对象）。
        # _self 不出现在 docstring Args 中，不会被加入 LLM 工具参数 schema。

        @filter.llm_tool(name=SETU_TOOL_NAME)
        async def get_setu(
            _self,
            event: AstrMessageEvent,
            tag: str = "",
            num: int = 1,
            r18: int = 0,
        ):
            '''获取 Pixiv 色图并发送到当前会话。

            Args:
                tag(string): 标签关键词，多个用竖线 | 表示或关系，如 萝莉|少女。可选。
                num(int): 获取数量，1 到 20，默认 1。可选。
                r18(int): 0 为非 R18，1 为 R18，2 为混合，默认 0。可选。
            '''
            if not _self.config.tool_enabled:
                yield event.plain_result("色图工具已被禁用。")
                return
            params = build_params_from_tool_args(tag=tag, num=num, r18=r18)
            async for result in _self._handle_setu(event, params):
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

    async def initialize(self) -> None:
        """插件激活后从 KV 加载已持久化的分会话自定义限流规则。"""
        await self._load_custom_limit_rules()

    # ------------------------------------------------------------------
    # KV 持久化：分会话自定义限流规则
    # ------------------------------------------------------------------

    _LIMIT_KV_KEY = "setu_custom_limits"

    async def _load_custom_limit_rules(self) -> None:
        """从 KV 加载全部自定义限流规则到内存。"""
        data = await self.get_kv_data(self._LIMIT_KV_KEY, {})
        if not isinstance(data, dict):
            return
        for umo, rules_data in data.items():
            if not isinstance(rules_data, list):
                continue
            rules = []
            for d in rules_data:
                if isinstance(d, dict):
                    try:
                        rules.append(LimitRule.from_dict(d))
                    except (ValueError, KeyError, TypeError):
                        pass
            if rules:
                self.rate_limiter.set_umo_custom_rules(umo, rules)
        if data:
            logger.info("%s 已加载 %d 个会话的自定义限流规则", PLUGIN_NAME, len(data))

    async def _save_custom_limit_rules(self) -> None:
        """将全部自定义限流规则持久化到 KV。"""
        data = self.rate_limiter.get_all_custom_rules_for_persist()
        await self.put_kv_data(self._LIMIT_KV_KEY, data)

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
        """为一条 setu 构造图片消息段（默认纯图片，可选附带元信息文本）。

        所有图片均由 image_cache 的 httpx 客户端下载（携带 Referer 头绕过
        Pixiv 防盗链），绝不使用 Comp.Image.fromURL（AstrBot 内置下载器
        不发送 Referer，遇到 i.pximg.net 会 403）。
        """
        url = SetuApiClient.pick_url(item)
        image_comp = None

        # 路径一：缓存启用时优先走缓存（命中或下载后存入缓存）
        if self.config.cache_enabled:
            local = await self.image_cache.get_local_path(item, size)
            if local:
                try:
                    image_comp = Comp.Image.fromFileSystem(local)
                except Exception:  # noqa: BLE001 - 本地图异常时回退临时下载
                    image_comp = None

        # 路径二：缓存禁用或缓存失败，下载到临时文件
        if image_comp is None and url:
            temp_path = await self.image_cache.download_to_temp(url)
            if temp_path:
                try:
                    image_comp = Comp.Image.fromFileSystem(temp_path)
                except Exception:  # noqa: BLE001
                    image_comp = None

        if image_comp is None:
            return None

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
    # 入口一：触发词（无前缀，直接发图，不走 LLM）
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> AsyncIterator:
        """拦截全部消息，匹配触发词后直接发图。未命中则放行。"""
        msg = (event.message_str or "").strip()
        if not msg:
            return
        matched = self.trigger.match(msg)
        if not matched:
            return
        _word, rest = matched
        parsed = Trigger.parse_args(rest)
        params = parsed.to_api_params()
        async for result in self._handle_setu(event, params):
            yield result

    # ------------------------------------------------------------------
    # 入口二：管理员指令 /setu on|off|status|limit|cache
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
        g_windows = self.rate_limiter.global_status()
        umo_windows = self.rate_limiter.umo_status(umo)
        cache = self.image_cache.stats() if self.config.cache_enabled else {"count": 0}

        lines = [f"本会话色图功能：{'开启' if enabled else '关闭'}"]
        if g_windows:
            lines.append("全局限流：" + ", ".join(str(w) for w in g_windows))
        if umo_windows:
            lines.append("分会话限流：" + ", ".join(str(w) for w in umo_windows))
        lines.append(f"缓存图片数：{cache.get('count', 0)}")
        yield event.plain_result("\n".join(lines))

    @setu_admin.command("limit")
    async def setu_limit(
        self,
        event: AstrMessageEvent,
        action: str = "",
        window: str = "",
        max_count: int = 0,
    ) -> AsyncIterator:
        """管理本会话的自定义限流规则。

        用法：
          /setu limit              查看当前所有限流规则
          /setu limit add 12h 3    添加规则：12小时最多3张
          /setu limit del 12h      删除指定窗口的规则
          /setu limit reset        清空全部自定义规则

        时间窗口格式：30s/5m/12h/7d（秒/分/时/天）
        """
        if not await self._check_admin(event):
            return
        umo = event.unified_msg_origin

        if action in ("", "list"):
            # 查看全部规则
            g_windows = self.rate_limiter.global_status()
            umo_windows = self.rate_limiter.umo_status(umo)
            customs = self.rate_limiter.get_umo_custom_rules(umo)
            lines = ["=== 限流规则 ==="]
            if g_windows:
                lines.append("全局：" + ", ".join(str(w) for w in g_windows))
            if umo_windows:
                lines.append("本会话：" + ", ".join(str(w) for w in umo_windows))
            if customs:
                lines.append(
                    "自定义规则："
                    + ", ".join(f"{r.label}:{r.max_count}" for r in customs),
                )
            else:
                lines.append("自定义规则：无")
            lines.append("")
            lines.append("用法：/setu limit add <窗口> <数量> | del <窗口> | reset")
            yield event.plain_result("\n".join(lines))

        elif action == "add":
            if not window or max_count <= 0:
                yield event.plain_result(
                    "用法：/setu limit add <窗口> <数量>\n"
                    "示例：/setu limit add 12h 3（12小时最多3张）\n"
                    "窗口格式：30s/5m/12h/7d",
                )
                return
            try:
                rule = LimitRule(
                    window_seconds=parse_window(window),
                    max_count=max_count,
                )
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return
            self.rate_limiter.add_umo_rule(umo, rule)
            await self._save_custom_limit_rules()
            yield event.plain_result(
                f"已添加限流规则：{rule.label} 最多 {rule.max_count} 张",
            )

        elif action == "del":
            if not window:
                yield event.plain_result("用法：/setu limit del <窗口>\n如 /setu limit del 12h")
                return
            try:
                window_sec = parse_window(window)
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return
            if self.rate_limiter.remove_umo_rule(umo, window_sec):
                await self._save_custom_limit_rules()
                yield event.plain_result(f"已删除 {window} 窗口的限流规则")
            else:
                yield event.plain_result(f"未找到 {window} 窗口的自定义规则")

        elif action == "reset":
            count = self.rate_limiter.clear_umo_rules(umo)
            await self._save_custom_limit_rules()
            yield event.plain_result(f"已清空 {count} 条自定义限流规则")

        else:
            yield event.plain_result(
                "未知操作。可用：list / add / del / reset\n"
                "示例：/setu limit add 12h 3",
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
