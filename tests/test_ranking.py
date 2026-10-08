from app import cases, ranking
from app.cases import Candidate


def test_topic_similarity_ranks_first(reports_dir):
    cands = cases.build_candidates(cases.import_folder(reports_dir)["reports"])["進捗【医事】"]
    top = ranking.rank(cands, "自動精算機IF 未合意、WBS 45/50完了", "構築")
    texts = [r.cand.text for r in top]
    assert "45/50" in texts[0], "トピックに最も近い候補が 1 位"
    auto = next(i for i, t in enumerate(texts) if "IF仕様が未合意" in t)
    assert auto < 3 and auto < next(i for i, t in enumerate(texts) if "帳票" in t), "トピックに近い候補が上位"


def test_cosine_of_bigrams():
    assert ranking.cosine(ranking.bigrams("WBS進捗"), ranking.bigrams("　・WBS進捗")) == 1.0, "記号・空白は無視"
    assert ranking.cosine(ranking.bigrams("あいう"), ranking.bigrams("かきく")) == 0.0


def test_phase_count_and_recency_bonuses():
    a = Candidate([["　・A"]], count=1, phases=["設計"], dates=["2025-01-01"])
    b = Candidate([["　・B"]], count=1, phases=["構築"], dates=["2025-01-01"])
    assert ranking.rank([a, b], "", "構築")[0].cand is b, "同じ工程に加点"
    c = Candidate([["　・C"]], count=5, phases=["設計"], dates=["2025-01-01"])
    assert ranking.rank([a, c], "", "")[0].cand is c, "出現回数の多い定型文が上位"
    d = Candidate([["　・D"]], count=1, phases=["設計"], dates=["2026-01-01"])
    assert ranking.rank([a, d], "", "")[0].cand is d, "新しい報告書ほど上位"


def test_similarity_outweighs_bonuses():
    near = Candidate([["　・自動精算機IFの仕様未確定"]], count=1, phases=["設計"], dates=["2025-01-01"])
    stock = Candidate([["　・定例会は毎週実施"]], count=9, phases=["構築"], dates=["2026-01-01"])
    assert ranking.rank([stock, near], "自動精算機IF 仕様未確定", "構築")[0].cand is near
