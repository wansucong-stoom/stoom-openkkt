"""Normalized assistant DB: scope membership, messages, monotonic changes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path.resolve()

    @contextmanager
    def connect(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.path, timeout=10)
        else:
            con = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if write:
            con.execute("PRAGMA secure_delete=ON")
        try:
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield con
            if write:
                con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        with self.connect(True) as con:
            con.executescript("""
              CREATE TABLE IF NOT EXISTS categories(id TEXT PRIMARY KEY,name TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS membership(category_id TEXT,chat_id TEXT,
                PRIMARY KEY(category_id,chat_id),
                FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE);
              CREATE TABLE IF NOT EXISTS selected(category_id TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS messages(chat_id TEXT,id TEXT,author_id TEXT,
                sent_at INTEGER,text TEXT,type INTEGER,deleted INTEGER,fingerprint TEXT,
                PRIMARY KEY(chat_id,id));
              CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,id TEXT,kind TEXT,observed_at TEXT);
              CREATE TABLE IF NOT EXISTS health(key TEXT PRIMARY KEY,value TEXT);
              CREATE INDEX IF NOT EXISTS changes_room ON changes(chat_id,seq);
              CREATE INDEX IF NOT EXISTS messages_time ON messages(chat_id,sent_at);
              CREATE TABLE IF NOT EXISTS commands(chat_id TEXT,id TEXT,body TEXT,
                received_at TEXT,state TEXT DEFAULT 'pending', PRIMARY KEY(chat_id,id));
              CREATE TABLE IF NOT EXISTS command_baseline(chat_id TEXT PRIMARY KEY,last_id INTEGER);
            """)

    @staticmethod
    def _active(con) -> set[str]:
        return {r[0] for r in con.execute("""SELECT DISTINCT m.chat_id FROM membership m
            JOIN selected s ON m.category_id=s.category_id""")}

    @staticmethod
    def _purge_inactive(con):
        for table in ("messages", "changes"):
            con.execute(f"""DELETE FROM {table} WHERE chat_id NOT IN
                (SELECT m.chat_id FROM membership m JOIN selected s
                 ON s.category_id=m.category_id)""")
        con.execute("""DELETE FROM health WHERE key LIKE 'room:%' AND substr(key,6) NOT IN
            (SELECT m.chat_id FROM membership m JOIN selected s ON s.category_id=m.category_id)""")

    def refresh_categories(self, categories: list[dict]):
        with self.connect(True) as con:
            con.execute("DELETE FROM membership")
            con.execute("DELETE FROM categories")
            for item in categories:
                con.execute("INSERT INTO categories VALUES(?,?)", (item["id"], item["name"]))
                con.executemany("INSERT INTO membership VALUES(?,?)",
                                [(item["id"], cid) for cid in item["chat_ids"]])
            # Retain selected IDs for status when a folder was deleted. A newly
            # created folder with the same name is NOT silently opted in.
            self._purge_inactive(con)
            self._health(con, "category_refresh", {"ok": True, "at": now()})

    def select(self, names: list[str]) -> dict:
        if len(names) != len(set(names)):
            raise ValueError("Duplicate category names")
        with self.connect(True) as con:
            ids = []
            for name in names:
                matches = con.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchall()
                if len(matches) != 1:
                    raise ValueError("Category name missing or ambiguous; refresh/list categories")
                ids.append(matches[0][0])
            con.execute("DELETE FROM selected")
            con.executemany("INSERT INTO selected VALUES(?)", [(x,) for x in ids])
            self._purge_inactive(con)
            return {"selected_categories": names, "active_chat_count": len(self._active(con))}

    def active(self) -> set[str]:
        with self.connect() as con:
            return self._active(con)

    @staticmethod
    def _health(con, key, value):
        con.execute("INSERT INTO health VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, ensure_ascii=False)))

    def record_health(self, key, value):
        with self.connect(True) as con:
            self._health(con, key, value)

    def ingest(self, chat_id: str, rows: list[dict], report: dict) -> int:
        changed = 0
        with self.connect(True) as con:
            if chat_id not in self._active(con):
                raise ValueError("Room is outside selected categories")
            for row in rows:
                fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                old = con.execute("SELECT fingerprint FROM messages WHERE chat_id=? AND id=?",
                                  (chat_id, row["id"])).fetchone()
                if old and old[0] == fingerprint:
                    continue
                con.execute("""INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(chat_id,id) DO UPDATE SET author_id=excluded.author_id,
                    sent_at=excluded.sent_at,text=excluded.text,type=excluded.type,
                    deleted=excluded.deleted,fingerprint=excluded.fingerprint""",
                            (chat_id, row["id"], row["author_id"], row["sent_at"], row["text"],
                             row["type"], int(row["deleted"]), fingerprint))
                con.execute("INSERT INTO changes(chat_id,id,kind,observed_at) VALUES(?,?,?,?)",
                            (chat_id, row["id"], "deleted" if row["deleted"] else "upsert", now()))
                changed += 1
            self._health(con, "room:" + chat_id, {"ok": True, "at": now(), **report})
        return changed

    def categories(self) -> list[dict]:
        with self.connect() as con:
            return [dict(r) for r in con.execute("""SELECT c.id,c.name,
              COUNT(m.chat_id) AS chat_count, s.category_id IS NOT NULL AS selected
              FROM categories c LEFT JOIN membership m ON m.category_id=c.id
              LEFT JOIN selected s ON s.category_id=c.id GROUP BY c.id ORDER BY c.name""")]

    def status(self) -> dict:
        with self.connect() as con:
            return {"categories": self.categories(), "active_chat_count": len(self._active(con)),
                    "message_count": con.execute("SELECT count(*) FROM messages").fetchone()[0],
                    "cursor": con.execute("SELECT coalesce(max(seq),0) FROM changes").fetchone()[0],
                    "missing_selected_categories": [r[0] for r in con.execute("""SELECT s.category_id
                      FROM selected s LEFT JOIN categories c ON c.id=s.category_id WHERE c.id IS NULL""")],
                    "health": {r[0]: json.loads(r[1]) for r in con.execute("SELECT key,value FROM health")}}

    def query(self, *, after=0, limit=50, text=None, chat_id=None) -> dict:
        if not isinstance(after, int) or after < 0 or not 1 <= limit <= 200:
            raise ValueError("Invalid cursor or limit (1..200)")
        with self.connect() as con:
            health = con.execute("SELECT value FROM health WHERE key='category_refresh'").fetchone()
            if not health or not json.loads(health[0]).get("ok"):
                raise ValueError("Category scope unavailable; refresh before reading messages")
            refreshed = datetime.fromisoformat(json.loads(health[0])["at"])
            if (datetime.now(timezone.utc) - refreshed).total_seconds() > 300:
                raise ValueError("Category scope expired; refresh before reading messages")
            active = self._active(con)
            if chat_id is not None and chat_id not in active:
                raise ValueError("Room is outside selected categories")
            if text is not None:
                if not text or len(text) > 200:
                    raise ValueError("Search text must contain 1..200 characters")
                pattern = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                sql = """SELECT chat_id,id,author_id,sent_at,text,type,deleted FROM messages
                    WHERE deleted=0 AND text LIKE ? ESCAPE '\\'"""
                params = [pattern]
                if chat_id is not None:
                    sql += " AND chat_id=?"; params.append(chat_id)
                sql += " ORDER BY sent_at DESC,chat_id,id LIMIT ?"; params.append(limit)
                return {"messages": [dict(r) for r in con.execute(sql, params)]}
            sql = """SELECT c.seq,c.kind,c.observed_at,m.chat_id,m.id,m.author_id,
                m.sent_at,m.text,m.type,m.deleted FROM changes c JOIN messages m
                ON m.chat_id=c.chat_id AND m.id=c.id WHERE c.seq>?"""
            params = [after]
            if chat_id is not None:
                sql += " AND m.chat_id=?"; params.append(chat_id)
            sql += " ORDER BY c.seq LIMIT ?"; params.append(limit)
            rows = [dict(r) for r in con.execute(sql, params)]
            return {"changes": rows, "next_cursor": rows[-1]["seq"] if rows else after,
                    "content_semantics": "current message state; changes are observation references"}

    def intake_commands(self, binding: dict, rows: list[dict], timestamp: int) -> int:
        from .commands import parse_command
        chat_id = str(binding["chat_id"])
        maximum = max((int(r["id"]) for r in rows), default=0)
        count = 0
        with self.connect(True) as con:
            baseline = con.execute("SELECT last_id FROM command_baseline WHERE chat_id=?", (chat_id,)).fetchone()
            if baseline is None:
                # First activation takes a baseline. Existing messages never run.
                con.execute("INSERT INTO command_baseline VALUES(?,?)", (chat_id, maximum))
                return 0
            for row in sorted(rows, key=lambda r: int(r["id"])):
                if int(row["id"]) <= baseline[0]:
                    continue
                body = parse_command(chat_id, row, binding, timestamp)
                if body:
                    con.execute("INSERT OR IGNORE INTO commands(chat_id,id,body,received_at) VALUES(?,?,?,?)",
                                (chat_id, row["id"], body, now()))
                    count += con.execute("SELECT changes()").fetchone()[0]
            con.execute("UPDATE command_baseline SET last_id=max(last_id,?) WHERE chat_id=?", (maximum, chat_id))
            self._health(con, "command_intake", {"ok": True, "at": now(), "queued": count})
        return count

    def pending_commands(self, limit=20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid command limit")
        with self.connect() as con:
            return [dict(r) for r in con.execute("""SELECT chat_id,id,body,received_at,state
                FROM commands WHERE state='pending' ORDER BY received_at,id LIMIT ?""", (limit,))]
