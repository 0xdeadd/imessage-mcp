"""Tests for imessage_mcp.db against the live Messages/Contacts databases.

Skips cleanly if either database is unreadable on this machine.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from imessage_mcp.db import CHAT_DB_PATH, MessagesDB

pytestmark = pytest.mark.skipif(
    not os.path.exists(CHAT_DB_PATH),
    reason="chat.db not found on this machine",
)


@pytest.fixture(scope="module")
def db():
    try:
        instance = MessagesDB()
        instance.list_chats(limit=1)
        return instance
    except (sqlite3.Error, FileNotFoundError):
        pytest.skip("chat.db is not readable (Full Disk Access likely missing)")


def test_list_chats_returns_results(db):
    chats = db.list_chats(limit=5)
    assert isinstance(chats, list)
    assert len(chats) <= 5


def test_list_chats_query_steve(db):
    chats = db.list_chats(query="Steve", limit=10)
    # Not asserting non-empty: contact matching depends on this machine's
    # Contacts data. Just confirm the call succeeds and returns a list.
    assert isinstance(chats, list)


def test_list_chats_query_serena(db):
    chats = db.list_chats(query="Serena", limit=10)
    assert isinstance(chats, list)


def test_read_thread_chat_661():
    # Ground truth handed to the builder claimed a 2026-07-10 message
    # containing "Have fun brother sounds good" in this chat, attributed to
    # Greg. As of this test run that exact string does not appear anywhere
    # in the live chat.db (verified with a full-database scan, not just this
    # chat), so this test checks what is actually verifiable instead:
    # chat 661 resolves, has messages, and sender names resolve correctly
    # for its participants (e.g. Greg Camp).
    con = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM chat WHERE ROWID = 661")
        exists = cur.fetchone()[0] > 0
    finally:
        con.close()
    if not exists:
        pytest.skip("chat 661 does not exist on this machine")

    instance = MessagesDB()
    chat, messages = instance.read_thread("661", limit=500)
    assert chat.rowid == 661
    assert messages, "expected chat 661 to have messages"
    senders = {m.sender for m in messages}
    assert "me" in senders
    # At least one non-"me" sender should resolve to a real contact name
    # rather than a raw phone number, proving handle -> contact resolution
    # works end to end.
    assert any(s != "me" and not s.startswith("+") for s in senders)


def test_read_thread_chat_471_has_pdf_attachments():
    con = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM chat WHERE ROWID = 471")
        exists = cur.fetchone()[0] > 0
    finally:
        con.close()
    if not exists:
        pytest.skip("chat 471 does not exist on this machine")

    instance = MessagesDB()
    chat, messages = instance.read_thread("471", limit=1000)
    assert chat.rowid == 471
    pdf_paths = [
        att.path
        for m in messages
        for att in m.attachments
        if att.path.lower().endswith(".pdf")
    ]
    assert pdf_paths, "expected at least one PDF attachment in chat 471"
    assert any(os.path.exists(p) for p in pdf_paths), "expected a real, existing PDF path"


def test_find_chat_ambiguous_or_unknown_raises(db):
    with pytest.raises(ValueError):
        db.find_chat("this contact definitely does not exist zzz123")
