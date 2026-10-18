"""Persistent memory — SQLite log of everything JARVIS heard and answered,
plus a quick-lookup fact store. Uses stdlib sqlite3 (DataBase Operations layer).

Thread-safety note: one connection is shared between the webserver's request
threads and background workers (watcher, chat). `check_same_thread=False` only
silences Python's guard — it does NOT serialize statements, and interleaved
execute/fetch on a shared connection corrupts responses (observed as fetchone()
returning None and watcher-thread crashes). Every operation therefore runs
under a class-level lock, execute+fetch as one atomic step."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import List, Optional, Tuple

from jarvis.config import MEMORY_DB


class Memory:
    _lock = threading.RLock()      # shared across instances: same DB file behind them

    def __init__(self):
        self.conn = sqlite3.connect(str(MEMORY_DB), check_same_thread=False, timeout=5)
        with Memory._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS interactions ("
                " id INTEGER PRIMARY KEY, ts TEXT, heard TEXT, reply TEXT, skill TEXT)")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, value TEXT)")
            self.conn.commit()

    def _query(self, sql: str, params: tuple = (), one: bool = False):
        with Memory._lock:
            cur = self.conn.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()

    def _write(self, sql: str, params: tuple = ()) -> None:
        with Memory._lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def record(self, heard: str, reply: str, skill: str = "") -> None:
        self._write(
            "INSERT INTO interactions (ts, heard, reply, skill) VALUES (?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), heard, reply, skill))

    def recent(self, n: int = 10, skill_prefix: str | None = None) -> List[Tuple[str, str, str]]:
        """Most recent exchanges (chronological). Optionally restrict to one skill
        family (e.g. skill_prefix='jarvis.chat') so command turns don't bleed into
        conversational context."""
        if skill_prefix:
            rows = self._query(
                "SELECT ts, heard, reply FROM interactions WHERE skill LIKE ? "
                "ORDER BY id DESC LIMIT ?", (skill_prefix + "%", n))
        else:
            rows = self._query(
                "SELECT ts, heard, reply FROM interactions ORDER BY id DESC LIMIT ?",
                (n,))
        return list(reversed(rows))

    def remember(self, key: str, value) -> None:
        self._write("INSERT OR REPLACE INTO facts (key, value) VALUES (?,?)",
                    (key, json.dumps(value)))

    def recall(self, key: str, default=None):
        row = self._query("SELECT value FROM facts WHERE key=?", (key,), one=True)
        return json.loads(row[0]) if row else default

    def stats(self) -> Tuple[int, str]:
        n = (self._query("SELECT COUNT(*) FROM interactions", one=True) or (0,))[0]
        first = (self._query("SELECT MIN(ts) FROM interactions", one=True) or (None,))[0]
        return n, first or "—"
