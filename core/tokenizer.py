"""日本語の形態素解析（Janome。純 Python で辞書を同梱しているため、オフラインの単一 exe でも動く）。

検索用のトークン：名詞・動詞・形容詞の基本形（数・代名詞・非自立語は除く）と、
連続する名詞をつないだ複合語（「マスタ」「移行」→「マスタ移行」）。
"""
from __future__ import annotations

import re
import threading
import unicodedata

_lock = threading.Lock()
_tokenizer = None

KEEP = ("名詞", "動詞", "形容詞")
DROP_SUB = ("非自立", "数", "代名詞", "接尾")
_WORD = re.compile(r"\w")
STOP = {"する", "いる", "ある", "なる", "れる", "られる", "こと", "もの", "ため", "よう", "これ", "それ", "の"}


def tokenizer():
    global _tokenizer
    with _lock:
        if _tokenizer is None:
            from janome.tokenizer import Tokenizer
            _tokenizer = Tokenizer()
        return _tokenizer


def _parts(text: str):
    return tokenizer().tokenize(unicodedata.normalize("NFKC", text or "").lower())


def tokens(text: str) -> list[str]:
    out: list[str] = []
    run: list[str] = []          # 連続する名詞

    def close_run():
        if len(run) > 1:
            out.append("".join(run))
        run.clear()

    for t in _parts(text):
        pos = t.part_of_speech.split(",")
        word = t.base_form if t.base_form and t.base_form != "*" else t.surface
        if pos[0] == "名詞" and pos[1] not in ("非自立", "代名詞", "数") and _WORD.search(t.surface):
            run.append(t.surface)
        else:
            close_run()
        if pos[0] not in KEEP or pos[1] in DROP_SUB or word in STOP or not _WORD.search(word):
            continue
        if len(word) == 1 and pos[0] != "名詞":
            continue
        out.append(word)
    close_run()
    return out


def person_names(text: str) -> list[str]:
    """人名（名詞,固有名詞,人名）。直後の「さん」「様」「氏」も含める。"""
    names, cur = [], ""
    for t in tokenizer().tokenize(text or ""):
        pos = t.part_of_speech.split(",")
        if pos[0] == "名詞" and pos[1] == "固有名詞" and pos[2] == "人名":
            cur += t.surface
            continue
        if cur and t.surface in ("さん", "様", "氏"):
            cur += t.surface
        if cur:
            names.append(cur)
            cur = ""
    if cur:
        names.append(cur)
    return [n for n in names if len(n) >= 2]
