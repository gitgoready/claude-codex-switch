#!/usr/bin/env python3
"""Codex -> Claude session converter.

Reads a Codex rollout JSONL file (under ``~/.codex/sessions/YYYY/MM/DD/``)
and writes a Claude-compatible JSONL into ``~/.claude/projects/<slug>/`` so
Claude Code can list and resume the migrated session.

Cross-platform: works on Windows, Linux and macOS. The target Claude project
directory is auto-detected from the Codex session's ``cwd`` field (and can be
overridden with ``--project-dir`` or the ``CLAUDE_PROJECT_DIR`` env var).

Run directly:
    python codex_to_claude.py --list
    python codex_to_claude.py --preview <path>
    python codex_to_claude.py --convert <path>
    python codex_to_claude.py --convert <path> --project-dir ~/.claude/projects/-home-<user-proj>
    python codex_to_claude.py --batch
    python codex_to_claude.py --convert --date 2026-05-01 --end-date 2026-05-24
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from common import (  # noqa: E402
    TITLE_PREFIX_FROM_CODEX,
    autodetect_claude_project_dir,
    build_migrated_title,
    claude_home,
    claude_projects_root,
    codex_home,
    codex_sessions_dir,
    codex_state_db,
    encode_claude_project_slug,
    error,
    first_display_user_message,
    format_timestamp_for_filename,
    info,
    is_synthetic_message,
    success,
    warn,
    write_jsonl,
)


# ----------------------------------------------------------------------------
# Data structures
# ----------------------------------------------------------------------------

@dataclass
class Message:
    role: str  # 'user' | 'assistant' | 'system'
    content: str
    timestamp: str = ''
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class CodexSession:
    path: str
    session_id: str = ''
    title: str = ''
    cwd: str = ''
    date: str = ''
    messages: List[Message] = field(default_factory=list)
    turn_context: Dict[str, Any] = field(default_factory=dict)
    compacted_history: List[Dict[str, Any]] = field(default_factory=list)
    total_lines: int = 0


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------

def parse_codex_session(session_path: str | Path) -> CodexSession:
    path = Path(session_path)
    with path.open('r', encoding='utf-8') as f:
        lines = f.readlines()

    session = CodexSession(path=str(path), total_lines=len(lines))
    messages: List[Message] = []
    current_turn_context: Dict[str, Any] = {}
    compacted_history: List[Dict[str, Any]] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        entry_type = entry.get('type')

        if entry_type == 'session_meta':
            payload = entry.get('payload', {})
            session.session_id = payload.get('id', '') or session.session_id
            session.cwd = payload.get('cwd', '') or session.cwd
            ts = payload.get('timestamp', '')
            if ts:
                session.date = ts[:10]

        elif entry_type == 'turn_context':
            current_turn_context = entry.get('payload', {})
            if not session.cwd and current_turn_context.get('cwd'):
                session.cwd = current_turn_context['cwd']

        elif entry_type == 'response_item':
            payload = entry.get('payload', {})
            role = payload.get('role')
            content = payload.get('content', [])

            if role == 'user' and content:
                text_parts: List[str] = []
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'input_text':
                        text = c.get('text', '')
                        if not is_synthetic_message(text):
                            text_parts.append(text)
                if text_parts:
                    messages.append(Message(
                        role='user',
                        content='\n\n'.join(text_parts),
                        timestamp=entry.get('timestamp', ''),
                    ))

            elif role == 'developer' and content:
                text_parts = []
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'input_text':
                        text_parts.append(c.get('text', ''))
                if text_parts:
                    text = '\n\n'.join(text_parts)
                    if not is_synthetic_message(text):
                        messages.append(Message(
                            role='assistant',
                            content=text,
                            timestamp=entry.get('timestamp', ''),
                        ))

            elif role == 'assistant' and content:
                text_parts = []
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'output_text':
                        text_parts.append(c.get('text', ''))
                if text_parts:
                    messages.append(Message(
                        role='assistant',
                        content='\n\n'.join(text_parts),
                        timestamp=entry.get('timestamp', ''),
                    ))

        elif entry_type == 'event_msg':
            payload = entry.get('payload', {})
            sub_type = payload.get('type')
            if sub_type == 'user_message':
                text = payload.get('message', '')
                if text and not is_synthetic_message(text):
                    messages.append(Message(
                        role='user',
                        content=text,
                        timestamp=entry.get('timestamp', ''),
                    ))
            elif sub_type == 'agent_message':
                text = payload.get('message', '')
                if text:
                    messages.append(Message(
                        role='assistant',
                        content=text,
                        timestamp=entry.get('timestamp', ''),
                    ))

        elif entry_type == 'compacted':
            replacement_history = entry.get('payload', {}).get('replacement_history', [])
            for msg in replacement_history:
                role = msg.get('role')
                content = msg.get('content', [])
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'input_text':
                        compacted_history.append({
                            'role': role,
                            'content': c.get('text', ''),
                        })

    session.messages = messages
    session.turn_context = current_turn_context
    session.compacted_history = compacted_history

    # Try to read the title from Codex's threads DB (best-effort).
    if not session.title:
        session.title = _lookup_codex_thread_title(session.session_id) or ''

    return session


def _lookup_codex_thread_title(session_id: str) -> str:
    """Look up the Codex thread title from state_5.sqlite if available."""
    if not session_id:
        return ''
    db_path = codex_state_db()
    if not db_path.exists():
        return ''
    try:
        conn = sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True)
        cursor = conn.cursor()
        table_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'"
        ).fetchone()
        if not table_exists:
            conn.close()
            return ''
        row = cursor.execute(
            'SELECT title FROM threads WHERE id = ? LIMIT 1', (session_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else ''
    except Exception:
        return ''


# ----------------------------------------------------------------------------
# Claude format builders
# ----------------------------------------------------------------------------

def create_claude_entry(
    msg: Message,
    session_id: str,
    parent_uuid: Optional[str],
    project_slug: str,
    cwd: str,
) -> Dict[str, Any]:
    claude_role = 'user' if msg.role == 'user' else 'assistant'
    if msg.role == 'user':
        content: Any = msg.content
    else:
        content = [{'type': 'text', 'text': msg.content}]

    return {
        'parentUuid': parent_uuid,
        'isSidechain': False,
        'type': msg.role,
        'message': {'role': claude_role, 'content': content},
        'isVisibleInTranscriptOnly': False,
        'isCompactSummary': False,
        'uuid': msg.uuid,
        'timestamp': msg.timestamp or datetime.utcnow().isoformat() + 'Z',
        'userType': 'external',
        'entrypoint': 'codex-vscode-migrated',
        'cwd': cwd,
        'sessionId': session_id,
        'version': '2.1.120',
        'gitBranch': 'main',
        'slug': project_slug,
    }


def create_migration_boundary(
    session: CodexSession,
    session_id: str,
    project_slug: str,
    cwd: str,
    migrated_title: str,
) -> Dict[str, Any]:
    """System entry that marks the start of a migrated session.

    Includes the new ``From Codex - <original title> - <ts>`` title so that
    Claude's transcript shows the origin clearly.
    """
    source_basename = Path(session.path).name
    return {
        'parentUuid': None,
        'isSidechain': False,
        'type': 'system',
        'subtype': 'migration_boundary',
        'content': (
            f'[From Codex] Session migrated from: {source_basename} '
            f'(dated {session.date}). Migrated title: {migrated_title}'
        ),
        'isMeta': True,
        'uuid': str(uuid.uuid4()),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'userType': 'external',
        'entrypoint': 'codex-vscode-migrated',
        'cwd': cwd,
        'sessionId': session_id,
        'version': '2.1.120',
        'gitBranch': 'main',
        'slug': project_slug,
    }


# ----------------------------------------------------------------------------
# Converter
# ----------------------------------------------------------------------------

def convert_session(
    session: CodexSession,
    target_project_dir: Optional[Path] = None,
    project_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert a parsed Codex session to a Claude-format JSONL file."""
    messages = session.messages
    if not messages:
        return {'status': 'no_messages', 'source': session.path}

    cwd = session.cwd or str(Path.home())

    # Decide target project dir: explicit > auto-detect by cwd > fallback
    # to the encoded cwd slug under ~/.claude/projects.
    if target_project_dir is None:
        target_project_dir = autodetect_claude_project_dir(cwd_hint=cwd)
    if target_project_dir is None:
        target_project_dir = claude_projects_root() / encode_claude_project_slug(cwd)
        target_project_dir.mkdir(parents=True, exist_ok=True)

    target_project_dir = Path(target_project_dir)
    target_project_dir.mkdir(parents=True, exist_ok=True)

    slug = project_slug or target_project_dir.name

    session_id = str(uuid.uuid4())

    # Build migrated title with "From Codex" prefix.
    original_title = session.title or first_display_user_message(messages) or 'untitled Codex session'
    first_ts = messages[0].timestamp or datetime.utcnow().isoformat() + 'Z'
    migrated_title = build_migrated_title(
        source_label=TITLE_PREFIX_FROM_CODEX,
        original_title=original_title,
        source_timestamp=first_ts,
    )

    converted_entries: List[Dict[str, Any]] = []

    migration_entry = create_migration_boundary(
        session, session_id, slug, cwd, migrated_title,
    )
    converted_entries.append(migration_entry)
    parent_uuid = migration_entry['uuid']

    if session.compacted_history:
        history_text = '\n'.join(
            f"{h.get('role', '')}: {h.get('content', '')[:300]}"
            for h in session.compacted_history[-5:]
            if h.get('content')
        )
        if history_text:
            history_entry = {
                'parentUuid': parent_uuid,
                'isSidechain': False,
                'type': 'system',
                'subtype': 'migration_compacted',
                'content': f'[From Codex] Conversation history:\n\n{history_text}',
                'isMeta': True,
                'uuid': str(uuid.uuid4()),
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'userType': 'external',
                'entrypoint': 'codex-vscode-migrated',
                'cwd': cwd,
                'sessionId': session_id,
                'version': '2.1.120',
                'gitBranch': 'main',
                'slug': slug,
            }
            converted_entries.append(history_entry)
            parent_uuid = history_entry['uuid']

    user_count = 0
    assistant_count = 0
    for msg in messages:
        entry = create_claude_entry(msg, session_id, parent_uuid, slug, cwd)
        converted_entries.append(entry)
        parent_uuid = entry['uuid']
        if msg.role == 'user':
            user_count += 1
        elif msg.role == 'assistant':
            assistant_count += 1

    target_path = target_project_dir / f'{session_id}.jsonl'
    write_jsonl(target_path, converted_entries)

    return {
        'status': 'success',
        'source': session.path,
        'target': str(target_path),
        'session_id': session_id,
        'title': migrated_title,
        'original_title': original_title,
        'date': session.date,
        'total_messages': len(messages),
        'user_messages': user_count,
        'assistant_messages': assistant_count,
        'cwd': cwd,
    }


# ----------------------------------------------------------------------------
# Discovery / CLI helpers
# ----------------------------------------------------------------------------

def find_all_codex_sessions() -> List[Path]:
    root = codex_sessions_dir()
    if not root.exists():
        return []
    sessions = list(root.rglob('rollout-*.jsonl'))
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions


def list_sessions() -> None:
    sessions = find_all_codex_sessions()
    info(f'Found {len(sessions)} Codex session(s) under {codex_sessions_dir()}')
    if not sessions:
        return

    print(f"\n{'Date':<12} {'Time':<10} {'Size':<10} Path")
    print('-' * 80)
    for path in sessions:
        size = path.stat().st_size
        size_str = f'{size / 1024:.1f}KB' if size < 1024 * 1024 else f'{size / 1024 / 1024:.1f}MB'
        basename = path.name
        parts = basename.replace('rollout-', '').replace('.jsonl', '').split('T')
        date_str = parts[0] if parts and parts[0] else 'unknown'
        time_str = parts[1][:8] if len(parts) > 1 and parts[1] else ''
        print(f'{date_str:<12} {time_str:<10} {size_str:<10} {str(path)[:60]}...')


def preview_session(path: str | Path) -> None:
    p = Path(path)
    if not p.exists():
        error(f'File not found: {p}')
        return

    session = parse_codex_session(p)
    print(f'\nSession preview: {p.name}')
    print(f'Date: {session.date}')
    print(f'Session ID: {session.session_id}')
    print(f'Title (from Codex DB): {session.title or "(none)"}')
    print(f'CWD: {session.cwd or "(none)"}')
    print(f'Total lines: {session.total_lines}')
    print(
        f'Parsed messages: {len(session.messages)} '
        f'(user={sum(1 for m in session.messages if m.role == "user")}, '
        f'assistant={sum(1 for m in session.messages if m.role == "assistant")})'
    )

    print('\nFirst 3 messages:')
    for i, msg in enumerate(session.messages[:3]):
        content = msg.content[:100] + ('...' if len(msg.content) > 100 else '')
        print(f'  [{i + 1}] {msg.role}: {content}')


def convert_single(
    path: str | Path,
    project_dir: Optional[Path] = None,
    project_slug: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        error(f'File not found: {p}')
        return None

    session = parse_codex_session(p)
    result = convert_session(
        session,
        target_project_dir=project_dir,
        project_slug=project_slug,
    )

    if result['status'] == 'success':
        success('Conversion succeeded (Codex -> Claude)')
        print(f'  Source: {result["source"]}')
        print(f'  Target: {result["target"]}')
        print(f'  Session ID: {result["session_id"]}')
        print(f'  Migrated title: {result["title"]}')
        # Truncate the original for display so we don't dump a multi-KB prompt.
        orig = result["original_title"]
        if len(orig) > 120:
            orig = orig[:119] + '…'
        print(f'  Original title: {orig}')
        print(f'  Date: {result["date"]}')
        print(f'  CWD: {result["cwd"]}')
        print(
            f'  Messages: {result["user_messages"]} user + '
            f'{result["assistant_messages"]} assistant'
        )
        print('\nOpen the project in Claude Code and the migrated session will appear in history.')
    else:
        error(f'Conversion failed: {result}')
    return result


def convert_batch(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    project_dir: Optional[Path] = None,
) -> int:
    sessions = find_all_codex_sessions()

    if start_date:
        sessions = [s for s in sessions if s.name >= f'rollout-{start_date}']
    if end_date:
        sessions = [s for s in sessions if s.name <= f'rollout-{end_date}T']

    info(f'Batch conversion: {len(sessions)} session(s) found')
    if not sessions:
        return 0

    results: List[Dict[str, Any]] = []
    for i, path in enumerate(sessions):
        session = parse_codex_session(path)
        result = convert_session(session, target_project_dir=project_dir)
        results.append(result)
        print(f'[{i + 1}/{len(sessions)}] {path.name}: {result["status"]}')

    ok = sum(1 for r in results if r['status'] == 'success')
    success(f'Done: {ok}/{len(sessions)} converted')

    report_path = claude_home() / 'codex_to_claude_report.json'
    with report_path.open('w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    info(f'Report saved to: {report_path}')
    return ok


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='codex_to_claude',
        description='Convert a Codex rollout JSONL into a Claude session JSONL.',
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('list', help='List Codex sessions')

    p_preview = sub.add_parser('preview', help='Preview a Codex session without converting')
    p_preview.add_argument('path', help='Path to a Codex rollout-*.jsonl file')

    p_convert = sub.add_parser('convert', help='Convert a single Codex session to Claude')
    p_convert.add_argument('path', nargs='?', help='Path to a Codex rollout-*.jsonl file')
    p_convert.add_argument('--date', help='Start date (YYYY-MM-DD) for batch filtering')
    p_convert.add_argument('--end-date', help='End date (YYYY-MM-DD) for batch filtering')
    p_convert.add_argument(
        '--project-dir',
        help='Target Claude project directory (default: auto-detect from Codex cwd)',
    )
    p_convert.add_argument(
        '--project-slug',
        help='Override the slug stored in each Claude entry (default: target dir name)',
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == 'list':
        list_sessions()
        return 0
    if args.cmd == 'preview':
        preview_session(args.path)
        return 0
    if args.cmd == 'convert':
        if args.path:
            project_dir = Path(args.project_dir).expanduser() if args.project_dir else None
            result = convert_single(
                args.path,
                project_dir=project_dir,
                project_slug=args.project_slug,
            )
            return 0 if result and result['status'] == 'success' else 1
        if args.date or args.end_date:
            ok = convert_batch(start_date=args.date, end_date=args.end_date)
            return 0 if ok >= 0 else 1
        parser.error('convert requires either a path or --date/--end-date for batch mode')
        return 1
    parser.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
