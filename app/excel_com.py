"""Excel 本体を COM で操作して雛形に記入する（図形・数式・コメント・結合セル・印刷範囲を崩さないため）。

［Excelに反映（プレビュー）］
  1. 雛形を一時フォルダにコピーし、Excel を表示状態で開く（原本は変更しない）
  2. 項目欄と記入内容を記入する
  3. 行が足りないセクションは、最終行の下に Rows.Insert で行を追加する（書式は上の行から引き継ぐ）
  4. 余った行は空欄にする（行は削除しない）
  5. 記入内容が空のセクションは雛形のまま残す
"""
from __future__ import annotations

import re
import shutil
import tempfile
from datetime import date
from pathlib import Path

from openpyxl.utils import column_index_from_string

XL_DOWN = -4121
XL_FORMAT_FROM_LEFT_OR_ABOVE = 0
XL_PASTE_FORMATS = -4122
XL_MOVE, XL_FREE_FLOATING = 2, 3
XL_OPEN_XML_WORKBOOK, XL_OPEN_XML_WORKBOOK_MACRO = 51, 52

YM = re.compile(r"[0-9０-９XxＸｘ×○〇]{2,4}\s*年\s*[0-9０-９XxＸｘ×○〇]{1,2}\s*月")
MEETINGS = {"工程承認会議": r"工程承認会議", "報告会": r"(?:プロジェクト)?報告会"}


def make_title(text: str, d: date, phase: str) -> str:
    """タイトルの年月と工程名「○○」を置き換える。"""
    text = YM.sub(f"{d.year}年{d.month}月", text or "", count=1)
    return text.replace("○○", phase, 1) if phase else text


def make_meeting(text: str, meeting: str) -> str:
    """会議種別の □ / ■ を切り替える。"""
    for name, pat in MEETINGS.items():
        text = re.sub(r"[□■](\s*" + pat + ")", ("■" if name == meeting else "□") + r"\1", text)
    return text


def _safe(v: str) -> str:
    """Excel が数式・日付・数値に変えないようにする。"""
    if re.match(r"^[=+\-@]", v) or re.fullmatch(r"[0-9.,/:\- ]+", v):
        return "'" + v
    return v


def _cell(ws, ref_or_rc):
    c = ws.Range(ref_or_rc) if isinstance(ref_or_rc, str) else ws.Cells(*ref_or_rc)
    return c.MergeArea.Cells(1, 1)


class Preview:
    """プレビュー中の Excel とブック。"""

    def __init__(self):
        self.app = None
        self.wb = None
        self.path: Path | None = None

    def alive(self) -> bool:
        try:
            return self.wb is not None and bool(self.wb.Name)
        except Exception:
            return False

    def _excel(self):
        import win32com.client
        try:
            if self.app is not None:
                _ = self.app.Visible
                return self.app
        except Exception:
            pass
        self.app = win32com.client.DispatchEx("Excel.Application")
        return self.app

    def reflect(self, template, fmt: dict, content: dict) -> Path:
        """content：{date, phase, meeting, items{key: 文}, sections{key: [[列ごとの文], ...]}}"""
        if self.alive():
            self.wb.Close(SaveChanges=False)
        self.wb = None
        tmp = Path(tempfile.mkdtemp(prefix="approval_report_"))
        self.path = tmp / ("preview" + Path(template).suffix)
        shutil.copy2(template, self.path)
        app = self._excel()
        app.Visible = True
        app.DisplayAlerts = False
        app.ScreenUpdating = False
        try:
            self.wb = app.Workbooks.Open(str(self.path))
            ws = self.wb.Worksheets(fmt["sheet"])
            ws.Activate()
            fill(app, ws, fmt, content)
            ws.Range("A1").Select()
        finally:
            app.ScreenUpdating = True
            app.DisplayAlerts = True
        return self.path

    def save_as(self, path) -> None:
        if not self.alive():
            raise RuntimeError("プレビューの Excel が閉じられています。［Excelに反映（プレビュー）］からやり直してください。")
        path = Path(path)
        fmt_no = XL_OPEN_XML_WORKBOOK_MACRO if path.suffix.lower() == ".xlsm" else XL_OPEN_XML_WORKBOOK
        self.app.DisplayAlerts = False
        try:
            self.wb.SaveAs(str(path), FileFormat=fmt_no)
        finally:
            self.app.DisplayAlerts = True


def fill(app, ws, fmt: dict, content: dict) -> None:
    d = content.get("date") or date.today()
    # タイトル・作成日・会議種別
    t = _cell(ws, fmt["title"]["cell"])
    t.Value = make_title(str(t.Value or ""), d, content.get("phase", ""))
    m = _cell(ws, fmt["meeting"]["cell"])
    if m.Value:
        m.Value = make_meeting(str(m.Value), content.get("meeting", "工程承認会議"))
    items = dict(content.get("items") or {})
    for it in fmt["items"]:
        if it["key"] == "date":
            _cell(ws, it["cell"]).Value = f"{d.year}/{d.month}/{d.day}"
        elif items.get(it["key"]):
            _cell(ws, it["cell"]).Value = _safe(items[it["key"]])
    # 文章セクション：下から順に処理する（行を追加しても、上のセクションの位置が変わらない）
    targets = [s for s in fmt["sections"] if s["kind"] != "manual" and s.get("start") and content["sections"].get(s["key"])]
    for s in sorted(targets, key=lambda s: -s["start"]):
        write_section(app, ws, s, content["sections"][s["key"]])


def write_section(app, ws, section: dict, rows: list[list[str]]) -> None:
    start, end = section["start"], section["end"]
    cols = [column_index_from_string(c) for c in section["cols"]]
    cap = max(0, end - start + 1)
    if len(rows) > cap:
        insert_rows(app, ws, max(end, start - 1), len(rows) - cap)
    for i in range(max(cap, len(rows))):
        r = start + i
        for j, c in enumerate(cols):
            cell = _cell(ws, (r, c))
            v = rows[i][j] if i < len(rows) and j < len(rows[i]) else ""
            if v:
                cell.Value = _safe(v)
            else:
                cell.MergeArea.ClearContents()


def insert_rows(app, ws, after: int, n: int) -> None:
    """after 行の下に n 行を追加する。書式（結合を含む）と行の高さは after 行から引き継ぐ。
    図形は行と一緒に移動させ、印刷範囲が after 行で終わっていれば広げる。"""
    floating = []
    for i in range(1, ws.Shapes.Count + 1):
        shp = ws.Shapes.Item(i)
        if shp.Placement == XL_FREE_FLOATING:
            floating.append(shp)
            shp.Placement = XL_MOVE
    area = str(ws.PageSetup.PrintArea or "")
    target = ws.Rows(f"{after + 1}:{after + n}")
    target.Insert(Shift=XL_DOWN, CopyOrigin=XL_FORMAT_FROM_LEFT_OR_ABOVE)
    ws.Rows(after).Copy()
    ws.Rows(f"{after + 1}:{after + n}").PasteSpecial(Paste=XL_PASTE_FORMATS)
    app.CutCopyMode = False
    height = ws.Rows(after).RowHeight
    ws.Rows(f"{after + 1}:{after + n}").RowHeight = height
    m = re.fullmatch(r"(\$?[A-Z]+\$?\d+:\$?[A-Z]+\$?)(\d+)", area.replace("'", "").split("!")[-1])
    if m and int(m.group(2)) == after:
        ws.PageSetup.PrintArea = f"{m.group(1)}{after + n}"
    for shp in floating:
        shp.Placement = XL_FREE_FLOATING


def active_cell():
    """Excel で選択中のセル (ブックのパス, シート名, 行, 列)。Excel が開いていなければ None。"""
    try:
        import win32com.client
        app = win32com.client.GetActiveObject("Excel.Application")
        c = app.ActiveCell
        if c is None:
            return None
        return str(app.ActiveWorkbook.FullName), str(c.Worksheet.Name), int(c.Row), int(c.Column)
    except Exception:
        return None


def open_visible(path) -> None:
    """雛形を Excel で開く（セルの指定用）。"""
    import win32com.client
    try:
        app = win32com.client.GetActiveObject("Excel.Application")
    except Exception:
        app = win32com.client.Dispatch("Excel.Application")
    app.Visible = True
    app.Workbooks.Open(str(path))
