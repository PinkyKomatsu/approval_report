"""トピックから記載例を出す（中核）。GUI には依存しない。

Advisor.build()  起動時・データ変更時に検索インデックスを作る（トークンは DB にキャッシュ）
Advisor.search() トピックから作業項目ごとの結果（欄ごとの推奨記載例と他の記載例・根拠）を返す
Advisor.render() 記載例に今回の値を差し込み、日本語・英語の文を作る
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from openpyxl.utils import column_index_from_string

from .db import Database
from .models import FIELDS, KEY_FIELDS
from .placeholders import Extractor, en_key, keys_in, specs_from, substitute
from .representative import Entry, Example, choose
from .search import SearchIndex
from .translate import Translator


@dataclass
class FieldResult:
    field: str
    rep: Example | None
    others: list
    limit: int | None = None          # 文字数の目安（過去事例の記載の長さから）


@dataclass
class ItemResult:
    key: str
    name: str
    wbs_no: str
    phase: str
    score: float
    cases: list                                     # 根拠：この作業項目の記載があった事例名
    total: int                                      # 取り込んだ事例の数
    fields: dict = field(default_factory=dict)      # 欄 → FieldResult
    name_en: str = ""


class Advisor:
    def __init__(self, db: Database, cfg: dict, templates: dict):
        self.db, self.cfg, self.templates = db, cfg, templates
        self.index: SearchIndex | None = None

    def build(self, progress=None) -> "Advisor":
        db = self.db
        rows = db.case_rows()
        index = SearchIndex(rows, db.row_tokens(), progress)
        if index.new_tokens:
            db.save_row_tokens(index.new_tokens)
        self.cases = {c.id: c for c in db.cases()}
        self.specs = specs_from(self.cfg)
        self.extractor = Extractor(self.specs)
        self.lengths: dict[str, list[int]] = {}
        pairs: dict[str, str] = {}
        for r in rows:
            for f, t in (r.texts or {}).items():
                self.lengths.setdefault(f, []).append(len(t))
                if r.texts_en.get(f):
                    pairs.setdefault(t.strip(), r.texts_en[f].strip())
        self.translator = Translator(db.glossary(), self.templates.get("phrases", []), pairs,
                                     self.cfg.get("translate", {}).get("drop_particles", []), self.specs)
        self.columns = self._columns()
        self.index = index
        return self

    @property
    def ready(self) -> bool:
        return self.index is not None

    def _columns(self) -> dict[str, str]:
        """コピーの列順：最後に取り込んだ過去事例の列（列 → 欄。英語の列は除く）。"""
        for c in sorted(self.cases.values(), key=lambda c: c.id, reverse=True):
            cols = {col: m for col, m in (c.columns or {}).items() if m in FIELDS}
            if cols:
                return dict(sorted(cols.items(), key=lambda kv: column_index_from_string(kv[0])))
        return {}

    def field_list(self) -> list[str]:
        """記載例を出す欄（過去事例の列順。WBS番号・工程・作業項目は除く）。"""
        wanted = [f for f in FIELDS if f not in KEY_FIELDS]
        order = [m for m in self.columns.values() if m in wanted]
        seen = set(order)
        for c in self.cases.values():
            for m in (c.columns or {}).values():
                if m in wanted and m not in seen:
                    order.append(m)
                    seen.add(m)
        return order

    def item_names(self) -> list[str]:
        return self.index.names() if self.ready else []

    # ------------------------------------------------------------------ 検索
    def search(self, topic: str) -> list[ItemResult]:
        if not self.ready or not (topic or "").strip():
            return []
        out = [self.item_result(h.key, h.score) for h in self.index.search(topic, self.cfg)]
        return [r for r in out if any(fr.rep for fr in r.fields.values())]     # 記載例のない項目（見出し行など）は出さない

    def item_result(self, key: str, score: float) -> ItemResult:
        g = self.index.groups[key]
        names = {cid: c.name for cid, c in self.cases.items()}
        res = ItemResult(key, g.name, g.wbs_no, g.phase, score,
                         sorted({names[r.case_id] for r in g.rows if r.case_id in names}), len(self.cases))
        for f in self.field_list():
            entries = [Entry(r.texts.get(f, ""), r.case_id, names.get(r.case_id, ""), r.texts_en.get(f, ""))
                       for r in g.rows if r.texts.get(f)]
            rep, others = choose(entries, self.extractor, f, self.specs, self.cfg)
            res.fields[f] = FieldResult(f, rep, others, self.guideline(f))
        res.name_en, _ = self.translator.translate(g.name)
        return res

    # ------------------------------------------------------------------ 表示
    def guideline(self, f: str) -> int | None:
        """文字数の目安：過去事例の記載の長さの 90 パーセンタイル（10 字単位で切り上げ）。"""
        import math
        lc = self.cfg.get("length", {})
        lens = sorted(self.lengths.get(f, []))
        if len(lens) < int(lc.get("min_samples", 5)):
            return None
        k = min(len(lens) - 1, max(0, math.ceil(len(lens) * float(lc.get("percentile", 90)) / 100) - 1))
        return int(math.ceil(lens[k] / 10.0) * 10)

    def values_en(self, values: dict[str, str]) -> dict[str, str]:
        return {en_key(k, self.specs): self.translator.exact.get(v, v) for k, v in values.items() if v}

    def render(self, ex: Example | None, values: dict[str, str]) -> tuple[str, str, list[str]]:
        """(日本語, 英語, 未翻訳の語)。values はプレースホルダー → 今回の値。"""
        if ex is None:
            return "", "", []
        ja = substitute(ex.template, values)
        en, un = (ex.en_template, []) if ex.en_template else self.translator.translate(ex.template)
        return ja, substitute(en, self.values_en(values)), un

    def translate(self, ja: str) -> tuple[str, list[str]]:
        return self.translator.translate(ja)

    @staticmethod
    def placeholder_keys(results: list[ItemResult]) -> list[str]:
        cnt: Counter = Counter()
        order = []
        for r in results:
            for fr in r.fields.values():
                for ex in [fr.rep] + list(fr.others):
                    for k in keys_in(ex.template if ex else ""):
                        if k not in cnt:
                            order.append(k)
                        cnt[k] += 1
        return order
