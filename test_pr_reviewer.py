import pytest

import pr_reviewer as pr


def test_parse_url():
    assert pr.parse_pr_url("https://github.com/a/b/pull/12") == ("a", "b", 12)


def test_parse_url_bad():
    with pytest.raises(SystemExit):
        pr.parse_pr_url("https://example.com/x")


def good():
    return {
        "summary": "s", "risk": "low", "risk_reason": "r",
        "ai_generated_percent": 150, "ai_generated_reason": "x",
        "issues": [{"file": "a.py", "severity": "weird", "description": "d", "suggestion": "s"}],
    }


def test_validate_clamps_and_fixes():
    r = pr.validate(good())
    assert r["ai_generated_percent"] == 100
    assert r["issues"][0]["severity"] == "low"


def test_validate_missing_field():
    r = good()
    del r["risk"]
    with pytest.raises(ValueError):
        pr.validate(r)


def test_gemini_schema_uppercases_types():
    s = pr.to_gemini_schema(pr.SCHEMA)
    assert s["type"] == "OBJECT" and s["properties"]["issues"]["items"]["type"] == "OBJECT"


def test_detect_provider():
    assert pr.detect_provider("AIzaXYZ") == "gemini"
    assert pr.detect_provider("gsk_abc") == "groq"
    assert pr.detect_provider("sk-or-v1-abc") == "openrouter"
    assert pr.detect_provider("sk-ant-abc") == "anthropic"
    assert pr.detect_provider("sk-abc") == "openai"
    assert pr.detect_provider("weird") is None


def test_extract_json_from_chatty_output():
    assert pr.extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
