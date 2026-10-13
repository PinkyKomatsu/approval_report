"""過去事例の取込（GUI に依存しない）。Excel ファイルは読むだけで、変更しない。"""
from __future__ import annotations

import csv
import logging
from datetime import date, datetime

from excel_io import profile as profile_mod, reader

from . import paths
from .db import Database
from .models import EN_SUFFIX, FIELDS, KEY_FIELDS, CaseRow
from .normalize import norm

log = logging.getLogger("approvalreport")


class DuplicateCase(Exception):
    pass


def text_of(v) -> str:
    """セルの値を文字列にする（日付は yyyy/mm/dd）。"""
    if isinstance(v, datetime):
        return f"{v:%Y/%m/%d}" if (v.hour, v.minute, v.second) == (0, 0, 0) else f"{v:%Y/%m/%d %H:%M}"
    if isinstance(v, date):
        return f"{v:%Y/%m/%d}"
    return reader.cell_text(v)


def seed_glossary(db: Database) -> int:
    """用語辞書の初期値（resources/glossary.csv）を入れる。英語が登録済みの語は変えない。"""
    current = db.glossary()
    terms = {}
    with open(paths.resource("glossary.csv"), encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip() and row[0].strip() != "日本語" and not current.get(row[0].strip()):
                terms[row[0].strip()] = row[1].strip()
    if terms:
        db.upsert_terms(terms, source="初期値", keep_existing_en=False)
    return len(terms)


def import_case(db: Database, path, cfg: dict, name: str | None = None, allow_duplicate: bool = False) -> int:
    """過去事例を取り込む。列（作業項目・報告内容など）は見出しから自動で判別する。"""
    p = reader.check(path)
    h = reader.sha256(p)
    dup = db.case_by_hash(h)
    if dup and not allow_duplicate:
        raise DuplicateCase(f"「{p.name}」は取込済みです（事例「{dup['name']}」と同じファイル）。")
    prof = profile_mod.detect(p, cfg)
    meanings = set(prof.get("columns", {}).values())
    if "name" not in meanings:
        raise ValueError(f"「{p.name}」で「作業項目」の列が見つかりません。見出しに「作業項目」「作業名」「タスク名」などがあるか確認してください。")
    if not any(m in FIELDS and m not in KEY_FIELDS for m in meanings):
        raise ValueError(f"「{p.name}」で報告内容などの欄が見つかりません。見出しに「報告内容」「作業内容」「課題」などがあるか確認してください。")
    rows = []
    phase_now = ""
    for seq, (r, v) in enumerate(profile_mod.read_rows(p, prof, cfg.get("profile", {}).get("blank_rows_to_stop", 5))):
        nm = text_of(v.get("name"))
        if not nm:
            continue
        phase = text_of(v.get("phase")) or phase_now
        phase_now = phase
        texts, texts_en = {}, {}
        for meaning, val in v.items():
            t = text_of(val)
            if not t or meaning in KEY_FIELDS:
                continue
            if meaning.endswith(EN_SUFFIX):
                texts_en[meaning[:-len(EN_SUFFIX)]] = t
            else:
                texts[meaning] = t
        rows.append(CaseRow(None, None, seq, r, text_of(v.get("wbs_no")), phase, nm, norm(nm), texts, texts_en))
    if not any(r.texts for r in rows):
        raise ValueError(f"「{p.name}」には報告内容などの記載がありません（ひな形やフォーマットではなく、記載済みの実用WBSを取り込んでください）。")
    cid = db.add_case(name or p.stem, p, h, prof["columns"], rows)
    log.info("過去事例を取り込みました（%d行）", len(rows))
    return cid
