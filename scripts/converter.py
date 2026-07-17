#!/usr/bin/env python3
"""Unified entry point for the claude-codex-switch skill.

Dispatches to ``claude_to_codex`` or ``codex_to_claude`` based on the first
argument. Run with no args to see the top-level help.

Examples:
    python converter.py claude-to-codex list
    python converter.py claude-to-codex convert ~/.claude/projects/<slug>/<id>.jsonl
    python converter.py codex-to-claude list
    python converter.py codex-to-claude convert ~/.codex/sessions/2026/07/17/rollout-*.jsonl
    python converter.py import ~/.claude/projects/<slug>/<id>.jsonl
    python converter.py status
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import claude_to_codex  # noqa: E402
import codex_import  # noqa: E402
import codex_to_claude  # noqa: E402
from common import (  # noqa: E402
    claude_home,
    claude_projects_root,
    codex_home,
    codex_sessions_dir,
    codex_state_db,
    error,
    info,
    list_claude_project_dirs,
)


VERSION = '1.0.0'

# Aliases the user can type. Each value is the canonical direction name.
DIRECTION_ALIASES = {
    'claude-to-codex': 'claude-to-codex',
    'c2x': 'claude-to-codex',
    'codex-to-claude': 'codex-to-claude',
    'x2c': 'codex-to-claude',
    'import': 'import',
    'status': 'status',
    '--version': 'version',
    '-V': 'version',
    '-h': 'help',
    '--help': 'help',
}


def _show_status() -> int:
    info('Claude-Codex Switch status')
    print(f'  CLAUDE_HOME      : {claude_home()}')
    print(f'  Claude projects  : {claude_projects_root()}')
    print(f'  CODEX_HOME       : {codex_home()}')
    print(f'  Codex sessions   : {codex_sessions_dir()}')
    print(f'  Codex state DB   : {codex_state_db()} (exists={codex_state_db().exists()})')
    print(f'  Python           : {sys.version.split()[0]} on {sys.platform}')
    print()
    dirs = list_claude_project_dirs()
    if dirs:
        info(f'Found {len(dirs)} Claude project folder(s):')
        for d in dirs[:10]:
            print(f'  - {d.name}')
        if len(dirs) > 10:
            print(f'  ... and {len(dirs) - 10} more')
    else:
        info('No Claude project folders found yet.')
    return 0


def _print_intro() -> None:
    print(f"""claude-codex-switch v{VERSION} - convert sessions between Claude Code and Codex

Usage:
    python converter.py <direction> <subcommand> [options]

Directions:
    claude-to-codex (alias: c2x)   Convert a Claude session into a Codex rollout
    codex-to-claude (alias: x2c)   Convert a Codex rollout into a Claude session
    import                         Import any JSONL into Codex (replaces codex-import.sh)
    status                         Show detected paths and DB health
    --version, -V                  Print skill version

Quick examples:
    python converter.py status
    python converter.py claude-to-codex list
    python converter.py claude-to-codex convert ~/.claude/projects/<slug>/<id>.jsonl
    python converter.py codex-to-claude list
    python converter.py codex-to-claude convert ~/.codex/sessions/2026/07/17/rollout-*.jsonl
    python converter.py import ~/.claude/projects/<slug>/<id>.jsonl --title "From Claude - bug fix"

Run `python converter.py <direction> --help` for full subcommand options.
""")


def _run_import(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog='claude-codex-switch import',
        description='Import a Claude/rollout JSONL into Codex (cross-platform).',
    )
    parser.add_argument('source', help='Path to a Claude session .jsonl or rollout .jsonl')
    parser.add_argument('--title', help='Override the title stored in Codex threads DB')
    parser.add_argument('--cwd', help='Override the cwd recorded in Codex')
    parsed = parser.parse_args(args)
    try:
        result = codex_import.import_session(parsed.source, title=parsed.title, cwd=parsed.cwd)
    except FileNotFoundError as e:
        error(str(e))
        return 2
    except Exception as e:  # noqa: BLE001
        error(f'Import failed: {e}')
        return 1

    info('Import succeeded')
    print(f"  Rollout   : {result['rollout_path']}")
    print(f"  Session ID: {result['session_id']}")
    print(f"  Title     : {result['title']}")
    print(f"  CWD       : {result['cwd']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        _print_intro()
        return 0

    head = argv[0]
    direction = DIRECTION_ALIASES.get(head)

    if direction is None:
        error(f'Unknown direction: {head!r}')
        _print_intro()
        return 2

    if direction == 'help':
        _print_intro()
        return 0
    if direction == 'version':
        print(f'claude-codex-switch skill v{VERSION}')
        return 0
    if direction == 'status':
        return _show_status()
    if direction == 'import':
        return _run_import(argv[1:])
    if direction == 'claude-to-codex':
        return claude_to_codex.main(argv[1:])
    if direction == 'codex-to-claude':
        return codex_to_claude.main(argv[1:])

    error(f'Unimplemented direction: {direction}')
    return 2


if __name__ == '__main__':
    sys.exit(main())
