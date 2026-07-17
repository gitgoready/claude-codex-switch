#!/usr/bin/env python3
"""Claude -> Codex session converter.

Reads a Claude session JSONL file (typically stored under
``~/.claude/projects/<project-slug>/<session-id>.jsonl``) and writes a
Codex-compatible ``rollout-*.jsonl`` under ``~/.codex/sessions/YYYY/MM/DD/``,
then registers the session in Codex's ``state_5.sqlite`` threads table so
Codex's UI lists it.

Cross-platform: works on Windows, Linux and macOS. All paths come from
``common.py`` (which honors ``CLAUDE_HOME`` / ``CODEX_HOME`` env overrides).

Run directly:
    python claude_to_codex.py --list
    python claude_to_codex.py --preview <path>
    python claude_to_codex.py --convert <path>
    python claude_to_codex.py --convert <path> --cwd /home/user/my-project
    python claude_to_codex.py --batch
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make sibling import work when run as a script or via -m.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from common import (  # noqa: E402
    TITLE_PREFIX_FROM_CLAUDE,
    autodetect_claude_project_dir,
    build_migrated_title,
    claude_projects_root,
    codex_home,
    codex_session_index,
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
from common import truncate_middle  # noqa: E402


# ----------------------------------------------------------------------------
# Constants that match the original Codex rollout shape
# ----------------------------------------------------------------------------

CODEX_CLI_VERSION = '0.133.0-alpha.1'
DEFAULT_MODEL = 'gpt-5.5'
DEFAULT_REASONING_EFFORT = 'xhigh'
DEFAULT_CWD_FALLBACK = str(Path.home())

TOOL_OUTPUT_MAX_CHARS = 12000
TOOL_INPUT_MAX_CHARS = 4000


# ----------------------------------------------------------------------------
# Data structures
# ----------------------------------------------------------------------------

@dataclass
class Message:
    role: str  # 'user' | 'assistant' | 'system' | 'tool'
    content: str
    timestamp: str = ''
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: str = 'text'  # text | tool_use | tool_result
    tool_name: str = ''
    tool_input: Any = None
    tool_use_id: str = ''
    is_error: bool = False


@dataclass
class ClaudeSession:
    path: str
    session_id: str = ''
    slug: str = ''
    date: str = ''
    cwd: str = ''
    messages: List[Message] = field(default_factory=list)
    total_entries: int = 0


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------

def stringify_claude_content(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    parts.append(item.get('text', ''))
                elif 'content' in item:
                    parts.append(stringify_claude_content(item.get('content')))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return '\n'.join(p for p in parts if p)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def strip_leading_synthetic_context(content: str) -> str:
    """Drop Claude IDE/environment wrappers while preserving the real prompt."""
    text = (content or '').strip()
    for tag in ('ide_opened_file', 'environment_context'):
        open_tag = f'<{tag}>'
        close_tag = f'</{tag}>'
        while text.startswith(open_tag):
            close_index = text.find(close_tag)
            if close_index == -1:
                return ''
            text = text[close_index + len(close_tag):].strip()
    return text


def append_text_message(
    messages: List[Message],
    role: str,
    content: str,
    timestamp: str,
    entry_uuid: str,
) -> None:
    content = (content or '').strip()
    if not content:
        return
    if role == 'user':
        content = strip_leading_synthetic_context(content)
        if not content:
            return
        if is_synthetic_message(content):
            return
    messages.append(Message(
        role=role,
        content=content,
        timestamp=timestamp,
        uuid=entry_uuid,
    ))


def parse_claude_session(session_path: str | Path) -> ClaudeSession:
    path = Path(session_path)
    with path.open('r', encoding='utf-8') as f:
        lines = f.readlines()

    session = ClaudeSession(path=str(path), total_entries=len(lines))
    messages: List[Message] = []
    first_session_id = ''
    first_slug = ''
    first_date = ''
    first_cwd = ''
    tool_names_by_id: Dict[str, str] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        msg_type = entry.get('type')

        if not first_session_id and entry.get('sessionId'):
            first_session_id = entry['sessionId']
        if not first_slug and entry.get('slug'):
            first_slug = entry['slug']
        if not first_date and entry.get('timestamp'):
            first_date = entry['timestamp'][:10]
        if not first_cwd and entry.get('cwd'):
            first_cwd = entry['cwd']

        if msg_type in ('user', 'assistant'):
            msg_content = entry.get('message', {}).get('content', '')
            timestamp = entry.get('timestamp', '')
            entry_uuid = entry.get('uuid', str(uuid.uuid4()))

            if isinstance(msg_content, list):
                text_parts: List[str] = []

                def flush_text() -> None:
                    if not text_parts:
                        return
                    append_text_message(
                        messages,
                        msg_type,
                        '\n\n'.join(text_parts),
                        timestamp,
                        entry_uuid,
                    )
                    text_parts.clear()

                for c in msg_content:
                    if not isinstance(c, dict):
                        continue
                    ctype = c.get('type')
                    if ctype == 'text':
                        text_parts.append(c.get('text', ''))
                    elif ctype == 'tool_use':
                        flush_text()
                        tool_id = c.get('id', str(uuid.uuid4()))
                        tool_name = c.get('name', 'unknown_tool')
                        tool_names_by_id[tool_id] = tool_name
                        messages.append(Message(
                            role='assistant',
                            content='',
                            timestamp=timestamp,
                            uuid=entry_uuid,
                            kind='tool_use',
                            tool_name=tool_name,
                            tool_input=c.get('input', {}),
                            tool_use_id=tool_id,
                        ))
                    elif ctype == 'tool_result':
                        flush_text()
                        tool_id = c.get('tool_use_id', '')
                        result = truncate_middle(
                            stringify_claude_content(c.get('content', '')),
                            TOOL_OUTPUT_MAX_CHARS,
                            note='from migrated Claude tool output',
                        )
                        messages.append(Message(
                            role='tool',
                            content=result,
                            timestamp=timestamp,
                            uuid=entry_uuid,
                            kind='tool_result',
                            tool_name=tool_names_by_id.get(tool_id, ''),
                            tool_use_id=tool_id,
                            is_error=bool(c.get('is_error')),
                        ))
                    elif ctype == 'thinking':
                        text_parts.append(c.get('thinking', '')[:500])
                flush_text()
            else:
                append_text_message(
                    messages,
                    msg_type,
                    stringify_claude_content(msg_content),
                    timestamp,
                    entry_uuid,
                )

        elif msg_type == 'system':
            subtype = entry.get('subtype', '')
            content = entry.get('content', '')
            if content and subtype == 'migration_boundary':
                messages.append(Message(
                    role='system',
                    content=f'[Migration] {content}',
                    timestamp=entry.get('timestamp', ''),
                    uuid=entry.get('uuid', str(uuid.uuid4())),
                ))

    session.session_id = first_session_id
    session.slug = first_slug
    session.date = first_date
    session.cwd = first_cwd
    session.messages = messages
    return session


# ----------------------------------------------------------------------------
# Codex format helpers
# ----------------------------------------------------------------------------

def fallback_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'


def parse_iso_timestamp(timestamp: str) -> Optional[datetime]:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def epoch_seconds(timestamp: str, default: Optional[int] = None) -> int:
    dt = parse_iso_timestamp(timestamp)
    if dt:
        return int(dt.timestamp())
    return default if default is not None else int(datetime.now().timestamp())


def epoch_millis(timestamp: str, default: Optional[int] = None) -> int:
    dt = parse_iso_timestamp(timestamp)
    if dt:
        return int(dt.timestamp() * 1000)
    return default if default is not None else int(datetime.now().timestamp() * 1000)


def compact_text(text: str, limit: Optional[int] = None) -> str:
    value = ' '.join((text or '').strip().split())
    if limit and len(value) > limit:
        return value[:limit - 1].rstrip() + '…'
    return value


def normalized_tool_name(tool_name: str) -> str:
    safe = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in (tool_name or 'tool'))
    return safe or 'tool'


def tool_call_arguments(msg: Message, cwd: str) -> Dict[str, Any]:
    tool_input = msg.tool_input if isinstance(msg.tool_input, dict) else {'input': msg.tool_input}
    if msg.tool_name == 'Bash':
        args: Dict[str, Any] = {'cmd': str(tool_input.get('command', ''))}
        description = tool_input.get('description')
        if description:
            args['description'] = str(description)
        args['workdir'] = cwd
        return args
    return {'tool': msg.tool_name or 'unknown_tool', 'input': tool_input}


def create_session_meta(session_id: str, cwd: str, timestamp: str) -> Dict[str, Any]:
    return {
        'timestamp': timestamp,
        'type': 'session_meta',
        'payload': {
            'id': session_id,
            'timestamp': timestamp,
            'cwd': cwd,
            'originator': 'codex_vscode',
            'cli_version': CODEX_CLI_VERSION,
            'source': 'vscode',
            'thread_source': 'user',
            'model_provider': 'openai',
            'base_instructions': {
                'text': (
                    'You are Codex, a coding agent based on GPT-5. You and the '
                    'user share one workspace, and your job is to collaborate '
                    'with them until their goal is genuinely handled.'
                ),
            },
        },
    }


def create_user_message_entry(msg: Message) -> Dict[str, Any]:
    return {
        'timestamp': msg.timestamp or fallback_timestamp(),
        'type': 'response_item',
        'payload': {
            'type': 'message',
            'role': 'user',
            'content': [{'type': 'input_text', 'text': msg.content}],
        },
    }


def create_assistant_message_entry(msg: Message) -> Dict[str, Any]:
    return {
        'timestamp': msg.timestamp or fallback_timestamp(),
        'type': 'response_item',
        'payload': {
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'output_text', 'text': msg.content}],
            'phase': 'commentary',
        },
    }


def create_tool_call_entry(msg: Message, cwd: str) -> Dict[str, Any]:
    timestamp = msg.timestamp or fallback_timestamp()
    if msg.tool_name == 'Bash':
        name = 'exec_command'
    else:
        name = f'claude_{normalized_tool_name(msg.tool_name)}'
    args = tool_call_arguments(msg, cwd)
    arguments = json.dumps(args, ensure_ascii=False, separators=(',', ':'))
    if len(arguments) > TOOL_INPUT_MAX_CHARS:
        args = {
            'tool': msg.tool_name or 'unknown_tool',
            'input_preview': truncate_middle(
                stringify_claude_content(msg.tool_input),
                TOOL_INPUT_MAX_CHARS,
                note='from migrated Claude tool input',
            ),
            'truncated': True,
        }
        arguments = json.dumps(args, ensure_ascii=False, separators=(',', ':'))
    return {
        'timestamp': timestamp,
        'type': 'response_item',
        'payload': {
            'type': 'function_call',
            'name': name,
            'arguments': arguments,
            'call_id': msg.tool_use_id or msg.uuid,
        },
    }


def create_tool_result_entry(msg: Message) -> Dict[str, Any]:
    timestamp = msg.timestamp or fallback_timestamp()
    output = msg.content or ''
    if msg.is_error:
        output = '[Claude tool error]\n' + output
    return {
        'timestamp': timestamp,
        'type': 'response_item',
        'payload': {
            'type': 'function_call_output',
            'call_id': msg.tool_use_id or msg.uuid,
            'output': output,
        },
    }


def create_task_started_entry(turn_id: str, timestamp: str) -> Dict[str, Any]:
    return {
        'timestamp': timestamp,
        'type': 'event_msg',
        'payload': {
            'type': 'task_started',
            'turn_id': turn_id,
            'started_at': epoch_seconds(timestamp),
            'model_context_window': 258400,
            'collaboration_mode_kind': 'default',
        },
    }


def create_user_event_entry(msg: Message) -> Dict[str, Any]:
    timestamp = msg.timestamp or fallback_timestamp()
    return {
        'timestamp': timestamp,
        'type': 'event_msg',
        'payload': {
            'type': 'user_message',
            'message': msg.content,
            'images': [],
            'local_images': [],
            'text_elements': [],
        },
    }


def create_agent_message_event(msg: Message) -> Dict[str, Any]:
    timestamp = msg.timestamp or fallback_timestamp()
    return {
        'timestamp': timestamp,
        'type': 'event_msg',
        'payload': {
            'type': 'agent_message',
            'message': msg.content,
            'phase': 'commentary',
            'memory_citation': None,
        },
    }


def create_task_complete_entry(
    turn_id: str, timestamp: str, last_agent_message: str, started_at: int
) -> Dict[str, Any]:
    completed_at = epoch_seconds(timestamp)
    return {
        'timestamp': timestamp,
        'type': 'event_msg',
        'payload': {
            'type': 'task_complete',
            'turn_id': turn_id,
            'last_agent_message': last_agent_message or '',
            'completed_at': completed_at,
            'duration_ms': max(0, (completed_at - started_at) * 1000),
            'time_to_first_token_ms': 0,
        },
    }


def create_turn_context_entry(
    turn_id: str, cwd: str, session_date: str, timestamp: str
) -> Dict[str, Any]:
    return {
        'timestamp': timestamp,
        'type': 'turn_context',
        'payload': {
            'turn_id': turn_id,
            'cwd': cwd,
            'current_date': session_date,
            'timezone': _local_timezone_name(),
            'approval_policy': 'never',
            'sandbox_policy': {'type': 'danger-full-access'},
            'permission_profile': {'type': 'disabled'},
            'model': DEFAULT_MODEL,
            'effort': DEFAULT_REASONING_EFFORT,
        },
    }


def _local_timezone_name() -> str:
    try:
        import zoneinfo  # type: ignore
        local = datetime.now().astimezone()
        return str(local.tzinfo) or 'UTC'
    except Exception:
        return 'UTC'


# ----------------------------------------------------------------------------
# Codex session index update
# ----------------------------------------------------------------------------

def update_session_index(thread_id: str, title: str, updated_at_ms: int) -> None:
    """Append/replace an entry in ~/.codex/session_index.jsonl."""
    index_path = codex_session_index()
    updated_at = (
        datetime.utcfromtimestamp(updated_at_ms / 1000)
        .isoformat(timespec='microseconds') + 'Z'
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
                item_id = item.get('id')
                if item_id:
                    items[item_id] = item

    items[thread_id] = {'id': thread_id, 'thread_name': title, 'updated_at': updated_at}
    ordered = sorted(
        items.values(),
        key=lambda item: item.get('updated_at', ''),
        reverse=True,
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open('w', encoding='utf-8') as f:
        for item in ordered:
            f.write(json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n')


# ----------------------------------------------------------------------------
# Converter
# ----------------------------------------------------------------------------

def convert_session(
    session: ClaudeSession,
    target_dir: Optional[Path] = None,
    register: bool = True,
    cwd: Optional[str] = None,
    session_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert a parsed Claude session to a Codex rollout JSONL file."""
    messages = session.messages
    if not messages:
        return {'status': 'no_messages', 'source': session.path}

    target_root = Path(target_dir) if target_dir else codex_sessions_dir()
    session_uuid = session_uuid or str(uuid.uuid4())
    cwd = cwd or session.cwd or DEFAULT_CWD_FALLBACK

    first_ts = messages[0].timestamp or fallback_timestamp()
    last_ts = messages[-1].timestamp or first_ts
    session_date = session.date or first_ts[:10] or datetime.utcnow().date().isoformat()

    date_dir = session_date.replace('-', '/')
    target_subdir = target_root / date_dir
    target_subdir.mkdir(parents=True, exist_ok=True)

    time_str = '00-00-00'
    if first_ts and len(first_ts) >= 19:
        time_str = first_ts[11:19].replace(':', '-')
    target_path = target_subdir / f'rollout-{session_date}T{time_str}-{session_uuid}.jsonl'

    entries: List[Dict[str, Any]] = []
    entries.append(create_session_meta(session_uuid, cwd, first_ts))

    active_turn_id = ''
    active_turn_started_at = 0
    last_agent_message = ''

    for msg in messages:
        timestamp = msg.timestamp or fallback_timestamp()

        if msg.kind == 'tool_use':
            if not active_turn_id:
                active_turn_id = str(uuid.uuid4())
                active_turn_started_at = epoch_seconds(timestamp)
                entries.append(create_task_started_entry(active_turn_id, timestamp))
            entries.append(create_tool_call_entry(msg, cwd))

        elif msg.kind == 'tool_result':
            if not active_turn_id:
                active_turn_id = str(uuid.uuid4())
                active_turn_started_at = epoch_seconds(timestamp)
                entries.append(create_task_started_entry(active_turn_id, timestamp))
            entries.append(create_tool_result_entry(msg))

        elif msg.role == 'user':
            if active_turn_id:
                entries.append(create_task_complete_entry(
                    active_turn_id, timestamp, last_agent_message, active_turn_started_at,
                ))
            active_turn_id = str(uuid.uuid4())
            active_turn_started_at = epoch_seconds(timestamp)
            last_agent_message = ''
            entries.append(create_task_started_entry(active_turn_id, timestamp))
            entries.append(create_user_message_entry(msg))
            entries.append(create_turn_context_entry(active_turn_id, cwd, session_date, timestamp))
            entries.append(create_user_event_entry(msg))

        elif msg.role == 'assistant':
            if not active_turn_id:
                active_turn_id = str(uuid.uuid4())
                active_turn_started_at = epoch_seconds(timestamp)
                entries.append(create_task_started_entry(active_turn_id, timestamp))
            entries.append(create_agent_message_event(msg))
            entries.append(create_assistant_message_entry(msg))
            last_agent_message = msg.content

        elif msg.role == 'system':
            # Migration boundary messages from prior runs are dropped; the
            # new title already encodes the origin.
            pass

    if active_turn_id:
        entries.append(create_task_complete_entry(
            active_turn_id, last_ts, last_agent_message, active_turn_started_at,
        ))

    write_jsonl(target_path, entries)

    # Title with "From Claude" prefix + original first user message + timestamp.
    original_title = first_display_user_message(messages) or 'untitled Claude session'
    title = build_migrated_title(
        source_label=TITLE_PREFIX_FROM_CLAUDE,
        original_title=original_title,
        source_timestamp=first_ts,
    )
    preview = compact_text(original_title, 500) or title

    if register:
        _register_in_codex_db(
            session_uuid=session_uuid,
            target_path=target_path,
            title=title,
            preview=preview,
            first_msg=original_title,
            created_at=epoch_seconds(first_ts),
            created_at_ms=epoch_millis(first_ts),
            cwd=cwd,
        )

    return {
        'status': 'success',
        'source': session.path,
        'target': str(target_path),
        'session_id': session_uuid,
        'title': title,
        'date': session_date,
        'total_messages': len(messages),
        'user_messages': sum(1 for m in messages if m.kind == 'text' and m.role == 'user'),
        'assistant_messages': sum(1 for m in messages if m.kind == 'text' and m.role == 'assistant'),
        'tool_calls': sum(1 for m in messages if m.kind == 'tool_use'),
        'tool_results': sum(1 for m in messages if m.kind == 'tool_result'),
    }


def _register_in_codex_db(
    *,
    session_uuid: str,
    target_path: Path,
    title: str,
    preview: str,
    first_msg: str,
    created_at: int,
    created_at_ms: int,
    cwd: str,
) -> None:
    db_path = codex_state_db()
    try:
        if not db_path.exists():
            raise FileNotFoundError(f'{db_path} does not exist')

        # Use a URI so Windows path quirks (drive letters, backslashes) are
        # handled consistently.
        conn = sqlite3.connect(f'file:{db_path.as_posix()}?mode=rwc', uri=True)
        cursor = conn.cursor()

        table_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'"
        ).fetchone()
        if not table_exists:
            raise RuntimeError('threads table does not exist in Codex state DB')

        updated_at = int(time.time())
        updated_at_ms = int(time.time() * 1000)

        values = {
            'id': session_uuid,
            'rollout_path': str(target_path),
            'created_at': created_at,
            'updated_at': updated_at,
            'source': 'vscode',
            'model_provider': 'openai',
            'cwd': cwd,
            'title': title,
            'sandbox_policy': '{"type":"danger-full-access"}',
            'approval_mode': 'never',
            'tokens_used': 0,
            'has_user_event': 1 if first_msg else 0,
            'archived': 0,
            'cli_version': CODEX_CLI_VERSION,
            'first_user_message': first_msg,
            'memory_mode': 'enabled',
            'model': DEFAULT_MODEL,
            'reasoning_effort': DEFAULT_REASONING_EFFORT,
            'created_at_ms': created_at_ms,
            'updated_at_ms': updated_at_ms,
            'thread_source': 'user',
            'preview': preview,
        }
        table_columns = [
            row[1] for row in cursor.execute('PRAGMA table_info(threads)').fetchall()
        ]
        insert_columns = [col for col in values if col in table_columns]
        placeholders = ', '.join(['?'] * len(insert_columns))
        sql = (
            f"INSERT OR REPLACE INTO threads "
            f"({', '.join(insert_columns)}) VALUES ({placeholders})"
        )
        cursor.execute(sql, tuple(values[col] for col in insert_columns))
        conn.commit()
        conn.close()
        update_session_index(session_uuid, title, updated_at_ms)
    except Exception as e:
        warn(f'Could not register session in Codex threads DB: {e}')
        warn('The rollout JSONL was still written; Codex will discover it on next scan.')


# ----------------------------------------------------------------------------
# Discovery / CLI helpers
# ----------------------------------------------------------------------------

def find_all_claude_sessions(project_dir: Optional[Path] = None) -> List[Path]:
    root = Path(project_dir) if project_dir else claude_projects_root()
    if not root.exists():
        return []
    sessions = [p for p in root.rglob('*.jsonl') if p.is_file()]
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions


def list_sessions(project_dir: Optional[Path] = None) -> None:
    sessions = find_all_claude_sessions(project_dir)
    info(f'Found {len(sessions)} Claude session(s) under {claude_projects_root()}')
    if not sessions:
        return

    print(f"\n{'Date':<12} {'Session ID':<36} {'Size':<10} Path")
    print('-' * 90)
    for path in sessions:
        size = path.stat().st_size
        size_str = f'{size / 1024:.1f}KB' if size < 1024 * 1024 else f'{size / 1024 / 1024:.1f}MB'
        try:
            with path.open('r', encoding='utf-8') as f:
                first_line = f.readline()
            first_entry = json.loads(first_line) if first_line.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            first_entry = {}

        session_id = (first_entry.get('sessionId') or 'unknown')[:36]
        date = (first_entry.get('timestamp') or 'unknown')[:10]
        print(f'{date:<12} {session_id:<36} {size_str:<10} {str(path)[:60]}...')


def preview_session(path: str | Path) -> None:
    p = Path(path)
    if not p.exists():
        error(f'File not found: {p}')
        return

    session = parse_claude_session(p)
    print(f'\nSession preview: {p.name}')
    print(f'Date: {session.date}')
    print(f'Session ID: {session.session_id}')
    print(f'Slug: {session.slug}')
    print(f'CWD: {session.cwd or "(none)"}')
    print(f'Total entries: {session.total_entries}')
    print(
        f'Parsed messages: {len(session.messages)} '
        f'(user={sum(1 for m in session.messages if m.kind == "text" and m.role == "user")}, '
        f'assistant={sum(1 for m in session.messages if m.kind == "text" and m.role == "assistant")}, '
        f'tool_calls={sum(1 for m in session.messages if m.kind == "tool_use")}, '
        f'tool_results={sum(1 for m in session.messages if m.kind == "tool_result")})'
    )

    print('\nFirst 3 messages:')
    for i, msg in enumerate(session.messages[:3]):
        if msg.kind == 'tool_use':
            content = f'{msg.tool_name} {stringify_claude_content(msg.tool_input)[:100]}'
        elif msg.kind == 'tool_result':
            content = msg.content[:100] + ('...' if len(msg.content) > 100 else '')
        else:
            content = msg.content[:100] + ('...' if len(msg.content) > 100 else '')
        print(f'  [{i + 1}] {msg.role}/{msg.kind}: {content}')


def convert_single(
    path: str | Path,
    cwd: Optional[str] = None,
    register: bool = True,
) -> Optional[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        error(f'File not found: {p}')
        return None
    session = parse_claude_session(p)
    result = convert_session(session, target_dir=None, register=register, cwd=cwd)

    if result['status'] == 'success':
        success('Conversion succeeded (Claude -> Codex)')
        print(f'  Source: {result["source"]}')
        print(f'  Target: {result["target"]}')
        print(f'  Session ID: {result["session_id"]}')
        print(f'  Title: {result["title"]}')
        print(f'  Date: {result["date"]}')
        print(
            f'  Messages: {result["user_messages"]} user + '
            f'{result["assistant_messages"]} assistant + '
            f'{result["tool_calls"]} tool calls + {result["tool_results"]} tool results'
        )
        print('\nOpen the project in Codex and the migrated session will appear in the history.')
    else:
        error(f'Conversion failed: {result}')
    return result


def convert_batch(project_dir: Optional[Path] = None) -> int:
    sessions = find_all_claude_sessions(project_dir)
    info(f'Batch conversion: {len(sessions)} session(s) found')
    if not sessions:
        return 0

    results: List[Dict[str, Any]] = []
    for i, path in enumerate(sessions):
        session = parse_claude_session(path)
        result = convert_session(session, target_dir=None)
        results.append(result)
        print(f'[{i + 1}/{len(sessions)}] {path.name}: {result["status"]}')

    ok = sum(1 for r in results if r['status'] == 'success')
    success(f'Done: {ok}/{len(sessions)} converted')
    return ok


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='claude_to_codex',
        description='Convert Claude session JSONL into a Codex rollout JSONL.',
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_list = sub.add_parser('list', help='List Claude sessions')
    p_list.add_argument('--project-dir', help='Override Claude project directory')

    p_preview = sub.add_parser('preview', help='Preview a Claude session without converting')
    p_preview.add_argument('path', help='Path to a Claude session .jsonl file')

    p_convert = sub.add_parser('convert', help='Convert a single Claude session to Codex')
    p_convert.add_argument('path', help='Path to a Claude session .jsonl file')
    p_convert.add_argument('--cwd', help='Override the working directory recorded in Codex')
    p_convert.add_argument(
        '--no-register',
        action='store_true',
        help='Write the rollout JSONL but skip registering in Codex state DB',
    )

    sub.add_parser('batch', help='Convert all Claude sessions found under the projects root')

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_dir: Optional[Path] = None
    if getattr(args, 'project_dir', None):
        project_dir = Path(args.project_dir).expanduser()

    if args.cmd == 'list':
        list_sessions(project_dir)
        return 0
    if args.cmd == 'preview':
        preview_session(args.path)
        return 0
    if args.cmd == 'convert':
        result = convert_single(
            args.path,
            cwd=args.cwd,
            register=not args.no_register,
        )
        return 0 if result and result['status'] == 'success' else 1
    if args.cmd == 'batch':
        ok = convert_batch(project_dir)
        return 0 if ok >= 0 else 1
    parser.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
