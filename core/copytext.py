"""コピー用のテキスト（Excel にそのまま貼り付けられるタブ区切り）。

列の並びは過去事例の列（最初の欄の列から最後の欄の列まで 1 列ずつ。欄のない列は空欄）。
Excel の行頭のセルに貼ると、各欄が過去事例と同じ列に入る。
セルの中の改行・タブ・" は、Excel の貼り付けの規則に合わせて "…" で囲む。
"""
from __future__ import annotations

from openpyxl.utils import column_index_from_string, get_column_letter


def layout(columns: dict[str, str]) -> list[tuple[str, str | None]]:
    """[(列, 欄 または None)]"""
    cols = {column_index_from_string(c): m for c, m in (columns or {}).items()}
    if not cols:
        return []
    return [(get_column_letter(c), cols.get(c)) for c in range(min(cols), max(cols) + 1)]


def quote(v: str) -> str:
    v = "" if v is None else str(v)
    if any(ch in v for ch in ("\t", "\n", "\r", '"')):
        return '"' + v.replace('"', '""') + '"'
    return v


def row_text(values: dict[str, str], cols: list[tuple[str, str | None]]) -> str:
    return "\t".join(quote(values.get(m, "")) if m else "" for _c, m in cols)


def rows_text(rows: list[dict[str, str]], cols: list[tuple[str, str | None]]) -> str:
    return "\n".join(row_text(r, cols) for r in rows)
