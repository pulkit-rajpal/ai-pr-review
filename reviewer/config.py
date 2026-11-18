"""Per-repo configuration from `.pr-reviewer.yml`."""
import fnmatch

CONFIG_NAMES = [".pr-reviewer.yml", ".pr-reviewer.yaml"]
SEV = {"low": 0, "medium": 1, "high": 2}

DEFAULTS = {
    "instructions": "",          # extra review guidance for the whole repo
    "path_instructions": [],     # [{path: "src/auth/*", instructions: "..."}]
    "learnings": [],             # standing rules, e.g. "We use tabs", "Ignore generated/ folder"
    "ignore": [],                # extra glob patterns to skip
    "max_inline_comments": 15,
    "min_severity": "low",       # lowest severity posted as an inline comment
    "context_files": 3,          # how many changed files to fetch in full for context
    "static_checks": True,
}


def parse_config(text):
    """Parse YAML text into a config dict merged over defaults. Raises ValueError on bad input."""
    try:
        import yaml
    except ImportError:
        raise ValueError("PyYAML is required for .pr-reviewer.yml (pip install pyyaml)")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {e}")
    if not isinstance(data, dict):
        raise ValueError("config must be a mapping")
    cfg = dict(DEFAULTS)
    for k, v in data.items():
        if k in DEFAULTS:
            cfg[k] = v
    if cfg["min_severity"] not in SEV:
        raise ValueError("min_severity must be low, medium or high")
    for k in ("ignore", "learnings", "path_instructions"):
        if not isinstance(cfg[k], list):
            raise ValueError(f"{k} must be a list")
    cfg["max_inline_comments"] = int(cfg["max_inline_comments"])
    cfg["context_files"] = int(cfg["context_files"])
    return cfg


def default_config():
    return dict(DEFAULTS)


def instructions_for(cfg, paths):
    """Path-specific instruction strings that apply to any of the changed paths."""
    out = []
    for item in cfg["path_instructions"]:
        if not isinstance(item, dict) or "path" not in item:
            continue
        pat = item["path"]
        if any(fnmatch.fnmatch(p, pat) for p in paths):
            out.append(f"For files matching `{pat}`: {item.get('instructions', '')}")
    return out
