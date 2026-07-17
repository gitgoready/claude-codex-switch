#!/usr/bin/env python3
"""
Claude to Codex Session Converter
=================================
将 Claude 会话记录转换为 Codex 可用的历史会话格式

用法:
    # 列出所有 Claude 会话
    python3 claude_to_codex.py --list

    # 预览单个会话
    python3 claude_to_codex.py --preview ~/.claude/projects/-home-zy-work-project-data-value/xxx.jsonl

    # 转换单个会话
    python3 claude_to_codex.py --convert ~/.claude/projects/-home-zy-work-project-data-value/xxx.jsonl

    # 批量转换所有会话
    python3 claude_to_codex.py --batch

输出目录: ~/.codex/sessions/YYYY/MM/DD/
"""

import json
import os
import sys
import uuid
import glob
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

# Codex 会话输出目录。Codex history 原生按 ~/.codex/sessions/YYYY/MM/DD 扫描/读取 rollout。
CODEX_HOME = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser()
CODEX_SESSIONS_DIR = str(CODEX_HOME / 'sessions')
CODEX_STATE_DB = CODEX_HOME / 'state_5.sqlite'
CODEX_SESSION_INDEX = CODEX_HOME / 'session_index.jsonl'
DEFAULT_CWD = '/home/zy/work/project/data_value'
CODEX_CLI_VERSION = '0.133.0-alpha.1'
DEFAULT_MODEL = 'gpt-5.5'
DEFAULT_REASONING_EFFORT = 'xhigh'

# Claude 项目目录
CLAUDE_PROJECT_DIR = '/home/zy/.claude/projects'


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Message:
    """代表一条对话消息"""
    role: str  # 'user' or 'assistant' or 'system' or 'tool'
    content: str
    timestamp: str = ''
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: str = 'text'  # text, tool_use, tool_result
    tool_name: str = ''
    tool_input: Any = None
    tool_use_id: str = ''
    is_error: bool = False


@dataclass
class ClaudeSession:
    """解析后的 Claude 会话数据"""
    path: str
    session_id: str
    slug: str
    date: str
    messages: List[Message] = field(default_factory=list)
    total_entries: int = 0


# ============================================================================
# PARSER
# ============================================================================

TOOL_OUTPUT_MAX_CHARS = 12000
TOOL_INPUT_MAX_CHARS = 4000


def stringify_claude_content(value: Any) -> str:
    """Convert Claude text/tool result content into readable text."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
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
        return '\n'.join(part for part in parts if part)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - limit
    return (
        text[:head]
        + f"\n\n[... omitted {omitted} chars from migrated Claude tool output ...]\n\n"
        + text[-tail:]
    )


def strip_leading_synthetic_context(content: str) -> str:
    """Drop Claude IDE/environment wrappers while preserving the real prompt after them."""
    text = content.strip()
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
    content = content.strip()
    if not content:
        return
    if role == 'user':
        content = strip_leading_synthetic_context(content)
        if not content:
            return
    # Filter out Claude synthetic local-command messages for cleaner conversion.
    if '<local-command' in content or '<command-' in content:
        return
    messages.append(Message(
        role=role,
        content=content,
        timestamp=timestamp,
        uuid=entry_uuid,
    ))

def parse_claude_session(session_path: str) -> ClaudeSession:
    """
    解析 Claude JSONL 文件为结构化数据
    """
    with open(session_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    session = ClaudeSession(
        path=session_path,
        session_id='',
        slug='',
        date='',
        total_entries=len(lines)
    )

    messages: List[Message] = []
    first_session_id = ''
    first_slug = ''
    first_date = ''
    tool_names_by_id: Dict[str, str] = {}

    for line in lines:
        try:
            entry = json.loads(line.strip())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Skip malformed lines
            continue

        msg_type = entry.get('type')

        # Collect metadata
        if not first_session_id and entry.get('sessionId'):
            first_session_id = entry['sessionId']
        if not first_slug and entry.get('slug'):
            first_slug = entry['slug']
        if not first_date and entry.get('timestamp'):
            first_date = entry['timestamp'][:10]

        if msg_type in ('user', 'assistant'):
            # Get content
            msg_content = entry.get('message', {}).get('content', '')
            timestamp = entry.get('timestamp', '')
            entry_uuid = entry.get('uuid', str(uuid.uuid4()))

            # Handle different content formats
            if isinstance(msg_content, list):
                # Claude content arrays can interleave text, tool_use, and tool_result.
                text_parts = []
                def flush_text() -> None:
                    nonlocal text_parts
                    append_text_message(
                        messages,
                        msg_type,
                        '\n\n'.join(text_parts),
                        timestamp,
                        entry_uuid,
                    )
                    text_parts = []

                for c in msg_content:
                    if isinstance(c, dict):
                        if c.get('type') == 'text':
                            text_parts.append(c.get('text', ''))
                        elif c.get('type') == 'tool_use':
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
                        elif c.get('type') == 'tool_result':
                            flush_text()
                            tool_id = c.get('tool_use_id', '')
                            result = truncate_middle(
                                stringify_claude_content(c.get('content', '')),
                                TOOL_OUTPUT_MAX_CHARS,
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
                        elif c.get('type') == 'thinking':
                            # Include thinking for assistant
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
            # Handle system messages (like compact_boundary, migration_boundary)
            subtype = entry.get('subtype', '')
            content = entry.get('content', '')
            if content:
                # Include migration boundary messages
                if subtype == 'migration_boundary':
                    messages.append(Message(
                        role='system',
                        content=f'[Migration] {content}',
                        timestamp=entry.get('timestamp', ''),
                        uuid=entry.get('uuid', str(uuid.uuid4()))
                    ))

    # Set metadata from first-seen values. Using sets here made the output
    # nondeterministic for multi-day sessions.
    session.session_id = first_session_id
    session.slug = first_slug
    session.date = first_date

    session.messages = messages
    return session


# ============================================================================
# CODEX FORMAT HELPERS
# ============================================================================

def parse_iso_timestamp(timestamp: str) -> Optional[datetime]:
    """Parse Codex/Claude ISO timestamps, accepting trailing Z."""
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError:
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


def fallback_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'


def compact_text(text: str, limit: Optional[int] = None) -> str:
    value = ' '.join((text or '').strip().split())
    if limit and len(value) > limit:
        return value[:limit - 3] + '...'
    return value


def is_display_user_message(content: str) -> bool:
    """Skip Claude/Codex synthetic user events when choosing a title."""
    text = (content or '').strip()
    if not text:
        return False
    synthetic_prefixes = (
        '<local-command',
        '<local-command-caveat',
        '<command-',
        '<command-name>',
        '<ide_opened_file>',
        '<environment_context>',
    )
    return not text.startswith(synthetic_prefixes)


def first_display_user_message(messages: List[Message]) -> str:
    for msg in messages:
        if msg.kind == 'text' and msg.role == 'user' and is_display_user_message(msg.content):
            return compact_text(msg.content)
    for msg in messages:
        if msg.kind == 'text' and msg.role == 'user':
            return compact_text(msg.content)
    return ''


def update_session_index(thread_id: str, title: str, updated_at_ms: int) -> None:
    """Update Codex's lightweight session index used by some history surfaces."""
    updated_at = (
        datetime.utcfromtimestamp(updated_at_ms / 1000)
        .isoformat(timespec='microseconds')
        + 'Z'
    )
    items = {}
    if CODEX_SESSION_INDEX.exists():
        with CODEX_SESSION_INDEX.open('r', encoding='utf-8') as f:
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

    items[thread_id] = {
        'id': thread_id,
        'thread_name': title,
        'updated_at': updated_at,
    }
    ordered = sorted(items.values(), key=lambda item: item.get('updated_at', ''), reverse=True)
    CODEX_SESSION_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with CODEX_SESSION_INDEX.open('w', encoding='utf-8') as f:
        for item in ordered:
            f.write(json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n')


# ============================================================================
# FORMAT CREATOR (Codex format)
# ============================================================================

def create_codex_entry_index() -> int:
    """全局 entry 索引器"""
    return {'index': 0, 'turn_id': 0}


def create_session_meta(session_id: str, cwd: str, timestamp: str) -> Dict:
    """创建 Codex session_meta 条目"""
    return {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": timestamp,
            "cwd": cwd,
            "originator": "codex_vscode",
            "cli_version": CODEX_CLI_VERSION,
            "source": "vscode",
            "thread_source": "user",
            "model_provider": "openai",
            "base_instructions": {
                "text": "You are Codex, a coding agent based on GPT-5. You and the user share one workspace, and your job is to collaborate with them until their goal is genuinely handled."
            }
        }
    }


def create_user_message_entry(msg: Message, index: int, turn_id: str) -> Dict:
    """创建 Codex user message 条目"""
    return {
        "timestamp": msg.timestamp or datetime.utcnow().isoformat() + 'Z',
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": msg.content
                }
            ]
        }
    }


def create_assistant_message_entry(msg: Message, index: int, turn_id: str) -> Dict:
    """创建 Codex assistant message 条目"""
    return {
        "timestamp": msg.timestamp or datetime.utcnow().isoformat() + 'Z',
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": msg.content
                }
            ],
            "phase": "commentary"
        }
    }


def normalized_tool_name(tool_name: str) -> str:
    safe = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in (tool_name or 'tool'))
    return safe or 'tool'


def tool_call_arguments(msg: Message, cwd: str) -> Dict[str, Any]:
    tool_input = msg.tool_input if isinstance(msg.tool_input, dict) else {"input": msg.tool_input}
    if msg.tool_name == 'Bash':
        args = {"cmd": str(tool_input.get('command', ''))}
        description = tool_input.get('description')
        if description:
            args["description"] = str(description)
        # Codex command calls normally carry cwd. Adding it improves resume context.
        args["workdir"] = cwd
        return args
    return {
        "tool": msg.tool_name or "unknown_tool",
        "input": tool_input,
    }


def create_tool_call_entry(msg: Message, cwd: str) -> Dict:
    """创建 Codex function_call 条目，保留 Claude 工具输入。"""
    timestamp = msg.timestamp or fallback_timestamp()
    if msg.tool_name == 'Bash':
        name = 'exec_command'
    else:
        name = f"claude_{normalized_tool_name(msg.tool_name)}"
    args = tool_call_arguments(msg, cwd)
    arguments = json.dumps(args, ensure_ascii=False, separators=(',', ':'))
    if len(arguments) > TOOL_INPUT_MAX_CHARS:
        args = {
            "tool": msg.tool_name or "unknown_tool",
            "input_preview": truncate_middle(
                stringify_claude_content(msg.tool_input),
                TOOL_INPUT_MAX_CHARS,
            ),
            "truncated": True,
        }
        arguments = json.dumps(args, ensure_ascii=False, separators=(',', ':'))
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": name,
            "arguments": arguments,
            "call_id": msg.tool_use_id or msg.uuid,
        }
    }


def create_tool_result_entry(msg: Message) -> Dict:
    """创建 Codex function_call_output 条目，保留 Claude 工具输出。"""
    timestamp = msg.timestamp or fallback_timestamp()
    output = msg.content or ''
    if msg.is_error:
        output = "[Claude tool error]\n" + output
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": msg.tool_use_id or msg.uuid,
            "output": output,
        }
    }


def create_task_started_entry(turn_id: str, timestamp: str) -> Dict:
    """创建 Codex task_started 事件"""
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "task_started",
            "turn_id": turn_id,
            "started_at": epoch_seconds(timestamp),
            "model_context_window": 258400,
            "collaboration_mode_kind": "default"
        }
    }


def create_user_event_entry(msg: Message) -> Dict:
    """创建 Codex user_message 事件。当前 Codex 解析器要求包含 message 字段。"""
    timestamp = msg.timestamp or fallback_timestamp()
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "user_message",
            "message": msg.content,
            "images": [],
            "local_images": [],
            "text_elements": []
        }
    }


def create_agent_message_event(msg: Message) -> Dict:
    """创建 Codex agent_message 事件。旧脚本缺少 message 字段，会导致 thread/list 解析失败。"""
    timestamp = msg.timestamp or fallback_timestamp()
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "agent_message",
            "message": msg.content,
            "phase": "commentary",
            "memory_citation": None
        }
    }


def create_task_complete_entry(turn_id: str, timestamp: str, last_agent_message: str, started_at: int) -> Dict:
    completed_at = epoch_seconds(timestamp)
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": turn_id,
            "last_agent_message": last_agent_message or "",
            "completed_at": completed_at,
            "duration_ms": max(0, (completed_at - started_at) * 1000),
            "time_to_first_token_ms": 0
        }
    }


# ============================================================================
# CONVERTER
# ============================================================================

def convert_session(
    session: ClaudeSession,
    target_dir: str,
    register: bool = True,
    cwd: str = DEFAULT_CWD,
    session_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """转换单个 Claude 会话为 Codex 格式"""

    messages = session.messages
    if not messages:
        return {'status': 'no_messages', 'source': session.path}

    # 生成 Codex session ID - 必须是 UUID 格式: XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
    # Codex 使用这种格式来识别会话
    session_uuid = session_uuid or str(uuid.uuid4())  # e.g. "019e4d0e-b947-7bb3-9aa2-b314a62913e0"

    first_ts = messages[0].timestamp or fallback_timestamp()
    last_ts = messages[-1].timestamp or first_ts
    session_date = session.date or first_ts[:10] or datetime.utcnow().date().isoformat()

    # 目标文件路径
    os.makedirs(target_dir, exist_ok=True)
    date_dir = session_date.replace('-', '/')
    target_subdir = os.path.join(target_dir, date_dir)
    os.makedirs(target_subdir, exist_ok=True)

    # 文件名格式: rollout-YYYY-MM-DDTHH-MM-SS-UUID.jsonl
    # 时间部分使用 session 的 timestamp 或默认 00-00-00
    time_str = "00-00-00"
    if first_ts:
        ts = first_ts  # e.g. "2026-04-27T14:14:45.629Z"
        time_str = ts[11:19].replace(':', '-')  # "14-14-45"
    target_path = os.path.join(target_subdir, f'rollout-{session_date}T{time_str}-{session_uuid}.jsonl')

    # 构建 Codex 格式的 entry 列表
    entries = []

    # 1. session_meta
    entries.append(create_session_meta(session_uuid, cwd, first_ts))

    # 2. 遍历消息，转换为 Codex 格式
    # 当前 Codex rollout 可解析的关键形状:
    # session_meta -> task_started -> user response_item -> turn_context
    # -> user_message event -> assistant response_item/agent_message event -> task_complete
    active_turn_id = ''
    active_turn_started_at = 0
    last_agent_message = ''

    for msg in messages:
        timestamp = msg.timestamp or datetime.utcnow().isoformat() + 'Z'

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
                    active_turn_id,
                    timestamp,
                    last_agent_message,
                    active_turn_started_at,
                ))

            active_turn_id = str(uuid.uuid4())
            active_turn_started_at = epoch_seconds(timestamp)
            last_agent_message = ''
            entries.append(create_task_started_entry(active_turn_id, timestamp))

            # User message
            entries.append(create_user_message_entry(msg, len(entries), active_turn_id))

            # Add turn_context after user message (signals end of user input)
            entries.append({
                "timestamp": timestamp,
                "type": "turn_context",
                "payload": {
                    "turn_id": active_turn_id,
                    "cwd": cwd,
                    "current_date": session_date,
                    "timezone": "Asia/Shanghai",
                    "approval_policy": "never",
                    "sandbox_policy": {"type": "danger-full-access"},
                    "permission_profile": {"type": "disabled"},
                    "model": DEFAULT_MODEL,
                    "effort": DEFAULT_REASONING_EFFORT
                }
            })
            entries.append(create_user_event_entry(msg))

        elif msg.role == 'assistant':
            if not active_turn_id:
                active_turn_id = str(uuid.uuid4())
                active_turn_started_at = epoch_seconds(timestamp)
                entries.append(create_task_started_entry(active_turn_id, timestamp))

            entries.append(create_agent_message_event(msg))
            # Assistant message
            entries.append(create_assistant_message_entry(msg, len(entries), active_turn_id))
            last_agent_message = msg.content

        elif msg.role == 'system':
            # 系统消息跳过（迁移信息已在开头包含）
            pass

    if active_turn_id:
        entries.append(create_task_complete_entry(
            active_turn_id,
            last_ts,
            last_agent_message,
            active_turn_started_at,
        ))

    # 写入文件
    with open(target_path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    # 注册会话到 Codex 的 threads 数据库
    if register:
        import sqlite3
        try:
            if not CODEX_STATE_DB.exists():
                raise FileNotFoundError(f'{CODEX_STATE_DB} does not exist')

            conn = sqlite3.connect(CODEX_STATE_DB)
            cursor = conn.cursor()

            table_exists = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'"
            ).fetchone()
            if not table_exists:
                raise RuntimeError('threads table does not exist')

            first_msg = first_display_user_message(messages)
            title = compact_text(first_msg, 200) or f'Migrated from Claude {session_date}'
            preview = compact_text(first_msg, 500) or title
            created_at = epoch_seconds(first_ts)
            created_at_ms = epoch_millis(first_ts)
            updated_at = int(time.time())
            updated_at_ms = int(time.time() * 1000)

            values = {
                'id': session_uuid,
                'rollout_path': target_path,
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
            print(f"Warning: Could not register session in threads database: {e}")

    return {
        'status': 'success',
        'source': session.path,
        'target': target_path,
        'session_id': session_uuid,
        'date': session_date,
        'total_messages': len(messages),
        'user_messages': sum(1 for m in messages if m.kind == 'text' and m.role == 'user'),
        'assistant_messages': sum(1 for m in messages if m.kind == 'text' and m.role == 'assistant'),
        'tool_calls': sum(1 for m in messages if m.kind == 'tool_use'),
        'tool_results': sum(1 for m in messages if m.kind == 'tool_result'),
    }


# ============================================================================
# UTILITIES
# ============================================================================

def find_all_claude_sessions(project_dir: Optional[str] = None) -> List[str]:
    """查找所有 Claude 会话文件"""
    if project_dir is None:
        project_dir = CLAUDE_PROJECT_DIR

    sessions = []
    for root, dirs, files in os.walk(project_dir):
        for f in files:
            if f.endswith('.jsonl'):
                sessions.append(os.path.join(root, f))

    sessions.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return sessions


def list_sessions():
    """列出所有 Claude 会话"""
    sessions = find_all_claude_sessions()

    print(f"\n找到 {len(sessions)} 个 Claude 会话:\n")
    print(f"{'日期':<12} {'Session ID':<36} {'大小':<10} {'路径'}")
    print("-" * 90)

    for path in sessions:
        size = os.path.getsize(path)
        size_str = f"{size/1024:.1f}KB" if size < 1024*1024 else f"{size/1024/1024:.1f}MB"

        # 提取 session info
        with open(path, 'r') as f:
            first_line = f.readline()
            first_entry = json.loads(first_line) if first_line else {}

        session_id = first_entry.get('sessionId', 'unknown')[:36]
        slug = first_entry.get('slug', 'unknown')
        date = first_entry.get('timestamp', '')[:10] if first_entry.get('timestamp') else 'unknown'

        print(f"{date:<12} {session_id:<36} {size_str:<10} {path[:50]}...")


def preview_session(path: str):
    """预览单个会话内容"""
    if not os.path.exists(path):
        print(f"错误: 文件不存在 - {path}")
        return

    session = parse_claude_session(path)

    print(f"\n会话预览: {os.path.basename(path)}")
    print(f"日期: {session.date}")
    print(f"Session ID: {session.session_id}")
    print(f"Slug: {session.slug}")
    print(f"总条目数: {session.total_entries}")
    print(
        f"解析消息数: {len(session.messages)} "
        f"(user={sum(1 for m in session.messages if m.kind=='text' and m.role=='user')}, "
        f"assistant={sum(1 for m in session.messages if m.kind=='text' and m.role=='assistant')}, "
        f"tool_calls={sum(1 for m in session.messages if m.kind=='tool_use')}, "
        f"tool_results={sum(1 for m in session.messages if m.kind=='tool_result')})"
    )

    print(f"\n前 3 条消息:")
    for i, msg in enumerate(session.messages[:3]):
        if msg.kind == 'tool_use':
            content = f"{msg.tool_name} {stringify_claude_content(msg.tool_input)[:100]}"
        elif msg.kind == 'tool_result':
            content = msg.content[:100] + '...' if len(msg.content) > 100 else msg.content
        else:
            content = msg.content[:100] + '...' if len(msg.content) > 100 else msg.content
        print(f"  [{i+1}] {msg.role}/{msg.kind}: {content}")


def convert_single(path: str):
    """转换单个会话"""
    if not os.path.exists(path):
        print(f"错误: 文件不存在 - {path}")
        return

    session = parse_claude_session(path)
    result = convert_session(session, CODEX_SESSIONS_DIR)

    if result['status'] == 'success':
        print(f"\n转换成功!")
        print(f"  源文件: {result['source']}")
        print(f"  目标: {result['target']}")
        print(f"  Session ID: {result['session_id']}")
        print(f"  日期: {result['date']}")
        print(
            f"  消息数: {result['user_messages']} user + "
            f"{result['assistant_messages']} assistant + "
            f"{result['tool_calls']} tool calls + {result['tool_results']} tool results"
        )
        print(f"\n在 Codex 中打开项目即可看到该会话")
    else:
        print(f"\n转换失败: {result}")


def convert_batch():
    """批量转换会话"""
    sessions = find_all_claude_sessions()

    print(f"\n批量转换: 找到 {len(sessions)} 个会话")

    results = []
    for i, path in enumerate(sessions):
        session = parse_claude_session(path)
        result = convert_session(session, CODEX_SESSIONS_DIR)
        results.append(result)
        print(f"[{i+1}/{len(sessions)}] {os.path.basename(path)}: {result['status']}")

    success = sum(1 for r in results if r['status'] == 'success')
    print(f"\n完成: {success}/{len(sessions)} 成功转换")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == '--list':
        list_sessions()

    elif cmd == '--preview':
        if len(sys.argv) < 3:
            print("用法: --preview <session_path>")
            sys.exit(1)
        preview_session(sys.argv[2])

    elif cmd == '--convert':
        if len(sys.argv) < 3:
            print("用法: --convert <session_path>")
            sys.exit(1)
        convert_single(sys.argv[2])

    elif cmd == '--batch':
        convert_batch()

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)
