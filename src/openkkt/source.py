"""Explicit-profile local source. No automatic account or chat enumeration."""
from contextlib import contextmanager, ExitStack
import hashlib
from pathlib import Path
import re
import sqlite3
import time

from .crypto import decrypt_snapshot
from .windows import process_identity, recover_keys
from .snapshot import EncryptedSnapshot, encrypted_connection


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
        self._process_identities = None

    def _verify_processes(self):
        try:
            identities = {pid: process_identity(pid) for pid in self.pids}
            if not identities:
                raise ValueError("Select at least one KakaoTalk process")
            if self._process_identities is None:
                self._process_identities = identities
            elif identities != self._process_identities:
                raise RuntimeError("Selected KakaoTalk process changed; reconfigure the collector")
        except (OSError, RuntimeError, ValueError):
            self._keys.clear()
            raise

    def _read(self, path: Path) -> tuple[bytes, dict]:
        self._verify_processes()
        data, wal = stable_pair(path)
        cache_id = (str(path), data[:16])
        keys = self._keys.get(cache_id)
        if keys:
            try:
                result = decrypt_snapshot(data, wal, *keys)
            except ValueError:
                self._keys.pop(cache_id, None)
            else:
                self._verify_processes()
                return result
        for pid in self.pids:
            try:
                keys = recover_keys(pid, data[:4096])
                plain, report = decrypt_snapshot(data, wal, *keys)
            except (OSError, RuntimeError, ValueError):
                continue
            self._verify_processes()
            self._keys[cache_id] = keys
            report["source_sha256"] = hashlib.sha256(data + wal).hexdigest()
            return plain, report
        raise RuntimeError("Target DB unavailable or unsupported; verify loaded chat and PID")

    def categories(self) -> list[dict]:
        plain, _ = self._read(self.profile / "chatfolder.edb")
        return read_categories(plain)

    def messages(self, chat_id: str) -> tuple[list[dict], dict]:
        if not re.fullmatch(r"[0-9]+", chat_id):
            raise ValueError("Invalid chat ID")
        plain, report = self._read(self.profile / "chat_data" / f"chatLogs_{chat_id}.edb")
        return read_messages(plain), report

    @contextmanager
    def _encrypted_connection(self, path: Path):
        self._verify_processes()
        data, wal = stable_pair(path)
        snapshot = EncryptedSnapshot(data, wal)
        cache_id = (str(path), data[:16])
        try:
            keys = self._keys.get(cache_id)
            if keys:
                try:
                    snapshot.unlock(keys)
                except ValueError:
                    self._keys.pop(cache_id, None)
                    keys = None
            if not keys:
                for pid in self.pids:
                    try:
                        keys = recover_keys(pid, snapshot.encrypted_page(1))
                        snapshot.unlock(keys)
                    except (OSError, RuntimeError, ValueError):
                        keys = None
                        continue
                    self._keys[cache_id] = keys
                    break
                if not keys:
                    raise RuntimeError("Target DB unavailable or unsupported")
            self._verify_processes()
            with encrypted_connection(snapshot) as con:
                yield con, snapshot
            self._verify_processes()
        finally:
            snapshot.close()

    def room_names(self, chat_ids: set[str]) -> dict[str, str]:
        """Fetch only selected room titles; never select previews or member data."""
        if not chat_ids:
            return {}
        validate_chat_ids(chat_ids)
        path = self.profile / "chat_data" / "chatListInfo.edb"
        if not path.is_file():
            return {}
        with self._encrypted_connection(path) as (con, _):
            columns = {r[1] for r in con.execute("PRAGMA table_info(chatRoomList)")}
            if not {"chatId", "chatRoomTitle"} <= columns:
                raise ValueError("Unsupported room title schema")
            names = {}
            for chat_id in sorted(chat_ids):
                rows = list(con.execute(
                    "SELECT chatRoomTitle FROM chatRoomList WHERE chatId=? OR chatId=? LIMIT 2",
                    (int(chat_id), chat_id)))
                if len(rows) > 1:
                    raise ValueError("Ambiguous room title")
                if rows and isinstance(rows[0][0], str) and rows[0][0].strip():
                    names[chat_id] = rows[0][0]
            return names

    def recent_messages(self, chat_ids: set[str], limit: int, *, require_chat_names=False) -> dict:
        """Choose global top-N IDs/times, then read at most N body rows, without caching."""
        validate_recent_limit(limit)
        validate_chat_ids(chat_ids)
        names = self.room_names(chat_ids)
        if require_chat_names and chat_ids - names.keys():
            raise RuntimeError("Requested room names unavailable; body access stopped")
        with ExitStack() as stack:
            connections, snapshots, candidates = {}, {}, []
            total_bytes = 0
            for chat_id in sorted(chat_ids):
                path = self.profile / "chat_data" / f"chatLogs_{chat_id}.edb"
                con, snapshot = stack.enter_context(self._encrypted_connection(path))
                total_bytes += snapshot.encrypted_bytes
                if total_bytes > 512 * 1024 * 1024:
                    raise ValueError("Combined recent snapshots exceed 512 MiB")
                connections[chat_id], snapshots[chat_id] = con, snapshot
                validate_message_schema(con)
                # No author or message column is selected during global ranking.
                for row_id, identity, stamp in con.execute(
                        "SELECT _rowid_,logId,sendAt FROM chatLogs WHERE deleted=0 AND type=1 "
                        "ORDER BY sendAt DESC,logId DESC LIMIT ?", (limit,)):
                    candidates.append((int(stamp or 0), int(identity), chat_id, row_id))
            candidates.sort(key=lambda row: (-row[0], row[2], -row[1]))
            messages = []
            for stamp, identity, chat_id, row_id in candidates[:limit]:
                row = next(iter(connections[chat_id].execute(
                    "SELECT logId,authorId,sendAt,message,type,deleted FROM chatLogs "
                    "WHERE _rowid_=? AND deleted=0 AND type=1", (row_id,))), None)
                if row is None:
                    raise ValueError("Ranked message missing from immutable snapshot")
                message = decode_message(row)
                message.update(chat_id=chat_id, chat_name=names.get(chat_id),
                               chat_name_status="available" if chat_id in names else "unavailable")
                messages.append(message)
            return {"messages": messages,
                    "content_semantics": "latest live text messages across requested rooms",
                    "read_scope": {"mode": "bounded_recent", "body_rows_read": len(messages),
                                   "body_limit": limit, "body_rows_saved": 0,
                                   "ranking_columns": ["logId", "sendAt"],
                                   "ranking_rows": len(candidates), "rooms_considered": len(chat_ids),
                                   "decryption": "on_demand_pages",
                                   "page_granularity": "pages may contain adjacent message bodies"},
                    "room_reports": {room: {**snapshot.report,
                                             "verified_pages": len(snapshot.verified)}
                                     for room, snapshot in snapshots.items()}}

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
        validate_message_schema(con)
        result = []
        for row in con.execute(
                "SELECT logId,authorId,sendAt,message,type,deleted FROM chatLogs"):
            result.append(decode_message(row))
        return result


def validate_chat_ids(chat_ids):
    if any(not isinstance(cid, str) or not re.fullmatch(r"[0-9]+", cid) for cid in chat_ids):
        raise ValueError("Invalid chat ID")


def validate_recent_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Invalid recent body limit (1..200)")


def validate_message_schema(con):
    columns = {r[1] for r in con.execute("PRAGMA table_info(chatLogs)")}
    if not {"logId", "authorId", "sendAt", "message", "type", "deleted"} <= columns:
        raise ValueError("Unsupported chatLogs schema")


def decode_message(row):
    identity, author, stamp, text, kind, deleted = row
    if text is not None and not isinstance(text, str):
        raise ValueError("Message is not decoded text")
    return {"id": str(identity), "author_id": str(author),
            "sent_at": int(stamp or 0), "text": "" if deleted else (text or ""),
            "type": kind, "deleted": bool(deleted)}
