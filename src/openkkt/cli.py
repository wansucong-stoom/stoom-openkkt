import argparse
import json
from pathlib import Path
import sys
import time

from .source import LiveSource
from .store import Store, now


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("profile", "store", "executable"):
        if key not in value:
            continue
        target = Path(value[key])
        value[key] = (path.resolve().parent / target).resolve() if not target.is_absolute() else target.resolve()
    if value["store"] == value["profile"] or value["profile"] in value["store"].parents:
        raise ValueError("Bridge store must be outside KakaoTalk's profile")
    pids = value.get("pids", [])
    if not value.get("executable") and (not pids or any(not isinstance(p, int) or p <= 0 for p in pids)):
        raise ValueError("Set current KakaoTalk PID(s) explicitly in config")
    return value


def sync_once(source, store, command_binding=None):
    try:
        store.refresh_categories(source.categories())
    except (OSError, RuntimeError, ValueError) as error:
        store.record_health("category_refresh", {"ok": False, "at": now(), "error": type(error).__name__})
        # Do not continue using a stale category allowlist.
        return {"ok": False, "reason": "category_refresh_failed", "rooms": []}
    results = []
    for chat_id in sorted(store.active()):
        try:
            rows, report = source.messages(chat_id)
            changed = store.ingest(chat_id, rows, report)
            results.append({"chat_id": chat_id, "ok": True, "changes": changed})
        except (OSError, RuntimeError, ValueError) as error:
            store.record_health("room:" + chat_id,
                                {"ok": False, "at": now(), "error": type(error).__name__})
            results.append({"chat_id": chat_id, "ok": False})
    commands = None
    if command_binding:
        try:
            rows, _ = source.messages(str(command_binding["chat_id"]))
            commands = {"ok": True, "queued": store.intake_commands(command_binding, rows, int(time.time()))}
        except (OSError, RuntimeError, ValueError) as error:
            store.record_health("command_intake", {"ok": False, "at": now(), "error": type(error).__name__})
            commands = {"ok": False}
    return {"ok": all(x["ok"] for x in results) and (commands is None or commands["ok"]),
            "rooms": results, "commands": commands}


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="stoom-openkkt local bridge")
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("categories")
    select = sub.add_parser("select")
    select.add_argument("names", nargs="*")
    sub.add_parser("sync")
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=30)
    watch.add_argument("--cycles", type=int, default=0, help="0 means until Ctrl+C")
    sub.add_parser("status")
    sub.add_parser("commands")
    query = sub.add_parser("changes")
    query.add_argument("--after", type=int, default=0)
    query.add_argument("--limit", type=int, default=50)
    search = sub.add_parser("search")
    search.add_argument("text")
    search.add_argument("--limit", type=int, default=50)
    server = sub.add_parser("serve")
    server.add_argument("--allow-scope-changes", action="store_true")
    server.add_argument("--live", action="store_true", help="Refresh source on each read; no separate watcher needed")
    read = sub.add_parser("read", help="Refresh selected rooms, then query on demand")
    read.add_argument("--after", type=int, default=0)
    read.add_argument("--limit", type=int, default=50)
    read.add_argument("--text")
    read.add_argument("--chat-id")
    read.add_argument("--recent", action="store_true")
    folders = sub.add_parser("folders", help="Refresh and list custom categories on demand")
    scope = sub.add_parser("scope", help="Refresh metadata and select explicit custom categories")
    scope.add_argument("names", nargs="*")
    gui = sub.add_parser("gui", help="Open the local settings GUI")
    gui.add_argument("--port", type=int, default=0)
    gui.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "gui":
            if not 0 <= args.port <= 65535:
                raise ValueError("Invalid GUI port")
            from .gui import run
            run(args.config, args.port, not args.no_browser); return 0
        config = load_config(args.config)
        store = Store(config["store"])
        if args.command in ("read", "folders", "scope") or (args.command == "serve" and args.live):
            from .reader import Reader
            reader = Reader(config, config_path=args.config)
            if args.command == "folders":
                output(reader.categories()); return 0
            if args.command == "scope":
                output(reader.select(args.names)); return 0
            if args.command == "read":
                output(reader.query(after=args.after, limit=args.limit, text=args.text, chat_id=args.chat_id,
                                    recent=args.recent)); return 0
            from .mcp_server import serve
            serve(reader.store, args.allow_scope_changes, False, reader); return 0
        if args.command == "init":
            store.initialize(); output({"initialized": True}); return 0
        if args.command == "serve":
            from .mcp_server import serve
            serve(store, args.allow_scope_changes, bool(config.get("command_binding"))); return 0
        if args.command == "status":
            output(store.status()); return 0
        if args.command == "commands":
            output(store.pending_commands()); return 0
        if args.command == "select":
            output(store.select(args.names)); return 0
        if args.command == "changes":
            output(store.query(after=args.after, limit=args.limit)); return 0
        if args.command == "search":
            output(store.query(text=args.text, limit=args.limit)); return 0
        if config.get("executable"):
            from .windows import discover_pids
            pids = discover_pids(config["executable"])
            if not pids:
                raise RuntimeError("Configured KakaoTalk is not running")
        else:
            pids = config["pids"]
        source = LiveSource(config["profile"], pids)
        try:
            if args.command == "categories":
                store.refresh_categories(source.categories()); output(store.categories()); return 0
            if args.command == "sync":
                result = sync_once(source, store, config.get("command_binding")); output(result); return 0 if result["ok"] else 2
            if args.interval < 5 or args.cycles < 0:
                raise ValueError("Watch interval must be >=5 seconds, cycles >=0")
            cycle = 0
            while True:
                output(sync_once(source, store, config.get("command_binding")))
                cycle += 1
                if args.cycles and cycle >= args.cycles:
                    return 0
                time.sleep(args.interval)
        finally:
            source.close()
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        # Do not echo paths, message contents, candidate keys or raw SQL.
        print(json.dumps({"ok": False, "error": type(error).__name__,
                          "hint": "Check local config, status and supported schema"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
