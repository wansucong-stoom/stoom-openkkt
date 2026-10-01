"""Encrypted synthetic fixtures only: no real account, process or conversation."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import hmac
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

import apsw
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from openkkt.crypto import PAGE
from openkkt.snapshot import EncryptedSnapshot, encrypted_connection
from openkkt.source import LiveSource, decode_message
from test_bridge import wal


KEY, MAC = b"K" * 32, b"M" * 32


def fixture(path, statements):
    con = apsw.Connection(str(path))
    con.execute("PRAGMA page_size=4096")
    con.reserve_bytes("main", 80)
    con.execute("BEGIN")
    for sql, params in statements:
        con.execute(sql, params)
    con.execute("COMMIT")
    con.close()
    plain = path.read_bytes()
    if plain[20] != 80:
        raise AssertionError("Fixture must use SQLCipher reserved bytes")
    result = []
    for number, offset in enumerate(range(0, len(plain), PAGE), 1):
        page = plain[offset:offset + PAGE]
        start = 16 if number == 1 else 0
        iv = number.to_bytes(16, "little")
        cipher = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
        body = cipher.update(page[start:-80]) + cipher.finalize()
        digest = hmac.new(MAC, body + iv + struct.pack("<I", number), "sha512").digest()
        result.append((b"S" * 16 if number == 1 else b"") + body + iv + digest)
    return b"".join(result)


class BoundedRecentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = LiveSource(self.root, [12345])
        self.addCleanup(self.source.close)
        self.fixtures = {}
        self.traces = {}
        for room in range(1, 6):
            sql = [("CREATE TABLE chatLogs(logId INTEGER PRIMARY KEY,authorId,sendAt,message,type,deleted)", ())]
            sql += [("INSERT INTO chatLogs VALUES(?,?,?,?,?,?)",
                     (i, 9, i * 10 + room, "가상 메시지 " + str(i), 1, 0)) for i in range(1, 101)]
            sql += [("INSERT INTO chatLogs VALUES(?,?,?,?,?,?)", (101, 9, 99999, "삭제됨", 1, 1)),
                    ("INSERT INTO chatLogs VALUES(?,?,?,?,?,?)", (102, 9, 99999, "시스템", 0, 0))]
            self.fixtures[str(room)] = fixture(self.root / f"room{room}.db", sql)

        @contextmanager
        def open_fake(path):
            room = path.stem.split("_")[-1]
            snapshot = EncryptedSnapshot(self.fixtures[room], b"")
            snapshot.unlock((KEY, MAC))
            try:
                with encrypted_connection(snapshot) as con:
                    self.traces[room] = []
                    con.set_exec_trace(lambda cursor, sql, params: self.traces[room].append(sql) or True)
                    yield con, snapshot
            finally:
                snapshot.close()

        self.open_patch = patch.object(self.source, "_encrypted_connection", side_effect=open_fake)
        self.open_patch.start()
        self.addCleanup(self.open_patch.stop)
        self.names = {str(i): f"가상 업무방 {i}" for i in range(1, 6)}
        self.name_patch = patch.object(self.source, "room_names", return_value=self.names)
        self.name_patch.start()
        self.addCleanup(self.name_patch.stop)

    def test_five_rooms_500_rows_read_only_20_bodies_globally(self):
        with patch("openkkt.source.decode_message", wraps=decode_message) as decode, \
             patch("openkkt.source.decrypt_snapshot", side_effect=AssertionError("Full decryption forbidden")), \
             patch.object(self.source, "messages", side_effect=AssertionError("Full history forbidden")):
            result = self.source.recent_messages(set(self.names), 20, require_chat_names=True)
        self.assertEqual(decode.call_count, 20)
        self.assertEqual(len(result["messages"]), 20)
        self.assertEqual([row["sent_at"] for row in result["messages"]],
                         sorted([i * 10 + room for i in range(1, 101) for room in range(1, 6)], reverse=True)[:20])
        self.assertEqual(result["read_scope"]["body_rows_saved"], 0)
        self.assertTrue(all(row["chat_name"] == self.names[row["chat_id"]] for row in result["messages"]))
        statements = [sql for trace in self.traces.values() for sql in trace]
        body = [sql for sql in statements if sql.startswith("SELECT") and "message" in sql]
        self.assertEqual(len(body), 20)
        self.assertTrue(all("WHERE _rowid_=?" in sql for sql in body))

    def test_room_filter_and_missing_required_name_prevent_other_body_reads(self):
        self.source.recent_messages({"2"}, 3)
        self.assertEqual(set(self.traces), {"2"})
        self.traces.clear()
        with patch.object(self.source, "room_names", return_value={}):
            with self.assertRaises(RuntimeError):
                self.source.recent_messages({"2"}, 3, require_chat_names=True)
        self.assertEqual(self.traces, {})

    def test_failed_room_prevents_any_body_reads(self):
        self.fixtures["5"] = b"broken"
        with patch("openkkt.source.decode_message", wraps=decode_message) as decode:
            with self.assertRaises(ValueError):
                self.source.recent_messages(set(self.names), 20)
        decode.assert_not_called()

    def test_invalid_limit_and_missing_optional_name_are_explicit(self):
        for limit in (0, 201, True, "20"):
            with self.assertRaises(ValueError):
                self.source.recent_messages({"2"}, limit)
        self.assertEqual(self.traces, {})
        with patch.object(self.source, "room_names", return_value={}):
            result = self.source.recent_messages({"2"}, 1)
        self.assertIsNone(result["messages"][0]["chat_name"])
        self.assertEqual(result["messages"][0]["chat_name_status"], "unavailable")


class LazySnapshotTests(unittest.TestCase):
    def test_live_title_query_selects_only_requested_names_without_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'chat_data').mkdir()
            path = root / 'chat_data/chatListInfo.edb'
            data = fixture(path, [
                ("CREATE TABLE chatRoomList(chatId INTEGER PRIMARY KEY,chatRoomTitle,lastMessage)", ()),
                ("INSERT INTO chatRoomList VALUES(?,?,?)", (1, "가상 업무방", "열람 금지 미리보기")),
                ("INSERT INTO chatRoomList VALUES(?,?,?)", (2, "가상 개인방", "다른 범주 미리보기"))])
            path.write_bytes(data)
            source = LiveSource(root, [12345])
            with patch('openkkt.source.process_identity', return_value=('fictional/kakaotalk.exe', 10)), \
                 patch('openkkt.source.recover_keys', return_value=(KEY, MAC)), \
                 patch('openkkt.source.decrypt_snapshot', side_effect=AssertionError('No full decryption')):
                self.assertEqual(source.room_names({'1'}), {'1': '가상 업무방'})
            source.close()

    def test_live_lazy_process_exit_blocks_return_and_clears_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'example.edb'
            data = fixture(path, [("CREATE TABLE example(value)", ()),
                                  ("INSERT INTO example VALUES('fictional')", ())])
            path.write_bytes(data)
            source = LiveSource(root, [12345])
            with patch('openkkt.source.process_identity', side_effect=[
                    ('fictional/kakaotalk.exe', 10), ('fictional/kakaotalk.exe', 10), OSError('exited')]), \
                 patch('openkkt.source.recover_keys', return_value=(KEY, MAC)):
                with self.assertRaises(OSError):
                    with source._encrypted_connection(path) as (con, _):
                        list(con.execute('SELECT value FROM example'))
            self.assertEqual(source._keys, {})
            source.close()

    def test_only_requested_pages_are_decrypted_and_writes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.db"
            encrypted = fixture(path, [("CREATE TABLE example(value)", ()),
                                       ("INSERT INTO example VALUES(?)", ("가상",)),
                                       ("CREATE TABLE untouched(value)", ())] +
                                [("INSERT INTO untouched VALUES(?)", ("x" * 8000,)) for _ in range(10)])
            snapshot = EncryptedSnapshot(encrypted, b"")
            snapshot.unlock((KEY, MAC))
            with encrypted_connection(snapshot) as con:
                self.assertEqual(list(con.execute("SELECT value FROM example")), [("가상",)])
                self.assertLess(len(snapshot.verified), snapshot.size)
                with self.assertRaises(apsw.ReadOnlyError):
                    con.execute("INSERT INTO example VALUES('forbidden')")
            snapshot.close()
            self.assertIsNone(snapshot.keys)

    def test_committed_wal_overlay_and_page_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.db"
            old = fixture(path, [("CREATE TABLE example(value)", ()),
                                 ("INSERT INTO example VALUES(?)", ("old",))])
            path.unlink()
            new = fixture(path, [("CREATE TABLE example(value)", ()),
                                 ("INSERT INTO example VALUES(?)", ("new",))])
            frames = [(n, len(new) // PAGE if n == len(new) // PAGE else 0,
                       new[(n - 1) * PAGE:n * PAGE]) for n in range(1, len(new) // PAGE + 1)]
            snapshot = EncryptedSnapshot(old, wal(frames))
            snapshot.unlock((KEY, MAC))
            with encrypted_connection(snapshot) as con:
                self.assertEqual(list(con.execute("SELECT value FROM example")), [("new",)])
            snapshot.close()
            damaged = bytearray(old)
            damaged[PAGE + 50] ^= 1
            snapshot = EncryptedSnapshot(bytes(damaged), b"")
            snapshot.unlock((KEY, MAC))
            with self.assertRaises(ValueError):
                with encrypted_connection(snapshot) as con:
                    list(con.execute("SELECT value FROM example"))
            snapshot.close()


if __name__ == "__main__":
    unittest.main()
