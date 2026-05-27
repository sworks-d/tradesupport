"""json_extract：LLM 応答の頑健 JSON 抽出。"""

from __future__ import annotations

from trading_agent.llm.json_extract import extract_json


def test_plain_json_passes_through() -> None:
    assert extract_json('{"importance": "high"}') == {"importance": "high"}


def test_markdown_fenced_json_is_extracted() -> None:
    text = '```json\n{"importance": "high"}\n```'
    assert extract_json(text) == {"importance": "high"}


def test_plain_code_fence_without_lang_tag() -> None:
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_json_embedded_in_prose() -> None:
    text = 'Sure, here is my answer: {"verdict": "buy", "score": 0.8} based on the data.'
    assert extract_json(text) == {"verdict": "buy", "score": 0.8}


def test_haiku_style_response_with_multiline_reasoning() -> None:
    # Haiku が実際に返してくる形式（topics_collector の prompt に対する応答）
    text = """```json
{
  "importance": "high",
  "reasoning": "重要度が高い理由をここに記載"
}
```"""
    result = extract_json(text)
    assert result["importance"] == "high"
    assert "重要度" in result["reasoning"]


def test_none_returns_empty_dict() -> None:
    assert extract_json(None) == {}


def test_empty_string_returns_empty_dict() -> None:
    assert extract_json("") == {}


def test_unparseable_text_returns_empty_dict() -> None:
    assert extract_json("This is not JSON at all.") == {}


def test_list_at_root_returns_empty_dict() -> None:
    # extract_json は dict 専用。リストが返ってきたら空 dict（型安全）
    assert extract_json('[1, 2, 3]') == {}


def test_nested_object_preserved() -> None:
    text = '```json\n{"outer": {"inner": {"deep": "value"}}}\n```'
    assert extract_json(text) == {"outer": {"inner": {"deep": "value"}}}


def test_japanese_and_special_chars_preserved() -> None:
    text = '{"narrative": "AI 半導体・受注残更新"}'
    assert extract_json(text) == {"narrative": "AI 半導体・受注残更新"}


def test_fence_with_extra_whitespace() -> None:
    text = '```json   \n  {"k": 1}  \n  ```   '
    assert extract_json(text) == {"k": 1}
