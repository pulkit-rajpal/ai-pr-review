"""Minimal GitHub REST client (only what the reviewer needs)."""
import requests

API = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(f"GitHub {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, token=None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "pr-reviewer", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
        self.has_token = bool(token)

    def _req(self, method, path, accept="application/vnd.github+json", **kw):
        r = self.s.request(method, API + path, headers={"Accept": accept}, timeout=30, **kw)
        if r.status_code >= 400:
            hint = ""
            if r.status_code in (401, 403, 404) and not self.has_token:
                hint = " (private repo or rate limit? set GITHUB_TOKEN)"
            raise GitHubError(r.status_code, r.text[:200] + hint)
        return r

    def pr(self, o, r, n):
        return self._req("GET", f"/repos/{o}/{r}/pulls/{n}").json()

    def diff(self, o, r, n):
        return self._req("GET", f"/repos/{o}/{r}/pulls/{n}", accept="application/vnd.github.v3.diff").text

    def compare_diff(self, o, r, base, head):
        return self._req("GET", f"/repos/{o}/{r}/compare/{base}...{head}", accept="application/vnd.github.v3.diff").text

    def get_file(self, o, r, path, ref):
        try:
            return self._req("GET", f"/repos/{o}/{r}/contents/{path}", accept="application/vnd.github.raw",
                             params={"ref": ref}).text
        except GitHubError as e:
            if e.status == 404:
                return None
            raise

    def issue(self, o, r, n):
        return self._req("GET", f"/repos/{o}/{r}/issues/{n}").json()

    def _paged(self, path):
        out = []
        for page in range(1, 6):
            batch = self._req("GET", path, params={"per_page": 100, "page": page}).json()
            out += batch
            if len(batch) < 100:
                break
        return out

    def issue_comments(self, o, r, n):
        return self._paged(f"/repos/{o}/{r}/issues/{n}/comments")

    def review_comments(self, o, r, n):
        return self._paged(f"/repos/{o}/{r}/pulls/{n}/comments")

    def create_comment(self, o, r, n, body):
        return self._req("POST", f"/repos/{o}/{r}/issues/{n}/comments", json={"body": body}).json()

    def update_comment(self, o, r, comment_id, body):
        return self._req("PATCH", f"/repos/{o}/{r}/issues/comments/{comment_id}", json={"body": body}).json()

    def create_review(self, o, r, n, commit_sha, comments):
        """Post inline comments as one PR review (event=COMMENT, never approves/blocks)."""
        return self._req("POST", f"/repos/{o}/{r}/pulls/{n}/reviews",
                         json={"commit_id": commit_sha, "event": "COMMENT", "comments": comments}).json()
