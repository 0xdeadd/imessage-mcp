# imessage-mcp

Local MCP server that lets Claude Code read Clint's iMessages straight from ~/Library/Messages/chat.db, and send
(only when IMESSAGE_SEND=1 is set on the server, and only after showing the exact text and getting a yes).
Registered user-wide: `claude mcp get imessage`. Change code here, then restart the Claude session to pick it up.

## Durable state
- PLAN.md: original design and scope. Phase 2 (autonomous replies) is NOT agreed; do not build it unasked.
- README.md: user-facing docs. Keep the registration command in sync with `claude mcp get imessage`.
- ~/.imessage-mcp/cursor.json: the only thing this project writes. Delete it to reset "what's new".
- Tests: `uv run pytest` (runs against the live chat.db, skips if unreadable).

## Facts that are easy to get wrong
- Message bodies live in `attributedBody` (typedstream), not `text`. See imessage_mcp/typedstream.py.
- This Mac's chat.db only holds messages from 2026-07-15 onward. Older threads are not on disk; do not claim
  a message is missing from the conversation just because it is missing from the DB.
- Contacts names come from the AddressBook sqlite files, all Sources unioned.
- Sending targets `chat id <guid>` via osascript with argv, one path for 1:1 and groups. Never spliced strings.

## Rules
- No em or en dashes anywhere. Commits attributed to Clint only, no AI trailers.
- Privacy: nothing leaves the Mac. Do not paste thread contents into commit messages, issues, or docs.
