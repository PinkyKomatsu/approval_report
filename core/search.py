"""トピック検索：形態素解析＋BM25 で全欄を全文検索し、作業項目名の類似度（rapidfuzz）と合算して順位を付ける。

各行の文書 = 作業項目名＋工程＋全欄の文（日本語）。トークンは DB（row_tokens）にキャッシュし、新しい行だけ解析する。
順位 = search.bm25 × BM25（最大値で 0〜1 に正規化。案件属性・優先ルールの重みを掛ける）
     + search.name × 作業項目名（または工程名）とトピックの類似度（0〜1）
作業項目ごとに、最も点の高い行の点を作業項目の点とする。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz, process

from . import tokenizer
from .models import CaseRow, MasterItem
from .normalize import norm


def group_key(r: CaseRow) -> str | None:
    """行が属する作業項目（ひな形の項目。対応付かない行は「extra:正規化名」）。"""
    if r.item_key and r.status in ("auto", "confirmed"):
        return r.item_key
    if r.norm:
        return "extra:" + r.norm
    return None


def doc_text(r: CaseRow) -> str:
    return " ".join([r.name or "", r.phase or ""] + [str(v) for v in (r.texts or {}).values() if v])


@dataclass
class Hit:
    key: str
    score: float
    bm25: float
    name_sim: float
    rows: list = field(default_factory=list)     # 点の付いた行（点の高い順）


class SearchIndex:
    def __init__(self, rows: list[CaseRow], items: list[MasterItem], cache: dict[int, list[str]] | None = None,
                 progress=None):
        cache = cache or {}
        self.rows = [r for r in rows if group_key(r)]
        self.new_tokens: dict[int, list[str]] = {}
        docs = []
        n = len(self.rows)
        for i, r in enumerate(self.rows):
            toks = cache.get(r.id) if r.id is not None else None
            if toks is None:
                toks = tokenizer.tokens(doc_text(r))
                if r.id is not None:
                    self.new_tokens[r.id] = toks
            docs.append(toks or ["\u0000"])
            if progress and i % 200 == 0:
                progress(int(i * 100 / max(1, n)), f"検索インデックスを作成しています（{i}/{n}行）")
        self.bm25 = BM25Okapi(docs) if docs else None
        self.items = {i.key: i for i in items}
        self.names: dict[str, str] = {i.key: i.norm for i in items}
        self.phases: dict[str, str] = {i.key: norm(i.phase) for i in items}
        self.extra_names: dict[str, str] = {}
        for r in self.rows:
            k = group_key(r)
            if k.startswith("extra:"):
                self.extra_names.setdefault(k, r.norm)
        self.by_key: dict[str, list[int]] = {}
        for idx, r in enumerate(self.rows):
            self.by_key.setdefault(group_key(r), []).append(idx)

    def rows_of(self, key: str) -> list[CaseRow]:
        return [self.rows[i] for i in self.by_key.get(key, [])]

    def name_similarity(self, topic: str, min_score: float) -> dict[str, float]:
        """作業項目名（ひな形外の項目名を含む）・工程名とトピックの類似度（0〜1）。"""
        q = norm(topic)
        out: dict[str, float] = {}
        if not q:
            return out
        choices = {**self.names, **self.extra_names}
        for _choice, score, key in process.extract(q, choices, scorer=fuzz.WRatio, limit=None, score_cutoff=min_score):
            out[key] = score / 100.0
        phase_hits = {p for p in set(self.phases.values()) if p and fuzz.ratio(q, p) >= 90}
        for key, p in self.phases.items():
            if p in phase_hits:
                out[key] = max(out.get(key, 0.0), 0.8)      # 工程名での検索：その工程の作業項目
        return out

    def search(self, topic: str, cfg: dict, weights: dict[int, float] | None = None,
               allowed_cases: set[int] | None = None) -> list[Hit]:
        s = cfg.get("search", {})
        w_bm, w_name = float(s.get("bm25", 0.7)), float(s.get("name", 0.5))
        weights = weights or {}
        top_w = max(list(weights.values()) + [1.0])
        q = tokenizer.tokens(topic)
        hits: dict[str, Hit] = {}
        if self.bm25 is not None and q:
            scores = self.bm25.get_scores(q)
            mx = float(scores.max()) if len(scores) else 0.0
            if mx > 0:
                for idx in scores.nonzero()[0]:
                    r = self.rows[idx]
                    if allowed_cases is not None and r.case_id not in allowed_cases:
                        continue
                    v = float(scores[idx]) / mx * (weights.get(r.case_id, 1.0) / top_w)
                    key = group_key(r)
                    h = hits.setdefault(key, Hit(key, 0.0, 0.0, 0.0))
                    h.rows.append((v, r))
                    h.bm25 = max(h.bm25, v)
        for key, sim in self.name_similarity(topic, float(s.get("name_min", 55))).items():
            h = hits.setdefault(key, Hit(key, 0.0, 0.0, 0.0))
            h.name_sim = sim
        out = []
        for h in hits.values():
            h.score = w_bm * h.bm25 + w_name * h.name_sim
            h.rows.sort(key=lambda x: -x[0])
            if h.score >= float(s.get("min_score", 0.12)):
                out.append(h)
        out.sort(key=lambda h: (-h.score, self.items[h.key].seq if h.key in self.items else 1 << 30))
        return out[: int(s.get("top_n", 30))]
