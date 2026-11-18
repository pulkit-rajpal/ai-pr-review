import pytest

from reviewer import cli, core, diffutil, render, scanner
from reviewer.config import instructions_for, parse_config
from reviewer.github import GitHubError
from reviewer.llm import LLM, LLMError, detect_provider, extract_json, to_gemini_schema
from reviewer.schema import SCHEMA, validate

DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,5 @@
 import os
-x = 1
+x = 2
+KEY = "AKIAABCDEFGHIJKLMNOP"
 print(x)
+eval(input())
diff --git a/package-lock.json b/package-lock.json
index 1..2 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,1 +1,1 @@
-a
+b
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1,1 +0,0 @@
-gone
"""


# ---------- diff parsing ----------
def test_parse_diff_line_numbers():
    files = diffutil.parse_diff(DIFF)
    app = files[0]
    assert app.path == "app.py"
    assert app.added == [(2, "x = 2"), (3, 'KEY = "AKIAABCDEFGHIJKLMNOP"'), (5, "eval(input())")]
    assert app.valid_lines == {1, 2, 3, 4, 5}
    assert files[2].status == "deleted"


def test_filter_ignored():
    files = diffutil.filter_files(diffutil.parse_diff(DIFF), ["old.*"])
    assert [f.path for f in files] == ["app.py"]


def test_render_diff_budget_skips_files():
    files = diffutil.filter_files(diffutil.parse_diff(DIFF))
    text, shown, skipped = diffutil.render_diff(files, 10)
    assert "app.py" in skipped and text == ""
    text, shown, _ = diffutil.render_diff(files, 5000)
    assert "    3 + KEY" in text and "app.py" in shown


# ---------- scanner ----------
def test_scanner_flags_secret_without_leaking_it():
    found = scanner.scan(diffutil.parse_diff(DIFF))
    assert {(f["line"], f["severity"]) for f in found} == {(3, "high"), (5, "medium")}
    assert all("AKIA" not in f["description"] for f in found)


def test_scanner_ignores_placeholders():
    d = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+password = "your_password_here_123"\n'
    assert scanner.scan(diffutil.parse_diff(d)) == []


# ---------- schema validation ----------
def good():
    return {
        "summary": "s", "risk": "LOW", "risk_reason": "r", "ai_generated_percent": 150,
        "ai_generated_reason": "x",
        "walkthrough": [{"file": "a.py", "change": "c"}],
        "sequence_diagram": "```mermaid\nsequenceDiagram\nA->>B: hi\n```",
        "issues": [
            {"file": "a.py", "line": 3, "severity": "weird", "description": "d", "suggestion": "s",
             "suggested_code": "x = 1"},
            {"file": "a.py", "line": 99, "severity": "high", "description": "bad line", "suggestion": "s",
             "suggested_code": "y"},
        ],
    }


def test_validate_normalises():
    r = validate(good(), {"a.py": {1, 2, 3}})
    assert r["risk"] == "low" and r["ai_generated_percent"] == 100
    assert r["sequence_diagram"].startswith("sequenceDiagram")
    first, second = r["issues"]
    assert first["severity"] == "low" and first["line"] == 3 and first["suggested_code"] == "x = 1"
    assert second["line"] is None and second["suggested_code"] is None  # line not in diff


def test_validate_drops_bad_diagram_and_fenced_suggestion():
    g = good()
    g["sequence_diagram"] = "graph TD; A-->B"
    g["issues"][0]["suggested_code"] = "```x```"
    r = validate(g)
    assert r["sequence_diagram"] == "" and r["issues"][0]["suggested_code"] is None


def test_validate_missing_field():
    g = good()
    del g["risk"]
    with pytest.raises(ValueError):
        validate(g)


# ---------- llm helpers ----------
def test_detect_provider():
    assert detect_provider("AIzaXYZ") == "gemini"
    assert detect_provider("gsk_abc") == "groq"
    assert detect_provider("sk-or-v1-abc") == "openrouter"
    assert detect_provider("sk-ant-abc") == "anthropic"
    assert detect_provider("sk-abc") == "openai"
    assert detect_provider("weird") is None


def test_llm_needs_provider():
    with pytest.raises(LLMError):
        LLM(None, "weird")
    with pytest.raises(LLMError):
        LLM("custom", None)  # needs base_url + model
    assert LLM("custom", None, "m", "http://localhost:11434/v1").model == "m"


def test_extract_json_and_gemini_schema():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    s = to_gemini_schema(SCHEMA)
    assert s["type"] == "OBJECT" and s["properties"]["issues"]["items"]["type"] == "OBJECT"


def test_llm_json_retries_once_on_bad_output(monkeypatch):
    llm = LLM("custom", None, "m", "http://x")
    replies = iter(["not json at all", '{"ok": true}'])
    monkeypatch.setattr(llm, "_call", lambda p, s: next(replies))
    assert llm.json("p", {}) == {"ok": True}


# ---------- config ----------
def test_config_parse_and_path_instructions():
    cfg = parse_config("min_severity: medium\npath_instructions:\n  - {path: 'src/auth/*', instructions: strict}\n")
    assert cfg["min_severity"] == "medium" and cfg["max_inline_comments"] == 15
    assert instructions_for(cfg, ["src/auth/login.py"]) == ["For files matching `src/auth/*`: strict"]
    assert instructions_for(cfg, ["README.md"]) == []
    with pytest.raises(ValueError):
        parse_config("min_severity: nope")


# ---------- commands / urls ----------
def test_parse_command():
    p = core.parse_command
    assert p("hello") is None
    assert p("@pr-reviewer") == ("help", "")
    assert p("@pr-reviewer review") == ("review", "")
    assert p("@pr-reviewer tests for the parser") == ("tests", "for the parser")
    assert p("@pr-reviewer ask why?") == ("ask", "why?")
    assert p("@pr-reviewer why is this needed?") == ("ask", "why is this needed?")


def test_parse_pr_ref():
    assert cli.parse_pr_ref("https://github.com/a/b/pull/12") == ("a", "b", 12)
    assert cli.parse_pr_ref("a/b#12") == ("a", "b", 12)
    with pytest.raises(SystemExit):
        cli.parse_pr_ref("https://example.com/x")


# ---------- end to end with fakes ----------
class FakeGH:
    def __init__(self, comments=None):
        self.comments = comments or []
        self.created, self.updated, self.reviews, self.compared = [], [], [], []

    def pr(self, o, r, n):
        return {"head": {"sha": "b" * 40}, "base": {"ref": "main"}, "title": "T", "body": "Fixes #7",
                "html_url": "https://github.com/o/r/pull/1"}

    def diff(self, o, r, n):
        return DIFF

    def compare_diff(self, o, r, a, b):
        self.compared.append((a, b))
        return DIFF

    def get_file(self, o, r, path, ref):
        return "x = 2\n" if path == "app.py" else None

    def issue(self, o, r, n):
        return {"title": "Add feature", "body": "details"}

    def issue_comments(self, o, r, n):
        return self.comments

    def review_comments(self, o, r, n):
        return []

    def create_comment(self, o, r, n, body):
        self.created.append(body)

    def update_comment(self, o, r, cid, body):
        self.updated.append((cid, body))

    def create_review(self, o, r, n, sha, comments):
        self.reviews.append(comments)


class FakeLLM:
    provider, model, diff_limit = "fake", "m", 60_000

    def __init__(self):
        self.prompts = []

    def json(self, prompt, schema):
        self.prompts.append(prompt)
        g = good()
        g["risk"] = "low"
        g["issues"] = [
            {"file": "app.py", "line": 2, "severity": "medium", "description": "x changed", "suggestion": "s",
             "suggested_code": "x = 3"},
            {"file": "app.py", "line": 99, "severity": "low", "description": "general", "suggestion": "s"},
        ]
        return g

    def text(self, prompt):
        self.prompts.append(prompt)
        return "an answer"


def test_review_posts_inline_and_summary():
    gh, llm = FakeGH(), FakeLLM()
    res = core.review_pr(gh, llm, "o", "r", 1, post=True)
    r = res.review
    assert r["risk"] == "high"  # raised by static secret finding
    prompt = llm.prompts[0]
    assert "Add feature" in prompt and "Full current contents" in prompt and "AKIA" in prompt  # issue + context
    assert "package-lock" not in prompt
    inline = gh.reviews[0]
    assert {c["line"] for c in inline} == {2, 3, 5}  # ai(2) + static(3, 5); line 99 has no valid line
    assert any("```suggestion\nx = 3\n```" in c["body"] for c in inline)
    body = gh.created[0]
    assert render.extract_sha(body) == "b" * 40 and "general" in body and "3 inline comment" in body


def test_second_run_same_sha_is_skipped_and_new_sha_is_incremental():
    old = [{"id": 5, "body": render.summary_marker("b" * 40)}]
    assert core.review_pr(FakeGH(old), FakeLLM(), "o", "r", 1, post=True) is None

    old = [{"id": 5, "body": render.summary_marker("a" * 40)}]
    gh = FakeGH(old)
    res = core.review_pr(gh, FakeLLM(), "o", "r", 1, post=True)
    assert gh.compared == [("a" * 40, "b" * 40)] and res.since_sha == "a" * 40
    assert gh.updated and gh.updated[0][0] == 5 and not gh.created  # summary updated in place


def test_inline_failure_falls_back_to_summary():
    class Boom(FakeGH):
        def create_review(self, *a):
            raise GitHubError(422, "bad line")

    gh = Boom()
    core.review_pr(gh, FakeLLM(), "o", "r", 1, post=True)
    assert "inline comment(s) posted" not in gh.created[0] and "x changed" in gh.created[0]


def test_no_post_means_no_writes():
    gh = FakeGH()
    core.review_pr(gh, FakeLLM(), "o", "r", 1, post=False)
    assert not (gh.created or gh.reviews or gh.updated)


def test_answer_pr_posts_reply():
    gh = FakeGH()
    assert core.answer_pr(gh, FakeLLM(), "o", "r", 1, "ask", "why?", post=True) == "an answer"
    assert gh.created[0].startswith(render.REPLY_MARKER) and "> why?" in gh.created[0]


def test_markdown_escapes_table_cells():
    res = render.Result(review=validate(good()), url="u", head_sha="c" * 40, provider="p", model="m")
    res.review["walkthrough"] = [{"file": "a|b.py", "change": "x | y\nz"}]
    assert "a\\|b.py" in render.markdown(res) and "x \\| y z" in render.markdown(res)
