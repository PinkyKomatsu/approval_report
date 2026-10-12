"""代表文の選定・採用率と閾値判定・ユーザールールの優先・プレースホルダー。"""
from conftest import key_of

from core import judgment as jd
from core.models import REQUIRED, REVIEW, UNNEEDED, Case, Rule
from core.placeholders import Extractor, specs_from, substitute
from core.representative import Entry, choose


# ---- 代表文の選定 ----
def _choose(texts, cfg, weights=None):
    ex = Extractor(specs_from(cfg))
    entries = [Entry(t, i, f"事例{i}", 2020 + i, (weights or {}).get(i, 1.0)) for i, t in enumerate(texts)]
    return choose(entries, ex, "report", specs_from(cfg), cfg)


def test_most_frequent_text_is_recommended_ignoring_values(cfg):
    rep, others = _choose(["移行リハーサルを2023/06/10に実施した。", "移行リハーサルを2025/03/02に実施した。",
                           "移行リハーサルを2回実施し、課題を洗い出した。"], cfg)
    assert rep.template == "移行リハーサルを{{日付}}に実施した。" and rep.count == 2, "日付の違いは同じ記載とみなす"
    assert rep.cases == ["事例0", "事例1"] and rep.label() == "事例0（2020年度）、事例1（2021年度）"
    assert [o.template for o in others] == ["移行リハーサルを{{件数}}実施し、課題を洗い出した。"]


def test_central_text_wins_when_frequency_ties(cfg):
    rep, _ = _choose(["点数マスタの移行を完了した。", "点数マスタと薬剤マスタの移行を完了した。", "連携仕様を協議中。"], cfg)
    assert rep.template == "点数マスタの移行を完了した。", "他の記載と似ている中心的な文"


def test_preferred_cases_weigh_more(cfg):
    rep, _ = _choose(["定例会を毎週実施した。", "定例会を隔週で実施した。", "定例会を隔週で実施した。"], cfg)
    assert rep.template == "定例会を隔週で実施した。"
    rep, _ = _choose(["定例会を毎週実施した。", "定例会を隔週で実施した。", "定例会を隔週で実施した。"], cfg, {0: 3.0})
    assert rep.template == "定例会を毎週実施した。", "優先ルールで重みを上げた事例の記載"


def test_others_max_five(cfg):
    _rep, others = _choose([f"作業{i}を実施した。" + "あ" * i for i in range(10)], cfg)
    assert len(others) == 5


# ---- 採用率・閾値 ----
def test_adoption_rate_and_thresholds(cfg):
    assert jd.stat_decision(0.7, cfg) == REQUIRED and jd.stat_decision(0.69, cfg) == REVIEW
    assert jd.stat_decision(0.3, cfg) == REVIEW and jd.stat_decision(0.29, cfg) == UNNEEDED
    assert jd.stat_decision(None, cfg) == REVIEW


def test_necessity_from_past_cases(advisor, db):
    res = {r.name: r for r in advisor.search("PACS連携テスト")}
    n = res["PACS連携テスト"].necessity
    assert (n.total, len(n.adopted), round(n.rate, 2), n.decision) == (3, 2, 0.67, REVIEW)
    assert res["PACS連携テスト"].reason_ja == "過去3件中2件（67%）で転記。"
    assert res["PACS連携テスト"].reason_en == "Included in 2 of 3 past projects (67%)."


def test_user_rule_overrides_statistics(advisor, db):
    db.save_rule(Rule(None, "PACS連携あり", conditions=[{"attr": "systems", "op": "含む", "value": "PACS"}],
                      decision=REQUIRED, targets=[key_of(db, "PACS連携テスト")], priority=10))
    db.save_rule(Rule(None, "優先度の低いルール", conditions=[], decision=UNNEEDED, keyword="PACS", priority=50))
    r = next(x for x in advisor.search("PACS連携テスト", {"systems": ["PACS", "電子カルテ"]}) if x.name == "PACS連携テスト")
    assert r.necessity.decision == REQUIRED and r.necessity.stat == REVIEW, "ルールは統計より優先"
    assert r.necessity.rule.name == "PACS連携あり", "優先順位の小さいルールを使う"
    assert "条件『PACS連携あり』に一致。" in r.reason_ja
    r2 = next(x for x in advisor.search("PACS連携テスト", {"systems": ["電子カルテ"]}) if x.name == "PACS連携テスト")
    assert r2.necessity.rule.name == "優先度の低いルール" and r2.necessity.decision == UNNEEDED
    db.save_rule(Rule(None, "無効なルール", enabled=False, decision=REQUIRED, keyword="PACS", priority=1))
    r3 = next(x for x in advisor.search("PACS連携テスト", {"systems": ["PACS"]}) if x.name == "PACS連携テスト")
    assert r3.necessity.rule.name == "PACS連携あり", "無効なルールは使わない"


def test_prefer_rule_changes_recommended_text(advisor, db):
    def rep():
        r = next(x for x in advisor.search("移行リハーサル") if x.name == "移行リハーサル")
        return r
    assert rep().fields["report"].rep.template.startswith("移行リハーサルを{{日付}}に実施し")
    db.save_rule(Rule(None, "架空メディカルの事例を優先", kind="prefer",
                      prefer={"attr": "vendor", "op": "＝", "value": "架空メディカル"}, weight=5.0))
    r = rep()
    assert r.fields["report"].rep.template == "移行リハーサルを{{件数}}実施し、課題を洗い出した。"
    assert "ルール『架空メディカルの事例を優先』により優先した事例あり。" in r.reason_ja


def test_prefer_weights():
    cases = [Case(1, "A", attrs={"vendor": "X"}), Case(2, "B", attrs={"vendor": "Y"})]
    w, used = jd.prefer_weights(cases, [Rule(None, "Xを優先", kind="prefer", prefer={"attr": "vendor", "op": "＝", "value": "X"},
                                             weight=2.0)])
    assert w == {1: 2.0, 2: 1.0} and [r.name for r in used[1]] == ["Xを優先"]


# ---- プレースホルダー ----
def test_placeholder_extraction(cfg):
    ex = Extractor(specs_from(cfg), vendors=["架空システムズ"])
    t = ex.extract("架空総合病院の点数マスタ1,200件を2025年6月10日までに移行し、架空システムズと6/20に確認した。", "report")
    assert t.template == "{{病院名}}の点数マスタ{{件数}}を{{日付}}までに移行し、{{ベンダー}}と{{日付2}}に確認した。"
    assert t.values == {"病院名": "架空総合病院", "件数": "1,200件", "日付": "2025年6月10日", "ベンダー": "架空システムズ",
                        "日付2": "6/20"}
    same = ex.extract("2025/06/10 と 2025/06/10 に実施", "report")
    assert same.template == "{{日付}} と {{日付}} に実施", "同じ値は同じプレースホルダー"
    person = ex.extract("担当は山田さん。", "report")
    assert person.template == "担当は{{担当者}}。"
    assert ex.extract("佐藤", "owner").template in ("{{担当者}}", "佐藤")


def test_placeholder_substitution(cfg):
    tpl = "{{病院名}}で{{日付}}に実施。{{日付2}}に再確認。"
    assert substitute(tpl, {"病院名": "架空中央病院", "日付": "2026/11/20"}) == "架空中央病院で2026/11/20に実施。{{日付2}}に再確認。"
    assert substitute(tpl, {}) == tpl, "未入力のプレースホルダーは残す"


def test_render_substitutes_both_languages(advisor):
    r = next(x for x in advisor.search("移行リハーサル") if x.name == "移行リハーサル")
    ja, en, _un = advisor.render(r.fields["report"].rep, {"日付": "2026/11/20"})
    assert ja == "移行リハーサルを2026/11/20に実施し、移行手順を確認した。"
    assert en == "The migration rehearsal was carried out on 2026/11/20, and the migration procedure was confirmed.", \
        "過去事例の英語の列（対訳）を優先し、同じ値をプレースホルダーにして差し替える"


def test_my_phrase_is_offered(advisor, db):
    key = key_of(db, "移行リハーサル")
    db.add_phrase(key, "report", "移行リハーサルを{{日付}}に実施予定。", "")
    advisor.build()
    r = next(x for x in advisor.search("移行リハーサル") if x.name == "移行リハーサル")
    first = r.fields["report"].others[0]
    assert first.source == "phrase" and first.label() == "自分の定型文"
