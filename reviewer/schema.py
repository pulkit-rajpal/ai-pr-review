"""Structured-output schema for reviews, plus strict validation of model output."""
import re

SEVERITIES = ("high", "medium", "low")

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "risk": {"type": "string", "enum": list(SEVERITIES)},
        "risk_reason": {"type": "string"},
        "ai_generated_percent": {"type": "integer"},
        "ai_generated_reason": {"type": "string"},
        "walkthrough": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "change": {"type": "string"}},
                "required": ["file", "change"],
            },
        },
        "sequence_diagram": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "description": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "suggested_code": {"type": "string"},
                },
                "required": ["file", "severity", "description", "suggestion"],
            },
        },
    },
    "required": [
        "summary", "risk", "risk_reason", "ai_generated_percent",
        "ai_generated_reason", "walkthrough", "issues",
    ],
}

REQUIRED = ("summary", "risk", "risk_reason", "ai_generated_percent", "ai_generated_reason", "issues")


def _clean_diagram(text):
    text = re.sub(r"^```(?:mermaid)?\s*|\s*```$", "", (text or "").strip())
    return text if text.startswith("sequenceDiagram") else ""


def validate(raw, valid_lines=None):
    """Normalise a model's review. `valid_lines` maps path -> set of commentable line numbers.

    Lines that can't be attached to the diff are dropped to None (the issue then goes in the summary).
    Raises ValueError if required fields are missing or malformed.
    """
    if not isinstance(raw, dict):
        raise ValueError("review is not an object")
    for k in REQUIRED:
        if k not in raw:
            raise ValueError(f"missing field: {k}")
    risk = str(raw["risk"]).lower()
    if risk not in SEVERITIES:
        raise ValueError("bad risk label")
    try:
        pct = max(0, min(100, int(float(raw["ai_generated_percent"]))))
    except (TypeError, ValueError):
        raise ValueError("bad ai_generated_percent")
    if not isinstance(raw["issues"], list):
        raise ValueError("issues must be a list")

    issues = []
    for i in raw["issues"]:
        if not isinstance(i, dict) or not i.get("description"):
            continue
        sev = str(i.get("severity", "low")).lower()
        path = str(i.get("file", ""))
        line = i.get("line")
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        if valid_lines is not None and (line not in valid_lines.get(path, ())):
            line = None
        code = i.get("suggested_code")
        if not line or not isinstance(code, str) or not code.strip() or "```" in code:
            code = None
        issues.append({
            "file": path, "line": line,
            "severity": sev if sev in SEVERITIES else "low",
            "description": str(i["description"]),
            "suggestion": str(i.get("suggestion", "")),
            "suggested_code": code, "source": "ai",
        })

    walk = []
    for w in raw.get("walkthrough") or []:
        if isinstance(w, dict) and w.get("file"):
            walk.append({"file": str(w["file"]), "change": str(w.get("change", ""))})

    return {
        "summary": str(raw["summary"]),
        "risk": risk,
        "risk_reason": str(raw["risk_reason"]),
        "ai_generated_percent": pct,
        "ai_generated_reason": str(raw["ai_generated_reason"]),
        "walkthrough": walk,
        "sequence_diagram": _clean_diagram(raw.get("sequence_diagram")),
        "issues": issues,
    }
