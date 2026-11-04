"""FastMCP server exposing read (and gated write) access to macOS Messages."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from imessage_mcp.db import Chat, Message, MessagesDB

mcp = FastMCP("imessage")

_db: MessagesDB | None = None

CURSOR_DIR = Path(os.path.expanduser("~/.imessage-mcp"))
CURSOR_FILE = CURSOR_DIR / "cursor.json"


def get_db() -> MessagesDB:
    global _db
    if _db is None:
        _db = MessagesDB()
    return _db


def _format_message(msg: Message) -> str:
    when = msg.date_iso or "?"
    body = (msg.text or "").strip()
    line = f"[{when}] {msg.sender}: {body}".rstrip()
    extras = []
    for att in msg.attachments:
        marker = "" if att.exists else " (missing)"
        extras.append(f"  \U0001F4CE {att.path}{marker}")
    for reaction in msg.reactions:
        extras.append(f"  [{reaction}]")
    if extras:
        line = line + "\n" + "\n".join(extras)
    return line


def _format_thread(chat: Chat, messages: list[Message]) -> str:
    header = f"{chat.display_name} (chat {chat.rowid}, {chat.chat_identifier})"
    if not messages:
        return f"{header}\nNo messages."
    body = "\n".join(_format_message(m) for m in messages)
    return f"{header}\n{body}"


@mcp.tool()
def list_chats(query: str = "", limit: int = 30) -> str:
    """List recent iMessage/SMS chats, optionally filtered by a case
    insensitive substring match against the chat name, contact names, or
    chat identifier. Returns one line per chat with ROWID, name, last
    message time, and message count."""
    db = get_db()
    chats = db.list_chats(query=query, limit=limit)
    if not chats:
        return f'No chats found matching "{query}".' if query else "No chats found."
    lines = []
    for c in chats:
        lines.append(
            f"{c.rowid}: {c.display_name} [{c.chat_identifier}] "
            f"last: {c.last_message_iso or 'never'} ({c.message_count} messages)"
        )
    return "\n".join(lines)


@mcp.tool()
def read_thread(chat: str, since: str = "", limit: int = 100) -> str:
    """Read messages from one chat, in date order.

    `chat` accepts a chat ROWID, a chat guid/chat_identifier, or a case
    insensitive substring of a contact/display name. If the name is
    ambiguous, this returns a list of candidate chats instead of messages.
    `since` is an optional "YYYY-MM-DD" or "YYYY-MM-DD HH:MM" local time
    filter. `limit` caps how many of the most recent matching messages come
    back (still returned oldest first)."""
    db = get_db()
    try:
        resolved, messages = db.read_thread(chat, since=since, limit=limit)
    except ValueError as exc:
        return str(exc)
    return _format_thread(resolved, messages)


@mcp.tool()
def search(text: str, chat: str = "", since: str = "", limit: int = 50) -> str:
    """Search decoded message bodies for a substring (case insensitive).

    Optionally restrict to one chat (same matching rules as read_thread) and
    to messages on or after `since` ("YYYY-MM-DD" or "YYYY-MM-DD HH:MM")."""
    db = get_db()
    try:
        results = db.search(text, chat=chat, since=since, limit=limit)
    except ValueError as exc:
        return str(exc)
    if not results:
        return f'No messages found matching "{text}".'
    lines = []
    for chat_obj, msg in results:
        lines.append(f"[{chat_obj.display_name}] {_format_message(msg)}")
    return "\n".join(lines)


def _load_cursor() -> int:
    if not CURSOR_FILE.exists():
        return 0
    try:
        data = json.loads(CURSOR_FILE.read_text())
        return int(data.get("last_seen_rowid", 0))
    except (json.JSONDecodeError, ValueError, OSError):
        return 0


def _save_cursor(rowid: int) -> None:
    CURSOR_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps({"last_seen_rowid": rowid}))


@mcp.tool()
def whats_new(mark_seen: bool = True) -> str:
    """Return every message across all chats since the last time this tool
    advanced its cursor, grouped by chat, oldest first within each chat.

    On the very first call (no cursor yet) it returns the last 24 hours.
    The cursor lives at ~/.imessage-mcp/cursor.json. When `mark_seen` is
    true (the default), the cursor advances to the newest message ROWID seen
    so the next call only returns messages after this one."""
    db = get_db()
    first_run = not CURSOR_FILE.exists()
    since_rowid = _load_cursor()
    if first_run:
        # No cursor yet: show the last 24 hours instead of all history.
        since_rowid = db.rowid_hours_ago(24)
    groups = db.whats_new(since_rowid)

    if not groups:
        if mark_seen:
            _save_cursor(db.max_rowid())
        return "Nothing new."

    sections = [_format_thread(chat, messages) for chat, messages in groups]
    highest = max(m.rowid for _, messages in groups for m in messages)

    if mark_seen:
        _save_cursor(max(highest, db.max_rowid()))

    return "\n\n".join(sections)


@mcp.tool()
def send(chat: str, text: str) -> str:
    """Send a message through Messages.app.

    Disabled by default. Set the environment variable IMESSAGE_SEND=1 on
    this server process to enable it. `chat` accepts the same matching rules
    as read_thread. Always show the exact text to the user and get a yes
    before calling this."""
    if os.environ.get("IMESSAGE_SEND") != "1":
        return (
            "Sending is disabled. Set IMESSAGE_SEND=1 in the server's "
            "environment to enable the send tool."
        )

    db = get_db()
    try:
        resolved = db.find_chat(chat)
    except ValueError as exc:
        return str(exc)

    if not text.strip():
        return "Refusing to send an empty message."

    # Every chat in chat.db, 1:1 or group, has a guid Messages.app accepts as
    # `chat id`, so one code path covers both. The text and guid travel as
    # argv items, never spliced into the script, so quotes and backslashes in
    # a message cannot break out of the AppleScript.
    script = (
        "on run argv\n"
        "  tell application \"Messages\" to send (item 1 of argv) "
        "to chat id (item 2 of argv)\n"
        "end run"
    )

    result = subprocess.run(
        ["osascript", "-e", script, text, resolved.guid],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return f"Send failed: {result.stderr.strip() or result.stdout.strip()}"
    return f"Sent to {resolved.display_name}."


def main() -> None:
    """Run over stdio (default) or local HTTP (--http / IMESSAGE_MCP_HTTP=1).

    HTTP mode exists so the server can run as a launchd service under a
    fixed binary path. macOS ties Full Disk Access to the process that
    launchd started, so that one grant survives Claude Code updates.
    """
    import sys

    use_http = "--http" in sys.argv[1:] or os.environ.get("IMESSAGE_MCP_HTTP") == "1"
    if not use_http:
        mcp.run()
        return
    mcp.settings.host = os.environ.get("IMESSAGE_MCP_HOST", "127.0.0.1")
    mcp.settings.port = int(os.environ.get("IMESSAGE_MCP_PORT", "8471"))
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
