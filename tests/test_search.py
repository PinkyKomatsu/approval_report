"""トピック検索の順位付け・照合・属性フィルター・性能。"""
import time

from conftest import key_of

from core import workflow
from core.advisor import FILTER_ONLY, FILTER_WEIGHT, Advisor
from core.matching import Matcher
from core.models import CaseRow, MasterItem
from core.normalize import norm
from core.search import SearchIndex


def names(results):
    return [r.name for r in results]


def test_topic_ranks_related_items_first(advisor):
    res = advisor.search("マスタ移行")
    top = names(res[:4])
    assert set(top) == {"マスタ移行", "点数マスタ移行", "薬剤マスタ移行", "移行リハーサル"}, top
    assert "PACS連携テスト" not in top


def test_full_text_hits_other_columns(advisor):
    """作業項目名にない語でも、報告内容・課題などの欄の文から見つける。"""
    res = advisor.search("差異")
    assert names(res)[0] in ("点数マスタ移行", "移行リハーサル")
    res = advisor.search("画像の連携")
    assert names(res)[0] == "PACS連携テスト"


def test_item_name_similarity_and_phase(advisor):
    assert names(advisor.search("連携テスト"))[:2] == ["電子カルテ連携テスト", "PACS連携テスト"] or \
        set(names(advisor.search("連携テスト"))[:2]) == {"電子カルテ連携テスト", "PACS連携テスト"}
    phase = names(advisor.search("診療報酬改定対応"))
    assert {"改定内容の確認", "点数マスタの改定反映"} <= set(phase[:4]), "工程名での検索は、その工程の作業項目"


def test_matching_priority_and_review(db, loaded):
    rows = {(r.case_id, r.name): r for r in db.case_rows()}
    b = loaded["case_B"]
    assert rows[(b, "移行リハーサル")].method == "wbs", "B は 3-3 形式の番号でも WBS 番号で一致"
    pending = rows[(b, "職員操作研修会")]
    assert pending.status == "pending" and pending.candidates[0][0] == key_of(db, "職員向け操作研修")
    assert rows[(b, "院内説明会の実施")].status == "extra"


def test_confirmed_match_is_saved_as_synonym(db, cfg, loaded):
    row = next(r for r in db.case_rows() if r.name == "職員操作研修会")
    workflow.confirm_match(db, row.id, key_of(db, "職員向け操作研修"))
    assert db.synonyms()[norm("職員操作研修会")] == key_of(db, "職員向け操作研修")
    m = Matcher(db.master_items(), db.synonyms(), cfg["match"])
    r = m.match_row(CaseRow(None, None, 0, 0, "", "教育・稼働", "職員操作研修会", norm("職員操作研修会")))
    assert (r.status, r.method) == ("auto", "synonym")


def test_attribute_filter_and_weight(advisor):
    pacs_project = {"systems": ["PACS"], "vendor": "架空システムズ", "update_type": "リプレース"}
    only = advisor.search("PACS連携テスト", pacs_project, FILTER_ONLY)
    r = next(x for x in only if x.name == "PACS連携テスト")
    assert r.necessity.total == 2 and r.necessity.rate == 1.0, "属性の近い A・C だけで採用率を出す"
    allc = next(x for x in advisor.search("PACS連携テスト") if x.name == "PACS連携テスト")
    assert allc.necessity.total == 3 and round(allc.necessity.rate, 2) == 0.67
    weighted = advisor.search("PACS連携テスト", pacs_project, FILTER_WEIGHT)
    assert weighted[0].name == "PACS連携テスト"


def test_extra_items_are_searchable(advisor):
    res = advisor.search("院内説明会")
    assert res[0].is_extra and res[0].name == "院内説明会の実施"
    assert res[0].fields["report"].rep.template == "院内説明会を実施した。"


def test_performance_50_cases_1000_rows(cfg, templates, db):
    """過去事例 50 件・各 1000 行で、検索が 1 秒以内（インデックスは起動時に作成済み）。"""
    import random
    random.seed(1)
    items = [MasterItem(f"k{i}", i, i, f"{i // 50 + 1}.{i % 50 + 1}", f"工程{i // 100}", f"作業項目{i:04d}の実施",
                        norm(f"作業項目{i:04d}の実施")) for i in range(1000)]
    words = ["マスタ", "移行", "連携", "テスト", "仕様", "確認", "調整", "帳票", "画面", "研修", "改定", "リハーサル"]
    vocab_cache = {}
    rows = []
    rid = 0
    for c in range(50):
        for i in range(1000):
            rid += 1
            text = "".join(random.sample(words, 4)) + "を実施した。"
            rows.append(CaseRow(rid, c, i, i, "", f"工程{i // 100}", items[i].name, items[i].norm,
                                texts={"report": text}, item_key=items[i].key, status="auto"))
            vocab_cache[rid] = random.sample(words, 4)          # 形態素解析の結果はキャッシュ済みとする
    t0 = time.perf_counter()
    index = SearchIndex(rows, items, vocab_cache)
    build = time.perf_counter() - t0
    adv = Advisor(db, cfg, templates)
    adv.build()
    adv.index, adv.items = index, {i.key: i for i in items}
    adv.cases = {c: type("C", (), {"id": c, "name": f"事例{c}", "attrs": {}})() for c in range(50)}
    adv.adopted = {c: {i.key for i in items} for c in range(50)}
    t0 = time.perf_counter()
    res = adv.search("マスタ移行")
    elapsed = time.perf_counter() - t0
    assert res and elapsed < 1.0, f"検索 {elapsed:.2f} 秒（インデックス作成 {build:.2f} 秒）"
