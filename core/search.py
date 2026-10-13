"""トピック検索：形態素解析＋BM25 で全欄を全文検索し、作業項目名の類似度（rapidfuzz）と合算して順位を付ける。

作業項目は、過去事例の作業項目名（正規化した名前が同じもの）でまとめる。
各行の文書 = 作業項目名＋工程＋全欄の文。トークンは DB（row_tokens）にキャッシュし、新しい行だけ解析する。
順位 = search.bm25 × BM25（最大値で 0〜1 に正規化）＋ search.name × 作業項目名（または工程名）とトピックの類似度
作業項目ごとに、最も点の高い行の点を作業項目の点とする。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz, process

from . import tokenizer
from .models import CaseRow
from .normalize import norm


def doc_text(r: CaseRow) -> str:
    return " ".join([r.name or "", r.phase or ""] + [str(v) for v in (r.texts or {}).values() if v])


@dataclass
class Group:
    key: str            # 正規化した作業項目名
    name: str           # 表示名（最も多い表記）
    phase: str
    wbs_no: str
    rows: list


@dataclass
class Hit:
    key: str
    score: float
    bm25: float = 0.0
    name_sim: float = 0.0


class SearchIndex:
    def __init__(self, rows: list[CaseRow], cache: dict[int, list[str]] | None = None, progress=None):
        cache = cache or {}
        self.rows = [r for r in rows if r.norm]
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
        by: dict[str, list[CaseRow]] = {}
        for r in self.rows:
            by.setdefault(r.norm, []).append(r)
        self.groups: dict[str, Group] = {}
        for k, rs in by.items():
            name = Counter(r.name for r in rs).most_common(1)[0][0]
            phase = Counter(r.phase for r in rs if r.phase).most_common(1)
            wbs = Counter(r.wbs_no for r in rs if r.wbs_no).most_common(1)
            self.groups[k] = Group(k, name, phase[0][0] if phase else "", wbs[0][0] if wbs else "", rs)
        self.order = {k: min(r.seq for r in g.rows) for k, g in self.groups.items()}

    def names(self) -> list[str]:
        """入力補完に使う作業項目名（過去事例に出てきた順）。"""
        return [g.name for g in sorted(self.groups.values(), key=lambda g: self.order[g.key])]

    def name_similarity(self, topic: str, min_score: float) -> dict[str, float]:
        q = norm(topic)
        out: dict[str, float] = {}
        if not q:
            return out
        choices = {k: k for k in self.groups}
        for _c, score, key in process.extract(q, choices, scorer=fuzz.WRatio, limit=None, score_cutoff=min_score):
            out[key] = score / 100.0
        for key, g in self.groups.items():          # 工程名での検索：その工程の作業項目
            if g.phase and fuzz.ratio(q, norm(g.phase)) >= 90:
                out[key] = max(out.get(key, 0.0), 0.8)
        return out

    def search(self, topic: str, cfg: dict) -> list[Hit]:
        s = cfg.get("search", {})
        w_bm, w_name = float(s.get("bm25", 0.7)), float(s.get("name", 0.5))
        hits: dict[str, Hit] = {}
        q = tokenizer.tokens(topic)
        if self.bm25 is not None and q:
            scores = self.bm25.get_scores(q)
            mx = float(scores.max()) if len(scores) else 0.0
            if mx > 0:
                for idx in scores.nonzero()[0]:
                    r = self.rows[idx]
                    h = hits.setdefault(r.norm, Hit(r.norm, 0.0))
                    h.bm25 = max(h.bm25, float(scores[idx]) / mx)
        for key, sim in self.name_similarity(topic, float(s.get("name_min", 55))).items():
            hits.setdefault(key, Hit(key, 0.0)).name_sim = sim
        out = []
        for h in hits.values():
            h.score = w_bm * h.bm25 + w_name * h.name_sim
            if h.score >= float(s.get("min_score", 0.12)):
                out.append(h)
        out.sort(key=lambda h: (-h.score, self.order.get(h.key, 0)))
        return out[: int(s.get("top_n", 30))]
