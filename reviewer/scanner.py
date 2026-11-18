"""Fast, deterministic static checks on added lines (no LLM, no extra tools).

This is a lightweight pattern scanner, not a replacement for Semgrep/CodeQL.
"""
import re

PLACEHOLDER = re.compile(r"(?i)example|your[_-]|xxx|changeme|placeholder|<[^>]+>|\$\{|dummy|test")

RULES = [
    ("high", "Possible AWS access key committed", re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "Remove it, rotate the key, and load secrets from the environment or a secret manager."),
    ("high", "Private key material committed", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
     "Remove it, rotate the key, and store it in a secret manager."),
    ("high", "Possible GitHub token committed", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
     "Revoke the token immediately and use encrypted secrets."),
    ("high", "Possible Slack token committed", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
     "Revoke the token and use encrypted secrets."),
    ("high", "Possible Google API key committed", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
     "Revoke the key and load it from the environment."),
    ("medium", "Hard-coded credential-looking value",
     re.compile(r"""(?i)(api[_-]?key|secret|passwd|password|token)\w*\s*[:=]\s*["'][A-Za-z0-9_\-/+=.]{12,}["']"""),
     "Load secrets from environment variables or a secret manager."),
    ("medium", "eval() on dynamic input can execute arbitrary code", re.compile(r"\beval\s*\("),
     "Avoid eval; parse data explicitly (e.g. json.loads / ast.literal_eval)."),
    ("medium", "Shell command execution with shell=True", re.compile(r"shell\s*=\s*True"),
     "Pass an argument list without shell=True to avoid command injection."),
    ("medium", "os.system runs a shell command", re.compile(r"\bos\.system\s*\("),
     "Use subprocess.run with an argument list."),
    ("medium", "Unsafe deserialization with pickle", re.compile(r"\bpickle\.loads?\s*\("),
     "Never unpickle untrusted data; use JSON or another safe format."),
    ("medium", "yaml.load without a safe loader", re.compile(r"\byaml\.load\s*\((?!.*Loader)"),
     "Use yaml.safe_load."),
    ("medium", "TLS certificate verification disabled", re.compile(r"verify\s*=\s*False"),
     "Keep certificate verification on; fix the trust store instead."),
    ("medium", "innerHTML / dangerouslySetInnerHTML can enable XSS",
     re.compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML"),
     "Use textContent or sanitize the HTML first."),
]


def scan(files):
    """Return issues (same shape as the LLM's) for added lines that match a rule."""
    found = []
    for f in files:
        if f.status == "deleted":
            continue
        for lineno, text in f.added:
            for sev, desc, rx, fix in RULES:
                m = rx.search(text)
                if not m:
                    continue
                if desc.startswith("Hard-coded") and PLACEHOLDER.search(m.group(0)):
                    continue
                found.append({
                    "file": f.path, "line": lineno, "severity": sev,
                    "description": desc, "suggestion": fix, "suggested_code": None, "source": "static",
                })
                break  # one finding per line
    return found
