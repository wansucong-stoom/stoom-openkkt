import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openkkt.reader import Reader


class FakeSource:
    rooms_read = []
    closed = 0

    def __init__(self, *args):
        pass

    def categories(self):
        return [{"id": "work", "name": "업무", "chat_ids": ["1"]},
                {"id": "private", "name": "개인", "chat_ids": ["2"]}]

    def messages(self, chat_id):
        self.rooms_read.append(chat_id)
        return [{"id": "10", "author_id": "9", "sent_at": 100,
                 "text": "회의 시간", "type": 1, "deleted": False}], {}

    def close(self):
        type(self).closed += 1

    def recent_messages(self, chat_ids, limit, *, require_chat_names=False):
        rows = []
        for room in sorted(chat_ids):
            items, _ = self.messages(room)
            rows.extend({**item, "chat_id": room, "chat_name": "가상 업무방"} for item in items)
        return {"messages": rows[:limit]}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reader = Reader({"profile": Path(self.tmp.name) / "source", "pids": [1],
                              "store": Path(self.tmp.name) / "reader.sqlite"})
        FakeSource.rooms_read = []
        FakeSource.closed = 0

    @patch("openkkt.reader.LiveSource", FakeSource)
    def test_metadata_and_unselected_query_do_not_read_any_room(self):
        self.assertEqual(len(self.reader.categories()), 2)
        self.assertEqual(self.reader.query(recent=True)["messages"], [])
        self.assertEqual(FakeSource.rooms_read, [])
        self.assertEqual(FakeSource.closed, 3)

    @patch("openkkt.reader.LiveSource", FakeSource)
    def test_query_refreshes_only_explicit_selected_categories(self):
        self.reader.select(["업무"])
        result = self.reader.query(recent=True)
        self.assertEqual([r["chat_id"] for r in result["messages"]], ["1"])
        self.assertEqual(FakeSource.rooms_read, ["1"])
        self.assertEqual(FakeSource.closed, 3)

    @patch("openkkt.reader.LiveSource", FakeSource)
    def test_source_unavailable_blocks_previously_collected_messages(self):
        self.reader.select(["업무"])
        self.reader.query(recent=True)
        with patch.object(self.reader, "_source", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.reader.query(recent=True)
        with self.assertRaises(ValueError):
            self.reader.store.query(recent=True)

    @patch("openkkt.reader.LiveSource", FakeSource)
    def test_recent_does_not_cache_body_and_invalid_request_does_not_read_rooms(self):
        self.reader.select(["업무"])
        self.reader.query(recent=True, limit=1)
        self.assertEqual(self.reader.store.status()["message_count"], 0)
        FakeSource.rooms_read = []
        for kwargs in ({"limit": 0}, {"limit": True}, {"text": "찾기"},
                       {"chat_id": "2"}, {"after": 1}):
            with self.assertRaises(ValueError):
                self.reader.query(recent=True, **kwargs)
        self.assertEqual(FakeSource.rooms_read, [])


if __name__ == "__main__":
    unittest.main()
