# Legacy scripts

These are the original Linux-only scripts the skill was refactored from. They
are kept here for reference and diffing against the new cross-platform
versions in [../scripts/](../scripts/).

- `claude_to_codex.py` - original Claude->Codex converter, hardcoded to
  `/home/zy/...` paths.
- `codex_to_claude.py` - original Codex->Claude converter, hardcoded to
  `/home/zy/.claude/projects/-home-zy-work-project-data-value`.
- `codex-import.sh` - bash helper that registers a rollout JSONL in Codex's
  `state_5.sqlite`. Linux-only.

Use the new [../scripts/converter.py](../scripts/converter.py) entry point
instead - it handles all three jobs cross-platform.
