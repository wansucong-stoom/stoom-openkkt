"""Synthetic liveness/snapshot checks; never access a real app or account."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openkkt.source import LiveSource
from openkkt.store import Store, now
from openkkt.windows import discover_pids


def message(identity="1", stamp=1000, **updates):
    row = dict(id=identity, author_id="100", sent_at=stamp,
               text="fictional request", type=1, deleted=False)
    row.update(updates)
    return row


class SourceLivenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.source = LiveSource(Path(self.tmp.name), [12345])
        self.path = Path(self.tmp.name) / "fixture.edb"
        self.pair = patch("openkkt.source.stable_pair", return_value=(b"S" * 4096, b""))
        self.snapshot = patch("openkkt.source.decrypt_snapshot", return_value=(b"fictional", {}))
        self.recovery = patch("openkkt.source.recover_keys", return_value=(b"K" * 32, b"M" * 32))
        self.identity = patch("openkkt.source.process_identity", return_value=("fictional/kakaotalk.exe", 10))
        self.read = self.pair.start()
        self.snapshot.start()
        self.recover = self.recovery.start()
        self.process = self.identity.start()

    def tearDown(self):
        self.identity.stop(); self.recovery.stop(); self.snapshot.stop(); self.pair.stop()
        self.source.close(); self.tmp.cleanup()

    def test_same_process_reuses_key_but_checks_identity(self):
        self.source._read(self.path)
        before = self.process.call_count
        self.source._read(self.path)
        self.assertEqual(self.recover.call_count, 1)
        self.assertGreater(self.process.call_count, before)

    def test_exit_clears_cache_and_blocks_source_file_read(self):
        self.source._read(self.path)
        self.process.side_effect = OSError("fictional process exited")
        self.read.reset_mock()
        with self.assertRaises(OSError):
            self.source._read(self.path)
        self.assertEqual(self.source._keys, {})
        self.read.assert_not_called()

    def test_recycled_pid_or_changed_image_cannot_reuse_key(self):
        self.source._read(self.path)
        for identity in (("fictional/kakaotalk.exe", 20), ("other/kakaotalk.exe", 10)):
            self.process.return_value = identity
            with self.assertRaises(RuntimeError):
                self.source._read(self.path)
            self.assertEqual(self.source._keys, {})
        self.assertEqual(self.recover.call_count, 1)

    def test_exit_during_decode_blocks_return_and_clears_cache(self):
        self.source._read(self.path)
        self.process.side_effect = [("fictional/kakaotalk.exe", 10), OSError("exited")]
        with self.assertRaises(OSError):
            self.source._read(self.path)
        self.assertEqual(self.source._keys, {})


class ProcessDiscoveryTests(unittest.TestCase):
    def test_exact_image_match_excludes_sandbox_path(self):
        expected = (Path.cwd() / "fictional" / "KakaoTalk.exe").resolve()
        candidates = iter([(10, "KakaoTalk.exe"), (20, "KakaoTalk.exe"), (30, "Other.exe")])

        def next_entry(handle, pointer):
            try:
                pid, name = next(candidates)
            except StopIteration:
                return False
            pointer._obj.th32ProcessID = pid
            pointer._obj.szExeFile = name
            return True

        kernel = SimpleNamespace(CreateToolhelp32Snapshot=Mock(return_value=44),
                                 Process32FirstW=Mock(side_effect=next_entry),
                                 Process32NextW=Mock(side_effect=next_entry), CloseHandle=Mock())
        identities = {10: (str(expected).casefold(), 1),
                      20: (str(expected.parent / "sandbox" / "KakaoTalk.exe").casefold(), 2)}
        with patch("openkkt.windows.sys.platform", "win32"), \
             patch("openkkt.windows.c.WinDLL", return_value=kernel, create=True), \
             patch("openkkt.windows.process_identity", side_effect=identities.__getitem__) as identify:
            self.assertEqual(discover_pids(expected), [10])
        self.assertEqual(identify.call_count, 2)
        kernel.CloseHandle.assert_called_once_with(44)


class StoreLivenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "fixture.sqlite")
        self.store.initialize()
        self.store.refresh_categories([dict(id="work", name="fictional", chat_ids=["11", "12"])])
        self.store.select(["fictional"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_uncollected_room_blocks_whole_scope_but_not_healthy_room(self):
        self.store.ingest("11", [message()], {})
        for query in ({}, {"text": "request"}, {"recent": True}):
            with self.assertRaises(ValueError):
                self.store.query(**query)
        self.assertEqual(len(self.store.query(chat_id="11")["changes"]), 1)
        with self.assertRaises(ValueError):
            self.store.query(chat_id="12")

    def test_failed_and_expired_rooms_block_cached_body(self):
        self.store.ingest("11", [message()], {})
        self.store.ingest("12", [], {})
        for state in ({"ok": False, "at": now()},
                      {"ok": True, "at": "2000-01-01T00:00:00+00:00"}):
            self.store.record_health("room:11", state)
            for query in ({}, {"chat_id": "11"}, {"text": "request"}, {"recent": True}):
                with self.assertRaises(ValueError):
                    self.store.query(**query)
        self.assertEqual(self.store.query(chat_id="12")["changes"], [])

    def test_recent_orders_by_sent_at_and_excludes_feed_and_deleted(self):
        self.store.ingest("11", [message("1", 1000), message("2", 3000),
                                  message("3", 4000, deleted=True, text=""),
                                  message("4", 5000, type=0)], {})
        self.store.ingest("12", [message("5", 2000)], {})
        self.assertEqual([r["id"] for r in self.store.query(recent=True, limit=2)["messages"]],
                         ["2", "5"])
        self.assertEqual([r["id"] for r in self.store.query(recent=True, chat_id="11")["messages"]],
                         ["2", "1"])
        with self.assertRaises(ValueError):
            self.store.query(recent=True, text="request")


if __name__ == "__main__":
    unittest.main()
