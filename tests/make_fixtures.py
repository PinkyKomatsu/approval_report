"""テスト用のダミーデータ（架空の病院名・ベンダー名だけを使う）を作る。

  master.xlsx   ひな形（作業項目だけで記載がない。過去事例として取り込めないことの確認用）
  case_A.xlsx   過去事例A（架空総合病院／架空システムズ／PACS連携あり／2023年度）
  case_B.xlsx   過去事例B（架空市民病院／架空メディカル／WBS番号を「3-1」形式に振り直し・一部の項目なし・表記ゆれ）
  case_C.xlsx   過去事例C（架空中央病院／架空システムズ／PACS連携あり／2025年度。英語の列あり）

  python tests/make_fixtures.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Border, Font, PatternFill, Side

OUT = Path(__file__).resolve().parent / "fixtures"

MASTER = [
    ("1", "プロジェクト管理"), ("1.1", "プロジェクト計画書の作成"), ("1.2", "定例会の運営"), ("1.3", "課題管理"),
    ("2", "要件定義"), ("2.1", "現行業務調査"), ("2.2", "要件定義書の作成"),
    ("3", "マスタ移行"), ("3.1", "点数マスタ移行"), ("3.2", "薬剤マスタ移行"), ("3.3", "移行リハーサル"),
    ("4", "テスト"), ("4.1", "単体テスト"), ("4.2", "電子カルテ連携テスト"), ("4.3", "PACS連携テスト"), ("4.4", "総合テスト"),
    ("5", "診療報酬改定対応"), ("5.1", "改定内容の確認"), ("5.2", "点数マスタの改定反映"),
    ("6", "教育・稼働"), ("6.1", "職員向け操作研修"), ("6.2", "本番稼働判定"),
]
HEADERS = ["No", "WBS番号", "工程", "作業項目", "作業内容", "報告内容", "", "進捗状況", "課題・懸念", "対応方針", "担当",
           "予定日", "実績日", "備考"]
THIN = Side(style="thin")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")


def _phase_of(wbs: str) -> str:
    top = wbs.split(".")[0]
    return next(n for w, n in MASTER if w == top)


def _sheet(wb, title="WBS"):
    ws = wb.active
    ws.title = title
    ws["A1"] = "医事会計システム更新 WBS"
    ws["A1"].font = Font(size=14, bold=True)
    ws["A2"] = "案件名："
    for c, h in enumerate(HEADERS, 1):
        cell = ws.cell(4, c, h or None)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.border = BOX
    ws.merge_cells("F4:G4")
    for col, w in zip("ABCDEFGHIJKLMN", (5, 8, 14, 22, 30, 24, 24, 10, 26, 26, 10, 11, 11, 16)):
        ws.column_dimensions[col].width = w
    return ws


def build_master(path: Path):
    wb = openpyxl.Workbook()
    ws = _sheet(wb, "ひな形")
    for i, (wbs, name) in enumerate(MASTER):
        r = 5 + i
        ws.cell(r, 2, wbs)
        ws.cell(r, 3, _phase_of(wbs))
        ws.cell(r, 4, name)
    wb.save(path)


# 作業項目 → (作業内容, 報告内容, 進捗状況, 課題・懸念, 対応方針, 担当)。{h}=病院名 {v}=ベンダー {d}=日付 {n}=件数
TEXT = {
    "プロジェクト計画書の作成": ("プロジェクト計画書を作成し承認を得る", "{h}向けのプロジェクト計画書を作成し、{d}に承認を得た。", "完了", "特になし", "特になし", "PM"),
    "定例会の運営": ("定例会を毎週開催する", "定例会を毎週実施し、議事録を共有した。", "対応中", "特になし", "特になし", "PM"),
    "現行業務調査": ("現行業務を調査する", "{h}の各部門にヒアリングを実施し、現行業務フローを作成した。", "完了", "一部の部門で調査が遅延", "追加のヒアリングを調整する", "SE"),
    "点数マスタ移行": ("点数マスタを移行する", "点数マスタ{n}を移行し、差異を確認した。", "完了", "差異が一部残っている", "{v}と差異の原因を確認する", "SE"),
    "薬剤マスタ移行": ("薬剤マスタを移行する", "薬剤マスタの移行を完了した。", "完了", "特になし", "特になし", "SE"),
    "移行リハーサル": ("移行リハーサルを実施する", "移行リハーサルを{d}に実施し、移行手順を確認した。", "完了", "移行時間が想定より長い", "本番では作業を並行して実施する", "SE"),
    "電子カルテ連携テスト": ("電子カルテとの連携を確認する", "電子カルテ連携テストを実施した。", "完了", "特になし", "特になし", "SE"),
    "PACS連携テスト": ("PACSとの連携を確認する", "PACS連携テストを{d}に実施し、画像の連携を確認した。", "完了", "PACSの仕様が未確定", "{v}と仕様を協議する", "SE"),
    "総合テスト": ("総合テストを実施する", "総合テストを実施し、不具合{n}を対応した。", "完了", "特になし", "特になし", "SE"),
    "改定内容の確認": ("診療報酬改定の内容を確認する", "診療報酬改定の内容を確認し、影響範囲を整理した。", "完了", "改定の詳細が未確定", "告示後に再度確認する", "SE"),
    "点数マスタの改定反映": ("点数マスタに改定を反映する", "点数マスタに改定内容を反映した。", "対応中", "特になし", "特になし", "SE"),
    "職員向け操作研修": ("職員向けに操作研修を実施する", "職員向け操作研修を{d}に実施した。", "完了", "特になし", "特になし", "教育担当"),
}
TEXT_EN = {
    "移行リハーサル": "The migration rehearsal was carried out on {d}, and the migration procedure was confirmed.",
}


def build_case(path: Path, hospital: str, vendor: str, date: datetime, skip=(), rename=None, wbs_dash=False,
               extra=None, overrides=None, english=False, count="1,200件"):
    rename, overrides = rename or {}, overrides or {}
    wb = openpyxl.Workbook()
    ws = _sheet(wb)
    if english:
        ws.cell(4, 15, "報告内容（英語）").font = Font(bold=True)
    r = 5
    for wbs, name in MASTER:
        if name in skip:
            continue
        ws.cell(r, 1, r - 4)
        ws.cell(r, 2, None if name in rename else (wbs.replace(".", "-") if wbs_dash else wbs))
        ws.cell(r, 3, _phase_of(wbs))
        ws.cell(r, 4, rename.get(name, name))
        t = overrides.get(name, TEXT.get(name))
        if t:
            fill = dict(h=hospital, v=vendor, d=f"{date:%Y/%m/%d}", n=count)
            work, report, progress, issue, policy, owner = (x.format(**fill) for x in t)
            for c, v in ((5, work), (6, report), (8, progress), (9, issue), (10, policy), (11, owner)):
                ws.cell(r, c, v)
            ws.cell(r, 12, date)
            ws.cell(r, 12).number_format = "yyyy/m/d"
            if english and name in TEXT_EN:
                ws.cell(r, 15, TEXT_EN[name].format(**fill))
        ws.merge_cells(start_row=r, start_column=6, end_row=r, end_column=7)
        r += 1
        if extra and name == extra[0]:
            ws.cell(r, 4, extra[1])
            ws.cell(r, 6, extra[2])
            r += 1
    wb.save(path)


def scrub(path: Path):
    """作成者などの個人情報をファイルのプロパティに残さない。"""
    wb = openpyxl.load_workbook(path)
    wb.properties.creator = ""
    wb.properties.lastModifiedBy = ""
    wb.save(path)


def build_all(out: Path = OUT) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    build_master(out / "master.xlsx")
    build_case(out / "case_A.xlsx", "架空総合病院", "架空システムズ", datetime(2023, 6, 10))
    build_case(out / "case_B.xlsx", "架空市民病院", "架空メディカル", datetime(2024, 9, 5), wbs_dash=True,
               skip=("PACS連携テスト",), rename={"職員向け操作研修": "職員操作研修会"},
               extra=("総合テスト", "院内説明会の実施", "院内説明会を実施した。"),
               overrides={"移行リハーサル": ("移行リハーサルを実施する", "移行リハーサルを2回実施し、課題を洗い出した。", "完了",
                                         "特になし", "特になし", "SE")}, count="980件")
    build_case(out / "case_C.xlsx", "架空中央病院", "架空システムズ", datetime(2025, 3, 2), english=True, count="1,500件")
    for p in out.glob("*.xlsx"):
        scrub(p)
    return out


if __name__ == "__main__":
    print(build_all(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT))
