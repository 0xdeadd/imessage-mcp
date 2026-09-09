"""Tests for the attributedBody typedstream decoder.

These run against the live ~/Library/Messages/chat.db so they exercise the
decoder on real archived blobs, not synthetic ones. If the database cannot be
opened (no Full Disk Access, or no Messages history on this machine), every
test in this module is skipped rather than failed.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from imessage_mcp.db import CHAT_DB_PATH
from imessage_mcp.typedstream import decode_attributed_body

pytestmark = pytest.mark.skipif(
    not os.path.exists(CHAT_DB_PATH),
    reason="chat.db not found on this machine",
)


def _connect():
    try:
        con = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM message LIMIT 1")
        return con
    except sqlite3.Error:
        pytest.skip("chat.db is not readable (Full Disk Access likely missing)")


def test_decode_success_rate_on_live_db():
    con = _connect()
    cur = con.cursor()
    cur.execute(
        "SELECT attributedBody FROM message "
        "WHERE (text IS NULL OR text = '') AND attributedBody IS NOT NULL"
    )
    rows = cur.fetchall()
    con.close()

    if not rows:
        pytest.skip("no attributedBody-only messages found on this machine")

    decoded = 0
    placeholder_only = 0
    for (blob,) in rows:
        text = decode_attributed_body(blob)
        if text:
            decoded += 1
        else:
            # Attachment-only messages carry just the U+FFFC placeholder,
            # which decode_attributed_body correctly reduces to None.
            placeholder_only += 1

    rate = decoded / len(rows)
    # Real-world messages are a mix of text and attachment-only bodies; text
    # bodies should decode at a very high rate.
    assert rate > 0.85, f"decode success rate too low: {rate:.1%} ({decoded}/{len(rows)})"


def test_decode_specific_known_message():
    con = _connect()
    cur = con.cursor()
    cur.execute(
        "SELECT attributedBody FROM message "
        "WHERE attributedBody IS NOT NULL AND date > 0 "
        "ORDER BY ROWID DESC LIMIT 200"
    )
    rows = cur.fetchall()
    con.close()

    if not rows:
        pytest.skip("no attributedBody rows found")

    # At least a handful of recent messages should decode to non-trivial text.
    decoded_any = any(decode_attributed_body(blob) for (blob,) in rows)
    assert decoded_any


def test_decode_empty_and_none_blob():
    assert decode_attributed_body(None) is None
    assert decode_attributed_body(b"") is None
    assert decode_attributed_body(b"garbage no marker here") is None


def test_strips_object_replacement_placeholder():
    # Synthetic blob mimicking an attachment-only body: NSString marker,
    # length byte 3, then the 3-byte UTF-8 encoding of U+FFFC.
    blob = b"NSString\x01\x94\x84\x01+\x03\xef\xbf\xbc\x86\x84"
    assert decode_attributed_body(blob) is None
