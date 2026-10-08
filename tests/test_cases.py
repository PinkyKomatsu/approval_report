from app import cases


def test_find_files_skips_temp_and_other_files(reports_dir):
    names = [p.name for p in cases.find_files(reports_dir)]
    assert sorted(names) == ["構築_0115.xlsx", "構築_0901.xlsx", "設計_0510.xlsx"], "サブフォルダも読み、~$ は除く"


def test_report_info_and_sections(reports_dir):
    data = cases.import_folder(reports_dir)
    assert not data["errors"]
    reps = {r["name"]: r for r in data["reports"]}
    a = reps["構築_0901.xlsx"]
    assert (a["phase"], a["date"], a["customer"]) == ("構築", "2025-09-01", "架空総合病院")
    assert reps["設計_0510.xlsx"]["phase"] == "設計"
    assert a["items"]["dept"] == "医療システム部" and a["items"]["overview"] == "医事会計システムの更新"
    # 行数が違っても（構築_0901 は各セクションに空行が 2 行多い）見出しを基準に読める
    assert a["sections"]["今後の予定"][:2] == [["　・定例会は毎週実施"], ["　・10月15日 結合テスト開始"]]
    assert "算定トラブル防止施策" not in a["sections"], "手入力の表は取り込まない"


def test_continuation_lines_and_dedupe(reports_dir):
    data = cases.import_folder(reports_dir)
    cands = cases.build_candidates(data["reports"])
    progress = {c.text: c for c in cands["進捗【医事】"]}
    assert "　・自動精算機メーカとのIF仕様が未確定\n　　⇒来月中に仕様確定予定" in progress, "⇒ の行は直前の行と 1 件"
    same = next(c for c in cands["今後の予定"] if c.rows == [["　・定例会は毎週実施"]])
    assert same.count == 3 and sorted(same.phases) == ["構築", "構築", "設計"]
    pair = next(c for c in cands["前回指摘事項"])
    assert pair.rows == [["　・IF仕様の確定時期", "　・メーカと9月末で合意"], ["", "　　⇒議事録で確認"]], "右だけの行も続き"
    assert len([c for c in cands["作業体制"] if c.rows == [["　・PM：山田、PL：佐藤"]]]) == 1


def test_item_values_newest_first(reports_dir):
    reps = cases.import_folder(reports_dir)["reports"]
    assert cases.item_values(reps, "customer") == ["架空市民病院", "架空総合病院"]
    assert cases.phases(reps)


def test_save_and_reload(reports_dir, tmp_path):
    data = cases.import_folder(reports_dir)
    cases.save_cases(data, tmp_path / "data" / "cases.json")
    assert cases.load_cases(tmp_path / "data" / "cases.json") == data


def test_phase_and_date_parsing():
    assert cases.phase_of("2026年1月 工程承認会議（構築工程）") == "構築"
    assert cases.phase_of("工程承認会議(テスト)") == "テスト"
    assert cases.phase_of("20XX年XX月 工程承認会議（○○工程）") == ""
    assert cases.date_of("2026年１月５日") == "2026-01-05" and cases.date_of("2026/10/9") == "2026-10-09"
