"""Persistent memory — SQLite log of everything JARVIS heard and answered,
plus a quick-lookup fact store. Uses stdlib sqlite3 (DataBase Operations layer)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List, Tuple

from jarvis.config import MEMORY_DB


class Memory:
    def __init__(self):
        self.conn = sqlite3.connect(str(MEMORY_DB), check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS interactions ("
            " id INTEGER PRIMARY KEY, ts TEXT, heard TEXT, reply TEXT, skill TEXT)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.commit()

    def record(self, heard: str, reply: str, skill: str = "") -> None:
        self.conn.execute(
            "INSERT INTO interactions (ts, heard, reply, skill) VALUES (?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), heard, reply, skill))
        self.conn.commit()

    def recent(self, n: int = 10) -> List[Tuple[str, str, str]]:
        rows = self.conn.execute(
            "SELECT ts, heard, reply FROM interactions ORDER BY id DESC LIMIT ?",
            (n,)).fetchall()
        return list(reversed(rows))

    def remember(self, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO facts (key, value) VALUES (?,?)",
                          (key, json.dumps(value)))
        self.conn.commit()

    def recall(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def stats(self) -> Tuple[int, str]:
        n = self.conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        first = self.conn.execute("SELECT MIN(ts) FROM interactions").fetchone()[0]
        return n, first or "—"
