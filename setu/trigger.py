"""触发词匹配与命令行参数解析。

需求 1：支持自定义触发词，且不带指令前缀也能触发。本模块负责两件事：

1. :meth:`Trigger.match` —— 判断消息开头是否命中任一触发词，返回剩余文本。
2. :meth:`Trigger.parse_args` —— 把剩余文本解析为可覆盖 API 参数的结构，
   支持 ``-n 3``、``-r18 2``、``--size regular`` 等开关，
   未带开关的词作为 ``tag``（标签），空格分隔。

例如 ``色图 -n 2 白丝 黑丝`` 经匹配去掉 ``色图`` 后，剩余 ``-n 2 白丝 黑丝``
解析为 ``{"num": 2, "tag": ["白丝", "黑丝"]}``。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 已知的可覆盖参数及其短/长开关名与类型
# num -> int, r18 -> int, size -> str, keyword -> str, uid -> int
_FLAG_INT = {"-n", "--num", "-r18", "--r18"}
_FLAG_STR = {"-s", "--size", "-kw", "--keyword"}
_FLAG_INT_LIST = {"-uid", "--uid"}


@dataclass
class ParsedArgs:
    """触发词命令行解析结果，键名与 Lolicon API 参数对齐。"""

    num: int | None = None
    r18: int | None = None
    size: list[str] | None = None
    keyword: str | None = None
    uid: list[int] | None = None
    tag: list[str] = field(default_factory=list)

    def to_api_params(self) -> dict:
        """转为可传入 api_client 的参数字典，仅包含非空字段。"""
        params: dict = {}
        if self.num is not None:
            params["num"] = self.num
        if self.r18 is not None:
            params["r18"] = self.r18
        if self.size:
            params["size"] = self.size
        if self.keyword:
            params["keyword"] = self.keyword
        if self.uid:
            params["uid"] = self.uid
        if self.tag:
            params["tag"] = self.tag
        return params


class Trigger:
    """触发词匹配器。按长度降序匹配，避免短词误吃长词。"""

    def __init__(self, trigger_words: list[str] | None = None):
        # 去重 + 去空格 + 按长度降序，保证优先匹配更长的触发词
        words = sorted(
            {w.strip() for w in (trigger_words or []) if w and w.strip()},
            key=len,
            reverse=True,
        )
        self._words = words

    @property
    def words(self) -> list[str]:
        return list(self._words)

    def match(self, message: str) -> tuple[str, str] | None:
        """判断 ``message`` 开头是否命中触发词。

        返回 ``(触发词, 剩余文本)``；未命中返回 ``None``。
        命中后触发词与剩余文本之间允许存在一个空格，剩余文本会做 strip。
        """
        if not message:
            return None
        text = message.lstrip()
        for word in self._words:
            if not text.startswith(word):
                continue
            rest = text[len(word) :]
            # 触发词后若紧跟一个空格，吞掉它
            if rest.startswith(" "):
                rest = rest[1:]
            return word, rest.strip()
        return None

    @staticmethod
    def parse_args(rest: str) -> ParsedArgs:
        """解析触发词之后的剩余文本为参数。

        支持的开关：
          -n/--num <int>          返回数量
          -r18/--r18 <int>        R18 等级 0/1/2
          -s/--size <spec>        图片规格，可重复
          -kw/--keyword <str>     关键字（含空格需用引号）
          -uid/--uid <int>        作者 uid，可重复
        其余裸词作为 tag 标签收集。

        用简单的空格分词，支持双引号包裹含空格的值。
        - 开关后跟的值无法解析时，仅丢弃该开关，让原词作为 tag 落地；
        - 末尾孤立的开关或未知开关（以 - 开头）不会作为 tag。
        """
        tokens = _tokenize(rest)
        args = ParsedArgs()
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            low = tok.lower()

            # 整型开关：-n/--num、-r18/--r18
            if low in _FLAG_INT:
                if i + 1 < n:
                    val = _parse_int(tokens[i + 1])
                    if val is not None:
                        if low in ("-n", "--num"):
                            args.num = val
                        else:
                            args.r18 = val
                        i += 2
                        continue
                    # 值无法解析：仅丢弃开关，让值在下一轮作为裸词处理
                    i += 1
                    continue
                # 末尾无值：丢弃开关
                i += 1
                continue

            # 字符串开关：-s/--size、-kw/--keyword
            if low in _FLAG_STR:
                if i + 1 < n:
                    val = tokens[i + 1]
                    if low in ("-s", "--size"):
                        if args.size is None:
                            args.size = []
                        args.size.append(val)
                    else:  # keyword
                        args.keyword = val
                    i += 2
                    continue
                i += 1
                continue

            # 整型列表开关：-uid/--uid
            if low in _FLAG_INT_LIST:
                if i + 1 < n:
                    val = _parse_int(tokens[i + 1])
                    if val is not None:
                        if args.uid is None:
                            args.uid = []
                        args.uid.append(val)
                        i += 2
                        continue
                    i += 1
                    continue
                i += 1
                continue

            # 裸词：未知开关（以 - 开头）丢弃，其余作为标签
            if tok and not tok.startswith("-"):
                args.tag.append(tok)
            i += 1
        return args


def _tokenize(text: str) -> list[str]:
    """简易分词：按空格切分，双引号内的内容作为一个整体。"""
    tokens: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def _parse_int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None
