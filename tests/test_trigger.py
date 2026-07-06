"""触发词匹配与参数解析测试。"""

from __future__ import annotations

from setu.trigger import Trigger, _tokenize


class TestTriggerMatch:
    def test_basic_match(self):
        t = Trigger(["色图", "来点色图", "setu"])
        assert t.match("色图") == ("色图", "")
        assert t.match("色图 白丝") == ("色图", "白丝")

    def test_long_word_priority(self):
        # "来点色图" 比 "色图" 长，应优先匹配前者
        t = Trigger(["色图", "来点色图"])
        assert t.match("来点色图 白丝") == ("来点色图", "白丝")

    def test_no_prefix_match(self):
        t = Trigger(["色图"])
        assert t.match("给我来点图") is None
        assert t.match("") is None
        assert t.match("   ") is None

    def test_leading_whitespace(self):
        t = Trigger(["色图"])
        assert t.match("  色图 白丝") == ("色图", "白丝")

    def test_empty_trigger_list(self):
        assert Trigger([]).match("色图") is None
        assert Trigger(["  "]).match("色图") is None

    def test_words_property(self):
        t = Trigger(["setu", "色图"])
        # 去重 + 按长度降序（setu 长 4，色图 长 2）
        assert t.words == ["setu", "色图"]


class TestParseArgs:
    def test_no_args(self):
        args = Trigger.parse_args("")
        assert args.tag == []
        assert args.num is None
        assert args.r18 is None
        assert args.to_api_params() == {}

    def test_tags_only(self):
        args = Trigger.parse_args("白丝 黑丝")
        assert args.tag == ["白丝", "黑丝"]
        assert args.to_api_params() == {"tag": ["白丝", "黑丝"]}

    def test_num_flag(self):
        args = Trigger.parse_args("-n 3 白丝")
        assert args.num == 3
        assert args.tag == ["白丝"]
        assert args.to_api_params() == {"num": 3, "tag": ["白丝"]}

    def test_r18_flag(self):
        args = Trigger.parse_args("-r18 2")
        assert args.r18 == 2
        assert args.to_api_params() == {"r18": 2}

    def test_size_flag_repeatable(self):
        args = Trigger.parse_args("--size regular --size original")
        assert args.size == ["regular", "original"]

    def test_keyword_with_quotes(self):
        args = Trigger.parse_args('-kw "原神 启动" 白丝')
        assert args.keyword == "原神 启动"
        assert args.tag == ["白丝"]

    def test_uid_flag_repeatable(self):
        args = Trigger.parse_args("-uid 123 -uid 456")
        assert args.uid == [123, 456]

    def test_combined(self):
        # parse_args 接收的是去掉触发词后的剩余文本
        args = Trigger.parse_args("-n 2 -r18 1 白丝 黑丝")
        assert args.num == 2
        assert args.r18 == 1
        assert args.tag == ["白丝", "黑丝"]

    def test_invalid_int_ignored_as_tag(self):
        # -n 后跟非数字：-n 被跳过，"abc" 作为 tag
        args = Trigger.parse_args("-n abc 白丝")
        assert args.num is None
        assert args.tag == ["abc", "白丝"]

    def test_missing_value_at_end(self):
        # -n 在末尾无值：-n 被跳过
        args = Trigger.parse_args("白丝 -n")
        assert args.num is None
        assert args.tag == ["白丝"]


class TestTokenizer:
    def test_simple(self):
        assert _tokenize("a b c") == ["a", "b", "c"]

    def test_quoted(self):
        assert _tokenize('"a b" c') == ["a b", "c"]

    def test_multiple_spaces(self):
        assert _tokenize("a   b") == ["a", "b"]
