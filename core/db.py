"""SQLite（%LOCALAPPDATA%\\ApprovalReport\\examples.db）。

テーブル
  cases       過去事例（sha256 で二重取込を検出。columns は読み取った列 → 欄）
  case_rows   過去事例の各行（作業項目名と、欄ごとの日本語・英語の文）
  row_tokens  検索インデックスのキャッシュ（各行の形態素解析の結果）
  glossary    用語辞書（日本語 → 英語）
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import paths
from .models import Case, CaseRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, source TEXT, sha256 TEXT, imported TEXT, columns TEXT);
CREATE TABLE IF NOT EXISTS case_rows(
  id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE, seq INTEGER, row INTEGER,
  wbs_no TEXT, phase TEXT, name TEXT, norm TEXT, texts TEXT, texts_en TEXT);
CREATE INDEX IF NOT EXISTS ix_rows_case ON case_rows(case_id);
CREATE TABLE IF NOT EXISTS row_tokens(
  row_id INTEGER PRIMARY KEY REFERENCES case_rows(id) ON DELETE CASCADE, tokens TEXT);
CREATE TABLE IF NOT EXISTS glossary(ja TEXT PRIMARY KEY, en TEXT DEFAULT '', source TEXT);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class Database:
    def __init__(self, path=None):
        self.path = str(path or paths.db_path())
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    # ---- 過去事例 ----
    def case_by_hash(self, sha256):
        r = self.conn.execute("SELECT * FROM cases WHERE sha256 = ?", (sha256,)).fetchone()
        return dict(r) if r else None

    def add_case(self, name, source, sha256, columns, rows: list[CaseRow]) -> int:
        with self.tx() as c:
            cid = c.execute("INSERT INTO cases(name, source, sha256, imported, columns) VALUES(?,?,?,?,?)",
                            (name, str(source), sha256, now(), _j(columns))).lastrowid
            c.executemany("INSERT INTO case_rows(case_id, seq, row, wbs_no, phase, name, norm, texts, texts_en) "
                          "VALUES(?,?,?,?,?,?,?,?,?)",
                          [(cid, r.seq, r.row, r.wbs_no, r.phase, r.name, r.norm, _j(r.texts), _j(r.texts_en))
                           for r in rows])
            return cid

    def cases(self) -> list[Case]:
        return [Case(id=r["id"], name=r["name"], source=r["source"], sha256=r["sha256"], imported=r["imported"],
                     columns=json.loads(r["columns"] or "{}"))
                for r in self.conn.execute("SELECT * FROM cases ORDER BY id").fetchall()]

    def rename_case(self, case_id, name):
        with self.tx() as c:
            c.execute("UPDATE cases SET name = ? WHERE id = ?", (name, case_id))

    def delete_case(self, case_id):
        with self.tx() as c:
            c.execute("DELETE FROM row_tokens WHERE row_id IN (SELECT id FROM case_rows WHERE case_id = ?)", (case_id,))
            c.execute("DELETE FROM case_rows WHERE case_id = ?", (case_id,))
            c.execute("DELETE FROM cases WHERE id = ?", (case_id,))

    def case_rows(self, case_id=None) -> list[CaseRow]:
        q = "SELECT * FROM case_rows" + (" WHERE case_id = ?" if case_id is not None else "") + " ORDER BY case_id, seq"
        return [CaseRow(id=r["id"], case_id=r["case_id"], seq=r["seq"], row=r["row"], wbs_no=r["wbs_no"],
                        phase=r["phase"], name=r["name"], norm=r["norm"], texts=json.loads(r["texts"] or "{}"),
                        texts_en=json.loads(r["texts_en"] or "{}"))
                for r in self.conn.execute(q, (case_id,) if case_id is not None else ()).fetchall()]

    def row_counts(self) -> dict[int, int]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT case_id, COUNT(*) FROM case_rows GROUP BY case_id")}

    # ---- 検索インデックスのキャッシュ ----
    def row_tokens(self) -> dict[int, list[str]]:
        return {r["row_id"]: json.loads(r["tokens"]) for r in self.conn.execute("SELECT * FROM row_tokens").fetchall()}

    def save_row_tokens(self, tokens: dict[int, list[str]]):
        with self.tx() as c:
            c.executemany("INSERT OR REPLACE INTO row_tokens(row_id, tokens) VALUES(?,?)",
                          [(rid, _j(t)) for rid, t in tokens.items()])

    # ---- 用語辞書 ----
    def glossary(self) -> dict[str, str]:
        return {r["ja"]: r["en"] or "" for r in self.conn.execute("SELECT ja, en FROM glossary").fetchall()}

    def upsert_terms(self, terms: dict[str, str], source="user", keep_existing_en=True):
        with self.tx() as c:
            for ja, en in terms.items():
                ja = (ja or "").strip()
                if not ja:
                    continue
                if keep_existing_en and not en:
                    c.execute("INSERT OR IGNORE INTO glossary(ja, en, source) VALUES(?, '', ?)", (ja, source))
                else:
                    c.execute("INSERT INTO glossary(ja, en, source) VALUES(?,?,?) "
                              "ON CONFLICT(ja) DO UPDATE SET en = excluded.en, source = excluded.source",
                              (ja, (en or "").strip(), source))

    def delete_term(self, ja):
        with self.tx() as c:
            c.execute("DELETE FROM glossary WHERE ja = ?", (ja,))
