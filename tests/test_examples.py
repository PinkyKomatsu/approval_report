"""取込・トピック検索・代表文の選定・プレースホルダー・日英生成・コピー・Excel を変更しないこと。"""
import csv
import os
import re
import time
from pathlib import Path

import pytest

from core import copytext, workflow
from core.advisor import Advisor
from core.models import CaseRow
from core.normalize import norm
from core.placeholders import Extractor, specs_from, substitute
from core.representative import Entry, choose
from core.search import SearchIndex
from core.translate import Translator
from excel_io import profile as profile_mod, reader


def names(results):
    return [r.name for r in results]


# ---- 取込 ----
def test_import_detects_columns_and_english(db, loaded):
    cases = {c.name: c for c in db.cases()}
    assert cases["case_A"].columns["F"] == "report" and cases["case_A"].columns["H"] == "progress"
    assert cases["case_C"].columns["O"] == "report_en", "見出しに（英語）を含む列は英語の列"
    row = next(r for r in db.case_rows(loaded["case_C"]) if r.name == "移行リハーサル")
    assert row.texts["report"].startswith("移行リハーサルを2025/03/02") and row.texts_en["report"].startswith("The migration")
    assert row.texts["plan_date"] == "2025/03/02", "日付のセルは yyyy/mm/dd の文字列にする"


def test_import_rejects_template_and_duplicates(db, cfg, loaded, fixtures_dir):
    with pytest.raises(ValueError, match="記載がありません"):
        workflow.import_case(db, fixtures_dir / "master.xlsx", cfg)
    with pytest.raises(workflow.DuplicateCase):
        workflow.import_case(db, fixtures_dir / "case_A.xlsx", cfg)
    workflow.import_case(db, fixtures_dir / "case_A.xlsx", cfg, allow_duplicate=True)
    assert len(db.cases()) == 4


# ---- トピック検索 ----
def test_topic_ranks_related_items_first(advisor):
    top = names(advisor.search("マスタ移行")[:3])
    assert set(top) == {"点数マスタ移行", "薬剤マスタ移行", "移行リハーサル"}, top
    assert "マスタ移行" not in names(advisor.search("マスタ移行")), "記載のない見出し行は出さない"


def test_full_text_and_phase_search(advisor):
    assert names(advisor.search("画像の連携"))[0] == "PACS連携テスト", "作業項目名にない語も、欄の文から見つける"
    assert names(advisor.search("差異"))[0] in ("点数マスタ移行", "移行リハーサル")
    assert {"改定内容の確認", "点数マスタの改定反映"} <= set(names(advisor.search("診療報酬改定対応"))[:3]), "工程名での検索"
    assert set(names(advisor.search("連携テスト"))[:2]) == {"電子カルテ連携テスト", "PACS連携テスト"}
    assert names(advisor.search("院内説明会"))[0] == "院内説明会の実施"


def test_results_show_evidence(advisor):
    r = next(x for x in advisor.search("PACS連携テスト") if x.name == "PACS連携テスト")
    assert r.cases == ["case_A", "case_C"] and r.total == 3
    assert r.name_en == "PACS integration test"


def test_performance_50_cases_1000_rows(cfg, templates, db):
    """過去事例 50 件・各 1000 行で、検索が 1 秒以内（インデックスは起動時に作成済み）。"""
    import random
    random.seed(1)
    words = ["マスタ", "移行", "連携", "テスト", "仕様", "確認", "調整", "帳票", "画面", "研修", "改定", "リハーサル"]
    rows, cache, rid = [], {}, 0
    for c in range(50):
        for i in range(1000):
            rid += 1
            name = f"作業項目{i:04d}の実施"
            rows.append(CaseRow(rid, c, i, i, "", f"工程{i // 100}", name, norm(name),
                                texts={"report": "".join(random.sample(words, 4)) + "を実施した。"}))
            cache[rid] = random.sample(words, 4)          # 形態素解析の結果はキャッシュ済み
    adv = Advisor(db, cfg, templates).build()
    adv.index = SearchIndex(rows, cache)
    adv.cases = {c: type("C", (), {"id": c, "name": f"事例{c}", "columns": {"D": "name", "F": "report"}})() for c in range(50)}
    t0 = time.perf_counter()
    res = adv.search("マスタ移行")
    elapsed = time.perf_counter() - t0
    assert res and elapsed < 1.0, f"検索 {elapsed:.2f} 秒"


# ---- 代表文の選定 ----
def _choose(texts, cfg):
    specs = specs_from(cfg)
    return choose([Entry(t, i, f"事例{i}") for i, t in enumerate(texts)], Extractor(specs), "report", specs, cfg)


def test_most_frequent_text_is_recommended_ignoring_values(cfg):
    rep, others = _choose(["移行リハーサルを2023/06/10に実施した。", "移行リハーサルを2025/03/02に実施した。",
                           "移行リハーサルを2回実施し、課題を洗い出した。"], cfg)
    assert rep.template == "移行リハーサルを{{日付}}に実施した。" and rep.count == 2, "日付の違いは同じ記載とみなす"
    assert rep.label() == "事例0、事例1"
    assert [o.template for o in others] == ["移行リハーサルを{{件数}}実施し、課題を洗い出した。"]


def test_central_text_wins_when_frequency_ties(cfg):
    rep, _ = _choose(["点数マスタの移行を完了した。", "点数マスタと薬剤マスタの移行を完了した。", "連携仕様を協議中。"], cfg)
    assert rep.template == "点数マスタの移行を完了した。", "他の記載と似ている中心的な文"


def test_others_max_five(cfg):
    _rep, others = _choose([f"作業{i}を実施した。" + "あ" * i for i in range(10)], cfg)
    assert len(others) == 5


# ---- プレースホルダー ----
def test_placeholder_extraction(cfg):
    ex = Extractor(specs_from(cfg))
    t = ex.extract("架空総合病院の点数マスタ1,200件を2025年6月10日までに移行し、6/20に確認した。", "report")
    assert t.template == "{{病院名}}の点数マスタ{{件数}}を{{日付}}までに移行し、{{日付2}}に確認した。"
    assert t.values == {"病院名": "架空総合病院", "件数": "1,200件", "日付": "2025年6月10日", "日付2": "6/20"}
    assert ex.extract("2025/06/10 と 2025/06/10 に実施", "report").template == "{{日付}} と {{日付}} に実施"
    assert ex.extract("担当は山田さん。", "report").template == "担当は{{担当者}}。"


def test_placeholder_substitution():
    tpl = "{{病院名}}で{{日付}}に実施。{{日付2}}に再確認。"
    assert substitute(tpl, {"病院名": "架空中央病院", "日付": "2026/11/20"}) == "架空中央病院で2026/11/20に実施。{{日付2}}に再確認。"
    assert substitute(tpl, {}) == tpl, "未入力のプレースホルダーは残す"


def test_render_substitutes_both_languages(advisor):
    r = next(x for x in advisor.search("移行リハーサル") if x.name == "移行リハーサル")
    ja, en, _ = advisor.render(r.fields["report"].rep, {"日付": "2026/11/20"})
    assert ja == "移行リハーサルを2026/11/20に実施し、移行手順を確認した。"
    assert en == "The migration rehearsal was carried out on 2026/11/20, and the migration procedure was confirmed.", \
        "過去事例の英語の列（対訳）を優先し、同じ値をプレースホルダーにして差し替える"
    assert [o.template for o in r.fields["report"].others] == ["移行リハーサルを{{件数}}実施し、課題を洗い出した。"]


# ---- 日英生成 ----
def _translator(cfg, templates, pairs=None):
    from core import paths
    with open(paths.resource("glossary.csv"), encoding="utf-8") as f:
        g = {r[0]: r[1] for r in csv.reader(f) if r and r[0] != "日本語"}
    return Translator(g, templates["phrases"], pairs, cfg["translate"]["drop_particles"], specs_from(cfg))


def test_translation_priority_and_untranslated(cfg, templates):
    tr = _translator(cfg, templates, pairs={"定例会を毎週実施した。": "Weekly meetings were held."})
    assert tr.translate("定例会を毎週実施した。") == ("Weekly meetings were held.", []), "過去事例の対訳を最優先"
    assert tr.translate("点数マスタ移行を完了した。") == ("Fee master migration was completed.", []), "定型文テンプレート"
    en, un = tr.translate("{{病院名}}の電子カルテ連携仕様を確認した。")
    assert en == "{{Hospital}} electronic medical record interface specification was confirmed." and un == []
    en, un = tr.translate("移行時間が想定より長い。")
    assert "【未翻訳:" in en and un and "が" not in un, "訳せない語を数える（助詞だけの部分は除く）"


def test_every_example_has_japanese_and_english(advisor):
    for r in advisor.search("テスト"):
        for fr in r.fields.values():
            if fr.rep:
                ja, en, _ = advisor.render(fr.rep, {})
                assert ja and en


def test_glossary_change_is_used(db, cfg, templates, loaded):
    db.upsert_terms({"想定より長い": "longer than expected", "時間": "time"}, keep_existing_en=False)
    adv = Advisor(db, cfg, templates).build()
    en, un = adv.translate("移行時間が想定より長い")
    assert en == "Migration time longer than expected" and not un


# ---- コピー ----
def test_row_copy_follows_case_column_order(advisor):
    cols = copytext.layout(advisor.columns)
    assert [c for c, _m in cols] == list("ABCDEFGHIJKLMN"), "最初の欄の列から最後の欄の列まで 1 列ずつ"
    assert dict(cols)["G"] is None, "欄のない列（結合セル F:G の G）は空欄"
    values = {"wbs_no": "3.3", "name": "移行リハーサル", "report": "移行リハーサルを実施した。", "progress": "完了",
              "plan_date": "2026/11/20"}
    cells = copytext.row_text(values, cols).split("\t")
    for col, m in advisor.columns.items():
        if m in values:
            assert cells[ord(col) - ord("A")] == values[m], f"{m} は {col} 列の位置"
    assert cells[0] == "" and len(cells) == 14


def test_copy_quoting():
    assert copytext.row_text({"report": 'a\tb"c'}, [("F", "report")]) == '"a\tb""c"'
    assert copytext.rows_text([{"name": "x"}, {"name": "y"}], [("A", "name")]) == "x\ny"


# ---- Excel ファイルを変更しない・外部通信しない ----
def test_excel_files_are_never_modified(db, cfg, templates, fixtures_dir):
    files = sorted(fixtures_dir.glob("*.xlsx"))
    before = {p: (reader.sha256(p), os.stat(p).st_mtime_ns) for p in files}
    for x in "ABC":
        workflow.import_case(db, fixtures_dir / f"case_{x}.xlsx", cfg)
    with pytest.raises(ValueError):
        workflow.import_case(db, fixtures_dir / "master.xlsx", cfg)
    adv = Advisor(db, cfg, templates).build()
    for r in adv.search("マスタ移行"):
        for fr in r.fields.values():
            adv.render(fr.rep, {"日付": "2026/1/1"})
    profile_mod.detect(fixtures_dir / "case_C.xlsx", cfg)
    after = {p: (reader.sha256(p), os.stat(p).st_mtime_ns) for p in files}
    assert after == before, "更新日時・ハッシュが変わっていない"


def test_no_network_or_excel_writing_code():
    root = Path(__file__).resolve().parent.parent
    banned = [r"import socket", r"import urllib", r"import http", r"import requests", r"from urllib", r"from http",
              r"\bwb\.save\(", r"workbook\.save\(", r"SaveAs", r"win32com", r"save_workbook", r"\.Workbook\(\)"]
    for p in [root / "main.py"] + [x for d in ("app", "core", "excel_io") for x in (root / d).rglob("*.py")]:
        text = p.read_text(encoding="utf-8")
        for b in banned:
            assert not re.search(b, text), f"{p.name} に「{b}」"
