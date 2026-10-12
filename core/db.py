"""SQLite（%LOCALAPPDATA%\\ApprovalReport\\advisor.db）。

テーブル
  formats       登録した様式（kind: master=ひな形 / format=フォーマット）。profile・analysis は JSON
  master_items  ひな形の作業項目（item_key はひな形を登録し直しても同じ項目なら同じ値）
  cases         過去事例（sha256 で二重取込を検出）。attrs は案件属性の JSON
  case_rows     過去事例の各行。texts（欄 → 日本語）・texts_en（欄 → 英語）と、ひな形項目への対応付け
  row_tokens    検索インデックスのキャッシュ（各行の形態素解析の結果）
  synonyms      照合確認で選んだ対応付け（正規化した名称 → item_key）
  rules         利用者独自の条件
  projects      今回の案件（属性・プレースホルダーの値）
  edits         案件ごとに編集した文（作業項目 × 欄）
  checklist     案件ごとの転記チェック（作業項目 × 欄）
  phrases       自分の定型文（作業項目 × 欄）
  glossary      用語辞書（日本語 → 英語）
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import paths
from .models import Case, CaseRow, MasterItem, Project, Rule

SCHEMA = """
CREATE TABLE IF NOT EXISTS formats(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT, file TEXT, source TEXT, sha256 TEXT,
  profile TEXT, analysis TEXT, created TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS master_items(
  id INTEGER PRIMARY KEY, format_id INTEGER NOT NULL, item_key TEXT NOT NULL, seq INTEGER, row INTEGER,
  wbs_no TEXT, phase TEXT, name TEXT, norm TEXT, level INTEGER, parent_key TEXT);
CREATE INDEX IF NOT EXISTS ix_items_format ON master_items(format_id);
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, source TEXT, sha256 TEXT, imported TEXT, attrs TEXT);
CREATE TABLE IF NOT EXISTS case_rows(
  id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE, seq INTEGER, row INTEGER,
  wbs_no TEXT, phase TEXT, name TEXT, norm TEXT, texts TEXT, texts_en TEXT,
  item_key TEXT, method TEXT, score REAL, status TEXT, candidates TEXT);
CREATE INDEX IF NOT EXISTS ix_rows_case ON case_rows(case_id);
CREATE TABLE IF NOT EXISTS row_tokens(
  row_id INTEGER PRIMARY KEY REFERENCES case_rows(id) ON DELETE CASCADE, tokens TEXT);
CREATE TABLE IF NOT EXISTS synonyms(norm TEXT PRIMARY KEY, item_key TEXT NOT NULL, created TEXT);
CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_en TEXT, enabled INTEGER DEFAULT 1, priority INTEGER DEFAULT 100,
  kind TEXT DEFAULT 'decision', conditions TEXT, decision TEXT, targets TEXT, keyword TEXT, prefer TEXT,
  weight REAL DEFAULT 2.0, created TEXT);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, attrs TEXT, placeholder_values TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS edits(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, item_key TEXT NOT NULL, field TEXT NOT NULL,
  ja TEXT, en TEXT, at TEXT, PRIMARY KEY(project_id, item_key, field));
CREATE TABLE IF NOT EXISTS checklist(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, item_key TEXT NOT NULL, field TEXT NOT NULL,
  done INTEGER DEFAULT 0, at TEXT, PRIMARY KEY(project_id, item_key, field));
CREATE TABLE IF NOT EXISTS phrases(
  id INTEGER PRIMARY KEY, item_key TEXT NOT NULL, field TEXT NOT NULL, ja TEXT NOT NULL, en TEXT, created TEXT);
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

    # ---- 様式（ひな形・フォーマット） ----
    def add_format(self, kind, name, file, source, sha256, profile, analysis) -> int:
        with self.tx() as c:
            c.execute("UPDATE formats SET active = 0 WHERE kind = ?", (kind,))
            cur = c.execute("INSERT INTO formats(kind, name, file, source, sha256, profile, analysis, created, active) "
                            "VALUES(?,?,?,?,?,?,?,?,1)",
                            (kind, name, str(file), str(source), sha256, _j(profile), _j(analysis), now()))
            return cur.lastrowid

    def active_format(self, kind) -> dict | None:
        r = self.conn.execute("SELECT * FROM formats WHERE kind = ? AND active = 1 ORDER BY id DESC LIMIT 1",
                              (kind,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["profile"] = json.loads(d["profile"] or "{}")
        d["analysis"] = json.loads(d["analysis"] or "{}")
        return d

    def update_format(self, format_id, profile, analysis=None):
        with self.tx() as c:
            c.execute("UPDATE formats SET profile = ? WHERE id = ?", (_j(profile), format_id))
            if analysis is not None:
                c.execute("UPDATE formats SET analysis = ? WHERE id = ?", (_j(analysis), format_id))

    def replace_master_items(self, format_id, items):
        with self.tx() as c:
            c.execute("DELETE FROM master_items WHERE format_id = ?", (format_id,))
            c.executemany("INSERT INTO master_items(format_id, item_key, seq, row, wbs_no, phase, name, norm, level, "
                          "parent_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          [(format_id, i.key, i.seq, i.row, i.wbs_no, i.phase, i.name, i.norm, i.level, i.parent_key)
                           for i in items])

    def master_items(self) -> list[MasterItem]:
        f = self.active_format("master")
        if not f:
            return []
        rows = self.conn.execute("SELECT * FROM master_items WHERE format_id = ? ORDER BY seq", (f["id"],)).fetchall()
        return [MasterItem(key=r["item_key"], seq=r["seq"], row=r["row"], wbs_no=r["wbs_no"], phase=r["phase"],
                           name=r["name"], norm=r["norm"], level=r["level"], parent_key=r["parent_key"]) for r in rows]

    # ---- 過去事例 ----
    def case_by_hash(self, sha256):
        r = self.conn.execute("SELECT * FROM cases WHERE sha256 = ?", (sha256,)).fetchone()
        return dict(r) if r else None

    def add_case(self, name, source, sha256, attrs, rows: list[CaseRow]) -> int:
        with self.tx() as c:
            cur = c.execute("INSERT INTO cases(name, source, sha256, imported, attrs) VALUES(?,?,?,?,?)",
                            (name, str(source), sha256, now(), _j(attrs or {})))
            cid = cur.lastrowid
            c.executemany("INSERT INTO case_rows(case_id, seq, row, wbs_no, phase, name, norm, texts, texts_en) "
                          "VALUES(?,?,?,?,?,?,?,?,?)",
                          [(cid, r.seq, r.row, r.wbs_no, r.phase, r.name, r.norm, _j(r.texts), _j(r.texts_en))
                           for r in rows])
            return cid

    def cases(self) -> list[Case]:
        return [Case(id=r["id"], name=r["name"], source=r["source"], sha256=r["sha256"], imported=r["imported"],
                     attrs=json.loads(r["attrs"] or "{}"))
                for r in self.conn.execute("SELECT * FROM cases ORDER BY id").fetchall()]

    def update_case(self, case_id, name=None, attrs=None):
        with self.tx() as c:
            if name:
                c.execute("UPDATE cases SET name = ? WHERE id = ?", (name, case_id))
            if attrs is not None:
                c.execute("UPDATE cases SET attrs = ? WHERE id = ?", (_j(attrs), case_id))

    def delete_case(self, case_id):
        with self.tx() as c:
            c.execute("DELETE FROM row_tokens WHERE row_id IN (SELECT id FROM case_rows WHERE case_id = ?)", (case_id,))
            c.execute("DELETE FROM case_rows WHERE case_id = ?", (case_id,))
            c.execute("DELETE FROM cases WHERE id = ?", (case_id,))

    def case_rows(self, case_id=None) -> list[CaseRow]:
        q = "SELECT * FROM case_rows" + (" WHERE case_id = ?" if case_id is not None else "") + " ORDER BY case_id, seq"
        rows = self.conn.execute(q, (case_id,) if case_id is not None else ()).fetchall()
        return [CaseRow(id=r["id"], case_id=r["case_id"], seq=r["seq"], row=r["row"], wbs_no=r["wbs_no"],
                        phase=r["phase"], name=r["name"], norm=r["norm"], texts=json.loads(r["texts"] or "{}"),
                        texts_en=json.loads(r["texts_en"] or "{}"), item_key=r["item_key"], method=r["method"],
                        score=r["score"], status=r["status"], candidates=json.loads(r["candidates"] or "[]"))
                for r in rows]

    def save_matches(self, results):
        with self.tx() as c:
            c.executemany("UPDATE case_rows SET item_key=?, method=?, score=?, status=?, candidates=? WHERE id=?",
                          [(m.item_key, m.method, m.score, m.status, _j(m.candidates), m.row_id) for m in results])

    def set_row_match(self, row_id, item_key, status, method="manual"):
        with self.tx() as c:
            c.execute("UPDATE case_rows SET item_key=?, status=?, method=? WHERE id=?", (item_key, status, method, row_id))

    # ---- 検索インデックスのキャッシュ ----
    def row_tokens(self) -> dict[int, list[str]]:
        return {r["row_id"]: json.loads(r["tokens"]) for r in self.conn.execute("SELECT * FROM row_tokens").fetchall()}

    def save_row_tokens(self, tokens: dict[int, list[str]]):
        with self.tx() as c:
            c.executemany("INSERT OR REPLACE INTO row_tokens(row_id, tokens) VALUES(?,?)",
                          [(rid, _j(t)) for rid, t in tokens.items()])

    def clear_row_tokens(self):
        with self.tx() as c:
            c.execute("DELETE FROM row_tokens")

    # ---- 同義語辞書 ----
    def synonyms(self) -> dict[str, str]:
        return {r["norm"]: r["item_key"] for r in self.conn.execute("SELECT * FROM synonyms").fetchall()}

    def add_synonym(self, norm, item_key):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO synonyms(norm, item_key, created) VALUES(?,?,?)", (norm, item_key, now()))

    # ---- ルール ----
    def rules(self) -> list[Rule]:
        return [Rule(id=r["id"], name=r["name"], name_en=r["name_en"] or "", enabled=bool(r["enabled"]),
                     priority=r["priority"], kind=r["kind"] or "decision", conditions=json.loads(r["conditions"] or "[]"),
                     decision=r["decision"] or "", targets=json.loads(r["targets"] or "[]"), keyword=r["keyword"] or "",
                     prefer=json.loads(r["prefer"] or "{}"), weight=float(r["weight"] or 2.0))
                for r in self.conn.execute("SELECT * FROM rules ORDER BY priority, id").fetchall()]

    def save_rule(self, rule: Rule) -> int:
        vals = (rule.name, rule.name_en, int(rule.enabled), rule.priority, rule.kind, _j(rule.conditions), rule.decision,
                _j(rule.targets), rule.keyword, _j(rule.prefer), rule.weight)
        with self.tx() as c:
            if rule.id:
                c.execute("UPDATE rules SET name=?, name_en=?, enabled=?, priority=?, kind=?, conditions=?, decision=?, "
                          "targets=?, keyword=?, prefer=?, weight=? WHERE id=?", vals + (rule.id,))
                return rule.id
            cur = c.execute("INSERT INTO rules(name, name_en, enabled, priority, kind, conditions, decision, targets, "
                            "keyword, prefer, weight, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", vals + (now(),))
            return cur.lastrowid

    def delete_rule(self, rule_id):
        with self.tx() as c:
            c.execute("DELETE FROM rules WHERE id = ?", (rule_id,))

    # ---- 今回の案件 ----
    def projects(self) -> list[Project]:
        return [Project(id=r["id"], name=r["name"], attrs=json.loads(r["attrs"] or "{}"),
                        values=json.loads(r["placeholder_values"] or "{}"))
                for r in self.conn.execute("SELECT * FROM projects ORDER BY updated DESC, id DESC").fetchall()]

    def project(self, project_id) -> Project | None:
        return next((p for p in self.projects() if p.id == project_id), None)

    def save_project(self, p: Project) -> int:
        with self.tx() as c:
            if p.id:
                c.execute("UPDATE projects SET name=?, attrs=?, placeholder_values=?, updated=? WHERE id=?",
                          (p.name, _j(p.attrs), _j(p.values), now(), p.id))
                return p.id
            cur = c.execute("INSERT INTO projects(name, attrs, placeholder_values, created, updated) VALUES(?,?,?,?,?)",
                            (p.name, _j(p.attrs), _j(p.values), now(), now()))
            return cur.lastrowid

    def delete_project(self, project_id):
        with self.tx() as c:
            for t in ("edits", "checklist"):
                c.execute(f"DELETE FROM {t} WHERE project_id = ?", (project_id,))
            c.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def edits(self, project_id) -> dict[tuple[str, str], tuple[str, str]]:
        return {(r["item_key"], r["field"]): (r["ja"] or "", r["en"] or "") for r in
                self.conn.execute("SELECT * FROM edits WHERE project_id = ?", (project_id,)).fetchall()}

    def save_edit(self, project_id, item_key, field, ja, en):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO edits(project_id, item_key, field, ja, en, at) VALUES(?,?,?,?,?,?)",
                      (project_id, item_key, field, ja, en, now()))

    def delete_edit(self, project_id, item_key, field):
        with self.tx() as c:
            c.execute("DELETE FROM edits WHERE project_id=? AND item_key=? AND field=?", (project_id, item_key, field))

    def checklist(self, project_id) -> dict[tuple[str, str], bool]:
        return {(r["item_key"], r["field"]): bool(r["done"]) for r in
                self.conn.execute("SELECT * FROM checklist WHERE project_id = ?", (project_id,)).fetchall()}

    def set_check(self, project_id, item_key, field, done: bool):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO checklist(project_id, item_key, field, done, at) VALUES(?,?,?,?,?)",
                      (project_id, item_key, field, int(done), now()))

    # ---- 自分の定型文 ----
    def phrases(self, item_key=None, field=None) -> list[dict]:
        q, args = "SELECT * FROM phrases", []
        if item_key is not None:
            q += " WHERE item_key = ? AND field = ?"
            args = [item_key, field]
        return [dict(r) for r in self.conn.execute(q + " ORDER BY id DESC", args).fetchall()]

    def add_phrase(self, item_key, field, ja, en) -> int:
        with self.tx() as c:
            c.execute("DELETE FROM phrases WHERE item_key=? AND field=? AND ja=?", (item_key, field, ja))
            return c.execute("INSERT INTO phrases(item_key, field, ja, en, created) VALUES(?,?,?,?,?)",
                             (item_key, field, ja, en, now())).lastrowid

    def delete_phrase(self, phrase_id):
        with self.tx() as c:
            c.execute("DELETE FROM phrases WHERE id = ?", (phrase_id,))

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
