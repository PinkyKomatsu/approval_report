"""コピー用のテキスト（Excel にそのまま貼り付けられるタブ区切り）。

列の並び（layout）
  align ：フォーマットの最初の欄の列から最後の欄の列まで、1 列ずつ並べる。数式の列・結合セルの左上以外の列・
          欄のない列は空欄にして、Excel の行頭セルに貼ると各欄が正しい列に入るようにする
  fields：フォーマットの欄だけを列順に並べる（数式の列は除く）
セルの中の改行・タブ・" は、Excel の貼り付けの規則に合わせて "…" で囲む。
"""
from __future__ import annotations

from openpyxl.utils import column_index_from_string, get_column_letter

ALIGN, FIELDS_ONLY = "align", "fields"


def layout(profile: dict, analysis: dict | None, mode: str = ALIGN) -> list[tuple[str, str | None]]:
    """[(列, 欄 または None)]"""
    analysis = analysis or {}
    cols = {column_index_from_string(c): m for c, m in (profile.get("columns") or {}).items()}
    if not cols:
        return []
    formula = set(analysis.get("formula_cols") or [])
    covered = set(analysis.get("covered_cols") or [])
    if mode == FIELDS_ONLY:
        return [(get_column_letter(c), m) for c, m in sorted(cols.items()) if get_column_letter(c) not in formula]
    out = []
    for c in range(min(cols), max(cols) + 1):
        letter = get_column_letter(c)
        m = cols.get(c)
        out.append((letter, None if (m is None or letter in formula or letter in covered) else m))
    return out


def quote(v: str) -> str:
    v = "" if v is None else str(v)
    if any(ch in v for ch in ("\t", "\n", "\r", '"')):
        return '"' + v.replace('"', '""') + '"'
    return v


def row_text(values: dict[str, str], cols: list[tuple[str, str | None]]) -> str:
    return "\t".join(quote(values.get(m, "")) if m else "" for _c, m in cols)


def rows_text(rows: list[dict[str, str]], cols: list[tuple[str, str | None]]) -> str:
    return "\n".join(row_text(r, cols) for r in rows)
