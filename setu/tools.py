"""LLM Tool 的定义与参数构建。

需求 2：提供 Tool 注册给 AstrBot 的 LLM 使用。

AstrBot 的 ``@filter.llm_tool`` 装饰器需作用于 ``Star`` 子类体内的方法，
因此带装饰器的 handler 定义在 ``plugin.py`` 中（确保 AstrBot 类扫描能注册到）。
本模块集中维护工具的元信息（名称、描述）与"工具参数 → API 参数"的转换逻辑，
供 ``plugin.py`` 的 handler 复用，也便于单独测试。
"""

from __future__ import annotations

from typing import Any

# 工具名称与描述（与 plugin.py 中 @filter.llm_tool 的 name 保持一致）
SETU_TOOL_NAME = "get_setu"
SETU_TOOL_DESCRIPTION = "获取 Pixiv 色图并发送到当前会话"

# num 的合法范围（对齐 Lolicon API）
NUM_MIN = 1
NUM_MAX = 20
# r18 的合法取值
R18_VALUES = (0, 1, 2)


def build_params_from_tool_args(
    tag: str | None = None,
    num: int | None = None,
    r18: int | None = None,
    keyword: str | None = None,
    uid: int | None = None,
) -> dict[str, Any]:
    """把 LLM 工具调用传入的参数转为 Lolicon API 参数字典。

    - ``tag`` 用 ``|`` 分隔多个标签的或关系，与 API 的 tag 数组语义对齐：
      单个字符串作为一组 AND，``|`` 分隔的词在该组内 OR。
      这里直接按 ``|`` 拆成多组以复用 API 的 OR 规则更直观：将整个字符串
      作为 tag 数组的一项，由 API 解释 ``|`` 为 OR。
    - ``num`` 夹取到 [1, 20]。
    - ``r18`` 仅接受 0/1/2，其余忽略。
    """
    params: dict[str, Any] = {}

    if tag and tag.strip():
        # 保留原样字符串，API 支持 "萝莉|少女" 形式的 OR
        params["tag"] = [tag.strip()]

    if num is not None:
        try:
            n = int(num)
        except (TypeError, ValueError):
            n = None
        if n is not None:
            params["num"] = max(NUM_MIN, min(NUM_MAX, n))

    if r18 is not None:
        try:
            r = int(r18)
        except (TypeError, ValueError):
            r = None
        if r in R18_VALUES:
            params["r18"] = r

    if keyword and keyword.strip():
        params["keyword"] = keyword.strip()

    if uid is not None:
        try:
            params["uid"] = [int(uid)]
        except (TypeError, ValueError):
            pass

    return params
