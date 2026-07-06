"""Lolicon API 客户端测试（用 respx 打桩网络）。"""

from __future__ import annotations

import httpx
import pytest
import respx

from setu.api_client import ApiError, SetuApiClient
from setu.config import SetuConfig


@pytest.fixture
def api_config() -> SetuConfig:
    return SetuConfig.from_raw(
        {
            "api": {
                "base_url": "https://api.lolicon.app/setu/v2",
                "r18": 0,
                "num": 1,
                "size": ["original"],
                "image_proxy": "i.pixiv.re",
                "tag": ["白丝"],
            },
            "network": {"http_proxy": "", "timeout": 10},
        }
    )


@pytest.fixture
def client(api_config: SetuConfig) -> SetuApiClient:
    return SetuApiClient(api_config)


class TestBuildParams:
    def test_defaults_from_config(self, client: SetuApiClient):
        params = client.build_params()
        assert params["r18"] == 0
        assert params["num"] == 1
        assert params["size"] == ["original"]
        assert params["proxy"] == "i.pixiv.re"
        assert params["tag"] == ["白丝"]

    def test_override_takes_priority(self, client: SetuApiClient):
        params = client.build_params({"num": 5, "r18": 2, "tag": ["黑丝"]})
        assert params["num"] == 5
        assert params["r18"] == 2
        assert params["tag"] == ["黑丝"]

    def test_override_none_ignored(self, client: SetuApiClient):
        params = client.build_params({"num": None, "r18": 1})
        assert params["num"] == 1  # 配置默认
        assert params["r18"] == 1

    def test_empty_optionals_omitted(self):
        cfg = SetuConfig.from_raw({})
        c = SetuApiClient(cfg)
        params = c.build_params()
        # keyword/tag/uid/aspectRatio 为空时不发送
        assert "keyword" not in params
        assert "tag" not in params
        assert "uid" not in params
        assert "aspectRatio" not in params
        assert "excludeAI" not in params

    def test_excludeAI_and_dsc(self):
        cfg = SetuConfig.from_raw({"api": {"excludeAI": True, "dsc": True}})
        c = SetuApiClient(cfg)
        params = c.build_params()
        assert params["excludeAI"] is True
        assert params["dsc"] is True

    def test_dates_included_when_positive(self):
        cfg = SetuConfig.from_raw({"api": {"dateAfter": 1700000000000}})
        c = SetuApiClient(cfg)
        params = c.build_params()
        assert params["dateAfter"] == 1700000000000

    def test_dates_omitted_when_zero(self, client: SetuApiClient):
        params = client.build_params()
        assert "dateAfter" not in params
        assert "dateBefore" not in params


class TestFetch:
    @respx.mock
    async def test_success(self, client: SetuApiClient):
        respx.post("https://api.lolicon.app/setu/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "error": "",
                    "data": [
                        {
                            "pid": 90551655,
                            "p": 0,
                            "uid": 43454954,
                            "title": "title",
                            "author": "author",
                            "r18": False,
                            "width": 1000,
                            "height": 1500,
                            "tags": ["白丝", "萝莉"],
                            "ext": "jpg",
                            "aiType": 1,
                            "uploadDate": 1623662759000,
                            "urls": {"original": "https://i.pixiv.re/img-original/x.jpg"},
                        }
                    ],
                },
            )
        )
        data = await client.fetch()
        assert len(data) == 1
        assert data[0]["pid"] == 90551655
        assert data[0]["urls"]["original"] == "https://i.pixiv.re/img-original/x.jpg"

    @respx.mock
    async def test_api_error_field(self, client: SetuApiClient):
        respx.post("https://api.lolicon.app/setu/v2").mock(
            return_value=httpx.Response(200, json={"error": "出错了", "data": []})
        )
        with pytest.raises(ApiError, match="出错了"):
            await client.fetch()

    @respx.mock
    async def test_http_error(self, client: SetuApiClient):
        respx.post("https://api.lolicon.app/setu/v2").mock(return_value=httpx.Response(500))
        with pytest.raises(ApiError, match="请求 Lolicon API 失败"):
            await client.fetch()

    @respx.mock
    async def test_non_json_response(self, client: SetuApiClient):
        respx.post("https://api.lolicon.app/setu/v2").mock(
            return_value=httpx.Response(200, text="not json")
        )
        with pytest.raises(ApiError, match="非 JSON"):
            await client.fetch()

    @respx.mock
    async def test_malformed_data(self, client: SetuApiClient):
        respx.post("https://api.lolicon.app/setu/v2").mock(
            return_value=httpx.Response(200, json={"error": "", "data": "not-a-list"})
        )
        with pytest.raises(ApiError, match="格式异常"):
            await client.fetch()

    @respx.mock
    async def test_mirrored_base_url(self):
        """镜像站域名切换：使用自定义 base_url。"""
        cfg = SetuConfig.from_raw({"api": {"base_url": "https://mirror.example.com/v2"}})
        c = SetuApiClient(cfg)
        respx.post("https://mirror.example.com/v2").mock(
            return_value=httpx.Response(200, json={"error": "", "data": []})
        )
        data = await c.fetch()
        assert data == []


class TestPickUrl:
    def test_prefer_original(self):
        item = {"urls": {"original": "o", "regular": "r"}}
        assert SetuApiClient.pick_url(item) == "o"

    def test_fallback_regular(self):
        item = {"urls": {"regular": "r"}}
        assert SetuApiClient.pick_url(item) == "r"

    def test_empty_urls(self):
        assert SetuApiClient.pick_url({"urls": {}}) == ""
        assert SetuApiClient.pick_url({}) == ""
        assert SetuApiClient.pick_url({"urls": "bad"}) == ""
