# imessage-mcp

A local MCP (Model Context Protocol) server that lets Claude Code read your
macOS Messages history directly, so you stop copy-pasting text threads into
chat by hand.

It reads `~/Library/Messages/chat.db` and your local Contacts database
directly with Python's stdlib `sqlite3`. There are no native dependencies and
nothing leaves your Mac: every tool is a local, read-only SQL query plus a
small typedstream decoder for the message bodies that Messages stores as
binary blobs.

## What it gives Claude

- `list_chats(query, limit)`: recent chats, with names resolved from Contacts.
- `read_thread(chat, since, limit)`: a thread in order, with sender names,
  attachment paths, and tapback reactions folded into the message they landed
  on.
- `search(text, chat, since, limit)`: full text search over decoded message
  bodies.
- `whats_new(mark_seen)`: everything new across every chat since the last
  call, using a small cursor file at `~/.imessage-mcp/cursor.json`.
- `send(chat, text)`: sends a message through Messages.app. Disabled unless
  you explicitly turn it on, see "Sending" below.

## Requirements

- macOS with Messages.app signed in and syncing.
- Full Disk Access granted to whatever process runs this server (your
  terminal, or Claude Code itself, depending on how you launch it). Without
  it, `~/Library/Messages/chat.db` will fail to open even in read-only mode.
  Grant it under System Settings, Privacy and Security, Full Disk Access.
- Python 3.12 or newer, managed with `uv`.

## Install

```
cd /Users/clint/Projects/imessage-mcp
uv sync
```

## Register with Claude Code

```
claude mcp add --scope user imessage -- uv run --directory /Users/clint/Projects/imessage-mcp python -m imessage_mcp.server
```

That registers the server once, for every project, over stdio.

## Sending is off by default

`send(chat, text)` only works when the server process has the environment
variable `IMESSAGE_SEND=1` set. Without it, the tool returns a plain message
saying sending is disabled, and does nothing else.

When enabled, sending goes through `osascript` and Messages.app:
- a one on one chat sends with `send text to buddy handle of (service 1
  whose service type is iMessage)`.
- a group chat sends with `send text to chat id "<guid>"`.

The first send will prompt macOS for Automation permission for whatever
process is driving Messages.app. Treat this as a real send: nothing here
double checks with you before the message goes out, so only turn
`IMESSAGE_SEND` on in a session where you are prepared to review the exact
text before asking the tool to send it.

## Privacy

Everything runs locally. The server opens `chat.db` and your Contacts
database read only (`sqlite3` URI `mode=ro`), decodes text and attachment
paths in process, and returns plain text to Claude. Nothing is uploaded
anywhere, and no network calls happen anywhere in this codebase.

## Tests

```
uv run pytest
```

The tests read your live `chat.db` and Contacts database and skip themselves
cleanly if either is unreadable (for example, no Full Disk Access, or run on
a machine with no Messages history).

## Layout

- `imessage_mcp/typedstream.py`: decodes the `attributedBody` blob that
  Messages uses instead of plain `text` for most rows.
- `imessage_mcp/db.py`: all the read only SQL against `chat.db` and Contacts.
- `imessage_mcp/server.py`: the FastMCP server and tool definitions.
- `tests/`: pytest tests against the live database.

## Running as a launchd service (survives Claude Code updates)

macOS grants Full Disk Access to the process that launchd started. When Claude
Code spawns this server over stdio, that process is Claude Code's own daemon,
whose binary path changes on every self-update, so the grant dies each time
(seen 2026-11-03 and 2026-11-04). The fix is to run the server as its own
launchd agent from a binary path that never changes and talk to it over HTTP.

```
./install-service.sh
```

What it does:

- copies the `uv` binary to `~/.local/share/imessage-mcp/imessage-mcp` (a
  fixed path; never replace that file or the grant breaks)
- installs `~/Library/LaunchAgents/com.clint.imessage-mcp.plist`, which runs
  `imessage-mcp run --directory <project> python -m imessage_mcp.server --http`
  on `127.0.0.1:8471`, KeepAlive, log at `~/Library/Logs/imessage-mcp.log`
- rewrites `mcpServers.imessage` in `~/.claude.json` to
  `{"type": "http", "url": "http://127.0.0.1:8471/mcp"}` (backup kept beside it)

Then, once, by hand: System Settings > Privacy & Security > Full Disk Access,
add `~/.local/share/imessage-mcp/imessage-mcp` and switch it on, and restart
the agent:

```
launchctl kickstart -k gui/$(id -u)/com.clint.imessage-mcp
```

Useful commands:

```
launchctl print gui/$(id -u)/com.clint.imessage-mcp | grep -E "state|pid"
tail -f ~/Library/Logs/imessage-mcp.log
```

The stdio mode still works (`python -m imessage_mcp.server` with no flag) for
other MCP clients. `--http` or `IMESSAGE_MCP_HTTP=1` selects HTTP;
`IMESSAGE_MCP_HOST` and `IMESSAGE_MCP_PORT` override the bind address.
