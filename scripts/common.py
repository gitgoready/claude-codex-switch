"""Shared utilities for the codex_converter skill.

Cross-platform path resolution, title formatting, and Claude/Codex
discovery helpers used by both directions of conversion.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ----------------------------------------------------------------------------
# Path discovery (Windows / Linux / macOS)
# ----------------------------------------------------------------------------

def claude_home() -> Path:
    """Return the Claude config directory.

    Honors CLAUDE_HOME if set, otherwise defaults to ~/.claude.
    """
    env = os.environ.get('CLAUDE_HOME')
    if env:
        return Path(env).expanduser()
    return Path.home() / '.claude'


def claude_projects_root() -> Path:
    """Return the directory that holds per-project Claude session folders."""
    return claude_home() / 'projects'


def codex_home() -> Path:
    """Return the Codex config directory.

    Honors CODEX_HOME if set, otherwise defaults to ~/.codex.
    """
    env = os.environ.get('CODEX_HOME')
    if env:
        return Path(env).expanduser()
    return Path.home() / '.codex'


def codex_sessions_dir() -> Path:
    return codex_home() / 'sessions'


def codex_state_db() -> Path:
    return codex_home() / 'state_5.sqlite'


def codex_session_index() -> Path:
    return codex_home() / 'session_index.jsonl'


def is_windows() -> bool:
    return os.name == 'nt' or sys.platform.startswith('win')


def is_macos() -> bool:
    return sys.platform == 'darwin'


def is_linux() -> bool:
    return sys.platform.startswith('linux')


# ----------------------------------------------------------------------------
# Project slug encoding (Claude encodes cwd into the project folder name)
# ----------------------------------------------------------------------------

_SLUG_REPLACE = re.compile(r'[^A-Za-z0-9._-]')


def encode_claude_project_slug(cwd: str | Path) -> str:
    """Encode a working directory the way Claude does for project folder names.

    Claude replaces path separators with '-' and keeps other safe characters.
    Example: /home/zy/work/proj  ->  -home-zy-work-proj
             C:\\Users\\me\\proj  ->  -C--Users-me-proj  (close enough; Claude
    uses the same scheme but with mixed slashes normalized.)
    """
    p = str(cwd).replace('\\', '/')
    # Strip trailing slash, then turn each '/' into '-'
    p = p.rstrip('/')
    return _SLUG_REPLACE.sub('-', p)


def decode_claude_project_slug(slug: str) -> str:
    """Best-effort reverse of encode_claude_project_slug.

    We can't perfectly invert the encoding (a literal '-' in a folder name
    collides with the separator), but for typical cwd paths this returns
    something readable. Used only for display.
    """
    if not slug:
        return ''
    if slug.startswith('-'):
        # Leading '-' came from a leading '/' or a drive letter like 'C:'.
        # On Windows the encoded form for 'C:\\Users\\me' is '-C--Users-me',
        # so we restore it as 'C:/Users/me'.
        rest = slug[1:]
        match = re.match(r'^([A-Za-z])-(.*)$', rest)
        if match:
            drive = match.group(1)
            tail = match.group(2).replace('-', '/')
            return f'{drive}:/{tail}'
        return '/' + rest.replace('-', '/')
    return slug.replace('-', '/')


def list_claude_project_dirs() -> list[Path]:
    """List all per-project session folders under ~/.claude/projects."""
    root = claude_projects_root()
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)


def autodetect_claude_project_dir(cwd_hint: Optional[str] = None) -> Optional[Path]:
    """Pick a Claude project dir.

    Strategy:
      1. If CLAUDE_PROJECT_DIR env is set, use it.
      2. If cwd_hint is given, encode it and check if that folder exists.
      3. If exactly one project dir exists, pick it.
      4. Otherwise return None and let the caller prompt the user.
    """
    env = os.environ.get('CLAUDE_PROJECT_DIR')
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p

    if cwd_hint:
        candidate = claude_projects_root() / encode_claude_project_slug(cwd_hint)
        if candidate.exists():
            return candidate
        # Also try the cwd_hint as a literal folder name (some users pass it
        # that way).
        direct = Path(cwd_hint).expanduser()
        if direct.exists() and direct.is_dir():
            return direct

    dirs = list_claude_project_dirs()
    if len(dirs) == 1:
        return dirs[0]
    return None


# ----------------------------------------------------------------------------
# Title formatting - the user-facing "From X - <name> - <date> <time>" pattern
# ----------------------------------------------------------------------------

# Codex thread.title column is TEXT; we keep room for the prefix and timestamp
# while leaving space for the original title.
TITLE_BUDGET = 200
TITLE_PREFIX_FROM_CLAUDE = 'From Claude'
TITLE_PREFIX_FROM_CODEX = 'From Codex'


def _compact(text: str, limit: int) -> str:
    """Collapse whitespace and truncate to limit, leaving room for an ellipsis."""
    value = ' '.join((text or '').strip().split())
    if limit and len(value) > limit:
        return value[: max(0, limit - 1)].rstrip() + '…'
    return value


def _safe_filename_segment(text: str, limit: int = 40) -> str:
    """Make a string safe to embed in a filename. Used for human-readable
    target filenames when generating Claude-side JSONL outputs."""
    cleaned = _compact(text, limit)
    cleaned = re.sub(r'[^A-Za-z0-9._\-一-鿿]+', '_', cleaned).strip('_')
    return cleaned or 'session'


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Python 3.11+ handles 'Z' natively; for older versions we rewrite it.
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _to_local(dt: datetime) -> datetime:
    """Convert a (possibly tz-aware) datetime to local naive time for display."""
    try:
        if dt.tzinfo is not None:
            return dt.astimezone().replace(tzinfo=None)
    except Exception:
        pass
    return dt


def format_timestamp_for_title(ts: Optional[str] = None) -> str:
    """Return YYYY-MM-DD HH:MM:SS in local time for use in titles."""
    dt = _parse_iso(ts) if ts else None
    if dt is None:
        dt = datetime.now()
    else:
        dt = _to_local(dt)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def format_timestamp_for_filename(ts: Optional[str] = None) -> str:
    """Return YYYYMMDD-HHMMSS in local time for use in filenames."""
    dt = _parse_iso(ts) if ts else None
    if dt is None:
        dt = datetime.now()
    else:
        dt = _to_local(dt)
    return dt.strftime('%Y%m%d-%H%M%S')


def build_migrated_title(
    source_label: str,
    original_title: str,
    source_timestamp: Optional[str] = None,
) -> str:
    """Construct a title like 'From Claude - <original> - 2026-07-17 14:30:05'.

    The total length is capped so Codex's thread.title column stays readable.
    """
    prefix = source_label.strip()
    orig = _compact(original_title, TITLE_BUDGET) or 'untitled session'
    ts_str = format_timestamp_for_title(source_timestamp)
    # Reserve space for prefix + separators + timestamp.
    reserved = len(prefix) + len(' - ') + len(' - ') + len(ts_str)
    orig_budget = max(20, TITLE_BUDGET - reserved)
    if len(orig) > orig_budget:
        orig = orig[: orig_budget - 1].rstrip() + '…'
    return f'{prefix} - {orig} - {ts_str}'


# ----------------------------------------------------------------------------
# Synthetic-message filtering (used by both directions to keep noise out of
# the first-user-message heuristic that drives title generation)
# ----------------------------------------------------------------------------

SYNTHETIC_PREFIXES = (
    '<local-command',
    '<local-command-caveat',
    '<command-',
    '<command-name>',
    '<ide_opened_file>',
    '<environment_context>',
    '<permissions',
    '<skills_instructions',
    '<collaboration_mode',
)


def is_synthetic_message(text: str) -> bool:
    if not text:
        return True
    return text.lstrip().startswith(SYNTHETIC_PREFIXES)


def first_display_user_message(messages: list[Any]) -> str:
    """Return the first user message worth showing in a title.

    Accepts either raw strings or objects with .role/.kind/.content attrs.
    """
    def _content_of(m: Any) -> str:
        if isinstance(m, str):
            return m
        if hasattr(m, 'content'):
            return m.content or ''
        if isinstance(m, dict):
            return m.get('content', '') or ''
        return ''

    for m in messages:
        role = getattr(m, 'role', None) or (m.get('role') if isinstance(m, dict) else None)
        kind = getattr(m, 'kind', None) or (m.get('kind') if isinstance(m, dict) else None) or 'text'
        if role == 'user' and kind == 'text':
            content = _content_of(m)
            if not is_synthetic_message(content):
                return _compact(content, TITLE_BUDGET)
    # Fallback: any user text at all.
    for m in messages:
        role = getattr(m, 'role', None) or (m.get('role') if isinstance(m, dict) else None)
        if role == 'user':
            content = _content_of(m)
            if content:
                return _compact(content, TITLE_BUDGET)
    return ''


# ----------------------------------------------------------------------------
# Small JSON helpers
# ----------------------------------------------------------------------------

def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def write_jsonl(path: str | Path, entries: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def truncate_middle(text: str, limit: int, note: str = 'omitted') -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n\n[... {omitted} chars {note} ...]\n\n{text[-tail:]}"


# ----------------------------------------------------------------------------
# CLI output helpers (work on both Windows cmd and POSIX shells)
# ----------------------------------------------------------------------------

def _ensure_utf8_stdout() -> None:
    """Reconfigure stdout/stderr to UTF-8 so Chinese and emoji print correctly.

    Windows cmd defaults to the system codepage (often cp936/GBK), which
    throws UnicodeEncodeError on characters outside it. This is a no-op on
    POSIX shells where stdout is already UTF-8.
    """
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (TypeError, ValueError, OSError):
            pass


# Called once at import time so all subsequent prints are safe.
_ensure_utf8_stdout()


def info(msg: str) -> None:
    print(f'[i] {msg}')


def success(msg: str) -> None:
    print(f'[+] {msg}')


def warn(msg: str) -> None:
    print(f'[!] {msg}', file=sys.stderr)


def error(msg: str) -> None:
    print(f'[x] {msg}', file=sys.stderr)


# ----------------------------------------------------------------------------
# Dataclass shared by both directions
# ----------------------------------------------------------------------------

@dataclass
class ConversionResult:
    status: str  # 'success' | 'no_messages' | 'error'
    source: str = ''
    target: str = ''
    session_id: str = ''
    title: str = ''
    date: str = ''
    total_messages: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    error: str = ''

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}
