#!/usr/bin/env python3
"""AI-powered GitHub PR reviewer.

Fetches a pull request, asks an LLM to review it, and prints:
  * overall risk label (high / medium / low)
  * estimated share of AI-generated code
  * per-issue findings
The model is forced to return JSON matching a fixed schema, which is validated
before printing, so output parsing is deterministic.

Default provider: Google Gemini free tier (no credit card).
Optional provider: Anthropic Claude (paid).
"""
import argparse
import json
import os
import re
import sys

import requests

MAX_DIFF_CHARS = 60_000  # keeps requests inside free-tier limits

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "risk": {"type": "string", "enum": ["high", "medium", "low"]},
        "risk_reason": {"type": "string"},
        "ai_generated_percent": {"type": "integer"},
        "ai_generated_reason": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "description": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["file", "severity", "description", "suggestion"],
            },
        },
    },
    "required": [
        "summary", "risk", "risk_reason",
        "ai_generated_percent", "ai_generated_reason", "issues",
    ],
}

PROMPT = """You are a senior engineer reviewing a GitHub pull request.

Return a review as JSON matching the provided schema.
- risk: overall merge risk. high = likely bugs, security problems, data loss, or
  breaking changes; medium = notable concerns; low = safe/minor.
- ai_generated_percent: your best estimate (0-100) of the share of ADDED code that
  looks AI-generated (boilerplate style, generic comments, uniform structure,
  over-explained trivia). This is a heuristic guess; say why in ai_generated_reason.
- issues: concrete problems only, referencing the file. Empty list if none.

PR title: {title}
PR description: {body}

Diff:
{diff}
"""


def parse_pr_url(url):
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.strip())
    if not m:
        sys.exit("Error: expected a URL like https://github.com/OWNER/REPO/pull/123")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_pr(owner, repo, number):
    headers = {"User-Agent": "pr-reviewer"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    base = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    meta = requests.get(base, headers={**headers, "Accept": "application/vnd.github+json"}, timeout=30)
    if meta.status_code in (401, 403, 404):
        sys.exit(f"GitHub error {meta.status_code}: private repo or rate limit? Set GITHUB_TOKEN. {meta.text[:200]}")
    meta.raise_for_status()
    diff = requests.get(base, headers={**headers, "Accept": "application/vnd.github.v3.diff"}, timeout=30)
    diff.raise_for_status()
    info = meta.json()
    return info.get("title", ""), info.get("body") or "(none)", diff.text


def call_gemini(prompt, key):
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": to_gemini_schema(SCHEMA),
            "temperature": 0,
        },
    }
    r = requests.post(url, params={"key": key}, json=body, timeout=120)
    if r.status_code != 200:
        sys.exit(f"Gemini error {r.status_code}: {r.text[:300]}")
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])


def to_gemini_schema(s):
    """Gemini wants upper-case type names."""
    if isinstance(s, dict):
        return {k: (v.upper() if k == "type" else to_gemini_schema(v)) for k, v in s.items()}
    if isinstance(s, list):
        return [to_gemini_schema(x) for x in s]
    return s


def call_anthropic(prompt, key):
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    body = {
        "model": model,
        "max_tokens": 4096,
        "tools": [{"name": "submit_review", "description": "Submit the PR review", "input_schema": SCHEMA}],
        "tool_choice": {"type": "tool", "name": "submit_review"},
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        json=body, timeout=120,
    )
    if r.status_code != 200:
        sys.exit(f"Anthropic error {r.status_code}: {r.text[:300]}")
    for block in r.json()["content"]:
        if block["type"] == "tool_use":
            return block["input"]
    sys.exit("Anthropic returned no structured review")


PROVIDERS = {
    # name: (base_url, default_model, supports_json_mode, max_diff_chars)
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", True, 15_000),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", False, 30_000),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", True, 60_000),
}


def detect_provider(key):
    """Guess the provider from the key's prefix."""
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("gsk_"):
        return "groq"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("sk-"):
        return "openai"
    return None


def extract_json(text):
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def call_openai_compat(prompt, key, base_url, model, json_mode):
    """Works with Groq, OpenRouter, OpenAI, Ollama, LM Studio, or any OpenAI-style API."""
    full = prompt + "\n\nRespond with ONLY a JSON object matching this JSON schema:\n" + json.dumps(SCHEMA)
    body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": full}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180,
    )
    if r.status_code != 200:
        sys.exit(f"LLM API error {r.status_code}: {r.text[:300]}")
    return extract_json(r.json()["choices"][0]["message"]["content"])


def validate(review):
    """Strict check so downstream code can trust the shape."""
    for k in SCHEMA["required"]:
        if k not in review:
            raise ValueError(f"missing field: {k}")
    if review["risk"] not in ("high", "medium", "low"):
        raise ValueError("bad risk label")
    review["ai_generated_percent"] = max(0, min(100, int(review["ai_generated_percent"])))
    for i in review["issues"]:
        if i.get("severity") not in ("high", "medium", "low"):
            i["severity"] = "low"
    return review


def render(review, url):
    colors = {"high": "\033[91m", "medium": "\033[93m", "low": "\033[92m"}
    c = colors[review["risk"]] if sys.stdout.isatty() else ""
    end = "\033[0m" if c else ""
    print(f"\nPR: {url}")
    print(f"Risk: {c}{review['risk'].upper()}{end} - {review['risk_reason']}")
    print(f"AI-generated code (estimate): ~{review['ai_generated_percent']}% - {review['ai_generated_reason']}")
    print(f"\nSummary:\n  {review['summary']}")
    if review["issues"]:
        print(f"\nIssues ({len(review['issues'])}):")
        for i in review["issues"]:
            print(f"  [{i['severity'].upper()}] {i['file']}\n    {i['description']}\n    Fix: {i['suggestion']}")
    else:
        print("\nNo issues found.")


def main():
    p = argparse.ArgumentParser(description="Review a GitHub PR with any LLM. Just provide an API key.")
    p.add_argument("pr_url", help="https://github.com/OWNER/REPO/pull/NUMBER")
    p.add_argument("--api-key", help="LLM API key (or set LLM_API_KEY). Provider is auto-detected.")
    p.add_argument("--provider", choices=["gemini", "groq", "openrouter", "openai", "anthropic", "custom"],
                   help="override auto-detection")
    p.add_argument("--model", help="override the default model")
    p.add_argument("--base-url", help="OpenAI-compatible endpoint (use with --provider custom, e.g. Ollama)")
    p.add_argument("--json", action="store_true", help="print raw JSON instead of formatted text")
    args = p.parse_args()

    key = args.api_key or os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY") \
        or os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_API_KEY") \
        or os.getenv("OPENAI_API_KEY")
    if not key and args.provider != "custom":
        sys.exit("No API key. Pass --api-key KEY or set LLM_API_KEY. Free keys: Gemini "
                 "(aistudio.google.com/apikey), Groq (console.groq.com/keys), OpenRouter (openrouter.ai/keys).")
    key = key or "none"
    provider = args.provider or detect_provider(key)
    if not provider:
        sys.exit("Could not detect provider from key. Add --provider (gemini|groq|openrouter|openai|anthropic|custom).")

    owner, repo, num = parse_pr_url(args.pr_url)
    title, body, diff = fetch_pr(owner, repo, num)

    limit = MAX_DIFF_CHARS if provider in ("gemini", "anthropic") else PROVIDERS.get(provider, (0, 0, 0, 15_000))[3]
    if len(diff) > limit:
        print(f"Note: diff truncated to {limit} chars to fit the model's limits.", file=sys.stderr)
        diff = diff[:limit]
    prompt = PROMPT.format(title=title, body=body[:3000], diff=diff)

    if provider == "gemini":
        if args.model:
            os.environ["GEMINI_MODEL"] = args.model
        raw = call_gemini(prompt, key)
    elif provider == "anthropic":
        if args.model:
            os.environ["ANTHROPIC_MODEL"] = args.model
        raw = call_anthropic(prompt, key)
    else:
        if provider == "custom":
            if not (args.base_url and args.model):
                sys.exit("--provider custom needs --base-url and --model")
            base, model, jm = args.base_url, args.model, False
        else:
            base, model, jm, _ = PROVIDERS[provider]
            model = args.model or model
        raw = call_openai_compat(prompt, key, base, model, jm)

    try:
        review = validate(raw)
    except (ValueError, KeyError, TypeError) as e:
        sys.exit(f"Model returned an invalid review ({e}). Try again or use a stronger --model.")

    if args.json:
        print(json.dumps(review, indent=2))
    else:
        render(review, args.pr_url)


if __name__ == "__main__":
    main()
