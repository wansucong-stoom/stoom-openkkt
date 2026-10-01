import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from openkkt.cli import sync_once
from openkkt.commands import parse_command
from openkkt.crypto import committed_frames, decrypt_snapshot, wal_checksum
from openkkt.source import read_categories
from openkkt.store import Store


KEY, MAC = b"K" * 32, b"M" * 32


def page(number, marker):
    plain = bytearray([marker] * 4096)
    if number == 1:
        plain[:16] = b"SQLite format 3\0"
        plain[16:18], plain[20] = b"\x10\x00", 80
    plain[-80:] = bytes(80)
    iv = bytes([marker]) * 16
    start = 16 if number == 1 else 0
    ctx = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    body = ctx.update(bytes(plain[start:-80])) + ctx.finalize()
    digest = hmac.new(MAC, body + iv + struct.pack("<I", number), "sha512").digest()
    return (b"S" * 16 if number == 1 else b"") + body + iv + digest, bytes(plain)


def wal(frames, order="<"):
    magic = 0x377F0682 if order == "<" else 0x377F0683
    header = struct.pack(">6I", magic, 3007000, 4096, 0, 11, 12)
    state = wal_checksum(header, order=order)
    data = header + struct.pack(">II", *state)
    for number, commit, image in frames:
        prefix = struct.pack(">II", number, commit)
        state = wal_checksum(prefix + image, state, order)
        data += prefix + header[16:24] + struct.pack(">II", *state) + image
    return data


def message(identity="1", text="가상 업무 요청", **updates):
    value = dict(id=identity, author_id="100", sent_at=1000, text=text, type=1, deleted=False)
    value.update(updates)
    return value


class CryptoTests(unittest.TestCase):
    def test_committed_overlay_ignores_uncommitted_and_partial_tail(self):
        first, first_plain = page(1, 1)
        old, _ = page(2, 2)
        new, new_plain = page(2, 3)
        pending, _ = page(2, 4)
        for order in ("<", ">"):
            decoded, report = decrypt_snapshot(first + old,
                wal([(2, 2, new), (2, 0, pending)], order) + b"partial", KEY, MAC)
            self.assertEqual(decoded, first_plain + new_plain)
            self.assertEqual(report["wal_committed_frames"], 1)
            self.assertGreater(report["wal_tail_bytes"], 0)

    def test_wrong_mac_and_authenticated_wal_corruption_rejected(self):
        first, _ = page(1, 1)
        with self.assertRaises(ValueError):
            decrypt_snapshot(first, b"", KEY, b"X" * 32)
        second, _ = page(2, 2)
        damaged = bytearray(second); damaged[50] ^= 1
        with self.assertRaises(ValueError):
            decrypt_snapshot(first + second, wal([(2, 2, bytes(damaged))]), KEY, MAC)

    def test_invalid_header_rejected_and_stale_tail_discarded(self):
        first, _ = page(1, 1)
        data = bytearray(wal([(1, 1, first)])); data[24] ^= 1
        with self.assertRaises(ValueError):
            committed_frames(bytes(data))
        good = wal([(1, 1, first), (1, 1, first)])
        damaged = bytearray(good); damaged[32 + 4120 + 8] ^= 1
        self.assertEqual(len(committed_frames(bytes(damaged))[0]), 1)

    def test_checksum_matches_real_sqlite_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.db"
            con = sqlite3.connect(path)
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("CREATE TABLE fixture(value)")
                con.execute("INSERT INTO fixture VALUES('fictional')"); con.commit()
                data = Path(str(path) + "-wal").read_bytes()
                frames, size, report = committed_frames(data)
                self.assertTrue(frames)
                self.assertGreater(size, 0)
                self.assertEqual(report["wal_tail_bytes"], 0)
            finally:
                con.close()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "bridge.sqlite")
        self.store.initialize()
        self.categories = [dict(id="work", name="업무", chat_ids=["11", "12"]),
                           dict(id="other", name="기타", chat_ids=["12", "13"])]
        self.store.refresh_categories(self.categories)
        self.store.select(["업무"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_room_identity_dedup_literal_search_and_cursor(self):
        self.assertEqual(self.store.ingest("11", [message(text="50% 확인")], {}), 1)
        self.assertEqual(self.store.ingest("11", [message(text="50% 확인")], {}), 0)
        self.store.ingest("12", [message()], {})
        rows = self.store.query()["changes"]
        self.assertEqual({r["chat_id"] for r in rows}, {"11", "12"})
        self.assertEqual(len(self.store.query(text="%")['messages']), 1)
        self.assertEqual(self.store.query(after=rows[-1]["seq"])["changes"], [])

    def test_selection_change_purges_outside_scope_but_keeps_overlap(self):
        for room in ("11", "12"):
            self.store.ingest(room, [message()], {})
        self.store.select(["기타"])
        self.assertEqual({r["chat_id"] for r in self.store.query()["changes"]}, {"12"})
        with self.assertRaises(ValueError):
            self.store.ingest("11", [message()], {})
        with self.assertRaises(ValueError):
            self.store.query(chat_id="11")

    def test_deleted_folder_and_recreated_name_do_not_expand_scope(self):
        self.store.ingest("11", [message()], {})
        self.store.refresh_categories([dict(id="new", name="업무", chat_ids=["13"])])
        self.assertEqual(self.store.active(), set())
        self.assertEqual(self.store.status()["message_count"], 0)
        self.assertEqual(self.store.status()["missing_selected_categories"], ["work"])

    def test_membership_change_and_rename_follow_stable_id(self):
        self.store.ingest("11", [message()], {})
        self.store.refresh_categories([dict(id="work", name="회사", chat_ids=["12"])])
        self.assertEqual(self.store.active(), {"12"})
        self.assertEqual(self.store.status()["message_count"], 0)

    def test_deleted_message_body_removed_and_failed_scope_blocks_reads(self):
        self.store.ingest("11", [message()], {})
        self.store.ingest("11", [message(text="", deleted=True)], {})
        self.assertEqual(self.store.query()["changes"][-1]["text"], "")
        self.assertEqual(self.store.query(text="요청")["messages"], [])
        self.store.record_health("category_refresh", {"ok": False})
        with self.assertRaises(ValueError):
            self.store.query()

    def test_category_failure_never_reads_chat_body(self):
        class Source:
            def categories(self):
                raise RuntimeError("unavailable")
            def messages(self, chat_id):
                raise AssertionError("Must not read")
        self.assertFalse(sync_once(Source(), self.store)["ok"])

    def test_stale_scope_requires_refresh_before_read(self):
        self.store.record_health("category_refresh", {"ok": True, "at": "2000-01-01T00:00:00+00:00"})
        with self.assertRaises(ValueError):
            self.store.query()

    def test_intake_no_history_replay_no_duplicates_no_reply_loop(self):
        binding = dict(chat_id="99", author_id="100", name="Elisa")
        self.assertEqual(self.store.intake_commands(binding, [message(text="Elisa 오래된 요청")], 1000), 0)
        rows = [message(), message("2", "Elisa 업무 요약해줘"),
                message("3", "[Elisa] 요약했습니다"), message("4", "Elisa 잘못된 발신자", author_id="200")]
        self.assertEqual(self.store.intake_commands(binding, rows, 1000), 1)
        self.assertEqual(self.store.intake_commands(binding, rows, 1000), 0)
        self.assertEqual(len(self.store.pending_commands()), 1)

    def test_mcp_stdio_handshake_and_read_only_tools(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Install optional mcp dependency")
        self.store.ingest("11", [message()], {})
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps(dict(profile=str(Path(self.tmp.name) / "profile"),
                                         store=str(self.store.path), pids=[12345])))

        async def verify():
            parameters = StdioServerParameters(command=sys.executable,
                args=["-m", "openkkt.cli", "--config", str(config), "serve"])
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    listing = await client.list_tools()
                    names = {t.name for t in listing.tools}
                    self.assertEqual(names, {"list_categories", "bridge_status", "get_changes", "search_messages"})
                    result = await client.call_tool("search_messages", {"text": "업무"})
                    self.assertFalse(result.isError)
                    self.assertIn("가상 업무 요청", result.content[0].text)
        asyncio.run(verify())


class SourceAndCommandTests(unittest.TestCase):
    def test_custom_category_schema(self):
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE TABLE ChatFolder(id,name,chatIds)")
            con.execute("INSERT INTO ChatFolder VALUES('demo','업무','11,12,11')")
            self.assertEqual(read_categories(con.serialize())[0]["chat_ids"], ["11", "12"])
        finally:
            con.close()

    def test_command_origin_and_age(self):
        binding = dict(chat_id="99", author_id="100")
        row = message(text='"Elisa" 컴퓨터 꺼줘')
        self.assertEqual(parse_command("99", row, binding, 1000), "컴퓨터 꺼줘")
        self.assertIsNone(parse_command("11", row, binding, 1000))
        self.assertIsNone(parse_command("99", row, binding, 1400))
        self.assertIsNone(parse_command("99", message(text="[Elisa] 네"), binding, 1000))


if __name__ == "__main__":
    unittest.main()
