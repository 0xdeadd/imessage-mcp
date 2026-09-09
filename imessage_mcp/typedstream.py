"""Decode the `attributedBody` blob stored by macOS Messages.

Modern Messages rows leave `message.text` empty and store the actual body as an
NSAttributedString archived with NSKeyedArchiver's older "typedstream" format
inside `message.attributedBody`. This module pulls the plain text back out
without pulling in a full typedstream/plist parser, since the layout around
the string payload is small and consistent in practice.

Observed layout (confirmed against real chat.db rows on 2026-09-09):

    b"NSString" + 0x01 + (0x94 | 0x95) + 0x84 + 0x01 + 0x2b(b"+") + <length> + <utf8 bytes>

`<length>` is either:
  - a single byte < 0x80: the byte count of the following UTF-8 text, or
  - 0x81 followed by a 2-byte little-endian length, for text >= 128 bytes.

The text run ends before the next `0x86 0x84` marker (start of the following
archived object, typically an NSDictionary of run attributes).
"""

from __future__ import annotations

MARKER = b"NSString"
END_MARKER = b"\x86\x84"
OBJECT_REPLACEMENT_CHAR = "￼"  # placeholder for inline attachments


def _strip_placeholders(text: str) -> str:
    return text.replace(OBJECT_REPLACEMENT_CHAR, "").strip()


def _decode_length_prefixed(blob: bytes, start: int) -> str | None:
    """Try the known length-prefix layout starting at the NSString marker."""
    # Look for the '+' (0x2b) that precedes the length byte, within a small
    # window after the marker. The bytes between "NSString" and "+" vary by
    # one byte (0x94 vs 0x95) depending on archiver version, so don't pin it.
    window_end = min(start + len(MARKER) + 8, len(blob))
    plus = blob.find(b"+", start + len(MARKER), window_end)
    if plus == -1:
        return None

    pos = plus + 1
    if pos >= len(blob):
        return None

    length_byte = blob[pos]
    if length_byte == 0x81:
        if pos + 3 > len(blob):
            return None
        length = int.from_bytes(blob[pos + 1 : pos + 3], "little")
        text_start = pos + 3
    elif length_byte < 0x80:
        length = length_byte
        text_start = pos + 1
    else:
        return None

    text_end = text_start + length
    if text_end > len(blob):
        return None

    raw = blob[text_start:text_end]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _decode_longest_printable_run(blob: bytes, start: int) -> str | None:
    """Fallback: grab the longest printable UTF-8 run after the marker."""
    search_start = start + len(MARKER)
    end = blob.find(END_MARKER, search_start)
    if end == -1:
        end = len(blob)
    chunk = blob[search_start:end]

    # Try decoding the whole chunk first, trimming a leading length/marker
    # byte or two if present, then fall back to scanning for printable runs.
    for trim in range(0, 6):
        try:
            candidate = chunk[trim:].decode("utf-8")
        except UnicodeDecodeError:
            continue
        cleaned = _strip_printable(candidate)
        if cleaned:
            return cleaned

    # Last resort: decode byte-by-byte, keeping printable characters, and
    # take the longest whitespace-delimited-ish run.
    best = ""
    current = []
    for byte in chunk:
        char = chr(byte)
        if 32 <= byte < 127 or byte >= 0xC0:
            current.append(char)
        else:
            run = "".join(current)
            if len(run) > len(best):
                best = run
            current = []
    run = "".join(current)
    if len(run) > len(best):
        best = run
    return best or None


def _strip_printable(text: str) -> str:
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return cleaned.strip()


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Return the plain-text message body from an attributedBody blob.

    Returns None if the blob is empty or no text could be recovered.
    """
    if not blob:
        return None

    start = blob.find(MARKER)
    if start == -1:
        return None

    text = _decode_length_prefixed(blob, start)
    if text is None:
        text = _decode_longest_printable_run(blob, start)

    if text is None:
        return None

    text = _strip_placeholders(text)
    return text or None
