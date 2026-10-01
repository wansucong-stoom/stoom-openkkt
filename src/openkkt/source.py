"""Explicit-profile local source. No automatic account or chat enumeration."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import sqlite3
import time

from .crypto import decrypt_snapshot
from .windows import recover_keys


@contextmanager
def snapshot_connection(plain: bytes):
    # The WAL has already been overlaid. In-memory SQLite cannot open a WAL
    # image, so change only the journaling version bytes on this private copy.
    image = bytearray(plain)
    if image[:16] != b"SQLite format 3\0":
        raise ValueError("Not a SQLite image")
    image[18:20] = b"\x01\x01"
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(bytes(image))
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA query_only=ON")
        if [r[0] for r in con.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("Snapshot integrity check failed")
        yield con
    finally:
        con.close()


def stable_pair(path: Path, attempts=3) -> tuple[bytes, bytes]:
    """Optimistic capture, never locks or writes KakaoTalk's files.

    Two identical full DB/WAL reads are required. This is not a SQLite read
    transaction; rapid writers can cause retry/failure rather than stale success.
    """
    wal = Path(str(path) + "-wal")
    journal = Path(str(path) + "-journal")

    def read():
        if journal.exists() and journal.stat().st_size:
            raise ValueError("Rollback journal present; retry after transaction")
        if path.stat().st_size + (wal.stat().st_size if wal.exists() else 0) > 512 * 1024 * 1024:
            raise ValueError("Snapshot exceeds 512 MiB limit")
        return path.read_bytes(), wal.read_bytes() if wal.exists() else b""

    for _ in range(attempts):
        first = read()
        second = read()
        if first == second:
            return first
        time.sleep(0.05)
    raise RuntimeError("Source changed during capture; retry later")


class LiveSource:
    def __init__(self, profile: Path, pids: list[int]):
        self.profile = profile.resolve()
        self.pids = pids
        self._keys = {}  # Process-local only; never serialized.

    def _read(self, path: Path) -> tuple[bytes, dict]:
        data, wal = stable_pair(path)
        cache_id = (str(path), data[:16])
        keys = self._keys.get(cache_id)
        if keys:
            try:
                return decrypt_snapshot(data, wal, *keys)
            except ValueError:
                self._keys.pop(cache_id, None)
        for pid in self.pids:
            try:
                keys = recover_keys(pid, data[:4096])
                plain, report = decrypt_snapshot(data, wal, *keys)
                self._keys[cache_id] = keys
                report["source_sha256"] = hashlib.sha256(data + wal).hexdigest()
                return plain, report
            except (OSError, RuntimeError, ValueError):
                continue
        raise RuntimeError("Target DB unavailable or unsupported; verify loaded chat and PID")

    def categories(self) -> list[dict]:
        plain, _ = self._read(self.profile / "chatfolder.edb")
        return read_categories(plain)

    def messages(self, chat_id: str) -> tuple[list[dict], dict]:
        if not re.fullmatch(r"[0-9]+", chat_id):
            raise ValueError("Invalid chat ID")
        plain, report = self._read(self.profile / "chat_data" / f"chatLogs_{chat_id}.edb")
        return read_messages(plain), report

    def close(self):
        self._keys.clear()


def read_categories(plain: bytes) -> list[dict]:
    with snapshot_connection(plain) as con:
        columns = {r[1] for r in con.execute("PRAGMA table_info(ChatFolder)")}
        if not {"id", "name", "chatIds"} <= columns:
            raise ValueError("Unsupported category schema")
        result = []
        for identity, name, raw in con.execute("SELECT id,name,chatIds FROM ChatFolder"):
            ids = [x.strip() for x in (raw or "").split(",") if x.strip()]
            if any(not re.fullmatch(r"[0-9]+", x) for x in ids):
                raise ValueError("Unsupported category membership encoding")
            result.append({"id": str(identity), "name": name,
                           "chat_ids": sorted(set(ids)), "kind": "custom"})
        return result


def read_messages(plain: bytes) -> list[dict]:
    with snapshot_connection(plain) as con:
        columns = {r[1] for r in con.execute("PRAGMA table_info(chatLogs)")}
        if not {"logId", "authorId", "sendAt", "message", "type", "deleted"} <= columns:
            raise ValueError("Unsupported chatLogs schema")
        result = []
        for identity, author, stamp, text, kind, deleted in con.execute(
                "SELECT logId,authorId,sendAt,message,type,deleted FROM chatLogs"):
            if text is not None and not isinstance(text, str):
                raise ValueError("Message is not decoded text")
            result.append({"id": str(identity), "author_id": str(author),
                           "sent_at": int(stamp or 0), "text": "" if deleted else (text or ""),
                           "type": kind, "deleted": bool(deleted)})
        return result
