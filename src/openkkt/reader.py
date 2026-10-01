"""On-demand local reader for connected-computer tasks and STDIO MCP hosts."""
from pathlib import Path

from .source import LiveSource
from .store import Store, now


class Reader:
    def __init__(self, config: dict, config_path: Path | None = None):
        self.config = config
        self.config_path = config_path
        self.store = Store(config["store"])
        self.store.initialize()

    def _source(self):
        if self.config.get("executable"):
            from .windows import discover_pids
            pids = discover_pids(Path(self.config["executable"]))
            if not pids:
                raise RuntimeError("Configured KakaoTalk executable is not running")
        else:
            pids = self.config["pids"]
        return LiveSource(self.config["profile"], pids)

    def refresh(self, *, collect=False):
        source = None
        try:
            if self.config_path:
                from .cli import load_config
                self.config = load_config(self.config_path)
                self.store = Store(self.config["store"])
                self.store.initialize()
            source = self._source()
            self.store.refresh_categories(source.categories())
        except (OSError, RuntimeError, ValueError) as error:
            self.store.record_health("category_refresh", {
                "ok": False, "at": now(), "error": type(error).__name__})
            if source:
                source.close()
            raise RuntimeError("Live category refresh failed; message access stopped") from None
        try:
            if collect:
                failures = []
                for chat_id in sorted(self.store.active()):
                    try:
                        rows, report = source.messages(chat_id)
                        self.store.ingest(chat_id, rows, report)
                    except (OSError, RuntimeError, ValueError) as error:
                        self.store.record_health("room:" + chat_id, {
                            "ok": False, "at": now(), "error": type(error).__name__})
                        failures.append(chat_id)
                if failures:
                    raise RuntimeError("Selected rooms could not all be refreshed; check bridge_status")
        finally:
            source.close()

    def categories(self):
        self.refresh()
        return self.store.categories()

    def status(self):
        try:
            self.refresh()
        except RuntimeError:
            pass
        return self.store.status()

    def select(self, names: list[str]):
        self.refresh()
        return self.store.select(names)

    def query(self, **kwargs):
        self.refresh(collect=True)
        return self.store.query(**kwargs)
