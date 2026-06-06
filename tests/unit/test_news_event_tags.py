"""derive_news_event_tags（wille/news_keywords.py）の単体テスト。

取得済 news/開示の見出しを classify_headline で +/−/0 分類し、銘柄単位の純インパクトで
news_positive / news_negative を立てる（record-only・A・¥0・LLM 不使用）。純中立・同点・材料なしは
推測せず無タグ（H10）。
"""

from __future__ import annotations

from trading_agent.wille.news_keywords import derive_news_event_tags


def test_positive_dominant_returns_news_positive() -> None:
    arts = [{"title": "通期業績を上方修正 増配も発表"}, {"title": "大型受注獲得"}]
    tags, evidence = derive_news_event_tags(arts)
    assert tags == ["news_positive"]
    ev = evidence["news_positive"]
    assert ev["pos"] == 2 and ev["neg"] == 0
    assert ev["sample_headlines"]  # 証拠の見出しサンプルが残る
    assert "業績" in ev["categories"]


def test_negative_dominant_from_disclosures() -> None:
    disc = [{"title": "リコール実施のお知らせ"}, {"title": "下方修正に関するお知らせ"}]
    tags, evidence = derive_news_event_tags(None, disc)
    assert tags == ["news_negative"]
    assert evidence["news_negative"]["neg"] == 2


def test_tie_returns_no_tag() -> None:
    """同点（pos==neg>0）は方向性不明 → 推測しない（H10）。"""
    mixed = [{"title": "上方修正"}, {"title": "下方修正"}]
    assert derive_news_event_tags(mixed) == ([], {})


def test_no_material_returns_no_tag() -> None:
    """辞書ヒットなし（材料なし）は無タグ。"""
    assert derive_news_event_tags([{"title": "本日の相場雑感"}]) == ([], {})


def test_empty_inputs_return_empty() -> None:
    assert derive_news_event_tags(None, None) == ([], {})
    assert derive_news_event_tags([], []) == ([], {})


def test_robust_against_non_dict_items() -> None:
    """非 dict 混入でも落ちず、有効な見出しだけ数える。"""
    tags, evidence = derive_news_event_tags([{"title": "増配"}, "ゴミ", None, 123])
    assert tags == ["news_positive"]
    assert evidence["news_positive"]["pos"] == 1


def test_news_and_disclosure_combined() -> None:
    """news と開示を合算して純インパクトを取る。"""
    arts = [{"title": "業務提携を発表"}]  # +
    disc = [{"title": "新薬のFDA承認取得"}]  # +（材料）
    tags, _ = derive_news_event_tags(arts, disc)
    assert tags == ["news_positive"]


def test_summary_fallback_when_no_title() -> None:
    """title 欠落時は summary を見出しとして使う。"""
    tags, _ = derive_news_event_tags([{"summary": "自社株買いを決議"}])
    assert tags == ["news_positive"]


def test_scanned_cap_recorded() -> None:
    """scanned は走査件数（上限 30）を記録する。"""
    arts = [{"title": "増配"}] * 50
    _, evidence = derive_news_event_tags(arts)
    assert evidence["news_positive"]["scanned"] == 30
