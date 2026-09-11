"""SQLite 저장소. 프로필(사람) 하나가 DB 파일 하나다.

원문 텍스트와 실명은 이 파일에만 있다. 외부로 나가는 값은 privacy 모듈을 거친다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
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
    model TEXT,
    social INTEGER                -- 1 누군가와 함께, 0 혼자, NULL 알 수 없음 (LLM 채점일 때만)
);

CREATE TABLE IF NOT EXISTS clusters (
    record_id TEXT NOT NULL,
    axis TEXT NOT NULL,           -- topic | mood | social
    cluster TEXT NOT NULL,
    PRIMARY KEY (record_id, axis)
);

CREATE TABLE IF NOT EXISTS cluster_info (
    axis TEXT NOT NULL,
    cluster TEXT NOT NULL,
    label TEXT NOT NULL,
    size INTEGER NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (axis, cluster)
);

CREATE TABLE IF NOT EXISTS entities (
    record_id TEXT NOT NULL,
    type TEXT NOT NULL,           -- person | place | activity | food | object | animal | event | org
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entities_name ON entities(type, name);

CREATE TABLE IF NOT EXISTS relations (
    record_id TEXT NOT NULL,
    s TEXT NOT NULL,
    p TEXT NOT NULL,
    o TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    keyword TEXT NOT NULL,
    record_id TEXT NOT NULL,
    label INTEGER NOT NULL,       -- 2 직접, 1 간접, 0 무관 (사람이 붙인 정답)
    labeler TEXT NOT NULL DEFAULT '',
    labeled_at TEXT NOT NULL,
    PRIMARY KEY (keyword, record_id, labeler)
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
    """스레드마다 연결을 따로 연다. API 서버는 요청을 스레드 풀에서 처리하므로 연결 하나를 나눠 쓰면
    커서 상태가 섞여 엉뚱한 행을 읽는다 (2026-09-11 평가 스크립트에서 실제로 겪음)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.conn.executescript(SCHEMA_SQL)
        # 예전 DB 이전
        for table, col, ddl in (("people", "relation", "TEXT"), ("emotions", "social", "INTEGER")):
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                with self.conn:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

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
        # 본문·태그·사진이 바뀐 기록은 예전에 계산한 벡터·정서·개체를 지운다 (다시 build 하면 새로 계산)
        changed = []
        for d in rows:
            old = self.conn.execute("SELECT text, tags, media FROM records WHERE id=?", (d["id"],)).fetchone()
            if old and (old["text"], old["tags"], old["media"]) != (d["text"], d["tags"], d["media"]):
                changed.append((d["id"],))
        if changed:
            with self.conn:
                for table in ("vectors", "emotions", "entities", "relations"):
                    self.conn.executemany(f"DELETE FROM {table} WHERE record_id=?", changed)
                self.conn.executemany("DELETE FROM cache WHERE key=?", [(f"entdone:{rid}",) for (rid,) in changed])
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
    def put_emotions(self, rows: Iterable[tuple]) -> None:
        """행: (기록 ID, 정서가, 각성도, 방식, 모델[, 함께 여부])."""
        rows = [tuple(r) + (None,) * (6 - len(r)) for r in rows]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO emotions (record_id, valence, arousal, backend, model, social) VALUES (?,?,?,?,?,?)",
                rows,
            )

    def social(self) -> dict[str, int | None]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT record_id, social FROM emotions")}

    # ---------- clusters ----------
    def put_clusters(self, axis: str, assign: dict[str, str], info: list[tuple[str, str, int, list[str]]]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM clusters WHERE axis=?", (axis,))
            self.conn.execute("DELETE FROM cluster_info WHERE axis=?", (axis,))
            self.conn.executemany("INSERT INTO clusters (record_id, axis, cluster) VALUES (?,?,?)",
                                  [(rid, axis, c) for rid, c in assign.items()])
            self.conn.executemany("INSERT INTO cluster_info (axis, cluster, label, size, keywords) VALUES (?,?,?,?,?)",
                                  [(axis, c, label, size, json.dumps(kw, ensure_ascii=False)) for c, label, size, kw in info])

    def clusters(self, axis: str) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        assign = {r[0]: r[1] for r in self.conn.execute("SELECT record_id, cluster FROM clusters WHERE axis=?", (axis,))}
        info = {r[0]: {"label": r[1], "size": r[2], "keywords": json.loads(r[3])}
                for r in self.conn.execute("SELECT cluster, label, size, keywords FROM cluster_info WHERE axis=?", (axis,))}
        return assign, info

    # ---------- entities ----------
    def put_entities(self, rid: str, ents: list[tuple[str, str]], rels: list[tuple[str, str, str]]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM entities WHERE record_id=?", (rid,))
            self.conn.execute("DELETE FROM relations WHERE record_id=?", (rid,))
            self.conn.executemany("INSERT INTO entities (record_id, type, name) VALUES (?,?,?)", [(rid, t, n) for t, n in ents])
            self.conn.executemany("INSERT INTO relations (record_id, s, p, o) VALUES (?,?,?,?)", [(rid, *x) for x in rels])

    def entity_rows(self) -> list[tuple[str, str, str]]:
        return [tuple(r) for r in self.conn.execute("SELECT record_id, type, name FROM entities")]

    def relation_rows(self) -> list[tuple[str, str, str, str]]:
        return [tuple(r) for r in self.conn.execute("SELECT record_id, s, p, o FROM relations")]

    def entity_done(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT DISTINCT record_id FROM entities")} | {
            r[0] for r in self.conn.execute("SELECT key FROM cache WHERE key LIKE 'entdone:%'")}

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

    # ---------- labels (사람이 붙인 정답) ----------
    def put_label(self, keyword: str, rid: str, label: int, labeler: str = "") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO labels (keyword, record_id, label, labeler, labeled_at) VALUES (?,?,?,?,?)",
                (keyword, rid, int(label), labeler, datetime.now(timezone.utc).isoformat()),
            )

    def labels(self, keyword: str | None = None) -> dict[str, dict[str, int]]:
        """키워드 → {기록 ID: 라벨}. 여러 사람이 붙였으면 가장 최근 값."""
        sql = "SELECT keyword, record_id, label FROM labels" + (" WHERE keyword=?" if keyword else "") + " ORDER BY labeled_at"
        out: dict[str, dict[str, int]] = {}
        for k, rid, lab in self.conn.execute(sql, (keyword,) if keyword else ()):
            out.setdefault(k, {})[rid] = lab
        return out

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

    # ---------- 전부 지우기 ----------
    def wipe(self) -> dict[str, int]:
        """모든 표의 행을 지우고 파일을 다시 쓴다. secure_delete 와 VACUUM 으로 지운 내용이 빈 페이지에 남지 않게 하고,
        WAL 파일도 비운다. 반환: 표마다 지운 행 수."""
        c = self.conn
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        c.execute("PRAGMA secure_delete=ON")
        counts = {}
        with c:
            for t in tables:
                counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                c.execute(f"DELETE FROM {t}")
        c.execute("VACUUM")
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return counts

    # ---------- imports ----------
    def log_import(self, source: str, path: str, n: int, note: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO imports (source, path, records, imported_at, note) VALUES (?,?,?,?,?)",
                (source, path, n, datetime.now(timezone.utc).isoformat(), note),
            )
