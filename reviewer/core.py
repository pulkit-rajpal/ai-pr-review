"""Orchestration: fetch -> analyse -> review -> (optionally) post to GitHub."""
import re
import sys

from . import config as cfgmod
from . import diffutil, prompts, render, scanner
from .github import GitHubError
from .render import Result
from .schema import SCHEMA, validate

SEV = cfgmod.SEV


def note(msg):
    print(msg, file=sys.stderr)


def load_config(gh, o, r, pr, config_path=None):
    """Config comes from a local file if given, else from the PR's BASE branch.

    Reading it from the base (not the PR head) stops a PR author from weakening their own review.
    """
    try:
        if config_path:
            with open(config_path, encoding="utf-8") as fh:
                return cfgmod.parse_config(fh.read())
        for name in cfgmod.CONFIG_NAMES:
            text = gh.get_file(o, r, name, pr["base"]["ref"])
            if text:
                return cfgmod.parse_config(text)
    except (ValueError, OSError, GitHubError) as e:
        note(f"Warning: ignoring config ({e})")
    return cfgmod.default_config()


def find_summary(gh, o, r, n):
    """Return (comment_id, reviewed_sha) of our earlier summary comment, or None."""
    found = None
    for c in gh.issue_comments(o, r, n):
        sha = render.extract_sha(c.get("body"))
        if sha:
            found = (c["id"], sha)  # keep the latest
    return found


def linked_issues(gh, o, r, body):
    nums = []
    for m in prompts.LINK_RX.finditer(body or ""):
        if int(m.group(1)) not in nums:
            nums.append(int(m.group(1)))
    out = []
    for n in nums[:3]:
        try:
            i = gh.issue(o, r, n)
        except GitHubError:
            continue
        out.append(f"#{n} {i.get('title', '')}\n{(i.get('body') or '')[:1000]}")
    return "\n\n".join(out)


def gather_context(gh, o, r, files, sha, count, budget):
    """Fetch full contents of the most-changed files so the model sees surrounding code."""
    cands = sorted((f for f in files if f.status != "deleted"), key=lambda f: len(f.added), reverse=True)[:count]
    if not cands or budget <= 0:
        return ""
    per = max(500, budget // len(cands))
    parts = []
    for f in cands:
        try:
            text = gh.get_file(o, r, f.path, sha)
        except GitHubError:
            text = None
        if text:
            parts.append(f"### {f.path}\n{text[:per]}")
    return "\n\n".join(parts)


def _prepare(gh, llm, o, r, n, pr, cfg, diff_text):
    files = diffutil.filter_files(diffutil.parse_diff(diff_text), cfg["ignore"])
    text, shown, skipped = diffutil.render_diff(files, llm.diff_limit)
    ctx = gather_context(gh, o, r, files, pr["head"]["sha"], cfg["context_files"], llm.diff_limit // 3)
    instr = ([cfg["instructions"]] if cfg["instructions"] else []) + [str(x) for x in cfg["learnings"]] \
        + cfgmod.instructions_for(cfg, [f.path for f in files])
    return files, text, skipped, ctx, instr


def review_pr(gh, llm, o, r, n, *, post=False, inline=True, force=False, config_path=None):
    """Review a PR. Returns a Result, or None if nothing to do (already reviewed / no reviewable files)."""
    pr = gh.pr(o, r, n)
    head = pr["head"]["sha"]
    cfg = load_config(gh, o, r, pr, config_path)

    prior = find_summary(gh, o, r, n) if post else None
    since = None
    if prior and not force:
        if prior[1] == head:
            note("Already reviewed this commit. Use --force to review again.")
            return None
        since = prior[1]
    diff_text = None
    if since:
        try:
            diff_text = gh.compare_diff(o, r, since, head)
        except GitHubError:
            note("Could not diff against the last reviewed commit (force-push?). Reviewing the full PR.")
            since = None
    if diff_text is None:
        diff_text = gh.diff(o, r, n)

    files, text, skipped, ctx, instr = _prepare(gh, llm, o, r, n, pr, cfg, diff_text)
    if not text:
        note("No reviewable changes (everything filtered out or empty).")
        return None

    static = scanner.scan(files) if cfg["static_checks"] else []
    prompt = prompts.review_prompt(
        title=pr.get("title", ""), body=pr.get("body") or "(none)", diff=text, max_issues=12,
        instructions=instr, static_found=static, context=ctx,
        issues_ctx=linked_issues(gh, o, r, pr.get("body")), skipped=skipped)
    review = validate(llm.json(prompt, SCHEMA), {f.path: f.valid_lines for f in files})

    for s in static:  # merge static findings, de-duplicated against the model's own
        if not any(i["file"] == s["file"] and i["line"] == s["line"] for i in review["issues"]):
            review["issues"].append(s)
    if any(s["severity"] == "high" for s in static) and review["risk"] != "high":
        review["risk"] = "high"
        review["risk_reason"] += " (raised to high: static checks found a likely committed secret)"
    review["issues"].sort(key=lambda i: -SEV[i["severity"]])

    res = Result(review=review, url=pr.get("html_url", f"{o}/{r}#{n}"), head_sha=head, since_sha=since,
                 skipped=skipped, provider=llm.provider, model=llm.model)
    if post:
        post_result(gh, o, r, n, res, cfg, inline, prior[0] if prior else None)
    return res


def post_result(gh, o, r, n, res, cfg, inline, summary_id):
    """Post inline comments (one PR review) then create/update the summary comment."""
    if inline:
        try:
            existing = {(c["path"], c.get("line") or c.get("original_line"), c["body"][:80])
                        for c in gh.review_comments(o, r, n) if render.INLINE_MARKER in c.get("body", "")}
            batch, picked = [], []
            floor = SEV[cfg["min_severity"]]
            for i in res.review["issues"]:
                if not i.get("line") or SEV[i["severity"]] < floor or len(picked) >= cfg["max_inline_comments"]:
                    continue
                body = render.inline_body(i)
                if (i["file"], i["line"], body[:80]) in existing:
                    i["posted_inline"] = True  # already on the PR from an earlier run
                    continue
                batch.append({"path": i["file"], "line": i["line"], "side": "RIGHT", "body": body})
                picked.append(i)
            if batch:
                gh.create_review(o, r, n, res.head_sha, batch)
                for i in picked:
                    i["posted_inline"] = True
        except GitHubError as e:
            note(f"Could not post inline comments ({e}); listing them in the summary instead.")
            for i in res.review["issues"]:
                i["posted_inline"] = False
    body = render.markdown(res)
    try:
        if summary_id:
            gh.update_comment(o, r, summary_id, body)
        else:
            gh.create_comment(o, r, n, body)
        note("Posted review to the PR.")
    except GitHubError as e:
        note(f"Could not post to GitHub ({e}). The review is printed above. "
             "(Fork PRs get a read-only token; use a token with pull-request write access.)")


def answer_pr(gh, llm, o, r, n, task, arg, *, post=False, config_path=None):
    """ask / tests / notes / docstrings: free-text answer about the PR."""
    pr = gh.pr(o, r, n)
    cfg = load_config(gh, o, r, pr, config_path)
    files, text, _, ctx, instr = _prepare(gh, llm, o, r, n, pr, cfg, gh.diff(o, r, n))
    if not text:
        note("No reviewable changes.")
        return None
    out = llm.text(prompts.task_prompt(task, arg, title=pr.get("title", ""), body=pr.get("body") or "(none)",
                                       diff=text, context=ctx, instructions=instr)).strip()
    print(out)
    if post:
        head = f"> {arg}\n\n" if task == "ask" and arg else ""
        try:
            gh.create_comment(o, r, n, f"{render.REPLY_MARKER}\n{head}{out}")
            note("Posted reply to the PR.")
        except GitHubError as e:
            note(f"Could not post to GitHub ({e}).")
    return out


MENTION = re.compile(r"@pr-reviewer\b", re.I)


def parse_command(text):
    """Parse a PR comment into (task, arg), or None if it doesn't mention @pr-reviewer."""
    if not MENTION.search(text or ""):
        return None
    rest = MENTION.sub("", text, count=1).strip()
    first, _, tail = rest.partition(" ")
    f = first.lower().strip(":,.")
    if not rest or f == "help":
        return ("help", "")
    if f in ("review", "re-review"):
        return ("review", "")
    if f in ("test", "tests"):
        return ("tests", tail.strip())
    if f in ("notes", "release-notes"):
        return ("notes", tail.strip())
    if f in ("docstrings", "docs"):
        return ("docstrings", tail.strip())
    if f == "ask":
        return ("ask", tail.strip())
    return ("ask", rest)
