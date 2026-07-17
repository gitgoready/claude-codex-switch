#!/usr/bin/env python3
"""Cross-platform replacement for the original Linux-only ``codex-import.sh``.

Takes a path to a Claude session JSONL (or any rollout-style JSONL you want
Codex to ingest), copies it into ``~/.codex/sessions/YYYY/MM/DD/`` if it's
not already there, and registers a row in Codex's ``state_5.sqlite``
``threads`` table so Codex lists the session.

The original shell script was Linux-only. This Python version works on
Windows, Linux and macOS and uses the shared ``common`` module for paths
and title formatting.

Usage:
    python codex_import.py <rollout-or-claude-jsonl>
    python codex_import.py <path> --title "From Claude - fix login - 2026-07-17 14-30"
    python codex_import.py <path> --cwd /home/zy/work/proj
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from common import (  # noqa: E402
    TITLE_PREFIX_FROM_CLAUDE,
    build_migrated_title,
    codex_home,
    codex_session_index,
    codex_sessions_dir,
    codex_state_db,
    error,
    first_display_user_message,
    info,
    success,
    warn,
)


CODEX_CLI_VERSION = '0.133.0-alpha.1'
DEFAULT_MODEL = 'gpt-5.5'
DEFAULT_REASONING_EFFORT = 'xhigh'


def _extract_session_id_and_first_user(path: Path) -> tuple[Optional[str], str, Optional[str]]:
    """Return (session_id, first_user_message, first_timestamp) from a JSONL.

    Handles both Codex rollout JSONL (entries with ``type=session_meta`` /
    ``event_msg`` / ``response_item``) and Claude session JSONL (entries with
    ``type=user`` / ``assistant`` / ``system`` and a top-level ``sessionId``).
    """
    session_id: Optional[str] = None
    first_user_msg = ''
    first_ts: Optional[str] = None

    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = e.get('timestamp')
            if ts and not first_ts:
                first_ts = ts

            # Codex session_meta carries the id.
            if not session_id and e.get('type') == 'session_meta':
                session_id = e.get('payload', {}).get('id')

            # Claude entries carry sessionId at the top level.
            if not session_id and e.get('sessionId'):
                session_id = e['sessionId']

            if first_user_msg:
                continue

            # Codex-style: event_msg with user_message payload.
            if e.get('type') == 'event_msg' and e.get('payload', {}).get('type') == 'user_message':
                msg = e['payload'].get('message', '')
                if msg and not _is_synthetic(msg):
                    first_user_msg = msg[:200]
                continue

            # Codex-style: response_item with role=user.
            if e.get('type') == 'response_item' and e.get('payload', {}).get('role') == 'user':
                for c in e['payload'].get('content', []):
                    if isinstance(c, dict) and c.get('type') == 'input_text':
                        txt = c.get('text', '')
                        if txt and not _is_synthetic(txt):
                            first_user_msg = txt[:200]
                            break
                continue

            # Claude-style: top-level type=user with message.content array.
            if e.get('type') == 'user':
                content = e.get('message', {}).get('content', '')
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get('type') == 'text':
                            txt = c.get('text', '')
                            if txt and not _is_synthetic(txt):
                                first_user_msg = txt[:200]
                                break
                elif isinstance(content, str) and not _is_synthetic(content):
                    first_user_msg = content[:200]

    return session_id, first_user_msg, first_ts


def _is_synthetic(text: str) -> bool:
    """Same filter the converters use, to skip IDE/command wrappers."""
    if not text:
        return True
    prefixes = (
        '<local-command',
        '<command-',
        '<ide_opened_file>',
        '<environment_context>',
        '<permissions',
        '<skills_instructions',
        '<collaboration_mode',
    )
    return text.lstrip().startswith(prefixes)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _register_thread(
    *,
    session_id: str,
    rollout_path: Path,
    title: str,
    first_user_message: str,
    cwd: str,
    created_at_ms: int,
) -> None:
    db_path = codex_state_db()
    if not db_path.exists():
        warn(f'Codex state DB not found at {db_path}; skipping DB registration.')
        return

    conn = sqlite3.connect(f'file:{db_path.as_posix()}?mode=rwc', uri=True)
    cursor = conn.cursor()
    table_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'"
    ).fetchone()
    if not table_exists:
        conn.close()
        raise RuntimeError('threads table does not exist in Codex state DB')

    now = int(time.time())
    preview = (first_user_message or title)[:500]

    values = {
        'id': session_id,
        'rollout_path': str(rollout_path),
        'created_at': int(created_at_ms / 1000),
        'updated_at': now,
        'source': 'vscode',
        'model_provider': 'openai',
        'cwd': cwd,
        'title': title,
        'sandbox_policy': '{"type":"danger-full-access"}',
        'approval_mode': 'never',
        'tokens_used': 0,
        'has_user_event': 1 if first_user_message else 0,
        'archived': 0,
        'cli_version': CODEX_CLI_VERSION,
        'first_user_message': first_user_message,
        'memory_mode': 'enabled',
        'model': DEFAULT_MODEL,
        'reasoning_effort': DEFAULT_REASONING_EFFORT,
        'created_at_ms': created_at_ms,
        'updated_at_ms': now * 1000,
        'thread_source': 'user',
        'preview': preview,
    }
    columns = [row[1] for row in cursor.execute('PRAGMA table_info(threads)').fetchall()]
    insert_cols = [c for c in values if c in columns]
    placeholders = ', '.join(['?'] * len(insert_cols))
    sql = (
        f'INSERT OR REPLACE INTO threads '
        f'({", ".join(insert_cols)}) VALUES ({placeholders})'
    )
    cursor.execute(sql, tuple(values[c] for c in insert_cols))
    conn.commit()
    conn.close()

    # Update the lightweight session index.
    index_path = codex_session_index()
    updated_at_iso = (
        datetime.utcfromtimestamp(now).isoformat(timespec='microseconds') + 'Z'
    )
    items: Dict[str, Dict[str, Any]] = {}
    if index_path.exists():
        with index_path.open('r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get('id'):
                    items[item['id']] = item
    items[session_id] = {'id': session_id, 'thread_name': title, 'updated_at': updated_at_iso}
    ordered = sorted(items.values(), key=lambda i: i.get('updated_at', ''), reverse=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open('w', encoding='utf-8') as f:
        for item in ordered:
            f.write(json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n')


def import_session(
    source: str | Path,
    *,
    title: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    src = Path(source).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f'Rollout file not found: {src}')

    session_id, first_user_msg, first_ts = _extract_session_id_and_first_user(src)
    if not session_id:
        # Generate a stable UUID if the source file doesn't carry one.
        session_id = str(uuid.uuid4())

    # Place a copy under ~/.codex/sessions/YYYY/MM/DD/ if not already there.
    sessions_root = codex_sessions_dir()
    if first_ts and len(first_ts) >= 10:
        date_dir = first_ts[:10].replace('-', '/')
    else:
        date_dir = datetime.now().strftime('%Y/%m/%d')
    dest_dir = sessions_root / date_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / src.name

    if src != dest_path:
        try:
            if src.resolve() == dest_path.resolve():
                pass
            else:
                # Copy so we don't mutate the user's original Claude file.
                dest_path.write_bytes(src.read_bytes())
        except OSError as e:
            warn(f'Could not copy {src} -> {dest_path}: {e}; using source path in DB.')

    # Title: explicit > built "From Claude - <orig> - <ts>" pattern.
    original_title = first_user_msg or 'Imported from Claude'
    final_title = title or build_migrated_title(
        source_label=TITLE_PREFIX_FROM_CLAUDE,
        original_title=original_title,
        source_timestamp=first_ts,
    )

    cwd = cwd or str(Path.home())
    created_at_ms = int(time.time() * 1000)
    dt = _parse_iso(first_ts)
    if dt:
        created_at_ms = int(dt.timestamp() * 1000)

    _register_thread(
        session_id=session_id,
        rollout_path=dest_path,
        title=final_title,
        first_user_message=first_user_msg,
        cwd=cwd,
        created_at_ms=created_at_ms,
    )

    return {
        'status': 'success',
        'rollout_path': str(dest_path),
        'session_id': session_id,
        'title': final_title,
        'first_user_message': first_user_msg,
        'cwd': cwd,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='codex_import',
        description='Import a Claude/rollout JSONL into Codex (cross-platform).',
    )
    parser.add_argument('source', help='Path to a Claude session .jsonl or rollout .jsonl')
    parser.add_argument('--title', help='Override the title stored in Codex threads DB')
    parser.add_argument('--cwd', help='Override the cwd recorded in Codex')
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = import_session(args.source, title=args.title, cwd=args.cwd)
    except FileNotFoundError as e:
        error(str(e))
        return 2
    except Exception as e:  # noqa: BLE001
        error(f'Import failed: {e}')
        return 1

    success('Import succeeded')
    print(f"  Rollout: {result['rollout_path']}")
    print(f"  Session ID: {result['session_id']}")
    print(f"  Title: {result['title']}")
    print(f"  CWD: {result['cwd']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
