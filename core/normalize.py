"""作業項目名の正規化（全角半角・空白・記号）。同じ作業項目をまとめるのと、名前の類似度に使う。"""
from __future__ import annotations

import re
import unicodedata

# 比較で無視する空白・記号（長音「ー」はカタカナ語の一部なので残す）
_SYMBOLS_RE = re.compile(r"[\s　・･\-－‐―_/／\\()（）\[\]［］【】「」『』〈〉<>＜＞、。,.．:：;；!！?？\"'`’“”〜~*＊#＃]+")


def nfkc(s) -> str:
    return unicodedata.normalize("NFKC", "" if s is None else str(s))


def norm(s) -> str:
    """作業項目名の正規化：NFKC・小文字化・空白と記号を除く。"""
    return _SYMBOLS_RE.sub("", nfkc(s).lower()).strip()
