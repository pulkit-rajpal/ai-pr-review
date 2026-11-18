"""Provider-agnostic LLM client: Gemini, Anthropic, and any OpenAI-compatible API."""
import json
import os
import re
import time

import requests


class LLMError(Exception):
    pass


PROVIDERS = {
    "gemini": dict(model="gemini-2.5-flash", limit=60_000),
    "anthropic": dict(model="claude-sonnet-5-5", limit=60_000),
    "groq": dict(base="https://api.groq.com/openai/v1", model="llama-3.3-70b-versatile", json_mode=True, limit=15_000),
    "openrouter": dict(base="https://openrouter.ai/api/v1", model="meta-llama/llama-3.3-70b-instruct:free",
                       json_mode=False, limit=30_000),
    "openai": dict(base="https://api.openai.com/v1", model="gpt-4o-mini", json_mode=True, limit=60_000),
    "custom": dict(json_mode=False, limit=15_000),
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


def to_gemini_schema(s):
    """Gemini wants upper-case type names."""
    if isinstance(s, dict):
        return {k: (v.upper() if k == "type" else to_gemini_schema(v)) for k, v in s.items()}
    if isinstance(s, list):
        return [to_gemini_schema(x) for x in s]
    return s


class LLM:
    def __init__(self, provider=None, key=None, model=None, base_url=None):
        provider = provider or detect_provider(key or "")
        if provider not in PROVIDERS:
            raise LLMError("Could not detect the provider from the key. "
                           "Add --provider (gemini|groq|openrouter|openai|anthropic|custom).")
        cfg = PROVIDERS[provider]
        self.provider, self.key = provider, key or "none"
        self.diff_limit = cfg["limit"]
        self.json_mode = cfg.get("json_mode", False)
        if provider == "custom":
            if not (base_url and model):
                raise LLMError("--provider custom needs --base-url and --model")
            self.base_url, self.model = base_url, model
        else:
            self.base_url = cfg.get("base")
            env_model = os.getenv(f"{provider.upper()}_MODEL")
            self.model = model or env_model or cfg["model"]

    # ---- public API ----
    def json(self, prompt, schema):
        """Return a parsed JSON object matching `schema` (retries once on malformed output)."""
        for attempt in range(2):
            p = prompt if attempt == 0 else prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
            raw = self._call(p, schema)
            try:
                return raw if isinstance(raw, dict) else extract_json(raw)
            except ValueError:
                continue
        raise LLMError("Model did not return valid JSON. Try again or use a stronger --model.")

    def text(self, prompt):
        out = self._call(prompt, None)
        return out if isinstance(out, str) else json.dumps(out)

    # ---- plumbing ----
    def _call(self, prompt, schema):
        if self.provider == "gemini":
            return self._gemini(prompt, schema)
        if self.provider == "anthropic":
            return self._anthropic(prompt, schema)
        return self._compat(prompt, schema)

    def _post(self, url, **kw):
        last = ""
        for attempt in range(4):
            try:
                r = requests.post(url, timeout=180, **kw)
            except requests.RequestException as e:
                last = str(e)
                if attempt == 3:
                    raise LLMError(f"network error: {last}")
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                try:
                    wait = float(r.headers.get("Retry-After", ""))
                except ValueError:
                    wait = 2 ** (attempt + 1)
                time.sleep(min(wait, 30))
                continue
            if r.status_code != 200:
                raise LLMError(f"{self.provider} API error {r.status_code}: {r.text[:300]}")
            return r.json()
        raise LLMError("unreachable")

    def _gemini(self, prompt, schema):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        gen = {"temperature": 0}
        if schema:
            gen.update(responseMimeType="application/json", responseSchema=to_gemini_schema(schema))
        data = self._post(url, headers={"x-goog-api-key": self.key},
                          json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen})
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise LLMError(f"Gemini returned no content (possibly blocked): {str(data)[:200]}")

    def _anthropic(self, prompt, schema):
        body = {"model": self.model, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]}
        if schema:
            body["tools"] = [{"name": "submit", "description": "Submit the result", "input_schema": schema}]
            body["tool_choice"] = {"type": "tool", "name": "submit"}
        data = self._post("https://api.anthropic.com/v1/messages",
                          headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"}, json=body)
        for block in data.get("content", []):
            if schema and block.get("type") == "tool_use":
                return block["input"]
            if not schema and block.get("type") == "text":
                return block["text"]
        raise LLMError("Anthropic returned no usable content")

    def _compat(self, prompt, schema):
        if schema:
            prompt += "\n\nRespond with ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)
        body = {"model": self.model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}
        if schema and self.json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post(self.base_url.rstrip("/") + "/chat/completions",
                          headers={"Authorization": f"Bearer {self.key}"}, json=body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LLMError(f"unexpected response: {str(data)[:200]}")
