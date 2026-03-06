"""SQLite metadata database for cross-session persistence."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)


class MetadataDB:
    """Persistent metadata storage using SQLite.

    Stores expedition, dive, sensor, and session history for
    cross-session lookup and management.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        cur = self._conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS expeditions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expedition_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (expedition_id) REFERENCES expeditions(id),
                UNIQUE(expedition_id, name)
            );
            CREATE TABLE IF NOT EXISTS sensors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                camera_profile_json TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dive_id INTEGER,
                session_file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (dive_id) REFERENCES dives(id)
            );
        """)
        self._conn.commit()

    def add_expedition(self, name: str) -> int:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO expeditions (name, created_at) VALUES (?, ?)",
            (name, datetime.now().isoformat()),
        )
        self._conn.commit()
        cur.execute("SELECT id FROM expeditions WHERE name = ?", (name,))
        return cur.fetchone()["id"]

    def add_dive(self, expedition_name: str, dive_name: str) -> int:
        exp_id = self.add_expedition(expedition_name)
        cur = self._conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO dives (expedition_id, name, created_at) VALUES (?, ?, ?)",
            (exp_id, dive_name, datetime.now().isoformat()),
        )
        self._conn.commit()
        cur.execute(
            "SELECT id FROM dives WHERE expedition_id = ? AND name = ?",
            (exp_id, dive_name),
        )
        return cur.fetchone()["id"]

    def add_session(self, dive_id: int, session_file_path: str) -> int:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO sessions (dive_id, session_file_path, created_at) VALUES (?, ?, ?)",
            (dive_id, session_file_path, datetime.now().isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_expeditions(self) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute("SELECT id, name, created_at FROM expeditions ORDER BY created_at DESC")
        return [dict(row) for row in cur.fetchall()]

    def list_dives(self, expedition_name: str) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute("""
            SELECT d.id, d.name, d.created_at
            FROM dives d JOIN expeditions e ON d.expedition_id = e.id
            WHERE e.name = ?
            ORDER BY d.created_at DESC
        """, (expedition_name,))
        return [dict(row) for row in cur.fetchall()]

    def list_sessions(self, expedition_name: str | None = None, dive_name: str | None = None) -> list[dict]:
        cur = self._conn.cursor()
        query = """
            SELECT s.id, e.name as expedition, d.name as dive,
                   s.session_file_path, s.created_at
            FROM sessions s
            LEFT JOIN dives d ON s.dive_id = d.id
            LEFT JOIN expeditions e ON d.expedition_id = e.id
        """
        params = []
        conditions = []
        if expedition_name:
            conditions.append("e.name = ?")
            params.append(expedition_name)
        if dive_name:
            conditions.append("d.name = ?")
            params.append(dive_name)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY s.created_at DESC"
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
