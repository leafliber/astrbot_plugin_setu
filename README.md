# astrbot_plugin_setu

基于 [Lolicon API v2](https://docs.api.lolicon.app/#/setu?id=api-v2) 的 AstrBot 色图插件。支持自定义触发词（无需指令前缀）、LLM 工具调用、API 全参数调整、分会话开关限流、本地图片缓存、代理与镜像站切换。

## 功能特性

- **触发词触发**：发送以配置触发词开头的消息即可获取色图，无需 `/` 前缀；支持命令行式参数（`-n`、`-r18`、`--size`、`-kw`、`-uid`）与标签。
- **LLM 工具**：注册 `get_setu` 工具供大模型在对话中按需调用。
- **参数调整**：对齐 Lolicon API v2 全部参数（r18、num、tag、size、proxy、keyword、uid、dateAfter/Before、dsc、excludeAI、aspectRatio）。
- **分会话开关**：按 `unified_msg_origin` 独立开启/关闭，状态持久化。
- **限流**：全局 + 分会话两级滑动窗口，互不影响。
- **本地缓存**：图片下载到 `data/plugin_data/astrbot_plugin_setu/cache/`，命中缓存直接发送本地文件，LRU + TTL 自动清理。
- **代理**：`http_proxy` 同时作用于 API 请求与图片下载。
- **镜像站**：`api.base_url` 切换 API 端点，`api.image_proxy` 切换图片反代域名。

## 安装

将本插件放入 AstrBot 的 `data/plugins/` 目录，AstrBot 会自动通过 `requirements.txt` 安装 `httpx` 依赖。重启或在 WebUI 插件管理处重载即可。

## 使用

### 触发词

默认触发词为 `色图`、`来点色图`、`setu`，可在 WebUI 配置页修改。示例：

```
色图
色图 -n 2 白丝 黑丝
色图 -r18 2 -s regular
来点色图 -kw 原神
```

默认只发送图片本身，不附带文字元信息。如需显示标题/作者/pid/标签，在配置页开启 **API 设置 → 显示图片元信息**（`show_metadata`）。

参数说明：

| 参数 | 说明 |
|---|---|
| `-n` / `--num` | 返回数量（1–20） |
| `-r18` / `--r18` | R18 等级 0/1/2 |
| `-s` / `--size` | 图片规格，可重复 |
| `-kw` / `--keyword` | 关键字（含空格用双引号） |
| `-uid` / `--uid` | 作者 uid，可重复 |
| 裸词 | 作为 tag 标签 |

### 管理指令

| 指令 | 说明 | 权限 |
|---|---|---|
| `/setu on` | 开启当前会话色图功能 | 管理员（可配置） |
| `/setu off` | 关闭当前会话色图功能 | 管理员（可配置） |
| `/setu status` | 查看会话状态与限流余量 | 所有人 |
| `/setu cache` | 清理本地图片缓存 | 管理员（可配置） |

`session.admin_only_toggle` 为 `true` 时，开关与清理指令仅管理员可用。

### LLM 工具

`tool.enabled` 为 `true` 时，大模型可在对话中调用 `get_setu(tag, num, r18)` 工具主动发送色图。

## 配置

全部配置项在 WebUI 插件配置页可视化编辑，对应 `_conf_schema.json`。主要分组：

- **trigger_words**：触发词列表
- **api**：API 端点、默认参数、图片反代域名
- **network**：HTTP 代理、超时
- **cache**：缓存开关、最大数量、有效期
- **rate_limit**：全局/分会话每分钟上限
- **session**：默认开关、仅管理员可开关
- **tool**：LLM 工具开关

## 开发

使用 uv 管理 Python 3.12 虚拟环境：

```bash
uv sync                    # 创建 .venv 并安装依赖
uv run pytest              # 运行测试（85 项）
uv run ruff format setu/   # 格式化
uv run ruff check setu/    # 静态检查
```

目录结构：业务代码全部在 `setu/`，测试在 `tests/`，入口 `main.py` 为薄封装。

## 目录结构

```
astrbot_plugin_setu/
├── main.py              # 入口
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 schema
├── requirements.txt     # 运行时依赖
├── pyproject.toml       # uv + ruff 配置
├── setu/                # 业务代码
│   ├── plugin.py        # Star 子类，装配各模块
│   ├── config.py        # 配置解析
│   ├── api_client.py    # Lolicon API 客户端
│   ├── image_cache.py   # 图片下载与缓存
│   ├── rate_limiter.py  # 限流
│   ├── session_manager.py
│   ├── trigger.py       # 触发词与参数解析
│   └── tools.py         # LLM Tool 参数构建
└── tests/               # 单元测试
```

## 许可证

AGPL-3.0
