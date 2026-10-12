"""トピック検索と報告内容の提示（中核）。GUI には依存しない。

Advisor.build()  起動時・データ変更時に検索インデックスを作る（トークンは DB にキャッシュ）
Advisor.search() トピックから作業項目ごとの結果（必要性・欄ごとの推奨記載例と他の記載例・根拠）を返す
Advisor.render() 記載例に今回の案件の値を差し込み、日本語・英語の文を作る
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from . import judgment as jd
from .db import Database
from .models import FIELDS
from .placeholders import Extractor, en_key, keys_in, specs_from, substitute
from .representative import Entry, Example, choose
from .search import SearchIndex, group_key
from .translate import Translator

FILTER_OFF, FILTER_WEIGHT, FILTER_ONLY = "off", "weight", "filter"


@dataclass
class FieldResult:
    field: str
    rep: Example | None
    others: list
    constraint: dict = field(default_factory=dict)
    limit: int | None = None


@dataclass
class ItemResult:
    key: str
    name: str
    wbs_no: str
    phase: str
    is_extra: bool
    score: float
    necessity: jd.Necessity
    cases: list                                     # 根拠：この作業項目の記載があった事例名
    fields: dict = field(default_factory=dict)      # 欄 → FieldResult
    name_en: str = ""
    reason_ja: str = ""
    reason_en: str = ""


def _pct(rate) -> int:
    return int(math.floor((rate or 0) * 100 + 0.5))


class Advisor:
    def __init__(self, db: Database, cfg: dict, templates: dict):
        self.db, self.cfg, self.templates = db, cfg, templates
        self.index: SearchIndex | None = None
        self.items: dict = {}
        self.format = db.active_format("format")

    # ------------------------------------------------------------------ 準備
    def build(self, progress=None) -> "Advisor":
        db = self.db
        items = db.master_items()
        rows = db.case_rows()
        cases = db.cases()
        index = SearchIndex(rows, items, db.row_tokens(), progress)
        if index.new_tokens:
            db.save_row_tokens(index.new_tokens)
        self.items = {i.key: i for i in items}
        self.cases = {c.id: c for c in cases}
        self.adopted = jd.adoption(items, rows)
        self.specs = specs_from(self.cfg)
        self.extractor = Extractor(self.specs, [c.attrs.get("vendor") for c in cases if (c.attrs or {}).get("vendor")])
        self.lengths: dict[str, list[int]] = {}
        pairs: dict[str, str] = {}
        for r in rows:
            for f, t in (r.texts or {}).items():
                self.lengths.setdefault(f, []).append(len(t))
                if r.texts_en.get(f):
                    pairs.setdefault(t.strip(), r.texts_en[f].strip())
        for p in db.phrases():
            if p.get("en"):
                pairs.setdefault(p["ja"].strip(), p["en"].strip())
        self.translator = Translator(db.glossary(), self.templates.get("phrases", []), pairs,
                                     self.cfg.get("translate", {}).get("drop_particles", []), self.specs)
        self.format = db.active_format("format")
        self.index = index
        return self

    @property
    def ready(self) -> bool:
        return self.index is not None

    def field_list(self) -> list[str]:
        """候補を出す欄（フォーマットがあれば、フォーマットにある欄だけ。並びはフォーマットの列順）。"""
        conf = self.cfg.get("fields", {})
        wanted = list(conf.get("text", [])) + list(conf.get("value", []))
        if self.format and self.format["profile"].get("columns"):
            from openpyxl.utils import column_index_from_string
            cols = sorted(self.format["profile"]["columns"].items(), key=lambda kv: column_index_from_string(kv[0]))
            return [m for _c, m in cols if m in wanted]
        return wanted

    def item_names(self) -> list[str]:
        """入力補完に使う作業項目名（ひな形の順）。"""
        return [i.name for i in sorted(self.items.values(), key=lambda i: i.seq)]

    # ------------------------------------------------------------------ 検索
    def case_scope(self, project_attrs: dict, mode: str, rules) -> tuple[set[int] | None, dict[int, float], dict]:
        """(対象事例（None ならすべて）, 事例の重み, 優先ルールを適用した事例)"""
        cases = list(self.cases.values())
        pw, used = jd.prefer_weights(cases, rules)
        weights = dict(pw)
        allowed = None
        if mode == FILTER_WEIGHT:
            aw = jd.case_weights(cases, project_attrs, self.cfg)
            weights = {cid: weights[cid] * aw.get(cid, 1.0) for cid in weights}
        elif mode == FILTER_ONLY and project_attrs:
            th = float(self.cfg.get("search", {}).get("filter_min_similarity", 0.5))
            allowed = set()
            for c in cases:
                s = jd.attr_similarity(c.attrs or {}, project_attrs, self.cfg)
                if s is not None and s >= th:
                    allowed.add(c.id)
        return allowed, weights, used

    def search(self, topic: str, project_attrs: dict | None = None, mode: str = FILTER_OFF) -> list[ItemResult]:
        if not self.ready or not (topic or "").strip():
            return []
        project_attrs = project_attrs or {}
        rules = jd.active_rules(self.db.rules(), project_attrs)
        allowed, weights, used = self.case_scope(project_attrs, mode, rules)
        hits = self.index.search(topic, self.cfg, weights, allowed)
        return [self.item_result(h.key, h.score, allowed, weights, rules, used) for h in hits]

    def item_result(self, key: str, score: float, allowed, weights, rules, used) -> ItemResult:
        item = self.items.get(key)
        rows = [r for r in self.index.rows_of(key) if allowed is None or r.case_id in allowed]
        if item:
            name, wbs_no, phase, is_extra = item.name, item.wbs_no, item.phase, False
        else:
            first = rows[0] if rows else None
            name = first.name if first else key.split(":", 1)[-1]
            wbs_no, phase, is_extra = "", first.phase if first else "", True
        case_ids = sorted(allowed) if allowed is not None else sorted(self.cases)
        names = {cid: c.name for cid, c in self.cases.items()}
        nec = jd.necessity(key, name, case_ids, self.adopted, names, rules, self.cfg)
        nec.prefer_rules = sorted({r.name for cid in {r.case_id for r in rows} for r in used.get(cid, [])})
        res = ItemResult(key, name, wbs_no, phase, is_extra, score, nec,
                         sorted({names[r.case_id] for r in rows if r.case_id in names}))
        fmt_fields = (self.format or {}).get("analysis", {}).get("fields", {}) if self.format else {}
        for f in self.field_list():
            entries = [Entry(r.texts.get(f, ""), r.case_id, names.get(r.case_id, ""),
                             (self.cases[r.case_id].attrs or {}).get("year") if r.case_id in self.cases else None,
                             weights.get(r.case_id, 1.0), r.texts_en.get(f, ""))
                       for r in rows if r.texts.get(f)]
            rep, others = choose(entries, self.extractor, f, self.specs, self.cfg)
            phrases = [self._phrase_example(p, f) for p in self.db.phrases(key, f)]
            others = (phrases + others)[: int(self.cfg.get("representative", {}).get("others", 5)) + len(phrases)]
            if rep is None and phrases:
                rep, others = phrases[0], others[1:]
            c = fmt_fields.get(f, {})
            res.fields[f] = FieldResult(f, rep, others, c, self.guideline(f, c))
        res.name_en, _ = self.translator.translate(name)
        res.reason_ja, res.reason_en = self.reasons(nec)
        return res

    def _phrase_example(self, p: dict, f: str) -> Example:
        t = self.extractor.extract(p["ja"], f)
        en = p.get("en") or ""
        from .placeholders import to_en_template
        return Example(t.template, dict(t.values), to_en_template(en, t.values, self.specs) if en else "",
                       source="phrase", phrase_id=p["id"], count=0)

    def reasons(self, nec: jd.Necessity) -> tuple[str, str]:
        t = self.templates
        ja, en = [], []
        if nec.total == 0:
            ja.append(t["reason_no_cases"]["ja"])
            en.append(t["reason_no_cases"]["en"])
        else:
            key = "reason_stat" if nec.adopted else "reason_none"
            v = dict(total=nec.total, adopted=len(nec.adopted), rate=_pct(nec.rate))
            ja.append(t[key]["ja"].format(**v))
            en.append(t[key]["en"].format(**v))
        if nec.rule:
            rule_en = nec.rule.name_en or self.translator.translate(nec.rule.name)[0]
            ja.append(t["reason_rule"]["ja"].format(rule=nec.rule.name))
            en.append(t["reason_rule"]["en"].format(rule=rule_en))
        for name in nec.prefer_rules:
            ja.append(t["reason_prefer"]["ja"].format(rule=name))
            en.append(t["reason_prefer"]["en"].format(rule=self.translator.translate(name)[0]))
        return "".join(ja), " ".join(en)

    # ------------------------------------------------------------------ 表示
    def guideline(self, f: str, constraint: dict) -> int | None:
        """文字数の目安：過去事例の記載が十分あればそのパーセンタイル（10 字単位で切り上げ）、なければ列幅×行数。"""
        lc = self.cfg.get("length", {})
        lens = sorted(self.lengths.get(f, []))
        if len(lens) >= int(lc.get("min_samples", 5)):
            k = min(len(lens) - 1, max(0, math.ceil(len(lens) * float(lc.get("percentile", 90)) / 100) - 1))
            return int(math.ceil(lens[k] / 10.0) * 10)
        if constraint.get("width_chars"):
            return int(constraint["width_chars"] * constraint.get("lines", 1))
        return None

    def values_en(self, values: dict[str, str]) -> dict[str, str]:
        out = {}
        for k, v in values.items():
            if v:
                out[en_key(k, self.specs)] = self.translator.exact.get(v, v)
        return out

    def render(self, ex: Example | None, values: dict[str, str]) -> tuple[str, str, list[str]]:
        """(日本語, 英語, 未翻訳の語)。values はプレースホルダー → 今回の案件の値。"""
        if ex is None:
            return "", "", []
        ja = substitute(ex.template, values)
        if ex.en_template:
            en, un = ex.en_template, []
        else:
            en, un = self.translator.translate(ex.template)
        return ja, substitute(en, self.values_en(values)), un

    def translate(self, ja: str) -> tuple[str, list[str]]:
        return self.translator.translate(ja)

    @staticmethod
    def convert_allowed(value: str, allowed: list[str] | None) -> tuple[str, str]:
        """プルダウンの許可値に変換する。(値, 説明)"""
        if not allowed or not value:
            return value, ""
        if value in allowed:
            return value, ""
        best = process.extractOne(value, allowed, scorer=fuzz.WRatio)
        if best and best[1] >= 60:
            return best[0], f"プルダウンの許可値に変換しました（{value} → {best[0]}）"
        return value, f"「{value}」はプルダウンの許可値にありません（許可値：{'、'.join(allowed)}）"

    @staticmethod
    def placeholder_keys(texts: list[str]) -> list[str]:
        seen = []
        for t in texts:
            for k in keys_in(t):
                if k not in seen:
                    seen.append(k)
        return seen


def field_name(f: str) -> str:
    return FIELDS.get(f, f)


__all__ = ["Advisor", "ItemResult", "FieldResult", "FILTER_OFF", "FILTER_WEIGHT", "FILTER_ONLY", "group_key"]
