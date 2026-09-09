# imessage-mcp: let Claude Code read (and, with confirmation, send) iMessages

Written 2026-09-09. Status: SHIPPED same day. Read tools + gated send built, registered user-wide, pushed to
github.com/0xdeadd/imessage-mcp. Live send not yet exercised (needs IMESSAGE_SEND=1 and a one-time Messages
automation prompt). Note: this Mac's chat.db only holds messages from 2026-07-15 onward.

## Why
Clint pastes iMessage threads (Steve, Greg/Addison group, Serena, Derek) into Claude Code sessions by hand.
Claude has already read ~/Library/Messages/chat.db directly with ad-hoc sqlite in the halo and serena-finances
projects (chat 661 = Greg group, chat 471 = Serena). This makes that a first-class tool available in every project.

## Ground truth on this Mac (checked 2026-09-09)
- chat.db readable from the terminal (Full Disk Access already granted): 3,485 messages, 503 chats.
- 3,423 of 3,471 messages have NO plain `text`; the body lives in the `attributedBody` blob (typedstream).
  A decoder is mandatory or the tool is useless.
- Contacts DB at ~/Library/Application Support/AddressBook/ (sqlite) for phone -> name. Unverified readable.
- Sending: /usr/bin/osascript -> Messages.app. Needs a one-time Automation permission prompt. `imsg` CLI not installed.
- openclaw (fork in ~/Projects/openclaw) has an iMessage channel but is a whole gateway; too heavy for this.

## Design: a local MCP server (Python, stdio), registered user-wide
Tools Claude gets in every session:
1. `list_chats(query?, limit)`: recent chats, names resolved from Contacts, last message time, unread hint.
2. `read_thread(chat, since?, limit)`: messages in order, sender names, attachments (file paths under
   ~/Library/Messages/Attachments so Claude can Read images/PDFs), reactions folded in.
3. `search(text, chat?, since?)`: full-text over decoded bodies.
4. `whats_new(cursor?)`: everything across all chats since the last call. This is the "keep up" tool.
5. `send(chat, text)`: sends as Clint via Messages.app. Gated two ways: an env flag on the server
   (IMESSAGE_SEND=1, off by default) AND Claude must show the exact text and get a yes in chat first.
   Group chats supported (AppleScript can target a chat by its guid).
Registration: `claude mcp add --scope user imessage -- uv run ~/Projects/imessage-mcp/server.py`
Optional skill `/texts` = "show me what's new" shortcut.

## Not in scope (phase 2, separate decision)
Autonomous replies "as Clint" (a daemon watching chat.db and answering on its own). Doable with launchd +
`claude -p`, but it needs a real trust model first. Phase 1 keeps a human on the send button.

## Stack
Python 3.12 + uv, `mcp` SDK, stdlib sqlite3 (no native builds), typedstream decoder (small, known pattern),
pytest against a copied chat.db snapshot. One repo: github.com/0xdeadd/imessage-mcp, MIT, no AI trailers.

## Build plan (Mode A: Fable plans, fast-worker builds, Fable reviews)
1. Decoder + read-only queries, verified against known receipts (Greg's "Have fun brother sounds good"
   2026-07-10 in chat 661; Serena's PDFs in chat 471).
2. MCP wrapper + registration + smoke test from a fresh Claude Code session.
3. Send tool behind the flag, tested on a message to Clint's own number.
4. README, card on clintphillips.dev.
Estimate: one working session for 1-2, a short second one for 3-4.
