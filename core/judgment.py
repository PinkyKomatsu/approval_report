"""必要性の判定と、ルール・案件属性による事例の重み。

- 採用：ひな形の作業項目が過去事例に転記されていれば、その事例では「採用」。子の項目が採用されていれば親も採用とみなす。
- 採用率 = 採用した事例数 ÷ 対象事例数（属性で絞り込んだ場合は絞り込んだ事例数）。
- 判定：採用率が required 以上＝必要、unneeded 未満＝不要、その間＝要検討。
- ルール（kind=decision）は統計による判定より優先する。kind=prefer は条件に合う事例の重みを上げる。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .models import REQUIRED, REVIEW, UNNEEDED, Case, CaseRow, MasterItem, Rule
from .normalize import norm


# ---------------------------------------------------------------------------
# 案件属性の類似度と重み
# ---------------------------------------------------------------------------

def bed_band(beds, bands: list[int]) -> int | None:
    try:
        b = int(beds)
    except (TypeError, ValueError):
        return None
    return sum(1 for x in bands if b >= x)


def _as_set(v) -> set[str]:
    if v is None or v == "":
        return set()
    if isinstance(v, (list, tuple, set)):
        return {norm(x) for x in v if str(x).strip()}
    return {norm(x) for x in str(v).replace("、", ",").split(",") if x.strip()}


def attr_similarity(case_attrs: dict, project: dict, cfg: dict) -> float | None:
    """0〜1。両方に入力がある属性だけで計算する。比べられる属性がなければ None。"""
    w = cfg.get("weighting", {})
    parts: list[tuple[float, float]] = []

    def filled(v):
        return v not in (None, "", [], ())

    if filled(case_attrs.get("update_type")) and filled(project.get("update_type")):
        parts.append((w.get("update_type", 0.3), 1.0 if case_attrs["update_type"] == project["update_type"] else 0.0))
    bands = cfg.get("bed_bands", [100, 200, 400])
    cb, pb = bed_band(case_attrs.get("beds"), bands), bed_band(project.get("beds"), bands)
    if cb is not None and pb is not None:
        parts.append((w.get("bed_band", 0.2), {0: 1.0, 1: 0.5}.get(abs(cb - pb), 0.0)))
    if filled(case_attrs.get("vendor")) and filled(project.get("vendor")):
        parts.append((w.get("vendor", 0.2), 1.0 if norm(case_attrs["vendor"]) == norm(project["vendor"]) else 0.0))
    for attr in ("systems", "tags"):
        a, b = _as_set(case_attrs.get(attr)), _as_set(project.get(attr))
        if a or b:
            if a and b:
                parts.append((w.get(attr, 0.2), len(a & b) / len(a | b)))
    try:
        dy = abs(int(case_attrs.get("year")) - int(project.get("year")))
        parts.append((w.get("year", 0.05), max(0.0, 1 - dy / max(1, w.get("year_span", 5)))))
    except (TypeError, ValueError):
        pass
    total = sum(p[0] for p in parts)
    if not parts or total <= 0:
        return None
    return sum(p[0] * p[1] for p in parts) / total


def case_weights(cases: list[Case], project: dict, cfg: dict) -> dict[int, float]:
    """事例の重み = 1 + coef × 属性の類似度（比べられない事例は 1）。"""
    w = cfg.get("weighting", {})
    coef = float(w.get("coef", 1.0))
    out = {}
    for c in cases:
        s = attr_similarity(c.attrs or {}, project or {}, cfg) if w.get("enabled", True) else None
        out[c.id] = 1.0 + coef * s if s is not None else 1.0
    return out


# ---------------------------------------------------------------------------
# ユーザー独自条件（ルール）
# ---------------------------------------------------------------------------

OPERATORS = ["＝", "≠", "含む", "含まない", "以上", "以下", "入力あり", "未入力"]
RULE_ATTRS = {"update_type": "更新種別", "beds": "病床規模", "vendor": "ベンダー名", "systems": "連携システム",
              "year": "実施年度", "tags": "自由タグ"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def condition_holds(cond: dict, project: dict) -> bool:
    attr, op, value = cond.get("attr"), cond.get("op"), cond.get("value")
    actual = project.get(attr)
    if op == "入力あり":
        return actual not in (None, "", [], ())
    if op == "未入力":
        return actual in (None, "", [], ())
    if actual in (None, "", [], ()):
        return False
    if op in ("含む", "含まない"):
        hit = bool(_as_set(value) & _as_set(actual)) if isinstance(actual, (list, tuple, set)) \
            else norm(value) in norm(actual)
        return hit if op == "含む" else not hit
    if op in ("以上", "以下"):
        a, b = _num(actual), _num(value)
        if a is None or b is None:
            return False
        return a >= b if op == "以上" else a <= b
    eq = _as_set(actual) == _as_set(value) if isinstance(actual, (list, tuple, set)) else norm(actual) == norm(value)
    return eq if op == "＝" else not eq


def rule_applies(rule: Rule, project: dict) -> bool:
    return rule.enabled and all(condition_holds(c, project) for c in rule.conditions)


def rule_targets(rule: Rule, key: str, name: str) -> bool:
    return key in rule.targets or (bool(rule.keyword) and norm(rule.keyword) in norm(name))


# ---------------------------------------------------------------------------
# 必要性
# ---------------------------------------------------------------------------

@dataclass
class Necessity:
    rate: float | None
    adopted: list[str]                  # 採用した事例名
    total: int
    stat: str                           # 統計による判定
    decision: str                       # ルールを反映した判定
    rule: Rule | None = None
    prefer_rules: list = field(default_factory=list)


def stat_decision(rate: float | None, cfg: dict) -> str:
    if rate is None:
        return REVIEW
    t = cfg.get("thresholds", {})
    if rate >= float(t.get("required", 0.7)) - 1e-9:
        return REQUIRED
    if rate < float(t.get("unneeded", 0.3)) - 1e-9:
        return UNNEEDED
    return REVIEW


def ancestors(items: list[MasterItem]) -> dict[str, list[str]]:
    by_key = {i.key: i for i in items}
    out = {}
    for i in items:
        chain, p = [], i.parent_key
        while p and p in by_key and p not in chain:
            chain.append(p)
            p = by_key[p].parent_key
        out[i.key] = chain
    return out


def adoption(items: list[MasterItem], rows: list[CaseRow]) -> dict[int, set[str]]:
    """事例ごとに採用した作業項目（子が採用なら親も）。ひな形外の行は "extra:正規化名" で数える。"""
    anc = ancestors(items)
    out: dict[int, set[str]] = defaultdict(set)
    for r in rows:
        if r.item_key and r.status in ("auto", "confirmed"):
            out[r.case_id].add(r.item_key)
            out[r.case_id].update(anc.get(r.item_key, []))
        elif r.status == "extra" and r.norm:
            out[r.case_id].add("extra:" + r.norm)
    return out


def active_rules(rules: list[Rule], project: dict) -> list[Rule]:
    return sorted([r for r in rules if rule_applies(r, project or {})], key=lambda r: (r.priority, r.id or 0))


def prefer_weights(cases: list[Case], rules: list[Rule]) -> tuple[dict[int, float], dict[int, list[Rule]]]:
    """kind=prefer のルールで、条件に合う事例の重みを weight 倍にする。"""
    w = {c.id: 1.0 for c in cases}
    used: dict[int, list[Rule]] = defaultdict(list)
    for rule in rules:
        if rule.kind != "prefer" or not rule.prefer:
            continue
        for c in cases:
            if condition_holds(rule.prefer, c.attrs or {}):
                w[c.id] *= max(0.0, float(rule.weight or 1.0))
                used[c.id].append(rule)
    return w, used


def necessity(key: str, name: str, case_ids: list[int], adopted: dict[int, set[str]], case_names: dict[int, str],
              rules: list[Rule], cfg: dict) -> Necessity:
    """rules は今回の案件に当てはまるルール（active_rules の結果）。"""
    total = len(case_ids)
    adopting = [c for c in case_ids if key in adopted.get(c, set())]
    rate = len(adopting) / total if total else None
    sd = stat_decision(rate, cfg)
    n = Necessity(rate, [case_names[c] for c in adopting], total, sd, sd)
    for rule in rules:
        if rule.kind == "decision" and rule_targets(rule, key, name):
            n.rule, n.decision = rule, rule.decision
            break
    return n
