"""データの型と、欄（列の意味）の定義。"""
from __future__ import annotations

from dataclasses import dataclass, field

# 欄（列の意味）。見出しから自動で判別する
FIELDS = {
    "no": "No（連番）",
    "wbs_no": "WBS番号",
    "phase": "工程",
    "name": "作業項目",
    "work": "作業内容",
    "report": "報告内容",
    "progress": "進捗状況",
    "issue": "課題・懸念",
    "policy": "対応方針",
    "owner": "担当",
    "plan_date": "予定日",
    "actual_date": "実績日",
    "note": "備考",
}
KEY_FIELDS = ("no", "wbs_no", "phase", "name")
EN_SUFFIX = "_en"     # 過去事例の英語の列（例：report_en ＝ 報告内容（英語））


def field_label(meaning: str) -> str:
    if meaning.endswith(EN_SUFFIX) and meaning[:-len(EN_SUFFIX)] in FIELDS:
        return FIELDS[meaning[:-len(EN_SUFFIX)]] + "（英語）"
    return FIELDS.get(meaning, meaning)


@dataclass
class CaseRow:
    id: int | None
    case_id: int | None
    seq: int
    row: int
    wbs_no: str
    phase: str
    name: str
    norm: str
    texts: dict = field(default_factory=dict)      # 欄 → 日本語の文
    texts_en: dict = field(default_factory=dict)   # 欄 → 英語の文（過去事例に英語の列がある場合）


@dataclass
class Case:
    id: int
    name: str
    source: str = ""
    sha256: str = ""
    imported: str = ""
    columns: dict = field(default_factory=dict)    # 列 → 欄（読み取った列。コピーの列順に使う）
