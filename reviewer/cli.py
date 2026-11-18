"""Command-line interface."""
import argparse
import json
import os
import re
import sys

from . import core, render
from .github import GitHub, GitHubError
from .llm import LLM, LLMError, detect_provider

KEY_ENVS = ("LLM_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY")


def parse_pr_ref(s):
    s = s.strip()
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", s) or re.match(r"^([^/\s]+)/([^#\s]+)#(\d+)$", s)
    if not m:
        sys.exit("Error: expected https://github.com/OWNER/REPO/pull/123 (or OWNER/REPO#123)")
    return m.group(1), m.group(2), int(m.group(3))


def build_parser():
    env = os.getenv
    p = argparse.ArgumentParser(prog="pr_reviewer",
                                description="Review a GitHub PR with any LLM. Just provide an API key.")
    p.add_argument("pr_url", help="https://github.com/OWNER/REPO/pull/NUMBER")
    p.add_argument("--api-key", help="LLM API key (or set LLM_API_KEY). Provider is auto-detected.")
    p.add_argument("--provider", default=env("PRR_PROVIDER") or None,
                   choices=["gemini", "groq", "openrouter", "openai", "anthropic", "custom"])
    p.add_argument("--model", default=env("PRR_MODEL") or None, help="override the default model")
    p.add_argument("--base-url", default=env("PRR_BASE_URL") or None, help="OpenAI-compatible endpoint (custom)")
    p.add_argument("--github-token", help="GitHub token (or GITHUB_TOKEN). Needed for --post and private repos.")
    p.add_argument("--post", action="store_true", help="post the review to the PR (summary + inline comments)")
    p.add_argument("--no-inline", action="store_true", help="with --post: only a summary comment, no inline comments")
    p.add_argument("--force", action="store_true", help="re-review even if this commit was already reviewed")
    p.add_argument("--config", help="path to a local .pr-reviewer.yml (default: read from the repo's base branch)")
    p.add_argument("--task", default="review", choices=["review", "ask", "tests", "notes", "docstrings"],
                   help="what to do (default: review)")
    p.add_argument("--ask", metavar="QUESTION", help="ask a question about the PR (same as --task ask)")
    p.add_argument("--from-comment", action="store_true",
                   help="read an '@pr-reviewer ...' command from env PRR_COMMENT (used by the GitHub Action)")
    p.add_argument("--fail-on", default=env("PRR_FAIL_ON") or None, choices=["medium", "high"],
                   help="exit with code 1 if the risk is at least this level (for CI gating)")
    p.add_argument("--json", action="store_true", help="print raw JSON instead of formatted text")
    return p


def find_key(args):
    if args.api_key:
        return args.api_key
    for name in KEY_ENVS:
        if os.getenv(name):
            return os.getenv(name)
    return None


def run(args):
    o, r, n = parse_pr_ref(args.pr_url)
    gh = GitHub(args.github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))
    task, arg = args.task, ""
    if args.from_comment:
        cmd = core.parse_command(os.getenv("PRR_COMMENT", ""))
        if not cmd:
            print("No @pr-reviewer command found; nothing to do.")
            return 0
        task, arg = cmd
    if args.ask is not None:
        task, arg = "ask", args.ask
    if task == "ask" and not arg:
        sys.exit("Error: ask needs a question, e.g. --ask \"why is this change needed?\"")

    if task == "help":
        print(render.HELP)
        if args.post:
            gh.create_comment(o, r, n, render.REPLY_MARKER + "\n" + render.HELP)
        return 0

    key = find_key(args)
    if not key and args.provider != "custom":
        sys.exit("No API key. Pass --api-key KEY or set LLM_API_KEY. Free keys: Gemini "
                 "(aistudio.google.com/apikey), Groq (console.groq.com/keys), OpenRouter (openrouter.ai/keys).")
    llm = LLM(args.provider or detect_provider(key or ""), key, args.model, args.base_url)

    if task != "review":
        core.answer_pr(gh, llm, o, r, n, task, arg, post=args.post, config_path=args.config)
        return 0

    res = core.review_pr(gh, llm, o, r, n, post=args.post, inline=not args.no_inline,
                         force=args.force or args.from_comment, config_path=args.config)
    if res is None:
        return 0
    if args.json:
        print(json.dumps(res.review, indent=2))
    else:
        render.terminal(res)
    if args.fail_on and core.SEV[res.review["risk"]] >= core.SEV[args.fail_on]:
        print(f"Failing: risk is {res.review['risk']} (--fail-on {args.fail_on}).", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (GitHubError, LLMError) as e:
        sys.exit(f"Error: {e}")
