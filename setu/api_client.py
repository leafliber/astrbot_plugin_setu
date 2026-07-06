"""Lolicon API v2 客户端。

需求 3：支持 API 中各种参数的调整。
需求 7：支持更换镜像站域名（base_url）与图片反代域名（image_proxy / API 的 proxy 参数）。

参数来源优先级：调用方传入 > 配置默认值 > API 默认值。
使用 httpx 异步客户端，支持 HTTP 代理（需求 6）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .config import SetuConfig

# API v2 默认 endpoint
DEFAULT_BASE_URL = "https://api.lolicon.app/setu/v2"
# Pixiv 原始域名（当 image_proxy 为假值时由 API 返回）
PIXIV_RAW_HOST = "i.pximg.net"


class ApiError(Exception):
    """Lolicon API 返回 error 或网络异常时的错误。"""


class SetuApiClient:
    """Lolicon API v2 客户端，封装参数构建、请求与响应解析。"""

    def __init__(
        self,
        config: SetuConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        # 允许外部注入 client，便于测试与共享连接池
        self._client = client
        self._owns_client = client is None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                proxy=self.config.http_proxy,
                timeout=self.config.timeout,
            )
        return self._client

    async def close(self) -> None:
        """关闭自建的 httpx 客户端（外部注入的不关闭）。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_params(self, override: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """合并配置默认值与调用方覆盖参数，构造 API 请求体。

        ``override`` 的键名与 Lolicon API 参数对齐。空值（0/空串/空列表）策略：
        - 显式传入的覆盖配置默认值；
        - 但 ``num``/``r18`` 这类有意义的 0 值需保留；
        - 为避免发送无意义字段，空的列表/字符串会被剔除。
        """
        api_cfg = self.config.api
        params: dict[str, Any] = {
            "r18": api_cfg.get("r18", 0),
            "num": api_cfg.get("num", 1),
            "size": list(api_cfg.get("size", ["original"]) or ["original"]),
        }

        # proxy 参数：API 的 proxy 字段，由 image_proxy 提供
        image_proxy = str(api_cfg.get("image_proxy", "") or "").strip()
        # 空字符串为假值 -> 返回原始 i.pximg.net，按 API 语义传空字符串即可
        params["proxy"] = image_proxy

        # 可选参数，仅在配置非空时加入
        self._maybe_set(params, "keyword", api_cfg.get("keyword", ""))
        self._maybe_set(params, "tag", api_cfg.get("tag", []))
        self._maybe_set(params, "uid", api_cfg.get("uid", []))
        if api_cfg.get("excludeAI"):
            params["excludeAI"] = True
        if api_cfg.get("dsc"):
            params["dsc"] = True
        self._maybe_set(params, "aspectRatio", api_cfg.get("aspectRatio", ""))
        if int(api_cfg.get("dateAfter", 0) or 0) > 0:
            params["dateAfter"] = int(api_cfg["dateAfter"])
        if int(api_cfg.get("dateBefore", 0) or 0) > 0:
            params["dateBefore"] = int(api_cfg["dateBefore"])

        # 调用方覆盖
        if override:
            for key, val in override.items():
                if val is None:
                    continue
                params[key] = val

        # tag 支持 "a|b" 字符串形式或列表形式；统一交给 API 处理
        return params

    async def fetch(self, override: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """请求 API 并返回 setu 列表（``data`` 数组）。"""
        params = self.build_params(override)
        url = self.config.base_url
        client = await self._ensure_client()
        try:
            resp = await client.post(url, json=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ApiError(f"请求 Lolicon API 失败: {exc}") from exc

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ApiError("Lolicon API 返回非 JSON 数据") from exc

        error = payload.get("error")
        if error:
            raise ApiError(f"Lolicon API 错误: {error}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise ApiError("Lolicon API 响应格式异常：缺少 data 数组")
        return data

    @staticmethod
    def pick_url(item: dict[str, Any]) -> str:
        """从一条 setu 中取出优先使用的图片 URL。

        优先级：original > regular > 任一可用 size。找不到返回空串。
        """
        urls = item.get("urls") or {}
        if not isinstance(urls, Mapping):
            return ""
        for size in ("original", "regular", "small", "thumb", "mini"):
            url = urls.get(size)
            if url:
                return str(url)
        return ""

    @staticmethod
    def _maybe_set(params: dict[str, Any], key: str, val: Any) -> None:
        """仅当 val 非空时写入 params。"""
        if val is None:
            return
        if isinstance(val, str) and not val.strip():
            return
        if isinstance(val, (list, tuple, dict)) and len(val) == 0:
            return
        params[key] = val
