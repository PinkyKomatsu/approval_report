"""推奨記載例の中の日付・固有名・数値を {{病院名}} {{日付}} のようなプレースホルダーに置き換える。

判定は規則による（オフライン）。設定の placeholders に、上から順に（先にあるものほど優先）
  pattern：正規表現
  source："person"（Janome の人名）
を書く。同じ名前で違う値が複数あれば {{日付}} {{日付2}} … と番号を付ける（同じ値は同じプレースホルダー）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import tokenizer

PH_RE = re.compile(r"\{\{([^{}]+)\}\}")
_HONORIFIC = re.compile(r"さん|様|氏")


@dataclass
class Spec:
    name: str
    en: str
    pattern: re.Pattern | None = None
    source: str = ""


@dataclass
class Templated:
    template: str                                   # プレースホルダーに置き換えた文
    values: dict = field(default_factory=dict)      # プレースホルダー（例：日付2）→ 元の値


def specs_from(cfg: dict) -> list[Spec]:
    out = []
    for p in cfg.get("placeholders", []):
        if not p.get("enabled", True) or not p.get("name"):
            continue
        pat = None
        if p.get("pattern"):
            try:
                pat = re.compile(p["pattern"])
            except re.error:
                continue
        out.append(Spec(p["name"], p.get("en") or p["name"], pat, p.get("source", "")))
    return out


def _spans(text: str, spec: Spec, field_name: str):
    if spec.pattern is not None:
        for m in spec.pattern.finditer(text):
            if m.group().strip():
                yield m.start(), m.end()
    elif spec.source == "person" and (field_name == "owner" or _HONORIFIC.search(text)):
        for n in tokenizer.person_names(text):
            for m in re.finditer(re.escape(n), text):
                yield m.start(), m.end()


class Extractor:
    def __init__(self, specs: list[Spec]):
        self.specs = specs
        self._cache: dict[tuple[str, str], Templated] = {}

    def extract(self, text: str, field_name: str = "") -> Templated:
        key = (text, field_name)
        if key in self._cache:
            return self._cache[key]
        taken = [False] * len(text)
        found = []                                   # (開始, 終了, 名前)
        for spec in self.specs:
            for s, e in _spans(text, spec, field_name):
                if any(taken[s:e]):
                    continue
                for i in range(s, e):
                    taken[i] = True
                found.append((s, e, spec.name))
        found.sort()
        values: dict[str, str] = {}
        by_value: dict[tuple[str, str], str] = {}
        counts: dict[str, int] = {}
        keys = []
        for s, e, name in found:
            v = text[s:e]
            k = by_value.get((name, v))
            if k is None:
                counts[name] = counts.get(name, 0) + 1
                k = name if counts[name] == 1 else f"{name}{counts[name]}"
                by_value[(name, v)] = k
                values[k] = v
            keys.append(k)
        out = text
        for (s, e, _name), k in sorted(zip(found, keys), reverse=True):
            out = out[:s] + "{{" + k + "}}" + out[e:]
        result = Templated(out, values)
        self._cache[key] = result
        return result


def keys_in(template: str) -> list[str]:
    seen = []
    for k in PH_RE.findall(template or ""):
        if k not in seen:
            seen.append(k)
    return seen


def base_name(key: str) -> str:
    return re.sub(r"\d+$", "", key)


def substitute(template: str, values: dict[str, str]) -> str:
    """値が入力されたプレースホルダーだけを差し替える（未入力は {{…}} のまま残す）。"""
    return PH_RE.sub(lambda m: values.get(m.group(1)) or m.group(0), template or "")


def en_key(key: str, specs: list[Spec]) -> str:
    """{{日付2}} → {{Date2}}"""
    base = base_name(key)
    en = next((s.en for s in specs if s.name == base), base)
    return en + key[len(base):]


def to_en_template(text_en: str, values: dict[str, str], specs: list[Spec]) -> str:
    """過去事例の英文の中で、日本語の元の値と同じ文字列（日付・数値・英字の名前など）をプレースホルダーにする。"""
    for k, v in sorted(values.items(), key=lambda kv: -len(kv[1])):
        if v and v in text_en:
            text_en = text_en.replace(v, "{{" + en_key(k, specs) + "}}")
    return text_en
