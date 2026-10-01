"""Loopback-only settings GUI. No external assets, telemetry, or send API."""
import json
import os
from pathlib import Path
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import tomllib
import webbrowser

from .reader import Reader
from .store import Store


class GuiState:
    def __init__(self, config_path: Path):
        self.config_path = config_path.resolve()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()

    def raw_config(self):
        defaults = {"profile": "", "store": "./data/assistant.sqlite",
                    "executable": "C:/Program Files/Kakao/KakaoTalk/KakaoTalk.exe"}
        if self.config_path.exists():
            defaults.update(json.loads(self.config_path.read_text(encoding="utf-8-sig")))
        return {key: str(defaults[key]) for key in ("profile", "store", "executable")}

    def reader(self):
        from .cli import load_config
        return Reader(load_config(self.config_path))

    def status(self):
        config = self.raw_config()
        store_path = Path(config["store"])
        if not store_path.is_absolute():
            store_path = self.config_path.parent / store_path
        bridge = {"categories": [], "active_chat_count": 0, "message_count": 0}
        if store_path.is_file():
            bridge = Store(store_path).status()
        host_home = os.environ.get("CODEX_HOME")
        if not host_home:
            user_home = os.environ.get("USERPROFILE")
            if not user_home:
                try:
                    user_home = str(Path.home())
                except RuntimeError:
                    user_home = None
            host_home = str(Path(user_home) / ".codex") if user_home else None
        config_home = Path(host_home) if host_home else None
        mcp = False
        try:
            host = tomllib.loads((config_home / "config.toml").read_text(encoding="utf-8-sig")) if config_home else {}
            mcp = "openkkt" in host.get("mcp_servers", {})
        except (OSError, ValueError):
            pass
        return {"config": config, "config_path": str(self.config_path),
                "installed": {"mcp": mcp,
                              "skill": bool(config_home and (config_home / "skills/openkkt-reader/SKILL.md").is_file())},
                "selected_categories": [c["name"] for c in bridge["categories"] if c["selected"]],
                "bridge_status": bridge, "version": "0.1.0"}

    def profiles(self):
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) / "Kakao/KakaoTalk/users" if local else None
        candidates = []
        if root and root.is_dir():
            for path in sorted(root.iterdir()):
                if path.is_dir() and (path / "chatfolder.edb").is_file():
                    candidates.append({"label": f"계정 폴더 {len(candidates) + 1}", "path": str(path)})
        return {"profiles": candidates,
                "default_executable": "C:/Program Files/Kakao/KakaoTalk/KakaoTalk.exe"}

    def save_config(self, body):
        if set(body) != {"profile", "store", "executable"} or any(
                not isinstance(v, str) or not v.strip() for v in body.values()):
            raise ValueError("데이터 폴더, 실행 파일, 보관 파일을 모두 지정해 주세요.")
        profile = Path(body["profile"]).resolve()
        executable = Path(body["executable"]).resolve()
        store_path = Path(body["store"])
        if not store_path.is_absolute():
            store_path = self.config_path.parent / store_path
        store_path = store_path.resolve()
        if not (profile / "chatfolder.edb").is_file() or not (profile / "chat_data").is_dir():
            raise ValueError("chatfolder.edb와 chat_data가 있는 본인 데이터 폴더를 선택해 주세요.")
        if not executable.is_file() or executable.name.casefold() != "kakaotalk.exe":
            raise ValueError("실제 설치된 KakaoTalk.exe를 지정해 주세요.")
        if store_path == profile or profile in store_path.parents or store_path.suffix not in (".sqlite", ".db"):
            raise ValueError("보관 파일은 원본 데이터 폴더 밖의 .sqlite 또는 .db 파일이어야 합니다.")
        # Changing account/storage first clears the previous monitoring scope.
        scope_changed = True
        if self.config_path.exists():
            try:
                previous = self.reader()
                scope_changed = (previous.config["profile"] != profile or previous.config["store"] != store_path
                                 or previous.config.get("executable") != executable)
                if scope_changed:
                    previous.store.select([])
            except (OSError, RuntimeError, ValueError, KeyError):
                pass
        config = {"profile": str(profile), "store": str(store_path), "executable": str(executable)}
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_name(self.config_path.name + ".tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)
        if scope_changed:
            self.reader().store.select([])
        return {"ok": True, "message": "설정을 저장했습니다. 범주를 불러와 선택해 주세요."}


def make_server(config_path: Path, port=0):
    state = GuiState(config_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never put private paths/categories/messages in access logs.

        def respond(self, status, body, *, html=False, cookie=False):
            encoded = body.encode("utf-8") if html else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie", f"openkkt_gui={state.token}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(encoded)

        def host_ok(self):
            return self.headers.get("Host") == self.server.expected_host

        def authenticated(self):
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookie["openkkt_gui"].value
                return secrets.compare_digest(value, state.token)
            except (KeyError, ValueError):
                return False

        def do_GET(self):
            if not self.host_ok():
                self.respond(403, {"ok": False, "error": "허용된 로컬 주소가 아닙니다."}); return
            if self.path == "/":
                self.respond(200, Path(__file__).with_name("gui.html").read_text(encoding="utf-8"), html=True, cookie=True); return
            if not self.authenticated():
                self.respond(401, {"ok": False, "error": "설정 화면에서 다시 접속해 주세요."}); return
            try:
                with state.lock:
                    if self.path == "/api/status":
                        result = state.status()
                    elif self.path == "/api/profiles":
                        result = state.profiles()
                    else:
                        self.respond(404, {"ok": False, "error": "요청을 찾을 수 없습니다."}); return
                self.respond(200, result)
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                self.respond(400, {"ok": False, "error": "개인 설정 파일을 확인해 주세요.", "error_type": type(error).__name__})

        def do_POST(self):
            if not self.host_ok() or not self.authenticated() or self.headers.get("Origin") != self.server.origin or self.headers.get("X-OpenKKT-GUI") != "1":
                self.respond(403, {"ok": False, "error": "설정 화면에서 요청해 주세요."}); return
            if self.headers.get("Content-Type") != "application/json":
                self.respond(415, {"ok": False, "error": "잘못된 요청 형식입니다."}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("요청 크기를 확인해 주세요.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("잘못된 설정 형식입니다.")
                with state.lock:
                    if self.path == "/api/config":
                        result = state.save_config(body)
                    elif self.path == "/api/categories":
                        result = {"categories": state.reader().categories()}
                    elif self.path == "/api/scope":
                        names = body.get("names")
                        if not isinstance(names, list) or len(names) > 100 or any(not isinstance(x, str) for x in names):
                            raise ValueError("선택할 범주를 확인해 주세요.")
                        result = state.reader().select(names)
                    elif self.path == "/api/recent":
                        limit = body.get("limit", 20)
                        if type(limit) is not int or not 1 <= limit <= 50:
                            raise ValueError("조회 개수는 1~50 사이여야 합니다.")
                        result = state.reader().query(recent=True, limit=limit)
                    else:
                        self.respond(404, {"ok": False, "error": "요청을 찾을 수 없습니다."}); return
                self.respond(200, result)
            except ValueError as error:
                # Only our fixed validation messages are shown; parser details are private.
                message = str(error) if type(error) is ValueError and len(str(error)) < 150 else "설정 값을 확인해 주세요."
                self.respond(400, {"ok": False, "error": message, "error_type": type(error).__name__})
            except (OSError, RuntimeError, KeyError) as error:
                self.respond(400, {"ok": False,
                    "error": "카카오톡 실행·로그인과 선택한 데이터 폴더를 확인해 주세요. 수집되지 않은 방은 카톡에서 열고 다시 조회할 수 있습니다.",
                    "error_type": type(error).__name__})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.expected_host = f"127.0.0.1:{server.server_port}"
    server.origin = "http://" + server.expected_host
    server.gui_state = state
    return server


def run(config_path: Path, port=0, open_browser=True):
    with make_server(config_path, port) as server:
        print(json.dumps({"gui_url": server.origin, "local_only": True}), flush=True)
        if open_browser:
            webbrowser.open(server.origin)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
