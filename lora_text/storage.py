"""SQLite log of every packet sent and received (history + experiment data)."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS packets (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,          -- unix time
    direction TEXT NOT NULL,          -- 'rx' | 'tx'
    payload   BLOB NOT NULL,
    kind      TEXT,                   -- 'plain' | 'reserved' | 'undecoded'
    text      TEXT,
    rssi      INTEGER,
    snr       INTEGER,
    airtime_ms REAL,
    tx_ok     INTEGER,
    node      TEXT,                   -- local callsign / label
    channel   TEXT,                   -- public | group | stream | other
    sender    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts ON packets(ts);
"""


class PacketLog:
    def __init__(self, path: str = "log.db", node: Optional[str] = None):
        self.node = node
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(packets)")}
        for col in ("channel", "sender"):          # upgrade older log files
            if col not in cols:
                self._db.execute(f"ALTER TABLE packets ADD COLUMN {col} TEXT")
        self._db.commit()

    def add(self, direction: str, payload: bytes, kind: str | None = None,
            text: str | None = None, rssi: int | None = None, snr: int | None = None,
            airtime_ms: float | None = None, tx_ok: bool | None = None,
            ts: float | None = None, channel: str | None = None,
            sender: str | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO packets (ts, direction, payload, kind, text, rssi, snr,"
                " airtime_ms, tx_ok, node, channel, sender) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts or time.time(), direction, payload, kind, text, rssi, snr,
                 airtime_ms, None if tx_ok is None else int(tx_ok), self.node,
                 channel, sender))
            self._db.commit()
            return cur.lastrowid

    def set_tx_ok(self, row_id: int, ok: bool) -> None:
        with self._lock:
            self._db.execute("UPDATE packets SET tx_ok=? WHERE id=?", (int(ok), row_id))
            self._db.commit()

    def update(self, row_id: int, kind: str, text: str | None,
               channel: str | None, sender: str | None) -> None:
        with self._lock:
            self._db.execute("UPDATE packets SET kind=?, text=?, channel=?, sender=? WHERE id=?",
                             (kind, text, channel, sender, row_id))
            self._db.commit()

    def rows(self, n: int = 300, kind: str | None = None) -> list[dict]:
        q = ("SELECT id, ts, direction, payload, kind, text, rssi, snr, airtime_ms, tx_ok,"
             " channel, sender FROM packets")
        args: tuple = ()
        if kind:
            q += " WHERE kind=?"
            args = (kind,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            cur = self._db.execute(q, args + (n,))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()][::-1]

    def recent(self, n: int = 50) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "SELECT ts, direction, kind, text, rssi, snr FROM packets"
                " ORDER BY id DESC LIMIT ?", (n,)).fetchall()[::-1]

    def prune(self, max_rows: int = 0, max_days: float = 0) -> int:
        """Delete messages older than max_days and beyond the newest max_rows
        (0 = no limit). Returns the number of rows deleted."""
        deleted = 0
        with self._lock:
            if max_days and max_days > 0:
                cur = self._db.execute("DELETE FROM packets WHERE ts < ?",
                                       (time.time() - max_days * 86400,))
                deleted += cur.rowcount
            if max_rows and max_rows > 0:
                cur = self._db.execute(
                    "DELETE FROM packets WHERE id <= (SELECT id FROM packets "
                    "ORDER BY id DESC LIMIT 1 OFFSET ?)", (max_rows,))
                deleted += cur.rowcount
            self._db.commit()
            if deleted > 1000:
                self._db.execute("VACUUM")          # give disk space back
        return deleted

    def stats(self) -> dict:
        with self._lock:
            rows, oldest = self._db.execute("SELECT COUNT(*), MIN(ts) FROM packets").fetchone()
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return {"rows": rows, "oldest": oldest, "bytes": size}

    def close(self) -> None:
        with self._lock:
            self._db.close()
