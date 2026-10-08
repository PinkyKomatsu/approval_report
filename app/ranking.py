"""候補の並べ替え（Excel がなくても動く）。

合計点 = トピックとの類似度（文字 2-gram のコサイン類似度）
       + 同じ工程の報告書なら加点
       + 出現回数（定型文ほど上位）
       + 新しい報告書ほど加点
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date

from .cases import Candidate

W_SIM = 1.0       # 類似度（0〜1）に掛ける重み
W_PHASE = 0.3     # 同じ工程
W_COUNT = 0.3     # 出現回数（最多の候補で満点）
W_RECENT = 0.2    # 新しさ（最新の報告書で満点）

_NOISE = re.compile(r"[\s・、。，．,.\-－ー―⇒→：:（）()【】「」『』■□●○◆◇※/／|｜]")


def bigrams(text: str) -> Counter:
    s = _NOISE.sub("", unicodedata.normalize("NFKC", text or ""))
    if len(s) < 2:
        return Counter([s]) if s else Counter()
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


@dataclass
class Ranked:
    cand: Candidate
    score: float
    sim: float


def _ordinal(d: str) -> int | None:
    try:
        return date.fromisoformat(d).toordinal()
    except (TypeError, ValueError):
        return None


def rank(cands: list[Candidate], topic: str = "", phase: str = "") -> list[Ranked]:
    if not cands:
        return []
    tv = bigrams(topic)
    max_count = max(c.count for c in cands)
    newest = {id(c): max((o for o in map(_ordinal, c.dates) if o), default=None) for c in cands}
    known = [o for o in newest.values() if o]
    lo, hi = (min(known), max(known)) if known else (0, 0)
    out = []
    for c in cands:
        sim = cosine(tv, bigrams(c.text)) if tv else 0.0
        score = W_SIM * sim
        if phase and phase in c.phases:
            score += W_PHASE
        if max_count > 1:
            score += W_COUNT * math.log1p(c.count) / math.log1p(max_count)
        o = newest[id(c)]
        if o and hi > lo:
            score += W_RECENT * (o - lo) / (hi - lo)
        out.append(Ranked(c, score, sim))
    out.sort(key=lambda r: (-r.score, -r.cand.count, r.cand.text))
    return out
