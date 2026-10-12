"""データの型と、欄（列の意味）の定義。"""
from __future__ import annotations

from dataclasses import dataclass, field

REQUIRED, REVIEW, UNNEEDED = "必要", "要検討", "不要"
DECISIONS = (REQUIRED, REVIEW, UNNEEDED)

# 欄（列の意味）。様式プロファイルの columns の値に使う
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
KEY_FIELDS = ("wbs_no", "phase", "name")
EN_SUFFIX = "_en"     # 過去事例の英語の列（例：report_en ＝ 報告内容（英語））


def field_label(meaning: str) -> str:
    if meaning.endswith(EN_SUFFIX) and meaning[:-len(EN_SUFFIX)] in FIELDS:
        return FIELDS[meaning[:-len(EN_SUFFIX)]] + "（英語）"
    return FIELDS.get(meaning, meaning)


@dataclass
class MasterItem:
    key: str
    seq: int
    row: int
    wbs_no: str
    phase: str
    name: str
    norm: str
    level: int = 1
    parent_key: str | None = None


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
    item_key: str | None = None
    method: str | None = None      # wbs / synonym / exact / fuzzy / manual / none
    score: float | None = None
    status: str | None = None      # auto / pending（確認待ち）/ confirmed / extra（ひな形外）
    candidates: list = field(default_factory=list)


@dataclass
class Case:
    id: int
    name: str
    source: str = ""
    sha256: str = ""
    imported: str = ""
    attrs: dict = field(default_factory=dict)

    @property
    def year(self):
        return (self.attrs or {}).get("year")


@dataclass
class Rule:
    """利用者独自の条件。
    kind="decision"：条件（今回の案件の属性）を満たせば、対象項目の必要性を decision にする
    kind="prefer"  ：条件を満たせば、prefer（事例の属性の条件）に合う事例の重みを weight 倍にする"""
    id: int | None
    name: str
    name_en: str = ""
    enabled: bool = True
    priority: int = 100
    kind: str = "decision"
    conditions: list = field(default_factory=list)   # [{"attr", "op", "value"}]（すべて満たすとき）
    decision: str = REQUIRED
    targets: list = field(default_factory=list)      # 対象項目の item_key
    keyword: str = ""                                # 項目名にこの語を含む項目も対象
    prefer: dict = field(default_factory=dict)       # {"attr", "op", "value"}：優先する事例の条件
    weight: float = 2.0


@dataclass
class MatchResult:
    row_id: int | None
    item_key: str | None
    method: str
    score: float
    status: str
    candidates: list = field(default_factory=list)


@dataclass
class Project:
    """今回の案件（転記の進捗・プレースホルダーの値・編集した文を案件ごとに保存する）。"""
    id: int
    name: str
    attrs: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)       # プレースホルダー名 → 値
