---
name: codex-converter
description: >-
  Convert coding-agent sessions between Claude Code and Codex (both
  directions). Use when the user wants to migrate an in-progress
  conversation from one agent to the other (e.g. after hitting a quota
  limit on one side), or when they ask to "convert this Codex session to
  Claude" / "convert this Claude session to Codex" / "import a Claude
  session into Codex". Produces a rollout JSONL + registers it in Codex's
  state DB, or writes a Claude-compatible JSONL into
  ~/.claude/projects/<slug>/, with a "From Claude" / "From Codex" title
  prefix that includes the original session name and a local timestamp.
  Cross-platform - Windows, Linux, macOS.
---

# Codex Converter Skill

Convert coding-agent sessions between **Claude Code** and **Codex** so you can
switch frameworks mid-task without losing context — typically because one
side's quota ran out.

Both directions are supported:

- **Claude → Codex**: reads `~/.claude/projects/<slug>/<id>.jsonl`, writes a
  Codex `rollout-*.jsonl` under `~/.codex/sessions/YYYY/MM/DD/`, and registers
  the session in `~/.codex/state_5.sqlite` so Codex lists it in history.
- **Codex → Claude**: reads `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`,
  writes a Claude-compatible JSONL into `~/.claude/projects/<slug>/`, and
  injects a `migration_boundary` system message so Claude's transcript shows
  the origin clearly.

## When to use

The user says any of:

- "Convert this Claude session to Codex" / "把这次 Claude 会话转给 Codex"
- "Convert my Codex session to Claude" / "把 Codex 会话转成 Claude"
- "I hit my Codex quota, switch me to Claude with the same context"
- "Import this Claude rollout into Codex"
- "Migrate the session to the other agent"

## Migrated session title

Both directions produce a title with the pattern:

```
From Claude - <original session name> - YYYY-MM-DD HH:MM:SS
From Codex  - <original session name> - YYYY-MM-DD HH:MM:SS
```

The original session name comes from:

- Claude → Codex: the first non-synthetic user message in the Claude session.
- Codex → Claude: the `title` column in Codex's `threads` table (looked up by
  session id), falling back to the first user message.

Timestamps are converted to **local time** for readability.

## How to run

Everything goes through one entry point: `scripts/converter.py`.

```bash
# Show detected paths and DB health
python scripts/converter.py status

# List sessions on either side
python scripts/converter.py claude-to-codex list
python scripts/converter.py codex-to-claude list

# Preview without converting
python scripts/converter.py claude-to-codex preview <path-to-claude-jsonl>
python scripts/converter.py codex-to-claude preview <path-to-codex-rollout>

# Convert a single session
python scripts/converter.py claude-to-codex convert <path-to-claude-jsonl>
python scripts/converter.py codex-to-claude convert <path-to-codex-rollout>

# Batch convert
python scripts/converter.py claude-to-codex batch
python scripts/converter.py codex-to-claude convert --date 2026-05-01 --end-date 2026-05-24

# Import any JSONL into Codex (replaces the old codex-import.sh)
python scripts/converter.py import <path-to-jsonl> --title "From Claude - bug fix"
```

Aliases: `c2x` for `claude-to-codex`, `x2c` for `codex-to-claude`.

## Environment overrides

Both `~/.claude` and `~/.codex` locations can be overridden:

- `CLAUDE_HOME` — defaults to `~/.claude`
- `CODEX_HOME` — defaults to `~/.codex`
- `CLAUDE_PROJECT_DIR` — when set, the Codex→Claude converter writes into
  this exact project folder instead of auto-detecting by cwd.

## What Claude should do when invoked

1. Run `python scripts/converter.py status` first to confirm both home
   directories exist and Python is available.
2. If the user gave you a path, convert that single session.
3. If the user gave you a date range or said "all sessions", use batch mode.
4. After conversion, tell the user **the exact target path** returned by the
   converter so they can open the project in the other tool and verify.
5. If the user's first user message looks synthetic (system prompt dump,
   environment_context wrapper, etc.) the converter already filters those
   out — no extra handling needed.

## Cross-platform notes

- All paths use `pathlib.Path`; backslashes on Windows and forward slashes
  on POSIX both work as input.
- `stdout` / `stderr` are reconfigured to UTF-8 at import time so Chinese
  characters in titles print cleanly on Windows cmd.
- SQLite connections open with `mode=rwc` URI so Windows drive letters and
  backslashes are handled consistently.
- Python 3.7+ supported.

## Files

```
scripts/
├── converter.py            # Unified CLI entry point
├── common.py               # Shared paths, title formatting, UTF-8 setup
├── claude_to_codex.py      # Claude JSONL -> Codex rollout JSONL + DB row
├── codex_to_claude.py      # Codex rollout JSONL -> Claude JSONL
└── codex_import.py         # Cross-platform replacement for codex-import.sh
```

## Limitations

- Tool-use and tool-result entries are preserved but truncated
  (input ≤ 4000 chars, output ≤ 12000 chars) to keep the rollout file
  readable.
- Claude's "thinking" blocks are kept but truncated to 500 chars each.
- Compacted history from Codex is preserved as a `migration_compacted`
  system entry, showing the last 5 items.
- If the Codex `state_5.sqlite` doesn't exist, the Claude→Codex converter
  still writes the rollout file but warns that it couldn't register the
  thread. Run Codex once to create the DB, then re-run.
