"""Authenticated SQLCipher 4 page decoding and committed WAL overlay.

Supports only 4096-byte pages, AES-256-CBC and HMAC-SHA512 with 80
reserved bytes. Keys are supplied in memory; this module does not persist them.
"""
import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE = 4096


def decrypt_page(page: bytes, number: int, key: bytes, mac: bytes) -> bytes:
    if len(page) != PAGE or number < 1 or len(key) != 32 or len(mac) != 32:
        raise ValueError("Invalid SQLCipher page or key length")
    start = 16 if number == 1 else 0
    payload, iv = page[start:-80], page[-80:-64]
    digest = hmac.new(mac, payload + iv + struct.pack("<I", number), "sha512").digest()
    if not hmac.compare_digest(digest, page[-64:]):
        raise ValueError(f"SQLCipher authentication failed on page {number}")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = (b"SQLite format 3\0" if number == 1 else b"")
    plain += cipher.update(payload) + cipher.finalize() + bytes(80)
    if number == 1 and (plain[16:18] != b"\x10\x00" or plain[20] != 80):
        raise ValueError("Unsupported decrypted page header")
    return plain


def wal_checksum(data: bytes, state=(0, 0), order="<") -> tuple[int, int]:
    if len(data) % 8:
        raise ValueError("WAL checksum input is not word-paired")
    a, b = state
    for x, y in struct.iter_unpack(order + "II", data):
        a = (a + x + b) & 0xFFFFFFFF
        b = (b + y + a) & 0xFFFFFFFF
    return a, b


def committed_frames(wal: bytes) -> tuple[list[tuple[int, bytes]], int, dict]:
    """Read valid prefix, discard stale/partial/uncommitted tail like SQLite.

    Checksums cover encrypted on-disk page bytes. Page HMACs are checked later.
    An invalid header is an error, never silently treated as an empty WAL.
    """
    if not wal:
        return [], 0, {"wal_frames": 0, "wal_committed_frames": 0, "wal_tail_bytes": 0}
    if len(wal) < 32:
        raise ValueError("Incomplete WAL header; retry snapshot")
    magic, version, size = struct.unpack_from(">III", wal)
    if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or size != PAGE:
        raise ValueError("Unsupported WAL header")
    order = "<" if magic == 0x377F0682 else ">"
    state = wal_checksum(wal[:24], order=order)
    if state != struct.unpack_from(">II", wal, 24):
        raise ValueError("WAL header checksum failed")
    frames, last_commit, db_size = [], 0, 0
    offset = 32
    while offset + 24 + PAGE <= len(wal):
        header = wal[offset:offset + 24]
        page = wal[offset + 24:offset + 24 + PAGE]
        number, commit = struct.unpack_from(">II", header)
        if number == 0 or header[8:16] != wal[16:24]:
            break
        next_state = wal_checksum(header[:8] + page, state, order)
        if next_state != struct.unpack_from(">II", header, 16):
            break
        state = next_state
        frames.append((number, page))
        offset += 24 + PAGE
        if commit:
            last_commit, db_size = len(frames), commit
    return frames[:last_commit], db_size, {
        "wal_frames": len(frames), "wal_committed_frames": last_commit,
        "wal_tail_bytes": len(wal) - (32 + last_commit * (24 + PAGE)),
    }


def decrypt_snapshot(data: bytes, wal: bytes, key: bytes, mac: bytes) -> tuple[bytes, dict]:
    if not data or len(data) % PAGE:
        raise ValueError("Database must contain complete SQLCipher pages")
    frames, size, report = committed_frames(wal)
    # Overlay BEFORE authenticating: an interrupted checkpoint can be repaired
    # by its committed WAL page. Authenticate every page in the final image.
    pages = [data[i:i + PAGE] for i in range(0, len(data), PAGE)]
    if size:
        # Prevent huge allocations for a damaged or malicious commit marker.
        max_written = max((n for n, _ in frames), default=0)
        if size > max(len(pages), max_written) or size * PAGE > 512 * 1024 * 1024:
            raise ValueError("Invalid or oversized WAL commit")
        pages = (pages + [b""] * max(0, size - len(pages)))[:size]
        for number, page in frames:
            if number <= size:
                pages[number - 1] = page
    plain = b"".join(decrypt_page(p, i + 1, key, mac) for i, p in enumerate(pages))
    report.update(verified_pages=len(pages), wal_applied=bool(frames))
    return plain, report


def hmac_key(key: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", key, bytes(x ^ 0x3A for x in salt), 2, 32)
