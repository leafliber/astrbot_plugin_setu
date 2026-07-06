"""图片下载与本地缓存。

需求 5：支持图片保存到本地存储做缓存。
缓存目录遵循 AstrBot 大文件存储规范：``data/plugin_data/{plugin_name}/cache/``。
命中缓存时直接返回本地路径，由 ``Comp.Image.fromFileSystem`` 发送，
避免重复远端请求并绕过 Pixiv 防盗链。
LRU + TTL 清理策略。

所有图片下载均由本模块的 httpx 客户端执行，统一携带 ``Referer: https://www.pixiv.net/``
请求头以绕过 Pixiv 图床防盗链（403）。即使缓存禁用，也通过 :meth:`download_to_temp`
下载到临时文件后以 ``Comp.Image.fromFileSystem`` 发送，绝不使用 ``Comp.Image.fromURL``
（后者委托 AstrBot 内置下载器，不发送 Referer，遇到 i.pximg.net 会 403）。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from .config import SetuConfig

# Pixiv 图床防盗链要求的 Referer
PIXIV_REFERER = "https://www.pixiv.net/"
# 模拟浏览器 UA，部分 CDN 可能拒绝无 UA 请求
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# 临时文件保留上限
MAX_TEMP_FILES = 50


class ImageCache:
    """图片本地缓存：按 ``pid_p_size.ext`` 命名，命中即返回本地路径。"""

    def __init__(
        self,
        config: SetuConfig,
        cache_dir: Path,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._owns_client = client is None
        # 记录文件最近访问时间，用于 LRU 清理
        self._access_times: dict[str, float] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                proxy=self.config.http_proxy,
                timeout=self.config.timeout,
                follow_redirects=True,
                headers={
                    "Referer": PIXIV_REFERER,
                    "User-Agent": DEFAULT_UA,
                },
            )
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _cache_path(self, item: Mapping[str, Any], size: str) -> Path:
        pid = item.get("pid", "unknown")
        p = item.get("p", 0)
        ext = str(item.get("ext", "jpg")).lstrip(".") or "jpg"
        return self.cache_dir / f"{pid}_p{p}_{size}.{ext}"

    def _is_expired(self, fpath: Path) -> bool:
        ttl_hours = int(self.config.cache.get("ttl_hours", 168) or 0)
        if ttl_hours <= 0:
            return False  # 0 表示永不失效
        mtime = fpath.stat().st_mtime
        age = (time.time() - mtime) / 3600
        return age > ttl_hours

    async def get_local_path(self, item: Mapping[str, Any], size: str = "original") -> str:
        """返回该图片的本地路径；未命中则下载后返回。下载失败返回空串。"""
        if not self.config.cache_enabled:
            return ""
        fpath = self._cache_path(item, size)

        # 命中且未过期
        if fpath.exists() and not self._is_expired(fpath):
            self._access_times[fpath.name] = time.time()
            return str(fpath)

        url = self._extract_url(item, size)
        if not url:
            return ""

        ok = await self._download(url, fpath)
        if not ok:
            return ""
        self._access_times[fpath.name] = time.time()
        await self._maybe_cleanup()
        return str(fpath)

    async def download_to_temp(self, url: str) -> str:
        """下载图片到临时文件（不经缓存），返回路径。失败返回空串。

        用于缓存禁用或缓存下载失败时的回退路径。临时文件以 ``tmp_`` 前缀
        存放于缓存目录，由 :meth:`_cleanup_temp` 定期清理。
        """
        if not url:
            return ""
        ext = _guess_ext(url)
        tmp = self.cache_dir / f"tmp_{int(time.time() * 1000)}.{ext}"
        ok = await self._download(url, tmp)
        if not ok:
            return ""
        await self._cleanup_temp()
        return str(tmp)

    @staticmethod
    def _extract_url(item: Mapping[str, Any], size: str) -> str:
        urls = item.get("urls") or {}
        if isinstance(urls, Mapping):
            # 优先取指定 size，回退 original/regular
            for candidate in (size, "original", "regular", "small"):
                url = urls.get(candidate)
                if url:
                    return str(url)
        return ""

    async def _download(self, url: str, dest: Path) -> bool:
        client = await self._ensure_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return False
        if not resp.content:
            return False
        # 先写临时文件再重命名，避免半写文件被当作缓存命中
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)
        return True

    async def _maybe_cleanup(self) -> None:
        """超过 max_count 时按 LRU 删除最旧文件。"""
        max_count = int(self.config.cache.get("max_count", 500) or 0)
        if max_count <= 0:
            return
        files = [f for f in self.cache_dir.iterdir() if f.is_file()]
        if len(files) <= max_count:
            return
        # 按访问时间（无记录则用 mtime）升序，删除最旧的若干个
        now = time.time()
        files.sort(key=lambda f: self._access_times.get(f.name, f.stat().st_mtime))
        excess = len(files) - max_count
        for f in files[:excess]:
            try:
                f.unlink()
            except OSError:
                pass
            self._access_times.pop(f.name, None)
        _ = now  # 占位，避免未使用警告

    def stats(self) -> dict[str, int]:
        """返回缓存统计，供状态指令展示。"""
        files = [f for f in self.cache_dir.iterdir() if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        return {"count": len(files), "total_bytes": total}

    async def _cleanup_temp(self) -> None:
        """清理临时文件，保留最近 MAX_TEMP_FILES 个。"""
        tmp_files = [
            f for f in self.cache_dir.iterdir()
            if f.is_file() and f.name.startswith("tmp_")
        ]
        if len(tmp_files) <= MAX_TEMP_FILES:
            return
        tmp_files.sort(key=lambda f: f.stat().st_mtime)
        for f in tmp_files[:-MAX_TEMP_FILES]:
            try:
                f.unlink()
            except OSError:
                pass


_VALID_EXTS = {"jpg", "jpeg", "png", "gif", "webp"}


def _guess_ext(url: str) -> str:
    """从 URL 推断图片扩展名，无法识别时返回 jpg。"""
    path = url.split("?")[0].rsplit("/", 1)[-1]
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in _VALID_EXTS:
            return ext
    return "jpg"
