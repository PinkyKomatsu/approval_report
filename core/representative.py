"""推奨記載例の選定：過去事例の記載から、最も代表的な文を 1 つ選ぶ。

1. 各記載の日付・固有名・数値をプレースホルダーにしてから、正規化した文が同じものを 1 つにまとめる
   （「A病院の…」と「B病院の…」は同じ記載とみなす）。
2. 点数 = freq × 出現頻度（記載のある事例の重みの合計を、最大値で 0〜1 に正規化）
        + central × 他の記載との類似度（rapidfuzz。他の記載の頻度で重み付けした平均。0〜1）
3. 最も点数の高いものを推奨、続く others 件を「他の記載例」にする。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz
from rapidfuzz.process import cdist

from .normalize import norm
from .placeholders import Extractor, Spec, to_en_template


@dataclass
class Entry:
    text: str
    case_id: int
    case_name: str
    year: object = None
    weight: float = 1.0
    text_en: str = ""


@dataclass
class Example:
    template: str                                   # 日本語（プレースホルダー入り）
    values: dict = field(default_factory=dict)      # 元の値（最初の記載の値）
    en_template: str = ""                           # 対訳がある場合の英語（プレースホルダー入り）
    cases: list = field(default_factory=list)       # 事例名（年度付きの表示は label で作る）
    years: list = field(default_factory=list)
    count: int = 0                                  # 記載のあった事例の数
    freq: float = 0.0
    central: float = 0.0
    score: float = 0.0
    source: str = "case"                            # case：過去事例 / phrase：自分の定型文
    phrase_id: int | None = None

    def label(self) -> str:
        if self.source == "phrase":
            return "自分の定型文"
        parts = []
        for name, year in zip(self.cases, self.years):
            parts.append(f"{name}（{year}年度）" if year else name)
        return "、".join(parts)


def choose(entries: list[Entry], extractor: Extractor, field_name: str, specs: list[Spec],
           cfg: dict) -> tuple[Example | None, list[Example]]:
    rcfg = cfg.get("representative", {})
    a, b, n_others = float(rcfg.get("freq", 0.6)), float(rcfg.get("central", 0.4)), int(rcfg.get("others", 5))
    groups: dict[str, Example] = {}
    weights: dict[str, dict[int, float]] = {}
    for e in entries:
        text = (e.text or "").strip()
        if not text:
            continue
        t = extractor.extract(text, field_name)
        k = norm(t.template)
        if not k:
            continue
        ex = groups.get(k)
        if ex is None:
            ex = groups[k] = Example(t.template, dict(t.values))
            weights[k] = {}
        if e.text_en and not ex.en_template:
            ex.en_template = to_en_template(e.text_en, t.values, specs)
        if e.case_id not in weights[k]:
            weights[k][e.case_id] = e.weight
            ex.cases.append(e.case_name)
            ex.years.append(e.year)
    if not groups:
        return None, []
    keys = list(groups)
    freq = {k: sum(weights[k].values()) for k in keys}
    top = max(freq.values()) or 1.0
    if len(keys) > 1:
        sims = cdist(keys, keys, scorer=fuzz.ratio)
        total_f = sum(freq.values())
        for i, k in enumerate(keys):
            other = total_f - freq[k]
            groups[k].central = (sum(sims[i][j] * freq[keys[j]] for j in range(len(keys)) if j != i) / other / 100.0
                                 if other else 0.0)
    else:
        groups[keys[0]].central = 1.0
    for k in keys:
        ex = groups[k]
        ex.count = len(weights[k])
        ex.freq = freq[k] / top
        ex.score = a * ex.freq + b * ex.central
    ranked = sorted(groups.values(), key=lambda x: (-x.score, -x.count, x.template))
    return ranked[0], ranked[1:1 + n_others]
