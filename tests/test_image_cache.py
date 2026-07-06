"""图片缓存测试（用 tmp_path 隔离文件系统，respx 打桩下载）。"""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from setu.config import SetuConfig
from setu.image_cache import ImageCache


def make_config(**cache_overrides) -> SetuConfig:
    cache = {"enabled": True, "max_count": 5, "ttl_hours": 168}
    cache.update(cache_overrides)
    return SetuConfig.from_raw({"cache": cache, "network": {"timeout": 10}})


class TestImageCache:
    @respx.mock
    async def test_miss_then_download(self, tmp_path: Path):
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        item = {
            "pid": 123,
            "p": 0,
            "ext": "jpg",
            "urls": {"original": "https://i.pixiv.re/img.jpg"},
        }
        respx.get("https://i.pixiv.re/img.jpg").mock(
            return_value=httpx.Response(200, content=b"\xff\xd8\xff\xe0fake-jpg")
        )
        path = await cache.get_local_path(item, "original")
        assert path
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"\xff\xd8\xff\xe0fake-jpg"

    async def test_hit_skips_download(self, tmp_path: Path):
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        item = {"pid": 456, "p": 0, "ext": "png", "urls": {"original": "https://x/y.png"}}
        # 预置缓存文件
        fpath = tmp_path / "cache" / "456_p0_original.png"
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_bytes(b"cached")
        path = await cache.get_local_path(item, "original")
        assert path == str(fpath)
        assert fpath.read_bytes() == b"cached"

    async def test_cache_disabled_returns_empty(self, tmp_path: Path):
        cfg = make_config(enabled=False)
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        item = {"pid": 1, "p": 0, "ext": "jpg", "urls": {"original": "https://x/y.jpg"}}
        path = await cache.get_local_path(item, "original")
        assert path == ""

    @respx.mock
    async def test_download_failure_returns_empty(self, tmp_path: Path):
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        item = {"pid": 789, "p": 0, "ext": "jpg", "urls": {"original": "https://x/y.jpg"}}
        respx.get("https://x/y.jpg").mock(return_value=httpx.Response(404))
        path = await cache.get_local_path(item, "original")
        assert path == ""

    async def test_no_url_returns_empty(self, tmp_path: Path):
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        item = {"pid": 1, "p": 0, "ext": "jpg", "urls": {}}
        path = await cache.get_local_path(item, "original")
        assert path == ""

    @respx.mock
    async def test_ttl_expiry_redownloads(self, tmp_path: Path, monkeypatch):
        """过期文件应被重新下载覆盖。"""

        fake_now = [1000.0]
        monkeypatch.setattr("setu.image_cache.time.time", lambda: fake_now[0])

        cfg = make_config(ttl_hours=1)
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        item = {"pid": 1, "p": 0, "ext": "jpg", "urls": {"original": "https://x/y.jpg"}}
        fpath = tmp_path / "cache" / "1_p0_original.jpg"
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_bytes(b"old")
        # mtime 设为很久以前
        import os

        os.utime(fpath, (fake_now[0] - 7200, fake_now[0] - 7200))  # 2小时前

        respx.get("https://x/y.jpg").mock(return_value=httpx.Response(200, content=b"new"))
        path = await cache.get_local_path(item, "original")
        assert Path(path).read_bytes() == b"new"

    @respx.mock
    async def test_lru_cleanup(self, tmp_path: Path):
        """超出 max_count 时删除最旧文件。"""
        cfg = make_config(max_count=2)
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 预置 2 个文件
        (cache_dir / "1_p0_original.jpg").write_bytes(b"a")
        (cache_dir / "2_p0_original.jpg").write_bytes(b"b")

        # 下载第 3 个，应触发清理，总数 <= 2
        item = {"pid": 3, "p": 0, "ext": "jpg", "urls": {"original": "https://x/3.jpg"}}
        respx.get("https://x/3.jpg").mock(return_value=httpx.Response(200, content=b"c"))
        await cache.get_local_path(item, "original")
        files = list(cache_dir.iterdir())
        assert len(files) <= 2

    def test_stats(self, tmp_path: Path):
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        d = tmp_path / "cache"
        d.mkdir(parents=True, exist_ok=True)
        (d / "a.jpg").write_bytes(b"1234")
        (d / "b.jpg").write_bytes(b"5678")
        s = cache.stats()
        assert s["count"] == 2
        assert s["total_bytes"] == 8

    @respx.mock
    async def test_url_fallback_to_regular(self, tmp_path: Path):
        """size=original 缺失时回退 regular 的 URL。"""
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        item = {
            "pid": 5,
            "p": 0,
            "ext": "jpg",
            "urls": {"regular": "https://x/r.jpg"},
        }
        respx.get("https://x/r.jpg").mock(return_value=httpx.Response(200, content=b"data"))
        path = await cache.get_local_path(item, "original")
        assert Path(path).read_bytes() == b"data"

    @respx.mock
    async def test_download_to_temp_basic(self, tmp_path: Path):
        """download_to_temp 下载到临时文件并返回路径。"""
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        respx.get("https://i.pximg.net/img/1.png").mock(
            return_value=httpx.Response(200, content=b"\x89PNGfake")
        )
        path = await cache.download_to_temp("https://i.pximg.net/img/1.png")
        assert path
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"\x89PNGfake"
        assert Path(path).name.startswith("tmp_")
        assert Path(path).suffix == ".png"

    @respx.mock
    async def test_download_to_temp_sends_referer(self, tmp_path: Path):
        """下载请求必须携带 Referer: https://www.pixiv.net/ 头。"""
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        captured_headers = {}

        def handler(request):
            captured_headers.update(request.headers)
            return httpx.Response(200, content=b"ok")

        respx.get("https://i.pximg.net/x.jpg").mock(side_effect=handler)
        await cache.download_to_temp("https://i.pximg.net/x.jpg")
        assert captured_headers.get("referer") == "https://www.pixiv.net/"
        assert "user-agent" in {k.lower() for k in captured_headers}

    @respx.mock
    async def test_download_to_temp_failure(self, tmp_path: Path):
        """下载失败返回空串。"""
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        respx.get("https://x/y.jpg").mock(return_value=httpx.Response(403))
        path = await cache.download_to_temp("https://x/y.jpg")
        assert path == ""

    async def test_download_to_temp_empty_url(self, tmp_path: Path):
        """空 URL 返回空串。"""
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        path = await cache.download_to_temp("")
        assert path == ""

    @respx.mock
    async def test_cache_download_sends_referer(self, tmp_path: Path):
        """缓存路径的下载也必须携带 Referer 头。"""
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")
        captured_headers = {}

        def handler(request):
            captured_headers.update(request.headers)
            return httpx.Response(200, content=b"img")

        item = {"pid": 9, "p": 0, "ext": "jpg", "urls": {"original": "https://i.pximg.net/9.jpg"}}
        respx.get("https://i.pximg.net/9.jpg").mock(side_effect=handler)
        await cache.get_local_path(item, "original")
        assert captured_headers.get("referer") == "https://www.pixiv.net/"

    @respx.mock
    async def test_download_to_temp_ext_guess(self, tmp_path: Path):
        """URL 无扩展名时默认 jpg，有扩展名时用对应扩展名。"""
        cfg = make_config()
        cache = ImageCache(cfg, cache_dir=tmp_path / "cache")

        # 无扩展名
        respx.get("https://x/noext").mock(return_value=httpx.Response(200, content=b"a"))
        path = await cache.download_to_temp("https://x/noext")
        assert Path(path).suffix == ".jpg"

        # webp
        respx.get("https://x/img.webp").mock(return_value=httpx.Response(200, content=b"b"))
        path = await cache.download_to_temp("https://x/img.webp")
        assert Path(path).suffix == ".webp"
