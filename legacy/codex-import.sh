#!/bin/bash
ROLLOUT="$1"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
DATE_DIR=$(echo "$ROLLOUT" | grep -oP '\d{4}/\d{2}/\d{2}')
DEST_DIR="$CODEX_HOME/sessions/$DATE_DIR"
mkdir -p "$DEST_DIR"
ROLLOUT_PATH="$DEST_DIR/$(basename "$ROLLOUT")"
[ "$(realpath "$ROLLOUT" 2>/dev/null)" != "$(realpath "$ROLLOUT_PATH" 2>/dev/null)" ] && cp "$ROLLOUT" "$DEST_DIR/"
echo "Rollout: $ROLLOUT_PATH"

python3 << PYEOF
import sqlite3, json, os, time
rp = "$ROLLOUT_PATH"
db = os.path.expanduser("$CODEX_HOME/state_5.sqlite")
sid, title = None, ""
with open(rp) as f:
    for line in f:
        try: e = json.loads(line)
        except: continue
        if not sid and e.get("type") == "session_meta": sid = e["payload"]["id"]
        if not title and e.get("type") == "event_msg" and e.get("payload",{}).get("type")=="user_message":
            title = e["payload"]["message"][:200]; break
if not sid: print("ERROR: no session_id"); exit(1)
title = title or "Imported from Claude"
now = int(time.time())
conn = sqlite3.connect(db)
conn.execute("""INSERT OR REPLACE INTO threads
    (id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,
     sandbox_policy,approval_mode,tokens_used,has_user_event,archived,cli_version,
     first_user_message,memory_mode,model,reasoning_effort,created_at_ms,updated_at_ms,
     thread_source,preview)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (sid, rp, 0, now, 'vscode', 'openai', os.path.expanduser("~"), title,
     '{"type":"danger-full-access"}', 'never', 0, 1, 0, '0.133.0-alpha.1',
     title, 'enabled', 'gpt-5.5', 'xhigh', 0, now*1000, 'user', title))
conn.commit(); conn.close()
print(f"OK: {sid}")
PYEOF
