"""Terminal output and GitHub-flavoured Markdown for PR comments."""
import re
import sys
from dataclasses import dataclass, field

EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
SUMMARY_MARKER = "pr-reviewer:summary"
INLINE_MARKER = "<!-- pr-reviewer:inline -->"
REPLY_MARKER = "<!-- pr-reviewer:reply -->"

HELP = """**PR Reviewer commands** — mention me in a comment:

- `@pr-reviewer review` — re-run a full review
- `@pr-reviewer ask <question>` (or just `@pr-reviewer <question>`) — ask about this PR
- `@pr-reviewer tests` — generate unit tests for the changes
- `@pr-reviewer docstrings` — generate docstrings
- `@pr-reviewer notes` — draft release notes
"""


@dataclass
class Result:
    review: dict
    url: str
    head_sha: str
    since_sha: str = None
    skipped: list = field(default_factory=list)
    provider: str = ""
    model: str = ""


def summary_marker(sha):
    return f"<!-- {SUMMARY_MARKER} sha={sha} -->"


def extract_sha(body):
    m = re.search(SUMMARY_MARKER + r" sha=([0-9a-f]{7,40})", body or "")
    return m.group(1) if m else None


def loc(i):
    return f"{i['file']}:{i['line']}" if i.get("line") else i["file"]


def inline_body(i):
    b = f"{EMOJI[i['severity']]} **{i['severity'].upper()}** — {i['description']}"
    if i.get("suggestion"):
        b += f"\n\n**Suggested fix:** {i['suggestion']}"
    if i.get("suggested_code"):
        b += f"\n\n```suggestion\n{i['suggested_code']}\n```"
    if i.get("source") == "static":
        b += "\n\n_Static check_"
    return b + "\n" + INLINE_MARKER


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def markdown(res):
    """Summary comment body. Issues already posted inline (issue['posted_inline']) are only counted."""
    r = res.review
    lines = [summary_marker(res.head_sha),
             f"## 🤖 PR Review — {EMOJI[r['risk']]} {r['risk'].capitalize()} risk", "",
             f"> {r['risk_reason']}", "",
             f"**AI-generated code (estimate):** ~{r['ai_generated_percent']}% — {r['ai_generated_reason']}", ""]
    if res.since_sha:
        lines += [f"_Incremental review of changes since `{res.since_sha[:7]}`._", ""]
    lines += ["### Summary", r["summary"], ""]
    if r["walkthrough"]:
        lines += ["### Walkthrough", "| File | Change |", "|---|---|"]
        lines += [f"| `{_cell(w['file'])}` | {_cell(w['change'])} |" for w in r["walkthrough"]]
        lines.append("")
    if r["sequence_diagram"]:
        lines += ["### Sequence diagram", "```mermaid", r["sequence_diagram"], "```", ""]
    inline = [i for i in r["issues"] if i.get("posted_inline")]
    rest = [i for i in r["issues"] if not i.get("posted_inline")]
    if rest:
        lines.append(f"### Issues ({len(rest)})")
        for i in rest:
            tag = " _(static check)_" if i.get("source") == "static" else ""
            lines.append(f"- {EMOJI[i['severity']]} **{i['severity'].upper()}** `{loc(i)}`{tag} — {i['description']}")
            if i.get("suggestion"):
                lines.append(f"  - Fix: {i['suggestion']}")
        lines.append("")
    if inline:
        lines += [f"💬 {len(inline)} inline comment(s) posted on the diff.", ""]
    if not r["issues"]:
        lines += ["No issues found. ✅", ""]
    if res.skipped:
        lines += [f"<sub>Not reviewed (too large for the model's context): {', '.join(res.skipped)}</sub>", ""]
    lines.append(f"<sub>Reviewed `{res.head_sha[:7]}` with {res.provider}/{res.model}. "
                 "AI output can be wrong; the AI-generated percentage is a heuristic guess. "
                 "Comment `@pr-reviewer help` for commands.</sub>")
    return "\n".join(lines)


def terminal(res, out=None):
    out = out or sys.stdout
    r = res.review
    colors = {"high": "\033[91m", "medium": "\033[93m", "low": "\033[92m"}
    c, end = (colors[r["risk"]], "\033[0m") if out.isatty() else ("", "")
    p = lambda s="": print(s, file=out)
    p(f"\nPR: {res.url}")
    if res.since_sha:
        p(f"(incremental: changes since {res.since_sha[:7]})")
    p(f"Risk: {c}{r['risk'].upper()}{end} - {r['risk_reason']}")
    p(f"AI-generated code (estimate): ~{r['ai_generated_percent']}% - {r['ai_generated_reason']}")
    p(f"\nSummary:\n  {r['summary']}")
    if r["walkthrough"]:
        p("\nWalkthrough:")
        for w in r["walkthrough"]:
            p(f"  {w['file']}: {w['change']}")
    if r["sequence_diagram"]:
        p("\nSequence diagram (Mermaid):\n" + "\n".join("  " + x for x in r["sequence_diagram"].splitlines()))
    if r["issues"]:
        p(f"\nIssues ({len(r['issues'])}):")
        for i in r["issues"]:
            tag = " (static check)" if i.get("source") == "static" else ""
            p(f"  [{i['severity'].upper()}] {loc(i)}{tag}\n    {i['description']}")
            if i.get("suggestion"):
                p(f"    Fix: {i['suggestion']}")
    else:
        p("\nNo issues found.")
    if res.skipped:
        p(f"\nNot reviewed (too large): {', '.join(res.skipped)}")
