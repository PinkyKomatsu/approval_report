"""日本語 → 英語（オフライン）。

優先順位
  1. 対訳：過去事例の英語の列・自分の定型文の英文（日本語の文がそのまま一致するもの）
  2. 定型文の日英対訳テンプレート（text_templates.json の phrases。{x} の部分は用語辞書で訳す）
  3. 用語辞書（日本語,英語）の最長一致での置き換え
訳せない語は英文に【未翻訳: ○○】と表示し、件数を数える。{{…}} のプレースホルダーは英語名にして残す。
"""
from __future__ import annotations

import re

from .placeholders import PH_RE, Spec, en_key

_JA_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ々〆]")
_SENT_RE = re.compile(r"([^。！？!?]*[。！？!?]?)")
_PUNCT = {"、": ",", "，": ",", "（": "(", "）": ")", "「": '"', "」": '"', "：": ":", "・": "/", "／": "/", "～": "-", "〜": "-"}


class Translator:
    def __init__(self, glossary: dict[str, str], phrases: list[dict] | None = None, pairs: dict[str, str] | None = None,
                 drop_particles: list[str] | None = None, specs: list[Spec] | None = None):
        self.exact = {ja: en for ja, en in glossary.items() if ja and en}
        self.terms = sorted(self.exact.items(), key=lambda t: -len(t[0]))
        self.pairs = {k: v for k, v in (pairs or {}).items() if k and v}
        self.drop = sorted(drop_particles or [], key=len, reverse=True)
        self.specs = specs or []
        self.phrases = []
        for p in phrases or []:
            ja, en = p.get("ja", ""), p.get("en", "")
            if not ja or not en:
                continue
            ja = ja.rstrip("。")                       # 文末の「。」の有無は問わない
            pat = re.escape(ja).replace(re.escape("{x}"), "(?P<x>.+?)")
            self.phrases.append((re.compile("^" + pat + "。?$"), en.rstrip(".")))

    # ---- 語句 ----
    def _is_dropped(self, chunk: str) -> bool:
        rest = chunk
        while rest:
            for d in self.drop:
                if rest.startswith(d):
                    rest = rest[len(d):]
                    break
            else:
                return False
        return True

    def terms_only(self, text: str) -> tuple[str, list[str]]:
        """用語辞書だけで訳す。"""
        text = (text or "").strip()
        if not text:
            return "", []
        if text in self.exact:
            return self.exact[text], []
        pieces: list[str] = []
        untranslated: list[str] = []
        buf = ""

        def flush():
            nonlocal buf
            chunk = buf.strip(" 　")
            buf = ""
            if not chunk:
                return
            if _JA_RE.search(chunk):
                if self._is_dropped(chunk):
                    return
                core = chunk
                for d in self.drop:            # 前後の助詞は落としてから未翻訳として表示する
                    if core.startswith(d) and len(core) > len(d):
                        core = core[len(d):]
                    if core.endswith(d) and len(core) > len(d):
                        core = core[:-len(d)]
                untranslated.append(core)
                pieces.append(f"【未翻訳: {core}】")
            else:
                pieces.append(chunk)

        i = 0
        while i < len(text):
            m = PH_RE.match(text, i)
            if m:
                flush()
                pieces.append("{{" + en_key(m.group(1), self.specs) + "}}")
                i = m.end()
                continue
            ch = text[i]
            if ch in _PUNCT:
                flush()
                pieces.append(_PUNCT[ch])
                i += 1
                continue
            for ja, en in self.terms:
                if text.startswith(ja, i):
                    flush()
                    pieces.append(en)
                    i += len(ja)
                    break
            else:
                buf += ch
                i += 1
        flush()
        out = " ".join(pieces)
        out = re.sub(r"\s+([,)])", r"\1", out)
        out = re.sub(r"\(\s+", "(", out)
        return out.strip(), untranslated

    # ---- 文 ----
    def _sentence(self, s: str) -> tuple[str, list[str]]:
        s = s.strip()
        if not s:
            return "", []
        for pat, en in self.phrases:
            m = pat.match(s)
            if m:
                x, un = self.terms_only(m.group("x")) if "x" in pat.groupindex else ("", [])
                out = en.replace("{x}", x) + ("." if s.endswith("。") else "")
                return (out[:1].upper() + out[1:]) if out and not out.startswith("{{") else out, un
        body = s.rstrip("。！？!?")
        en, un = self.terms_only(body)
        if en and s[-1:] in "。！？!?":
            en += "." if s[-1] == "。" else s[-1]
        return (en[:1].upper() + en[1:]) if en and not en.startswith("{{") else en, un

    def translate(self, text: str) -> tuple[str, list[str]]:
        """(英語, 未翻訳の語のリスト)"""
        text = (text or "").strip()
        if not text:
            return "", []
        if text in self.pairs:
            return self.pairs[text], []
        lines, untranslated = [], []
        for line in text.splitlines():
            parts = []
            for s in _SENT_RE.findall(line):
                en, un = self._sentence(s)
                if en:
                    parts.append(en)
                untranslated += [u for u in un if u not in untranslated]
            lines.append(" ".join(parts))
        return "\n".join(lines), untranslated
