"""Parse unified diffs, track new-file line numbers, filter ignored files."""
import fnmatch
import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

DEFAULT_IGNORE = [
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
    "go.sum", "*.min.js", "*.min.css", "*.map", "dist/*", "build/*", "node_modules/*", "vendor/*",
    "*.svg", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.pdf", "*.woff", "*.woff2", "*.snap",
]


@dataclass
class FileDiff:
    path: str
    status: str = "modified"  # added | deleted | modified
    binary: bool = False
    lines: list = field(default_factory=list)  # (kind, new_lineno|None, text); kind in "+", "-", " "

    @property
    def added(self):
        return [(n, t) for k, n, t in self.lines if k == "+"]

    @property
    def valid_lines(self):
        """New-file line numbers that GitHub lets us attach an inline comment to."""
        return {n for k, n, _ in self.lines if k in ("+", " ") and n}


def parse_diff(text):
    files, cur, in_hunk, new = [], None, False, 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            cur = FileDiff(path=m.group(2) if m else raw)
            files.append(cur)
            in_hunk = False
            continue
        if cur is None:
            continue
        if not in_hunk:
            if raw.startswith("new file mode"):
                cur.status = "added"
            elif raw.startswith("deleted file mode"):
                cur.status = "deleted"
            elif raw.startswith("+++ b/"):
                cur.path = raw[6:]
            elif raw.startswith("rename to "):
                cur.path = raw[10:]
            elif raw.startswith("Binary files"):
                cur.binary = True
        m = HUNK.match(raw)
        if m:
            new, in_hunk = int(m.group(1)), True
            continue
        if not in_hunk:
            continue
        if raw.startswith("+"):
            cur.lines.append(("+", new, raw[1:]))
            new += 1
        elif raw.startswith("-"):
            cur.lines.append(("-", None, raw[1:]))
        elif raw.startswith("\\"):
            continue
        else:
            cur.lines.append((" ", new, raw[1:] if raw.startswith(" ") else raw))
            new += 1
    return files


def is_ignored(path, patterns):
    base = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(base, p) for p in patterns)


def filter_files(files, extra_ignore=()):
    pats = DEFAULT_IGNORE + list(extra_ignore)
    return [f for f in files if not f.binary and not is_ignored(f.path, pats)]


def render_diff(files, budget):
    """Render diffs with new-file line numbers in the left column, within a char budget.

    Returns (text, shown_paths, skipped_paths). Skipped = didn't fit; truncated files count as shown.
    """
    cap = max(2000, budget // 3)
    out, shown, skipped, used = [], [], [], 0
    for f in files:
        if not f.lines:
            continue
        rows = [f"### {f.path} ({f.status})"]
        for k, n, t in f.lines:
            rows.append(f"{n:>5} {k} {t}" if n else f"      {k} {t}")
        text = "\n".join(rows)
        if len(text) > cap:
            text = text[:cap] + "\n... [rest of this file's diff truncated]"
        if used + len(text) > budget:
            skipped.append(f.path)
            continue
        out.append(text)
        used += len(text)
        shown.append(f.path)
    return "\n\n".join(out), shown, skipped
