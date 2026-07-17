# Claude-Codex Switch（会话互转工具）

[![GitHub stars](https://img.shields.io/github/stars/gitgoready/claude-codex-switch?style=social)](https://github.com/gitgoready/claude-codex-switch)
[![GitHub Repo](https://img.shields.io/badge/GitHub-gitgoready%2Fclaude--codex--switch-blue)](https://github.com/gitgoready/claude-codex-switch)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows | Linux | macOS](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#跨平台说明)
[![Python 3.7+](https://img.shields.io/badge/Python-3.7%2B-blue)](https://www.python.org/)

> ⭐ **如果这个工具救过你的会话，欢迎 [star 支持](https://github.com/gitgoready/claude-codex-switch/stargazers)！** ⭐

[English](README.md) · [中文](README.zh.md)

## 演示

> 🎬 **录屏即将上线** - 30-60 秒 GIF，展示 Claude -> Codex 完整转换流程，
> 包括 `From Claude - <原会话名> - <时间戳>` 标题出现在 Codex 历史列表里的效果。
>
> 自己录制：保存为 `docs/demo.gif`，然后把上面这块替换成 `![演示](docs/demo.gif)`。

在 **Claude Code** 与 **Codex** 之间互转会话，让你在一方额度用完后，
可以快速切换到另一个 agent 框架继续同一个任务，而不会丢失上下文。

本工具同时以两种形态发布：

- 一个 **Claude Code Skill**（带 `SKILL.md`，Claude 读取后能自动调用）；
- 一个 **独立 Python CLI**，Windows / Linux / macOS 都能直接运行。

## 功能特性

- **双向转换**：Claude → Codex 与 Codex → Claude 都支持。
- **跨平台**：纯 Python 3.7+，没有 shell 脚本依赖。Windows、Linux、macOS
  开箱即用。
- **会话名称优化**：转换后的会话标题统一为
  `From Claude - <原会话名> - 2026-07-17 14:30:05` 或
  `From Codex  - <原会话名> - 2026-07-17 14:30:05`，一眼就能看出会话来源
  与时间。
- **注册到 Codex 历史库**：转给 Codex 时，会话不仅写入 rollout 文件，还会
  插入 `~/.codex/state_5.sqlite` 的 `threads` 表，Codex UI 立刻就能看到。
- **保留工具调用**：`tool_use` / `tool_result` 条目会被保留（为可读性做了
  截断）。
- **保留压缩历史**：Codex 的 compacted 摘要会作为 `migration_compacted`
  系统消息写入 Claude 侧。
- **过滤合成消息**：自动剔除 Claude 的 `<ide_opened_file>`、
  `<environment_context>`、`<local-command>` 等包裹层，确保"首条用户消息"
  拿到的是真实输入。

## 快速开始

### 1. 作为 Claude Code Skill 安装（推荐）

把本目录拷贝或软链接到你的 Claude skills 目录：

```bash
# Linux / macOS
mkdir -p ~/.claude/skills
ln -s /path/to/claude-codex-switch ~/.claude/skills/claude-codex-switch

# Windows（PowerShell，管理员权限）
New-Item -ItemType Directory -Path "$env:USERPROFILE\.claude\skills" -Force
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\claude-codex-switch" -Target "D:\path\to\claude-codex-switch"
```

之后在任意 Claude Code 会话里直接说：

> 把我当前的 Claude 会话转成 Codex 的。

Claude 就会自动调用本工具完成转换。

### 2. 直接作为 Python CLI 运行

```bash
git clone https://github.com/gitgoready/claude-codex-switch.git
cd claude-codex-switch

# 先确认路径被正确识别
python scripts/converter.py status
```

Windows 上的示例输出：

```
[i] Claude-Codex Switch status
  CLAUDE_HOME      : C:\Users\<username>\.claude
  Claude projects  : C:\Users\<username>\.claude\projects
  CODEX_HOME       : C:\Users\<username>\.codex
  Codex sessions   : C:\Users\<username>\.codex\sessions
  Codex state DB   : C:\Users\<username>\.codex\state_5.sqlite (exists=True)
  Python           : 3.7.4 on win32
```

## 用法

### 列出会话

```bash
python scripts/converter.py claude-to-codex list
python scripts/converter.py codex-to-claude list
```

### 预览（不实际转换）

```bash
python scripts/converter.py claude-to-codex preview ~/.claude/projects/<slug>/<id>.jsonl
python scripts/converter.py codex-to-claude preview ~/.codex/sessions/2026/07/17/rollout-*.jsonl
```

### 转换单个会话

```bash
# Claude -> Codex
python scripts/converter.py claude-to-codex convert ~/.claude/projects/<slug>/<id>.jsonl

# Codex -> Claude
python scripts/converter.py codex-to-claude convert ~/.codex/sessions/2026/07/17/rollout-*.jsonl
```

可选参数：

- `--cwd <path>`（Claude→Codex）：覆盖写入 Codex rollout 的 cwd 字段。
- `--no-register`（Claude→Codex）：只写 rollout JSONL，不写入 Codex 状态库。
- `--project-dir <path>`（Codex→Claude）：直接写入指定的 Claude 项目目录，
  不走自动检测。
- `--project-slug <name>`（Codex→Claude）：覆盖每条 Claude 条目里的 `slug`
  字段。

### 批量转换

```bash
# 把所有 Claude 会话转成 Codex
python scripts/converter.py claude-to-codex batch

# 按日期范围把 Codex 会话转成 Claude
python scripts/converter.py codex-to-claude convert --date 2026-05-01 --end-date 2026-05-24
```

### 把任意 JSONL 导入 Codex（替代 `codex-import.sh`）

```bash
python scripts/converter.py import ~/.claude/projects/<slug>/<id>.jsonl --title "From Claude - bug fix"
```

这是原 `codex-import.sh`（仅 Linux）的跨平台替代品。

### 命令别名

- `c2x` = `claude-to-codex`
- `x2c` = `codex-to-claude`

```bash
python scripts/converter.py c2x list
python scripts/converter.py x2c convert <path>
```

## 会话标题是怎么生成的

两个方向的标题格式统一为：

```
From Claude - <原会话名> - YYYY-MM-DD HH:MM:SS
From Codex  - <原会话名> - YYYY-MM-DD HH:MM:SS
```

- **原会话名**：Claude 侧取第一条非合成的用户消息；Codex 侧优先取
  `threads` 表里的 `title` 字段，找不到时回落到首条用户消息。
- **时间戳**：统一转成本地时间，便于阅读。
- **总长度**：截断到约 200 字符，避免超出 Codex `thread.title` 列。

**Codex → Claude** 方向还会把标题写进 Claude JSONL 开头的
`migration_boundary` 系统条目里，让 Claude 转写记录里能清楚看到来源。

## 环境变量

| 变量名               | 默认值         | 作用                                                          |
| -------------------- | -------------- | ------------------------------------------------------------- |
| `CLAUDE_HOME`        | `~/.claude`    | 覆盖 Claude 配置目录。                                         |
| `CODEX_HOME`         | `~/.codex`     | 覆盖 Codex 配置目录。                                          |
| `CLAUDE_PROJECT_DIR` | （自动检测）   | 强制 Codex→Claude 转换器写入此目录。                           |

## 目录结构

```
claude-codex-switch/
├── SKILL.md                 # Claude Code 读取的 Skill 定义
├── README.md                # 英文文档
├── README.zh.md             # 中文文档（本文件）
├── LICENSE                  # MIT
├── .gitignore
├── scripts/
│   ├── converter.py         # 统一 CLI 入口
│   ├── common.py            # 共享路径、标题格式化、UTF-8 设置
│   ├── claude_to_codex.py   # Claude JSONL -> Codex rollout + DB 行
│   ├── codex_to_claude.py   # Codex rollout -> Claude JSONL
│   └── codex_import.py      # 跨平台替代 codex-import.sh
```

## 已知限制

- 工具输入截断到 4000 字符，工具输出截断到 12000 字符。被截掉的中间部分
  会替换为 `[... N chars omitted ...]` 标记。
- Claude 的 "thinking" 块每条最多保留 500 字符。
- Codex 的 compacted 历史只保留最后 5 条，写入 `migration_compacted`
  系统条目。
- 如果 Codex `state_5.sqlite` 不存在，Claude→Codex 仍然会写出 rollout
  文件，但会警告无法注册到 threads 表。请先打开一次 Codex 让它创建数据库，
  再重新运行。
- Claude 侧其实没有"会话标题"概念（UI 显示的是首条用户消息）。因此
  Codex→Claude 转换出来的标题只会出现在 `migration_boundary` 系统条目里，
  不会出现在 Claude 的 UI 中。

## 故障排查

**Windows 上报 `UnicodeEncodeError: 'gbk' codec can't encode ...`**

工具在 import 时会把 stdout/stderr 重新配置为 UTF-8，正常情况不会出现。
若仍出现，运行前在 shell 里设置 `PYTHONIOENCODING=utf-8`。

**Claude→Codex 时报 `threads table does not exist`**

这台机器还没用过 Codex。先打开一次 Codex，让它创建
`~/.codex/state_5.sqlite`，再重新运行。

**转换后 Codex 历史列表里看不到新会话**

工具会同时写 `threads` 表和 `~/.codex/session_index.jsonl`。如果 Codex 已经
在运行，重启一下让它重新加载索引。

**Codex→Claude 转换写到了错误的项目目录**

Codex→Claude 转换器会把 Codex 会话的 `cwd` 字段按 Claude 的编码方式转成
项目目录名。如果检测失败，请显式传
`--project-dir ~/.claude/projects/<your-slug>`。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
