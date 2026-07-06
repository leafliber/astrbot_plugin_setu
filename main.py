"""AstrBot 插件入口。

AstrBot 通过导入本文件发现插件：取 ``main.py`` 中 ``Star`` 的子类作为插件类。
本文件保持极简，仅做注册与委托，业务逻辑全部在 ``setu/`` 包内。
"""

from __future__ import annotations

from astrbot.api.star import register

from setu.plugin import SetuPlugin


@register(
    "astrbot_plugin_setu",
    "cassia",
    "基于 Lolicon API v2 的色图插件",
    "1.0.0",
    "https://github.com/cassia/astrbot_plugin_setu",
)
class Main(SetuPlugin):
    """插件入口类，逻辑由 :class:`setu.plugin.SetuPlugin` 提供。"""

    pass
