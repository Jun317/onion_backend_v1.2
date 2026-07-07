from engine.embed import FakeEmbedder
from engine.evaluate import evaluate, pair_similarities, sweep


def test_sweep_precision_recall():
    # 유사도 [0.9(same), 0.5(diff), 0.85(same)] → tau 0.8 이면 same 둘 다 잡고 diff 배제
    sims = [0.9, 0.5, 0.85]
    labels = [True, False, True]
    rows = sweep(sims, labels, 0.80, 0.80, 0.02)
    r = rows[0]
    assert r["tp"] == 2 and r["fp"] == 0 and r["fn"] == 0
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_pair_similarities_same_higher_than_diff():
    pairs = [{"a": "한국은행 기준금리 인하", "b": "한국은행 기준금리 인하 결정", "same": True},
             {"a": "한국은행 기준금리 인하", "b": "삼성전자 신제품 공개", "same": False}]
    sims = pair_similarities(pairs, FakeEmbedder())
    assert sims[0] > sims[1]   # 같은 사건 쌍이 더 유사


def test_evaluate_on_shipped_goldset_fake():
    res = evaluate(fake=True)
    assert res["n_pairs"] >= 8 and res["n_same"] >= 1
    assert "tau" in res["best"] and 0.0 <= res["best"]["f1"] <= 1.0
