#!/usr/bin/env python3
"""
Codex to Claude Session Converter
==================================
将 ~/.codex/sessions 下的会话记录转换为 .claude 目录下可读取的格式

用法:
    # 列出所有 Codex 会话
    python3 converter.py --list

    # 预览单个会话（不转换）
    python3 converter.py --preview ~/.codex/sessions/2026/05/22/rollout-xxx.jsonl

    # 转换单个会话
    python3 converter.py --convert ~/.codex/sessions/2026/05/22/rollout-xxx.jsonl

    # 批量转换所有会话
    python3 converter.py --batch

    # 转换指定日期范围的会话
    python3 converter.py --convert --date 2026-05-01 --end-date 2026-05-24

输出目录: ~/.claude/projects/-home-zy-work-project-data-value/
"""

import json
import os
import sys
import uuid
import glob
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

# Claude 项目目录
CLAUDE_PROJECT_DIR = '/home/zy/.claude/projects/-home-zy-work-project-data-value'

# Codex 会话目录
CODEX_SESSIONS_DIR = os.path.expanduser('~/.codex/sessions')


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Message:
    """代表一条对话消息"""
    role: str  # 'user' or 'assistant' or 'system'
    content: str
    timestamp: str = ''
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class CodexSession:
    """解析后的 Codex 会话数据"""
    path: str
    date: str
    messages: List[Message] = field(default_factory=list)
    turn_context: Dict[str, Any] = field(default_factory=dict)
    compacted_history: List[Dict] = field(default_factory=list)
    tool_results: List[Dict] = field(default_factory=list)
    total_lines: int = 0


# ============================================================================
# PARSER
# ============================================================================

def parse_codex_session(session_path: str) -> CodexSession:
    """
    解析 Codex rollout JSONL 文件为结构化数据
    提取对话流程，保留工具交互
    """
    with open(session_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    session = CodexSession(
        path=session_path,
        date='',
        total_lines=len(lines)
    )

    messages = []
    current_turn_context = {}
    compacted_history = []

    i = 0
    while i < len(lines):
        entry = json.loads(lines[i])
        entry_type = entry.get('type')

        if entry_type == 'session_meta':
            payload = entry.get('payload', {})
            session.date = payload.get('timestamp', '')[:10]

        elif entry_type == 'turn_context':
            current_turn_context = entry.get('payload', {})

        elif entry_type == 'response_item':
            payload = entry.get('payload', {})
            role = payload.get('role')
            content = payload.get('content', [])

            if role == 'user' and content:
                # 用户消息 - user 使用 input_text
                text_parts = []
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'input_text':
                        text = c.get('text', '')
                        # 跳过系统级指令，保留实际用户输入
                        if not text.startswith('<permissions') and \
                           not text.startswith('<skills_instructions') and \
                           not text.startswith('<environment_context'):
                            text_parts.append(text)
                if text_parts:
                    text = '\n\n'.join(text_parts)
                    messages.append(Message(
                        role='user',
                        content=text,
                        timestamp=entry.get('timestamp', '')
                    ))

            elif role == 'developer' and content:
                # 开发者消息 (Codex 的系统/助手指令)
                text_parts = []
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'input_text':
                        text_parts.append(c.get('text', ''))
                if text_parts:
                    text = '\n\n'.join(text_parts)
                    # 跳过权限和协作模式指令
                    if not text.startswith('<permissions') and \
                       not text.startswith('<collaboration_mode'):
                        messages.append(Message(
                            role='assistant',
                            content=text,
                            timestamp=entry.get('timestamp', '')
                        ))

            elif role == 'assistant' and content:
                # 助手消息 - assistant 使用 output_text
                text_parts = []
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'output_text':
                        text_parts.append(c.get('text', ''))
                if text_parts:
                    text = '\n\n'.join(text_parts)
                    messages.append(Message(
                        role='assistant',
                        content=text,
                        timestamp=entry.get('timestamp', '')
                    ))

        elif entry_type == 'user_message':
            # 独立的用户消息条目
            payload = entry.get('payload', {})
            content_list = payload.get('content', [])
            for c in content_list:
                if isinstance(c, dict) and c.get('type') == 'input_text':
                    text = c.get('text', '')
                    if text and not text.startswith('<permissions'):
                        messages.append(Message(
                            role='user',
                            content=text,
                            timestamp=entry.get('timestamp', '')
                        ))
                        break

        elif entry_type == 'compacted':
            # 压缩历史
            replacement_history = entry.get('payload', {}).get('replacement_history', [])
            for msg in replacement_history:
                role = msg.get('role')
                content = msg.get('content', [])
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'input_text':
                        compacted_history.append({
                            'role': role,
                            'content': c.get('text', '')
                        })

        i += 1

    session.messages = messages
    session.turn_context = current_turn_context
    session.compacted_history = compacted_history

    return session


# ============================================================================
# FORMAT CREATOR
# ============================================================================

def create_claude_entry(msg: Message, session_id: str, parent_uuid: Optional[str],
                        project_slug: str, cwd: str) -> Dict:
    """从 Message 创建 Claude 格式的 JSON 条目"""

    claude_role = 'user' if msg.role == 'user' else 'assistant'

    # 注意：Claude 原生格式中用户消息内容是字符串，助手消息内容是数组
    if msg.role == 'user':
        content = msg.content
    else:
        content = [{"type": "text", "text": msg.content}]

    return {
        'parentUuid': parent_uuid,
        'isSidechain': False,
        'type': msg.role,
        'message': {
            'role': claude_role,
            'content': content
        },
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
        'slug': project_slug
    }


def create_migration_boundary(session: CodexSession, session_id: str, project_slug: str, cwd: str) -> Dict:
    """创建迁移边界系统消息"""
    return {
        'parentUuid': None,
        'isSidechain': False,
        'type': 'system',
        'subtype': 'migration_boundary',
        'content': f'[From Codex] Session migrated from: {os.path.basename(session.path)} (dated {session.date})',
        'isMeta': True,
        'uuid': str(uuid.uuid4()),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'userType': 'external',
        'entrypoint': 'codex-vscode-migrated',
        'cwd': cwd,
        'sessionId': session_id,
        'version': '2.1.120',
        'gitBranch': 'main',
        'slug': project_slug
    }


# ============================================================================
# CONVERTER
# ============================================================================

def convert_session(session: CodexSession, target_dir: str, project_slug: str) -> Dict[str, Any]:
    """转换单个 Codex 会话为 Claude 格式"""

    messages = session.messages
    if not messages:
        return {'status': 'no_messages', 'source': session.path}

    session_id = str(uuid.uuid4())
    cwd = session.turn_context.get('cwd', '/home/zy/work/project/data_value')

    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f'{session_id}.jsonl')

    converted_entries = []
    parent_uuid = None

    # 1. 创建迁移边界消息
    migration_entry = create_migration_boundary(session, session_id, project_slug, cwd)
    converted_entries.append(migration_entry)
    parent_uuid = migration_entry['uuid']

    # 2. 添加压缩历史（如果有）
    if session.compacted_history:
        history_text = '\n'.join([
            f"{h.get('role','')}: {h.get('content','')[:300]}"
            for h in session.compacted_history[-5:]
            if h.get('content')
        ])
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
                'slug': project_slug
            }
            converted_entries.append(history_entry)
            parent_uuid = history_entry['uuid']

    # 3. 转换所有消息
    user_count = 0
    assistant_count = 0

    for msg in messages:
        entry = create_claude_entry(msg, session_id, parent_uuid, project_slug, cwd)
        converted_entries.append(entry)
        parent_uuid = entry['uuid']

        if msg.role == 'user':
            user_count += 1
        elif msg.role == 'assistant':
            assistant_count += 1

    # 4. 写入文件
    with open(target_path, 'w', encoding='utf-8') as f:
        for entry in converted_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return {
        'status': 'success',
        'source': session.path,
        'target': target_path,
        'session_id': session_id,
        'date': session.date,
        'total_messages': len(messages),
        'user_messages': user_count,
        'assistant_messages': assistant_count,
        'cwd': cwd
    }


# ============================================================================
# UTILITIES
# ============================================================================

def find_all_codex_sessions() -> List[str]:
    """查找所有 Codex 会话文件"""
    pattern = os.path.join(CODEX_SESSIONS_DIR, '**', 'rollout-*.jsonl')
    sessions = glob.glob(pattern, recursive=True)
    sessions.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return sessions


def list_sessions():
    """列出所有 Codex 会话"""
    sessions = find_all_codex_sessions()

    print(f"\n找到 {len(sessions)} 个 Codex 会话:\n")
    print(f"{'日期':<12} {'时间':<12} {'大小':<10} {'路径'}")
    print("-" * 80)

    for path in sessions:
        size = os.path.getsize(path)
        size_str = f"{size/1024:.1f}KB" if size < 1024*1024 else f"{size/1024/1024:.1f}MB"

        # 提取日期时间
        basename = os.path.basename(path)
        # rollout-2026-05-22T08-21-11-019e4d0e-...jsonl
        parts = basename.replace('rollout-', '').replace('.jsonl', '').split('T')
        date_str = parts[0] if len(parts) > 0 else 'unknown'
        time_str = parts[1][:8] if len(parts) > 1 else ''

        print(f"{date_str:<12} {time_str:<12} {size_str:<10} {path[:60]}...")


def preview_session(path: str):
    """预览单个会话内容"""
    if not os.path.exists(path):
        print(f"错误: 文件不存在 - {path}")
        return

    session = parse_codex_session(path)

    print(f"\n会话预览: {os.path.basename(path)}")
    print(f"日期: {session.date}")
    print(f"总行数: {session.total_lines}")
    print(f"解析消息数: {len(session.messages)} (user={sum(1 for m in session.messages if m.role=='user')}, assistant={sum(1 for m in session.messages if m.role=='assistant')})")
    print(f"CWD: {session.turn_context.get('cwd', 'N/A')}")

    print(f"\n前 3 条消息:")
    for i, msg in enumerate(session.messages[:3]):
        content = msg.content[:100] + '...' if len(msg.content) > 100 else msg.content
        print(f"  [{i+1}] {msg.role}: {content}")


def convert_single(path: str):
    """转换单个会话"""
    if not os.path.exists(path):
        print(f"错误: 文件不存在 - {path}")
        return

    session = parse_codex_session(path)
    result = convert_session(session, CLAUDE_PROJECT_DIR, '-home-zy-work-project-data-value')

    if result['status'] == 'success':
        print(f"\n转换成功!")
        print(f"  源文件: {result['source']}")
        print(f"  目标: {result['target']}")
        print(f"  会话ID: {result['session_id']}")
        print(f"  日期: {result['date']}")
        print(f"  消息数: {result['user_messages']} user + {result['assistant_messages']} assistant")
        print(f"\n在 Claude Code 中打开此项目即可看到该会话")
    else:
        print(f"\n转换失败: {result}")


def convert_batch(start_date: Optional[str] = None, end_date: Optional[str] = None):
    """批量转换会话"""
    sessions = find_all_codex_sessions()

    # 按日期过滤
    if start_date:
        sessions = [s for s in sessions if os.path.basename(s) >= f"rollout-{start_date}"]
    if end_date:
        sessions = [s for s in sessions if os.path.basename(s) <= f"rollout-{end_date}T"]

    print(f"\n批量转换: 找到 {len(sessions)} 个会话")

    results = []
    for i, path in enumerate(sessions):
        session = parse_codex_session(path)
        result = convert_session(session, CLAUDE_PROJECT_DIR, '-home-zy-work-project-data-value')
        results.append(result)
        print(f"[{i+1}/{len(sessions)}] {os.path.basename(path)}: {result['status']}")

    success = sum(1 for r in results if r['status'] == 'success')
    print(f"\n完成: {success}/{len(sessions)} 成功转换")

    # 保存转换报告
    report_path = os.path.join(CLAUDE_PROJECT_DIR, 'migration_report.json')
    with open(report_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"报告已保存: {report_path}")


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
        # 支持 --convert <path> 或 --convert --date <start> --end-date <end>
        if len(sys.argv) >= 3 and not sys.argv[2].startswith('--'):
            # 单个文件转换
            convert_single(sys.argv[2])
        elif '--date' in sys.argv:
            # 日期范围转换
            idx = sys.argv.index('--date')
            start = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
            end_idx = sys.argv.index('--end-date') if '--end-date' in sys.argv else -1
            end = sys.argv[end_idx + 1] if end_idx > 0 else None
            convert_batch(start, end)
        else:
            print("用法:\n  --convert <session_path>\n  --convert --date 2026-05-01 --end-date 2026-05-24")
            sys.exit(1)

    elif cmd == '--batch':
        convert_batch()

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)