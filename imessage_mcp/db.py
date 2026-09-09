"""Read-only access to the macOS Messages database (~/Library/Messages/chat.db)
and the local Contacts (AddressBook) databases.

Everything here is read-only: chat.db and the AddressBook databases are opened
with sqlite3 URI mode=ro so we never risk touching Messages.app's live state.
"""

from __future__ import annotations

import glob
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from imessage_mcp.typedstream import decode_attributed_body

CHAT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")
ATTACHMENTS_ROOT = os.path.expanduser("~/Library/Messages/Attachments")
ADDRESSBOOK_GLOB = os.path.expanduser(
    "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
)

# Apple's "Mac absolute time" epoch: 2001-01-01 00:00:00 UTC.
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# Tapback (reaction) associated_message_type values.
# 2000-2005 = reaction added, 3000-3005 = reaction removed. 2006 carries a
# custom emoji tapback in associated_message_emoji instead of a fixed label.
TAPBACK_LABELS = {
    2000: "Loved",
    2001: "Liked",
    2002: "Disliked",
    2003: "Laughed at",
    2004: "Emphasized",
    2005: "Questioned",
}


def _open_ro(path: str) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def apple_time_to_iso(value: int | None) -> str | None:
    """Convert an Apple nanosecond timestamp to a local-time ISO string."""
    if not value:
        return None
    # Modern chat.db stores nanoseconds since the Apple epoch; older rows
    # (rare, pre-Big-Sur databases) used seconds. Nanoseconds always land in
    # a huge integer range, so use magnitude to tell them apart.
    seconds = value / 1_000_000_000 if value > 10**12 else value
    dt = APPLE_EPOCH + timedelta(seconds=seconds)
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


class ContactBook:
    """Phone/email -> display name lookup, built from every AddressBook
    source under ~/Library/Application Support/AddressBook/Sources/*.

    Multiple "Sources" directories exist when Contacts has more than one
    account (iCloud, on-device, etc.); we union all of them.
    """

    def __init__(self) -> None:
        self._by_last10: dict[str, str] = {}
        self._by_email: dict[str, str] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for db_path in glob.glob(ADDRESSBOOK_GLOB):
            try:
                self._load_one(db_path)
            except sqlite3.Error:
                continue

    def _load_one(self, db_path: str) -> None:
        con = _open_ro(db_path)
        try:
            cur = con.cursor()

            def full_name(row: sqlite3.Row) -> str | None:
                first = (row["ZFIRSTNAME"] or "").strip()
                last = (row["ZLASTNAME"] or "").strip()
                org = (row["ZORGANIZATION"] or "").strip()
                name = f"{first} {last}".strip()
                return name or org or None

            cur.execute(
                """
                SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER
                FROM ZABCDRECORD r
                JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
                WHERE p.ZFULLNUMBER IS NOT NULL
                """
            )
            for row in cur.fetchall():
                name = full_name(row)
                number = row["ZFULLNUMBER"]
                if not name or not number:
                    continue
                last10 = _digits(number)[-10:]
                if len(last10) == 10:
                    self._by_last10.setdefault(last10, name)

            cur.execute(
                """
                SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, e.ZADDRESS
                FROM ZABCDRECORD r
                JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
                WHERE e.ZADDRESS IS NOT NULL
                """
            )
            for row in cur.fetchall():
                name = full_name(row)
                email = row["ZADDRESS"]
                if not name or not email:
                    continue
                self._by_email.setdefault(email.strip().lower(), name)
        finally:
            con.close()

    def resolve(self, handle: str) -> str:
        """Resolve a chat.db handle id (phone or email) to a display name.

        Falls back to the raw handle when no contact matches.
        """
        self._load()
        if not handle:
            return handle
        if "@" in handle:
            return self._by_email.get(handle.strip().lower(), handle)
        last10 = _digits(handle)[-10:]
        if len(last10) == 10:
            return self._by_last10.get(last10, handle)
        return handle


@dataclass
class Attachment:
    path: str
    mime_type: str | None
    exists: bool


@dataclass
class Message:
    rowid: int
    date_iso: str | None
    sender: str  # "me" or resolved contact name / raw handle
    text: str | None
    attachments: list[Attachment] = field(default_factory=list)
    reactions: list[str] = field(default_factory=list)  # e.g. "Loved by Greg"


@dataclass
class Chat:
    rowid: int
    guid: str
    display_name: str
    chat_identifier: str
    last_message_iso: str | None
    message_count: int


def _resolve_attachment_path(filename: str | None) -> str | None:
    if not filename:
        return None
    if filename.startswith("~"):
        return os.path.expanduser(filename)
    return filename


def _chat_display_name(row: sqlite3.Row, participants: list[str]) -> str:
    if row["display_name"]:
        return row["display_name"]
    if participants:
        return ", ".join(participants)
    return row["chat_identifier"] or f"chat {row['ROWID']}"


class MessagesDB:
    def __init__(self, db_path: str = CHAT_DB_PATH, contacts: ContactBook | None = None):
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"Messages database not found at {db_path}. "
                "Full Disk Access may not be granted to this process."
            )
        self.db_path = db_path
        self.contacts = contacts or ContactBook()

    def _connect(self) -> sqlite3.Connection:
        return _open_ro(self.db_path)

    # -- chats --------------------------------------------------------

    def _participants_for_chat(self, cur: sqlite3.Cursor, chat_rowid: int) -> list[str]:
        cur.execute(
            """
            SELECT h.id FROM handle h
            JOIN chat_handle_join chj ON chj.handle_id = h.ROWID
            WHERE chj.chat_id = ?
            """,
            (chat_rowid,),
        )
        return [self.contacts.resolve(row["id"]) for row in cur.fetchall()]

    def list_chats(self, query: str = "", limit: int = 30) -> list[Chat]:
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute(
                """
                SELECT c.ROWID, c.guid, c.display_name, c.chat_identifier,
                       MAX(cmj.message_date) AS last_date,
                       COUNT(cmj.message_id) AS msg_count
                FROM chat c
                LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
                GROUP BY c.ROWID
                ORDER BY last_date DESC
                """
            )
            rows = cur.fetchall()
            results: list[Chat] = []
            needle = query.strip().lower()
            for row in rows:
                participants = self._participants_for_chat(cur, row["ROWID"])
                name = _chat_display_name(row, participants)
                haystack = " ".join(
                    [
                        name.lower(),
                        (row["chat_identifier"] or "").lower(),
                        " ".join(p.lower() for p in participants),
                    ]
                )
                if needle and needle not in haystack:
                    continue
                if not row["msg_count"]:
                    # Dead group shells with no messages on this Mac only add noise.
                    continue
                results.append(
                    Chat(
                        rowid=row["ROWID"],
                        guid=row["guid"],
                        display_name=name,
                        chat_identifier=row["chat_identifier"] or "",
                        last_message_iso=apple_time_to_iso(row["last_date"]),
                        message_count=row["msg_count"] or 0,
                    )
                )
                if len(results) >= limit:
                    break
            return results
        finally:
            con.close()

    def find_chat(self, chat: str) -> Chat:
        """Resolve a chat argument (ROWID, guid, chat_identifier, or a
        case-insensitive substring of a contact/display name) to one Chat.

        Raises ValueError with a candidate listing if ambiguous or not found.
        """
        chat = chat.strip()
        con = self._connect()
        try:
            cur = con.cursor()

            if chat.isdigit():
                cur.execute(
                    "SELECT ROWID, guid, display_name, chat_identifier FROM chat WHERE ROWID = ?",
                    (int(chat),),
                )
                row = cur.fetchone()
                if row:
                    participants = self._participants_for_chat(cur, row["ROWID"])
                    cur.execute(
                        "SELECT COUNT(*) AS c, MAX(message_date) AS d FROM chat_message_join WHERE chat_id = ?",
                        (row["ROWID"],),
                    )
                    stats = cur.fetchone()
                    return Chat(
                        rowid=row["ROWID"],
                        guid=row["guid"],
                        display_name=_chat_display_name(row, participants),
                        chat_identifier=row["chat_identifier"] or "",
                        last_message_iso=apple_time_to_iso(stats["d"]),
                        message_count=stats["c"] or 0,
                    )

            cur.execute(
                "SELECT ROWID, guid, display_name, chat_identifier FROM chat WHERE guid = ? OR chat_identifier = ?",
                (chat, chat),
            )
            row = cur.fetchone()
            if row:
                participants = self._participants_for_chat(cur, row["ROWID"])
                cur.execute(
                    "SELECT COUNT(*) AS c, MAX(message_date) AS d FROM chat_message_join WHERE chat_id = ?",
                    (row["ROWID"],),
                )
                stats = cur.fetchone()
                return Chat(
                    rowid=row["ROWID"],
                    guid=row["guid"],
                    display_name=_chat_display_name(row, participants),
                    chat_identifier=row["chat_identifier"] or "",
                    last_message_iso=apple_time_to_iso(stats["d"]),
                    message_count=stats["c"] or 0,
                )

            candidates = self.list_chats(query=chat, limit=10)
            if len(candidates) == 1:
                return candidates[0]
            if not candidates:
                raise ValueError(f'No chat found matching "{chat}".')
            listing = "\n".join(
                f"  {c.rowid}: {c.display_name} ({c.chat_identifier})" for c in candidates
            )
            raise ValueError(
                f'"{chat}" is ambiguous, matched {len(candidates)} chats:\n{listing}\n'
                "Use the ROWID to disambiguate."
            )
        finally:
            con.close()

    # -- messages -------------------------------------------------------

    def _attachments_for_message(self, cur: sqlite3.Cursor, message_rowid: int) -> list[Attachment]:
        cur.execute(
            """
            SELECT a.filename, a.mime_type FROM attachment a
            JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
            WHERE maj.message_id = ?
            """,
            (message_rowid,),
        )
        out = []
        for row in cur.fetchall():
            path = _resolve_attachment_path(row["filename"])
            if not path:
                continue
            out.append(Attachment(path=path, mime_type=row["mime_type"], exists=os.path.exists(path)))
        return out

    def _load_reactions(self, cur: sqlite3.Cursor, chat_rowid: int) -> dict[str, list[str]]:
        """Map target message guid -> list of "<Label> by <name>" strings."""
        cur.execute(
            """
            SELECT m.associated_message_guid, m.associated_message_type,
                   m.associated_message_emoji, m.is_from_me, m.handle_id
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            WHERE cmj.chat_id = ? AND m.associated_message_type BETWEEN 2000 AND 2006
              AND m.associated_message_guid IS NOT NULL
            """,
            (chat_rowid,),
        )
        by_target: dict[str, list[str]] = {}
        for row in cur.fetchall():
            raw_guid = row["associated_message_guid"] or ""
            target_guid = raw_guid.split("/", 1)[-1] if "/" in raw_guid else raw_guid
            if row["is_from_me"]:
                who = "me"
            else:
                who = self._handle_name(cur, row["handle_id"])
            atype = row["associated_message_type"]
            if atype == 2006:
                label = row["associated_message_emoji"] or "Reacted"
                note = f"{label} from {who}"
            else:
                label = TAPBACK_LABELS.get(atype, "Reacted")
                note = f"{label} by {who}"
            by_target.setdefault(target_guid, []).append(note)
        return by_target

    def _handle_name(self, cur: sqlite3.Cursor, handle_id: int | None) -> str:
        if not handle_id:
            return "unknown"
        cur.execute("SELECT id FROM handle WHERE ROWID = ?", (handle_id,))
        row = cur.fetchone()
        if not row:
            return "unknown"
        return self.contacts.resolve(row["id"])

    def read_thread(self, chat: str, since: str = "", limit: int = 100) -> tuple[Chat, list[Message]]:
        resolved = self.find_chat(chat)
        con = self._connect()
        try:
            cur = con.cursor()
            reactions_by_guid = self._load_reactions(cur, resolved.rowid)

            since_clause = ""
            params: list = [resolved.rowid]
            if since:
                since_date = _parse_since(since)
                since_clause = "AND m.date >= ?"
                params.append(since_date)

            cur.execute(
                f"""
                SELECT m.ROWID, m.guid, m.date, m.is_from_me, m.handle_id, m.text,
                       m.attributedBody, m.associated_message_type
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE cmj.chat_id = ? {since_clause}
                  AND (m.associated_message_type = 0 OR m.associated_message_type IS NULL
                       OR m.associated_message_type >= 2000)
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = list(reversed(cur.fetchall()))

            messages: list[Message] = []
            for row in rows:
                atype = row["associated_message_type"] or 0
                if 2000 <= atype <= 3005:
                    # Tapbacks are folded into their target message, not shown
                    # as standalone rows.
                    continue

                text = row["text"] or decode_attributed_body(row["attributedBody"])
                sender = "me" if row["is_from_me"] else self._handle_name(cur, row["handle_id"])
                attachments = self._attachments_for_message(cur, row["ROWID"])
                notes = reactions_by_guid.get(row["guid"], [])
                messages.append(
                    Message(
                        rowid=row["ROWID"],
                        date_iso=apple_time_to_iso(row["date"]),
                        sender=sender,
                        text=text,
                        attachments=attachments,
                        reactions=notes,
                    )
                )
            return resolved, messages
        finally:
            con.close()

    def search(self, text: str, chat: str = "", since: str = "", limit: int = 50) -> list[tuple[Chat, Message]]:
        con = self._connect()
        try:
            cur = con.cursor()

            chat_filter_rowid = None
            if chat:
                chat_filter_rowid = self.find_chat(chat).rowid

            params: list = []
            where = ["(m.associated_message_type = 0 OR m.associated_message_type IS NULL)"]
            if chat_filter_rowid is not None:
                where.append("cmj.chat_id = ?")
                params.append(chat_filter_rowid)
            if since:
                where.append("m.date >= ?")
                params.append(_parse_since(since))

            cur.execute(
                f"""
                SELECT m.ROWID, m.guid, m.date, m.is_from_me, m.handle_id, m.text,
                       m.attributedBody, cmj.chat_id
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE {' AND '.join(where)}
                ORDER BY m.date DESC
                LIMIT 2000
                """,
                params,
            )

            needle = text.lower()
            results: list[tuple[Chat, Message]] = []
            chat_cache: dict[int, Chat] = {}
            for row in cur.fetchall():
                body = row["text"] or decode_attributed_body(row["attributedBody"])
                if not body or needle not in body.lower():
                    continue
                chat_id = row["chat_id"]
                if chat_id not in chat_cache:
                    chat_cache[chat_id] = self.find_chat(str(chat_id))
                sender = "me" if row["is_from_me"] else self._handle_name(cur, row["handle_id"])
                msg = Message(
                    rowid=row["ROWID"],
                    date_iso=apple_time_to_iso(row["date"]),
                    sender=sender,
                    text=body,
                )
                results.append((chat_cache[chat_id], msg))
                if len(results) >= limit:
                    break
            return results
        finally:
            con.close()

    def whats_new(self, since_rowid: int) -> list[tuple[Chat, list[Message]]]:
        """All messages with ROWID > since_rowid, grouped by chat, oldest first
        within each chat. Returns list of (Chat, [Message, ...])."""
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute(
                """
                SELECT DISTINCT cmj.chat_id FROM chat_message_join cmj
                JOIN message m ON m.ROWID = cmj.message_id
                WHERE m.ROWID > ?
                  AND (m.associated_message_type = 0 OR m.associated_message_type IS NULL)
                """,
                (since_rowid,),
            )
            chat_ids = [row["chat_id"] for row in cur.fetchall()]
        finally:
            con.close()

        out = []
        for chat_id in chat_ids:
            resolved, messages = self.read_thread(str(chat_id), limit=500)
            newer = [m for m in messages if m.rowid > since_rowid]
            if newer:
                out.append((resolved, newer))
        return out

    def rowid_hours_ago(self, hours: int) -> int:
        """Highest message ROWID older than `hours` hours (0 if none)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_ns = int((cutoff - APPLE_EPOCH).total_seconds() * 1_000_000_000)
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute("SELECT MAX(ROWID) AS m FROM message WHERE date < ?", (cutoff_ns,))
            row = cur.fetchone()
            return row["m"] or 0
        finally:
            con.close()

    def max_rowid(self) -> int:
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute("SELECT MAX(ROWID) AS m FROM message")
            row = cur.fetchone()
            return row["m"] or 0
        finally:
            con.close()


def _parse_since(since: str) -> int:
    """Parse a `since` argument into an Apple-epoch nanosecond timestamp.

    Accepts "YYYY-MM-DD" or "YYYY-MM-DD HH:MM" in local time.
    """
    since = since.strip()
    dt = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(since, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        raise ValueError(f'Could not parse since="{since}". Use "YYYY-MM-DD" or "YYYY-MM-DD HH:MM".')
    dt = dt.astimezone()
    delta = dt.astimezone(timezone.utc) - APPLE_EPOCH
    return int(delta.total_seconds() * 1_000_000_000)
