#!/bin/zsh
# Install the iMessage MCP server as a launchd service on a fixed binary path.
#
# Why: macOS grants Full Disk Access to the process launchd started. Claude Code
# spawns stdio MCP servers under its own daemon, whose binary path changes on
# every self-update, so the grant dies with each update. Running the server as
# its own launchd agent from a copy of uv that never moves fixes that for good.
#
# Idempotent. Re-run after cloning or after moving the project.
set -euo pipefail

PROJECT="${0:A:h}"
STABLE_DIR="$HOME/.local/share/imessage-mcp"
STABLE_BIN="$STABLE_DIR/imessage-mcp"          # a copy of uv; the name is what shows in System Settings
PLIST="$HOME/Library/LaunchAgents/com.clint.imessage-mcp.plist"
LABEL="com.clint.imessage-mcp"
PORT="${IMESSAGE_MCP_PORT:-8471}"
LOG="$HOME/Library/Logs/imessage-mcp.log"
CLAUDE_JSON="$HOME/.claude.json"

mkdir -p "$STABLE_DIR" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

# 1. Stable binary. Copy only when missing: replacing the file would break the
#    Full Disk Access grant, which is tied to this exact file.
if [[ ! -x "$STABLE_BIN" ]]; then
  SRC="$(readlink -f "$(command -v uv)")"
  cp "$SRC" "$STABLE_BIN"
  chmod 755 "$STABLE_BIN"
  echo "copied uv -> $STABLE_BIN (grant Full Disk Access to this file once)"
else
  echo "stable binary present: $STABLE_BIN"
fi

# 2. launchd agent
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$STABLE_BIN</string>
    <string>run</string>
    <string>--directory</string>
    <string>$PROJECT</string>
    <string>python</string>
    <string>-m</string>
    <string>imessage_mcp.server</string>
    <string>--http</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>IMESSAGE_MCP_PORT</key><string>$PORT</string>
    <key>UV_CACHE_DIR</key><string>$HOME/.cache/uv</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "launchd agent $LABEL loaded (log: $LOG)"

# 3. Point Claude Code at the HTTP endpoint (user-level ~/.claude.json)
if [[ -f "$CLAUDE_JSON" ]]; then
  cp "$CLAUDE_JSON" "$CLAUDE_JSON.bak-imessage-$(date +%Y%m%d%H%M%S)"
  python3 - "$CLAUDE_JSON" "$PORT" <<'PY'
import json, sys
path, port = sys.argv[1], sys.argv[2]
d = json.load(open(path))
d.setdefault("mcpServers", {})["imessage"] = {"type": "http", "url": f"http://127.0.0.1:{port}/mcp"}
json.dump(d, open(path, "w"), indent=2)
print(f"~/.claude.json: mcpServers.imessage -> http://127.0.0.1:{port}/mcp")
PY
fi

echo
echo "One-time step, by hand: System Settings > Privacy & Security > Full Disk Access,"
echo "add $STABLE_BIN and switch it on, then: launchctl kickstart -k gui/$(id -u)/$LABEL"
