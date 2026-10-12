"""日英生成・未翻訳語の検出・コピーの列位置・様式の解析・Excel ファイルを変更しないこと。"""
import csv
import json
import os

from openpyxl.utils import column_index_from_string

from core import copytext, workflow
from core.placeholders import specs_from
from core.translate import Translator
from excel_io import inspect, preview, profile as profile_mod, reader


def _translator(cfg, templates, pairs=None):
    from core import paths
    with open(paths.resource("glossary.csv"), encoding="utf-8") as f:
        g = {r[0]: r[1] for r in csv.reader(f) if r and r[0] != "日本語"}
    return Translator(g, templates["phrases"], pairs, cfg["translate"]["drop_particles"], specs_from(cfg))


# ---- 日英生成 ----
def test_translation_priority_and_untranslated(cfg, templates):
    tr = _translator(cfg, templates, pairs={"定例会を毎週実施した。": "Weekly meetings were held."})
    assert tr.translate("定例会を毎週実施した。") == ("Weekly meetings were held.", []), "過去事例の対訳を最優先"
    assert tr.translate("点数マスタ移行を完了した。") == ("Fee master migration was completed.", []), "定型文テンプレート"
    assert tr.translate("移行リハーサルを実施する")[0] == "Migration rehearsal will be carried out"
    en, un = tr.translate("{{病院名}}の電子カルテ連携仕様を確認した。")
    assert en == "{{Hospital}} electronic medical record interface specification was confirmed." and un == []
    en, un = tr.translate("移行時間が想定より長い。")
    assert "【未翻訳: 時間】" in en or "【未翻訳:" in en
    assert un and all("が" != u for u in un), "助詞だけの部分は未翻訳にしない"


def test_every_suggestion_has_japanese_and_english(advisor):
    for r in advisor.search("マスタ移行"):
        assert r.name_en and r.reason_en
        for fr in r.fields.values():
            if fr.rep:
                ja, en, _ = advisor.render(fr.rep, {})
                assert ja and en


def test_untranslated_terms_are_counted(advisor):
    _en, un = advisor.translate("架空の作業を進める")
    assert un, "用語辞書にない語は【未翻訳】として数える"


# ---- コピーの列位置 ----
def test_row_copy_columns_follow_format_order(db, loaded):
    f = db.active_format("format")
    cols = copytext.layout(f["profile"], f["analysis"], copytext.ALIGN)
    assert [c for c, _m in cols] == list("ABCDEFGHIJKLMN"), "フォーマットの最初の欄の列から最後の欄の列まで 1 列ずつ"
    assert dict(cols)["A"] is None, "数式の列（No）は空欄"
    assert dict(cols)["G"] is None, "結合セル F:G の G は空欄"
    values = {"wbs_no": "3.3", "name": "移行リハーサル", "report": "移行リハーサルを実施した。", "progress": "完了",
              "plan_date": "2026/11/20", "no": "1"}
    cells = copytext.row_text(values, cols).split("\t")
    pos = {m: i for i, (_c, m) in enumerate(cols) if m}
    for m, v in values.items():
        if m == "no":
            assert cells[0] == "", "数式の列には書かない"
            continue
        col = f["profile"]["columns"]
        letter = next(c for c, mm in col.items() if mm == m)
        assert cells[column_index_from_string(letter) - 1] == v, f"{m} は {letter} 列の位置"
        assert pos[m] == column_index_from_string(letter) - 1
    assert len(cells) == 14


def test_fields_only_layout_and_quoting(db, loaded):
    f = db.active_format("format")
    cols = copytext.layout(f["profile"], f["analysis"], copytext.FIELDS_ONLY)
    assert [m for _c, m in cols][:3] == ["wbs_no", "phase", "name"] and "no" not in [m for _c, m in cols]
    assert copytext.row_text({"report": 'a\tb"c'}, [("F", "report")]) == '"a\tb""c"'
    assert copytext.rows_text([{"name": "x"}, {"name": "y"}], [("A", "name")]) == "x\ny"


# ---- 様式の解析 ----
def test_profile_and_constraints(db, loaded, fixtures_dir, cfg):
    f = db.active_format("format")
    p, a = f["profile"], f["analysis"]
    assert p["columns"]["F"] == "report" and p["columns"]["H"] == "progress" and p["header_row"] == 4
    assert a["fields"]["progress"]["allowed"] == ["未着手", "対応中", "完了", "中止"]
    assert a["fields"]["report"]["merged"] == "F5:G5"
    assert a["fields"]["plan_date"]["number_format"] == "yyyy/m/d"
    assert a["formula_cols"] == ["A"] and a["covered_cols"] == ["G"]
    cells, status = inspect.target_cells(a, p, "x", "3.3", "移行リハーサル", "report")
    assert (cells, status) == (["F15"], "ok")
    case_c = profile_mod.detect(fixtures_dir / "case_C.xlsx", cfg)
    assert case_c["columns"]["O"] == "report_en", "見出しに（英語）を含む列は英語の列"


def test_target_pick_is_saved(db, loaded):
    workflow.set_target(db, "extra:院内説明会の実施", "report", "F40")
    f = db.active_format("format")
    assert inspect.target_cells(f["analysis"], f["profile"], "extra:院内説明会の実施", "", "院内説明会の実施", "report") \
        == (["F40"], "picked")
    assert inspect.target_cells(f["analysis"], f["profile"], "extra:院内説明会の実施", "", "院内説明会の実施", "issue") \
        == (["I40"], "picked"), "同じ作業項目の他の欄は指定した行"


def test_allowed_value_conversion(advisor):
    assert advisor.convert_allowed("完了", ["未着手", "対応中", "完了"]) == ("完了", "")
    v, note = advisor.convert_allowed("対応中です", ["未着手", "対応中", "完了"])
    assert v == "対応中" and "変換" in note
    v, note = advisor.convert_allowed("保留", ["未着手", "対応中", "完了"])
    assert v == "保留" and "許可値にありません" in note


def test_char_guideline(advisor):
    assert advisor.guideline("report", {}) % 10 == 0
    assert advisor.guideline("unknown", {"width_chars": 12, "lines": 2}) == 24


# ---- Excel ファイルを変更しない ----
def test_excel_files_are_never_modified(db, cfg, templates, fixtures_dir):
    from core.advisor import Advisor
    files = sorted(fixtures_dir.glob("*.xlsx"))
    before = {p: (reader.sha256(p), os.stat(p).st_mtime_ns) for p in files}
    workflow.register_master(db, fixtures_dir / "master.xlsx", cfg)
    workflow.register_format(db, fixtures_dir / "format.xlsx", cfg)
    for x in "ABC":
        workflow.import_case(db, fixtures_dir / f"case_{x}.xlsx", cfg)
    adv = Advisor(db, cfg, templates).build()
    for r in adv.search("マスタ移行"):
        for fr in r.fields.values():
            adv.render(fr.rep, {"日付": "2026/1/1"})
    workflow.set_target(db, "x", "report", "F9")
    preview.sheet_grid(fixtures_dir / "format.xlsx", "WBS")
    inspect.analyze_format(fixtures_dir / "format.xlsx", db.active_format("format")["profile"])
    profile_mod.detect(fixtures_dir / "case_C.xlsx", cfg)
    after = {p: (reader.sha256(p), os.stat(p).st_mtime_ns) for p in files}
    assert after == before, "更新日時・ハッシュが変わっていない"


def test_no_network_or_excel_writing_code():
    """外部通信・Excel への保存を行うコードがないこと（静的な確認）。"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    import re
    banned = [r"import socket", r"import urllib", r"import http", r"import requests", r"from urllib", r"from http",
              r"\bwb\.save\(", r"workbook\.save\(", r"SaveAs", r"win32com", r"save_workbook", r"\.Workbook\(\)"]
    for p in list((root / "app").rglob("*.py")) + list((root / "core").rglob("*.py")) + list((root / "excel_io").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        for b in banned:
            assert not re.search(b, text), f"{p.name} に「{b}」"
    json.loads((root / "resources" / "default_config.json").read_text(encoding="utf-8"))
