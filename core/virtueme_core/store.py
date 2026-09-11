"""SQLite 저장소. 프로필(사람) 하나가 DB 파일 하나다.

원문 텍스트와 실명은 이 파일에만 있다. 외부로 나가는 값은 privacy 모듈을 거친다.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .records import Record

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    modality TEXT NOT NULL,
    text TEXT,
    media TEXT NOT NULL DEFAULT '[]',
    author TEXT,
    is_self INTEGER NOT NULL DEFAULT 1,
    thread TEXT,
    participants TEXT NOT NULL DEFAULT '[]',
    lat REAL,
    lon REAL,
    place TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS records_ts ON records(ts);
CREATE INDEX IF NOT EXISTS records_day ON records(day);
CREATE INDEX IF NOT EXISTS records_thread ON records(thread, ts);

CREATE TABLE IF NOT EXISTS vectors (
    record_id TEXT NOT NULL,
    space TEXT NOT NULL,          -- text | vision
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (record_id, space)
);

CREATE TABLE IF NOT EXISTS emotions (
    record_id TEXT PRIMARY KEY,
    valence REAL NOT NULL,        -- -1 (부정) .. 1 (긍정)
    arousal REAL NOT NULL,        -- 0 (차분) .. 1 (격앙·들뜸)
    backend TEXT NOT NULL,        -- llm | anchor | siglip | truth
    model TEXT
);

CREATE TABLE IF NOT EXISTS people (
    name TEXT PRIMARY KEY,        -- 원래 표시 이름 (로컬 전용)
    alias TEXT NOT NULL,          -- 나, 지인A, 지인B … (본인 이름이 여럿이면 모두 "나")
    is_self INTEGER NOT NULL DEFAULT 0,
    relation TEXT                 -- 사용자가 붙인 관계 (예: 회사 동기). 신원이 드러나지 않는 말만 쓴다
);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    records INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    note TEXT
);
"""

_JSON_FIELDS = ("media", "participants", "tags", "meta")


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA_SQL)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(people)")}
        if "relation" not in cols:  # 예전 DB 이전
            with self.conn:
                self.conn.execute("ALTER TABLE people ADD COLUMN relation TEXT")

    def close(self) -> None:
        self.conn.close()

    # ---------- records ----------
    def upsert_records(self, records: Iterable[Record]) -> int:
        rows = []
        for r in records:
            d = r.to_dict()
            for k in _JSON_FIELDS:
                d[k] = json.dumps(d[k], ensure_ascii=False)
            d["is_self"] = int(bool(d["is_self"]))
            d["day"] = r.day
            rows.append(d)
        if not rows:
            return 0
        # 본문·태그·사진이 바뀐 기록은 예전에 계산한 벡터와 정서를 지운다 (다시 build 하면 새로 계산)
        changed = []
        for d in rows:
            old = self.conn.execute("SELECT text, tags, media FROM records WHERE id=?", (d["id"],)).fetchone()
            if old and (old["text"], old["tags"], old["media"]) != (d["text"], d["tags"], d["media"]):
                changed.append((d["id"],))
        if changed:
            with self.conn:
                self.conn.executemany("DELETE FROM vectors WHERE record_id=?", changed)
                self.conn.executemany("DELETE FROM emotions WHERE record_id=?", changed)
        cols = list(rows[0].keys())
        sql = (
            f"INSERT INTO records ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)}) "
            f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols if c != 'id')}"
        )
        with self.conn:
            self.conn.executemany(sql, rows)
        return len(rows)

    def _row_to_record(self, row: sqlite3.Row) -> Record:
        d = dict(row)
        d.pop("day", None)
        for k in _JSON_FIELDS:
            d[k] = json.loads(d[k])
        d["is_self"] = bool(d["is_self"])
        return Record(**d)

    def records(self, where: str = "", params: tuple[Any, ...] = ()) -> list[Record]:
        sql = "SELECT * FROM records" + (f" WHERE {where}" if where else "") + " ORDER BY ts, id"
        return [self._row_to_record(r) for r in self.conn.execute(sql, params)]

    def record(self, rid: str) -> Record | None:
        row = self.conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        return self._row_to_record(row) if row else None

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    def summary(self) -> dict[str, Any]:
        c = self.conn
        by_source = {r[0]: r[1] for r in c.execute("SELECT source, COUNT(*) FROM records GROUP BY source")}
        by_kind = {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind")}
        first, last = c.execute("SELECT MIN(ts), MAX(ts) FROM records").fetchone()
        days = c.execute("SELECT COUNT(DISTINCT day) FROM records").fetchone()[0]
        self_n = c.execute("SELECT COUNT(*) FROM records WHERE is_self=1").fetchone()[0]
        vec = {r[0]: r[1] for r in c.execute("SELECT space, COUNT(*) FROM vectors GROUP BY space")}
        emo = {r[0]: r[1] for r in c.execute("SELECT backend, COUNT(*) FROM emotions GROUP BY backend")}
        return {
            "records": self.count(),
            "self_records": self_n,
            "by_source": by_source,
            "by_kind": by_kind,
            "first": first,
            "last": last,
            "active_days": days,
            "vectors": vec,
            "emotions": emo,
        }

    # ---------- vectors ----------
    def put_vectors(self, space: str, model: str, ids: list[str], mat: np.ndarray) -> None:
        mat = np.asarray(mat, dtype=np.float32)
        rows = [(rid, space, model, mat.shape[1], mat[i].tobytes()) for i, rid in enumerate(ids)]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO vectors (record_id, space, model, dim, vec) VALUES (?,?,?,?,?)", rows
            )

    def vectors(self, space: str) -> tuple[list[str], np.ndarray]:
        rows = self.conn.execute(
            "SELECT record_id, dim, vec FROM vectors WHERE space=? ORDER BY record_id", (space,)
        ).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        ids = [r[0] for r in rows]
        mat = np.stack([np.frombuffer(r[2], dtype=np.float32, count=r[1]) for r in rows])
        return ids, mat

    def vector_ids(self, space: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT record_id FROM vectors WHERE space=?", (space,))}

    def vector_model(self, space: str) -> str | None:
        row = self.conn.execute("SELECT model FROM vectors WHERE space=? LIMIT 1", (space,)).fetchone()
        return row[0] if row else None

    # ---------- emotions ----------
    def put_emotions(self, rows: Iterable[tuple[str, float, float, str, str | None]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO emotions (record_id, valence, arousal, backend, model) VALUES (?,?,?,?,?)",
                list(rows),
            )

    def emotions(self) -> dict[str, tuple[float, float, str]]:
        return {
            r[0]: (r[1], r[2], r[3])
            for r in self.conn.execute("SELECT record_id, valence, arousal, backend FROM emotions")
        }

    # ---------- people ----------
    def people(self) -> dict[str, str]:
        """실명 → 가명."""
        return {r["name"]: r["alias"] for r in self.conn.execute("SELECT alias, name FROM people")}

    def add_person(self, name: str, alias: str, is_self: bool) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO people (name, alias, is_self) VALUES (?,?,?)", (name, alias, int(is_self))
            )

    def relations(self) -> dict[str, str | None]:
        """가명 → 관계. 본인("나")은 빼고, 관계어 연락처("엄마")는 관계가 곧 이름이다."""
        out: dict[str, str | None] = {}
        for r in self.conn.execute("SELECT alias, relation FROM people WHERE is_self=0"):
            if r[0] not in out or r[1]:
                out[r[0]] = r[1]
        return out

    def set_relation(self, alias: str, relation: str | None) -> int:
        with self.conn:
            cur = self.conn.execute("UPDATE people SET relation=? WHERE alias=?", (relation, alias))
        return cur.rowcount

    def self_names(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT name FROM people WHERE is_self=1")]

    # ---------- cache ----------
    def cache_get(self, key: str) -> Any | None:
        row = self.conn.execute("SELECT value FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )

    def cache_items(self, prefix: str) -> Iterator[tuple[str, Any]]:
        for r in self.conn.execute("SELECT key, value FROM cache WHERE key LIKE ?", (prefix + "%",)):
            yield r[0], json.loads(r[1])

    # ---------- imports ----------
    def log_import(self, source: str, path: str, n: int, note: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO imports (source, path, records, imported_at, note) VALUES (?,?,?,?,?)",
                (source, path, n, datetime.now(timezone.utc).isoformat(), note),
            )
