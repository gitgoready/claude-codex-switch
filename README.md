# Claude-Codex Switch

[![GitHub stars](https://img.shields.io/github/stars/gitgoready/claude-codex-switch?style=social)](https://github.com/gitgoready/claude-codex-switch)
[![GitHub Repo](https://img.shields.io/badge/GitHub-gitgoready%2Fclaude--codex--switch-blue)](https://github.com/gitgoready/claude-codex-switch)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows | Linux | macOS](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#cross-platform-notes)
[![Python 3.7+](https://img.shields.io/badge/Python-3.7%2B-blue)](https://www.python.org/)

> ⭐ **If this tool saved your session, please [star the repo](https://github.com/gitgoready/claude-codex-switch/stargazers)!** ⭐

[English](README.md) · [中文](README.zh.md)

## Demo

> 🎬 **Screen recording coming soon** - a 30-60s GIF showing a Claude -> Codex
> conversion end-to-end, including the `From Claude - <original> - <timestamp>`
> title appearing in Codex's history list.
>
> To add your own: record a GIF, save as `docs/demo.gif`, then replace this
> block with `![Demo](docs/demo.gif)`.

Convert coding-agent sessions between **Claude Code** and **Codex** so you can
switch frameworks mid-task without losing context - typically because one
side's quota ran out and you want to continue the same conversation in the
other tool.

The converter ships as a **Claude Code Skill** (a folder with a `SKILL.md`
that tells Claude how to run it) plus a **standalone Python CLI** that works
on Windows, Linux, and macOS.

## Features

- **Bidirectional**: Claude -> Codex and Codex -> Claude.
- **Cross-platform**: pure Python 3.7+, no shell scripts. Windows, Linux,
  macOS all work out of the box.
- **Smart session naming**: every converted session gets a title like
  `From Claude - <original session name> - 2026-07-17 14:30:05` so you can
  tell at a glance where it came from and when.
- **Codex history registration**: when converting to Codex, the session is
  inserted into `~/.codex/state_5.sqlite` so Codex's UI lists it
  immediately - no need to wait for a scan.
- **Tool calls preserved**: `tool_use` / `tool_result` entries survive the
  conversion (truncated to keep the file readable).
- **Compacted history preserved**: Codex's compacted summaries are stored as
  a `migration_compacted` system message on the Claude side.
- **Synthetic-message filtering**: Claude's `<ide_opened_file>`,
  `<environment_context>`, `<local-command>` and similar wrappers are
  stripped so the first-user-message heuristic picks a real prompt.

## Quick start

### 1. Install as a Claude Code Skill (recommended)

Copy or symlink this folder to your Claude skills directory:

```bash
# Linux / macOS
mkdir -p ~/.claude/skills
ln -s /path/to/claude-codex-switch ~/.claude/skills/claude-codex-switch

# Windows (PowerShell, admin shell)
New-Item -ItemType Directory -Path "$env:USERPROFILE\.claude\skills" -Force
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\claude-codex-switch" -Target "D:\path\to\claude-codex-switch"
```

Then in any Claude Code session you can say things like:

> Convert my current Claude session to Codex.

and Claude will run the converter for you.

### 2. Using from Codex

Codex doesn't have a skill-discovery system like Claude Code, so you can't
"install" this tool into Codex and invoke it by natural language. Instead,
Codex users have two options:

**Option A - Run the CLI from a terminal** (before or after a Codex session):

```bash
# List your Codex sessions
python scripts/converter.py codex-to-claude list

# Convert a Codex session to Claude
python scripts/converter.py codex-to-claude convert ~/.codex/sessions/2026/07/17/rollout-*.jsonl
```

Then open Claude Code in the target project - the migrated session appears
in history with a `From Codex - <original> - <timestamp>` title.

**Option B - Ask Codex to run the converter for you** (Codex can execute
shell commands). In a Codex session, say:

> Run `python /path/to/claude-codex-switch/scripts/converter.py codex-to-claude convert <session-path>`

Codex will execute the command and report the result. This is handy when
you're already in a Codex session and want to hand off to Claude Code
because your Codex quota ran out.

> **Tip:** To make Codex aware of the converter without repeating the path,
> add a one-line note to your project's `AGENTS.md` (Codex's equivalent of
> `CLAUDE.md`):
> ```
> Session conversion tool: python /path/to/claude-codex-switch/scripts/converter.py
> ```

### 3. Run directly as a Python CLI

```bash
git clone https://github.com/gitgoready/claude-codex-switch.git
cd claude-codex-switch

# Check that paths are detected correctly
python scripts/converter.py status
```

Sample output on Windows:

```
[i] Claude-Codex Switch status
  CLAUDE_HOME      : C:\Users\<username>\.claude
  Claude projects  : C:\Users\<username>\.claude\projects
  CODEX_HOME       : C:\Users\<username>\.codex
  Codex sessions   : C:\Users\<username>\.codex\sessions
  Codex state DB   : C:\Users\<username>\.codex\state_5.sqlite (exists=True)
  Python           : 3.7.4 on win32
```

## Usage

### List sessions

```bash
python scripts/converter.py claude-to-codex list
python scripts/converter.py codex-to-claude list
```

### Preview a session without converting

```bash
python scripts/converter.py claude-to-codex preview ~/.claude/projects/<slug>/<id>.jsonl
python scripts/converter.py codex-to-claude preview ~/.codex/sessions/2026/07/17/rollout-*.jsonl
```

### Convert a single session

```bash
# Claude -> Codex
python scripts/converter.py claude-to-codex convert ~/.claude/projects/<slug>/<id>.jsonl

# Codex -> Claude
python scripts/converter.py codex-to-claude convert ~/.codex/sessions/2026/07/17/rollout-*.jsonl
```

Optional flags:

- `--cwd <path>` (Claude->Codex): override the cwd recorded in the Codex
  rollout.
- `--no-register` (Claude->Codex): write the rollout JSONL but skip
  inserting into the Codex state DB.
- `--project-dir <path>` (Codex->Claude): write into this specific Claude
  project folder instead of auto-detecting by cwd.
- `--project-slug <name>` (Codex->Claude): override the `slug` field stored
  in each Claude entry.

### Batch convert

```bash
# All Claude sessions -> Codex
python scripts/converter.py claude-to-codex batch

# Codex sessions in a date range -> Claude
python scripts/converter.py codex-to-claude convert --date 2026-05-01 --end-date 2026-05-24
```

### Import any JSONL into Codex (replaces `codex-import.sh`)

```bash
python scripts/converter.py import ~/.claude/projects/<slug>/<id>.jsonl --title "From Claude - bug fix"
```

This is the cross-platform replacement for the original Linux-only
`codex-import.sh` shell script.

### Aliases

- `c2x` = `claude-to-codex`
- `x2c` = `codex-to-claude`

```bash
python scripts/converter.py c2x list
python scripts/converter.py x2c convert <path>
```

## How session titles are built

Both directions produce a title with this shape:

```
From Claude - <original session name> - YYYY-MM-DD HH:MM:SS
From Codex  - <original session name> - YYYY-MM-DD HH:MM:SS
```

- The **original session name** is taken from the first non-synthetic user
  message on the Claude side, or from the `title` column of Codex's
  `threads` table on the Codex side (falling back to the first user
  message).
- The **timestamp** is converted to local time for readability.
- The total title length is capped at ~200 characters so it fits in
  Codex's `thread.title` column.

When converting **Codex -> Claude**, the title also appears inside a
`migration_boundary` system entry at the top of the Claude JSONL, so the
Claude transcript shows the origin clearly.

## Environment variables

| Variable             | Default        | Purpose                                                        |
| -------------------- | -------------- | ------------------------------------------------------------- |
| `CLAUDE_HOME`        | `~/.claude`    | Override the Claude config directory.                         |
| `CODEX_HOME`         | `~/.codex`     | Override the Codex config directory.                          |
| `CLAUDE_PROJECT_DIR` | (auto-detect)  | Force the Codex->Claude converter to write into this folder.  |

## Project layout

```
claude-codex-switch/
├── SKILL.md                 # Skill definition Claude Code reads
├── README.md                # This file (English)
├── README.zh.md             # Chinese documentation
├── LICENSE                  # MIT
├── .gitignore
├── scripts/
│   ├── converter.py         # Unified CLI entry point
│   ├── common.py            # Shared paths, title formatting, UTF-8 setup
│   ├── claude_to_codex.py   # Claude JSONL -> Codex rollout + DB row
│   ├── codex_to_claude.py   # Codex rollout -> Claude JSONL
│   └── codex_import.py      # Cross-platform replacement for codex-import.sh
```

## Limitations

- Tool input is truncated to 4000 chars, tool output to 12000 chars. The
  truncated middle is replaced with a `[... N chars omitted ...]` marker.
- Claude "thinking" blocks are kept but truncated to 500 chars each.
- Codex compacted history is preserved as the last 5 items in a
  `migration_compacted` system entry.
- If the Codex `state_5.sqlite` doesn't exist when you convert
  Claude -> Codex, the rollout JSONL is still written but the converter
  warns it couldn't register the thread. Run Codex once to create the DB,
  then re-run.
- Claude's per-session "title" doesn't really exist - Claude Code shows
  sessions by their first user message. The migrated title therefore
  appears only inside the `migration_boundary` system entry on the Claude
  side, not in any Claude UI.

## Troubleshooting

**`UnicodeEncodeError: 'gbk' codec can't encode ...` on Windows**

The converter reconfigures stdout/stderr to UTF-8 at import time, so this
shouldn't happen. If it does, set `PYTHONIOENCODING=utf-8` in your shell
before running.

**`threads table does not exist` when converting Claude -> Codex**

You haven't run Codex yet on this machine. Open Codex once so it creates
`~/.codex/state_5.sqlite`, then re-run the converter.

**Converted session doesn't appear in Codex's history list**

The converter registers the session in the `threads` table and updates
`~/.codex/session_index.jsonl`. If Codex was already running, restart it
so it reloads the index.

**Codex -> Claude conversion wrote into the wrong project folder**

The Codex->Claude converter auto-detects the target folder by encoding the
Codex session's `cwd` field the way Claude does. If that fails, pass
`--project-dir ~/.claude/projects/<your-slug>` explicitly.

## License

MIT - see [LICENSE](LICENSE).
