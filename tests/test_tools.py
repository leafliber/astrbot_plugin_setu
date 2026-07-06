"""LLM Tool 参数构建测试。"""

from __future__ import annotations

from setu.tools import R18_VALUES, SETU_TOOL_NAME, build_params_from_tool_args


class TestBuildParamsFromToolArgs:
    def test_name_and_desc(self):
        assert SETU_TOOL_NAME == "get_setu"

    def test_tag(self):
        params = build_params_from_tool_args(tag="萝莉|少女")
        assert params == {"tag": ["萝莉|少女"]}

    def test_tag_stripped(self):
        params = build_params_from_tool_args(tag="  白丝  ")
        assert params == {"tag": ["白丝"]}

    def test_tag_empty_omitted(self):
        params = build_params_from_tool_args(tag="   ")
        assert "tag" not in params

    def test_num_clamped_low(self):
        params = build_params_from_tool_args(num=0)
        assert params["num"] == 1  # 夹到下界

    def test_num_clamped_high(self):
        params = build_params_from_tool_args(num=100)
        assert params["num"] == 20  # 夹到上界

    def test_num_invalid_omitted(self):
        params = build_params_from_tool_args(num="abc")
        assert "num" not in params

    def test_r18_valid(self):
        for r in R18_VALUES:
            assert build_params_from_tool_args(r18=r)["r18"] == r

    def test_r18_invalid_omitted(self):
        params = build_params_from_tool_args(r18=5)
        assert "r18" not in params

    def test_keyword(self):
        params = build_params_from_tool_args(keyword="原神")
        assert params["keyword"] == "原神"

    def test_uid(self):
        params = build_params_from_tool_args(uid=12345)
        assert params["uid"] == [12345]

    def test_uid_invalid_omitted(self):
        params = build_params_from_tool_args(uid="abc")
        assert "uid" not in params

    def test_combined(self):
        params = build_params_from_tool_args(tag="白丝", num=3, r18=1)
        assert params == {"tag": ["白丝"], "num": 3, "r18": 1}

    def test_all_none(self):
        assert build_params_from_tool_args() == {}
