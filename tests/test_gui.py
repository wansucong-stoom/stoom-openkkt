"""Loopback GUI boundary tests using fictional paths and a fake reader only."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from openkkt.gui import GuiState, make_server


class FakeReader:
    def __init__(self):
        self.config = {"profile": Path("fictional-profile"), "store": Path("fictional.sqlite"),
                       "executable": Path("fictional/KakaoTalk.exe")}
        self.store = Mock()
        self.categories = Mock(return_value=[{"id": "demo", "name": "fictional", "selected": False}])
        self.query = Mock(return_value={"messages": []})
        self.select = Mock(side_effect=self._select)

    def _select(self, names):
        if names not in ([], ["fictional"]):
            raise ValueError("Category name missing or ambiguous; refresh/list categories")
        return {"selected_categories": names, "active_chat_count": len(names)}


class GuiBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.json"
        self.reader = FakeReader()
        self.server = make_server(self.config)
        self.server.gui_state.reader = Mock(return_value=self.reader)
        self.server.gui_state.status = Mock(return_value={"config": {"profile": "fictional"}})
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            con.request(method, path, body=json.dumps(body) if body is not None else None,
                        headers=headers or {})
            response = con.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            con.close()

    def auth_headers(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        return {"Cookie": headers["Set-Cookie"].split(";", 1)[0],
                "Origin": self.server.origin, "X-OpenKKT-GUI": "1",
                "Content-Type": "application/json"}

    def test_status_requires_cookie_and_host_must_match_exactly(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.server.gui_state.status.assert_not_called()
        status, headers, _ = self.request("GET", "/", headers={"Host": "rebind.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("Set-Cookie", headers)
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, body = self.request("GET", "/api/status", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["config"]["profile"], "fictional")

    def test_posts_require_auth_origin_custom_header_and_json(self):
        allowed = self.auth_headers()
        denied = []
        for missing in ("Cookie", "Origin", "X-OpenKKT-GUI"):
            denied.append({k: v for k, v in allowed.items() if k != missing})
        denied += [{**allowed, "Origin": "https://external.example"},
                   {**allowed, "Host": "rebind.example"},
                   {**allowed, "Cookie": "openkkt_gui=wrong"}]
        for headers in denied:
            with self.subTest(headers=list(headers)):
                # Authentication is checked before consuming the request body.
                status, _, _ = self.request("POST", "/api/categories", None, headers)
                self.assertEqual(status, 403)
        self.reader.categories.assert_not_called()
        status, _, _ = self.request("POST", "/api/categories", None,
                                    {**allowed, "Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = self.request("POST", "/api/categories", {}, allowed)
        self.assertEqual(status, 200)
        self.reader.categories.assert_called_once_with()

    def test_invalid_scope_and_recent_payloads_never_bypass_reader(self):
        headers = self.auth_headers()
        for body in ({}, {"names": "fictional"}, {"names": [42]},
                     {"names": ["fictional"] * 101}):
            status, _, _ = self.request("POST", "/api/scope", body, headers)
            self.assertEqual(status, 400)
        self.reader.select.assert_not_called()
        status, _, _ = self.request("POST", "/api/scope", {"names": ["outside-scope"]}, headers)
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/scope", {"names": ["fictional"]}, headers)
        self.assertEqual(status, 200)
        for limit in (True, 0, 51, "20"):
            status, _, _ = self.request("POST", "/api/recent", {"limit": limit}, headers)
            self.assertEqual(status, 400)
        self.reader.query.assert_not_called()

    def fictional_install(self):
        profile = Path(self.tmp.name) / "fixture-profile"
        profile.mkdir()
        (profile / "chatfolder.edb").write_bytes(b"fictional metadata")
        (profile / "chat_data").mkdir()
        executable = Path(self.tmp.name) / "KakaoTalk.exe"
        executable.write_bytes(b"fictional executable; never invoked")
        return profile, executable

    def test_save_config_rejects_store_inside_original_profile(self):
        profile, executable = self.fictional_install()
        state = GuiState(self.config)
        state.reader = Mock(return_value=self.reader)
        for store in (profile, profile / "assistant.sqlite", profile / "chat_data" / "assistant.db"):
            with self.subTest(store=store.name), self.assertRaises(ValueError):
                state.save_config({"profile": str(profile), "executable": str(executable),
                                   "store": str(store)})
        self.assertFalse(self.config.exists())
        state.reader.assert_not_called()

    def test_save_config_resolves_relative_store_and_clears_previous_scope(self):
        profile, executable = self.fictional_install()
        self.config.write_text(json.dumps({"profile": "old-fixture", "store": "old.sqlite",
                                           "executable": "old-fixture/KakaoTalk.exe"}), encoding="utf-8")
        state = GuiState(self.config)
        state.reader = Mock(return_value=self.reader)
        self.reader.config = {"profile": Path("old-fixture"), "store": Path("old.sqlite"),
                              "executable": Path("old-fixture/KakaoTalk.exe")}
        result = state.save_config({"profile": str(profile), "executable": str(executable),
                                    "store": "data/new.sqlite"})
        self.assertTrue(result["ok"])
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(Path(saved["store"]), (self.config.parent / "data/new.sqlite").resolve())
        self.assertEqual(set(saved), {"profile", "store", "executable"})
        self.assertEqual(self.reader.store.select.call_count, 2)
        self.assertEqual(self.reader.store.select.call_args.args, ([],))
        self.assertFalse(self.config.with_name(self.config.name + ".tmp").exists())
        self.reader.config = {key: Path(value) for key, value in saved.items()}
        state.save_config({"profile": str(profile), "executable": str(executable),
                           "store": "data/new.sqlite"})
        self.assertEqual(self.reader.store.select.call_count, 2,
                         "Saving unchanged settings must preserve the selected scope")


if __name__ == "__main__":
    unittest.main()
